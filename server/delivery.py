from base64 import b64decode, b64encode
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from uuid import UUID
import os

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints, model_validator

from server.db import connect
from server.delivery_devices import authorize_device, delivery_devices_router, token_matches
from server.delivery_uploads import MAX_ARTIFACT, Chunk, Manifest, assemble, declare, store_chunk
from server.errors import Problem
from server.github_issues import GitHubIssues, active_connection, connection
from server.project_context import handoff_outdated, latest_requirement
from server.projects import membership, record
from server.requirement_groups import group_handoff, group_message, group_scope


Commit = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{40}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
Bounded = Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class DispatchInput(BaseModel):
    id: UUID
    secret: Digest | None = None
    desktop: bool = False
    automatic: bool = False
    revision: int = Field(ge=1)
    plan_id: UUID
    commit: Commit | None = None

    @model_validator(mode='after')
    def transport(self):
        if self.desktop == (self.secret is not None):
            raise ValueError('Use a paired device or an individual dispatch token')
        if self.automatic and (not self.desktop or self.commit is not None):
            raise ValueError('Automatic coding starts at the pinned base on a paired device')
        return self


class DeviceInput(BaseModel):
    device: UUID


class CheckResult(BaseModel):
    key: Bounded
    run: Bounded
    command: str = Field(min_length=1, max_length=4000)
    environment: Bounded
    exit_code: int
    output: str = Field(max_length=16000)


class CodingEvidence(BaseModel):
    run: UUID
    files: list[Bounded] = Field(min_length=1, max_length=20)


class ExecutionReceipt(DeviceInput):
    commit: Commit
    digest: Digest | None = None
    artifact: str | None = Field(default=None, max_length=((MAX_ARTIFACT + 2) // 3) * 4)
    status: Literal['succeeded', 'failed', 'cancelled', 'needs_review']
    checks: list[CheckResult] = Field(max_length=40)
    coding: CodingEvidence | None = None

    @model_validator(mode='after')
    def check_terminal(self):
        if self.status == 'succeeded' and (not self.checks or any(row.exit_code != 0 or not row.output.strip() for row in self.checks)):
            raise ValueError('Successful execution requires command output and zero exit codes')
        if self.status == 'failed' and (not self.checks or all(row.exit_code == 0 for row in self.checks)):
            raise ValueError('Failed execution requires a failed command')
        return self


class BuildInput(BaseModel):
    revision: int = Field(ge=1)
    candidate_id: UUID
    ci_run_id: int = Field(gt=0, lt=2**63)


class PublishInput(BaseModel):
    revision: int = Field(ge=1)
    candidate_id: UUID


def current_plan(db, project, plan_id):
    plan = db.execute('''SELECT * FROM mad_version_plan_revisions WHERE project_id=%s AND plan_id=%s
                         ORDER BY revision DESC LIMIT 1''', (project, plan_id)).fetchone()
    if not plan:
        raise Problem('version_not_found', 404)
    return plan


def dispatch_row(db, identifier):
    row = db.execute('SELECT * FROM mad_delivery_dispatches WHERE id=%s', (identifier,)).fetchone()
    if not row:
        raise Problem('dispatch_not_found', 404)
    return row


def view(row):
    return {**{key: value for key, value in row.items() if key not in {'secret_digest', 'artifact'}},
            'has_artifact': bool(row.get('has_artifact', row.get('artifact') is not None))}


def authorize_actor(db, group, actor, stage):
    role = db.execute('SELECT role FROM mad_project_roles WHERE project_id=%s AND user_id=%s',
                      (group['project_id'], actor)).fetchone()
    if not role or role['role'] != stage or group[stage + '_id'] != actor:
        raise Problem('permission_denied', 403)


def fresh(db, row, group):
    expected = row['input']
    linked = connection(db, row['project_id'])
    return (linked and linked['enabled'] and linked['revision'] == expected['connection_revision']
            and linked['repository_id'] == expected['repository_id']
            and group['stage'] == row['stage'] and group['state'] == 'active' and group['revision'] == row['group_revision']
            and group['requirement_revision'] == expected['requirement_revision']
            and current_plan(db, row['project_id'], UUID(expected['plan_id']))['revision'] == expected['plan_revision'])


def terminal_handoff(db, service, group, dispatch, receipt):
    stage = dispatch['stage']
    run = service.group_run(db, group, dispatch['id'], external=True)
    proof = [f'{item.key} · exit {item.exit_code} · tebb {item.run}' for item in receipt.checks]
    cases = [{'criterion': index, 'result': 'passed', 'evidence': proof[index - 1]}
             for index in range(1, len(dispatch['input']['criteria']) + 1)] if stage == 'testing' else []
    verdict = 'passed' if stage == 'testing' else 'delivered'
    summary = f'tebb {"独立验收通过" if stage == "testing" else "候选已回传"} · {receipt.commit[:12]}'
    db.execute('''INSERT INTO mad_handoffs (run_id,project_id,requirement_id,requirement_revision,author,role,summary,
                    evidence,context_references,verdict,candidate_run,case_results)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'[]',%s,%s,%s)''',
               (run['id'], run['project_id'], run['requirement_id'], run['requirement_revision'], run['user_id'], stage,
                summary, Jsonb(proof), verdict, run['candidate_run'], Jsonb(cases)))
    db.execute('UPDATE mad_agent_runs SET checkpoint=1 WHERE id=%s', (run['id'],))
    group_handoff(db, run, {'verdict': verdict})


def artifact_bytes(data):
    if data.artifact is None:
        raise Problem('artifact_required', 409)
    try:
        payload = b64decode(data.artifact, validate=True)
    except ValueError as exc:
        raise Problem('artifact_mismatch', 422) from exc
    if not payload or len(payload) > MAX_ARTIFACT or sha256(payload).hexdigest() != data.digest:
        raise Problem('artifact_mismatch', 422)
    return payload


def approved_candidate(db, project, plan_id, revision, candidate_id):
    plan = current_plan(db, project, plan_id)
    if plan['revision'] != revision or plan['status'] != 'committed' or len(plan['requirement_ids']) != 1:
        raise Problem('delivery_plan_changed', 409)
    candidate = dispatch_row(db, candidate_id)
    group = db.execute('SELECT * FROM mad_requirement_groups WHERE project_id=%s AND requirement_id=%s',
                       (project, plan['requirement_ids'][0])).fetchone()
    if (not group or candidate['group_id'] != group['id'] or group['stage'] != 'completed'
            or candidate['stage'] != 'development' or candidate['status'] != 'succeeded'
            or candidate['input']['plan_id'] != str(plan_id) or candidate['input']['plan_revision'] != revision
            or candidate['input']['requirement_revision'] != group['requirement_revision']):
        raise Problem('delivery_evidence_required', 409)
    handoff = db.execute('SELECT * FROM mad_handoffs WHERE run_id=%s', (group['last_handoff'],)).fetchone()
    check = dispatch_row(db, group['last_handoff']) if handoff and handoff['candidate_run'] == candidate_id else None
    if (not check or check['stage'] != 'testing' or check['status'] != 'succeeded'
            or check['actor'] == candidate['actor'] or check['receipt']['digest'] != candidate['receipt']['digest']
            or check['input']['plan_id'] != str(plan_id) or check['input']['plan_revision'] != revision
            or handoff_outdated(db, handoff)):
        raise Problem('delivery_evidence_required', 409)
    return plan, group, candidate, check


def verify_ci(github, candidate, run_id):
    if candidate['input'].get('automatic'):
        raise Problem('delivery_patch_needs_commit', 409)
    repository_id = candidate['input']['repository_id']
    repository, _ = github.repository(f'/repositories/{repository_id}', repository_id)
    result = github.read(f'/repositories/{repository_id}/actions/runs/{run_id}')
    if (result.get('id') != run_id or result.get('head_sha') != candidate['receipt']['commit']
            or result.get('status') != 'completed' or result.get('conclusion') != 'success'
            or result.get('event') != 'push' or result.get('path') != '.github/workflows/ci.yml'
            or (result.get('repository') or {}).get('id') != repository_id
            or (result.get('head_repository') or {}).get('id') != repository_id):
        raise Problem('delivery_ci_mismatch', 409)
    return {'run_id': run_id, 'commit': result['head_sha'], 'url': f'https://github.com/{repository}/actions/runs/{run_id}'}


def local_receipt(root, candidate, plan):
    digest = candidate['receipt']['digest']
    payload = bytes(candidate['artifact'])
    if sha256(payload).hexdigest() != digest:
        raise Problem('artifact_mismatch', 409)
    filename = f"candidate-{candidate['id']}.patch"
    if not root:
        raise Problem('release_channel_unavailable', 503)
    folder = Path(root) / str(plan['project_id']) / str(plan['plan_id']) / str(plan['revision'])
    if os.name == 'nt':
        folder = folder.resolve()
        value = str(folder)
        if not value.startswith('\\\\?\\'):
            folder = Path('\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value)
    target = folder / filename
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            file = NamedTemporaryFile(dir=folder, delete=False)
            temporary = Path(file.name)
            try:
                with file:
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
                os.link(temporary, target)
            except FileExistsError:
                pass
            finally:
                temporary.unlink()
        if target.stat().st_size != len(payload) or target.read_bytes() != payload:
            raise Problem('artifact_mismatch', 409)
        published = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc)
    except OSError as exc:
        raise Problem('release_channel_unavailable', 503) from exc
    return {'channel': 'local-pilot', 'filename': filename, 'digest': digest, 'commit': candidate['receipt']['commit']}, published


def delivery_router(settings, identity, service, github_transport=None):
    router = APIRouter(prefix='/api')
    devices = delivery_devices_router(settings.database_url, identity)
    authenticated = Depends(identity)
    github = GitHubIssues(settings.github_read_token, github_transport)

    @router.post('/groups/{group_id}/dispatches')
    def dispatch(group_id: UUID, data: DispatchInput, user=authenticated):
        with connect(settings.database_url) as db:
            group = group_scope(db, group_id, user['id'])
            prior = db.execute('SELECT * FROM mad_delivery_dispatches WHERE id=%s', (data.id,)).fetchone()
            secret = sha256((data.secret or '').encode()).digest()
            if prior:
                if (prior['group_id'] != group_id or prior['actor'] != user['id'] or prior['group_revision'] != data.revision
                        or prior['input']['plan_id'] != str(data.plan_id) or prior['input']['requested_commit'] != data.commit
                        or prior['desktop'] != data.desktop or bool(prior['input'].get('automatic')) != data.automatic
                        or not compare_digest(prior['secret_digest'], secret)):
                    raise Problem('operation_conflict', 409)
                authorize_actor(db, group, user['id'], prior['stage'])
                return view(prior)
            stage = group['stage']
            if stage not in {'development', 'testing'} or group['state'] != 'active' or group['auto_run']:
                raise Problem('stage_changed', 409)
            authorize_actor(db, group, user['id'], stage)
            if group['revision'] != data.revision or db.execute(
                '''SELECT 1 FROM mad_agent_runs WHERE group_id=%s AND group_revision=%s UNION ALL
                   SELECT 1 FROM mad_delivery_dispatches WHERE group_id=%s AND group_revision=%s
                     AND status IN ('queued','running','needs_review') LIMIT 1''',
                (group_id, data.revision, group_id, data.revision)
            ).fetchone():
                raise Problem('version_conflict', 409)
            plan = current_plan(db, group['project_id'], data.plan_id)
            if plan['status'] != 'committed' or plan['requirement_ids'] != [group['requirement_id']]:
                raise Problem('delivery_plan_changed', 409)
            requirement = latest_requirement(db, group['project_id'], group['requirement_id'])
            pin = requirement['specification']
            linked = active_connection(db, group['project_id'])
            if not pin or linked['repository_id'] != pin['repository_id']:
                raise Problem('spec_confirmation_required', 409)
            candidate = None
            if stage == 'testing':
                candidate = dispatch_row(db, group['last_handoff'])
                if (candidate['stage'] != 'development' or candidate['actor'] == user['id']
                        or candidate['input']['plan_revision'] != plan['revision']
                        or candidate['input']['plan_id'] != str(data.plan_id)):
                    raise Problem('independent_review_required', 409)
            if ((stage == 'development' and not data.automatic and (not data.commit or data.commit == pin['commit']))
                    or (candidate and (data.commit or data.automatic))):
                raise Problem('invalid_request', 422)
            frozen = {'repository': pin['repository'], 'repository_id': pin['repository_id'], 'base': pin['commit'],
                      'connection_revision': linked['revision'],
                      'requested_commit': data.commit, 'commit': candidate['receipt']['commit'] if candidate else data.commit or pin['commit'],
                      'candidate_id': str(candidate['id']) if candidate else None,
                      'digest': candidate['receipt']['digest'] if candidate else None,
                      'requirement_revision': requirement['revision'], 'criteria_revision': pin['criteria_revision'],
                      'criteria': requirement['criteria'], 'checks': plan['required_checks'], 'plan_id': str(data.plan_id),
                      'plan_revision': plan['revision']}
            if data.automatic:
                frozen.update(automatic=True, instruction=requirement['body'])
            if candidate and candidate['input'].get('automatic'):
                if not data.desktop:
                    raise Problem('delivery_desktop_required', 409)
                frozen.update(patch_only=True, paths=candidate['receipt']['coding']['files'])
            row = db.execute('''INSERT INTO mad_delivery_dispatches (id,project_id,group_id,group_revision,actor,stage,secret_digest,input,desktop)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                       (data.id, group['project_id'], group_id, group['revision'], user['id'], stage, secret, Jsonb(frozen), data.desktop)).fetchone()
            record(db, group['project_id'], user['id'], data.id, 'tebb_dispatched')
            return view(row)

    def worker(db, identifier, device, authorization):
        row = dispatch_row(db, identifier)
        if row['desktop']:
            paired = authorize_device(db, device, authorization)
            if paired['actor'] != row['actor']:
                raise Problem('dispatch_not_found', 404)
        elif not token_matches(row['secret_digest'], authorization):
            raise Problem('dispatch_not_found', 404)
        group = group_scope(db, row['group_id'], row['actor'])
        authorize_actor(db, group, row['actor'], row['stage'])
        return dispatch_row(db, identifier), group

    @router.post('/executor/{identifier}/claim')
    def claim(identifier: UUID, data: DeviceInput, authorization: Annotated[str, Header()] = ''):
        with connect(settings.database_url) as db:
            row, group = worker(db, identifier, data.device, authorization)
            if row['device'] is not None and row['device'] != data.device:
                raise Problem('dispatch_claimed', 409)
            if row['status'] in {'queued', 'running'} and not fresh(db, row, group):
                raise Problem('stage_changed', 409)
            if row['status'] == 'queued':
                row = db.execute("UPDATE mad_delivery_dispatches SET device=%s,status='running' WHERE id=%s RETURNING *",
                                 (data.device, identifier)).fetchone()
            return view(row)

    @router.put('/executor/{identifier}/receipt')
    def receive(identifier: UUID, data: ExecutionReceipt, authorization: Annotated[str, Header()] = ''):
        with connect(settings.database_url) as db:
            row, group = worker(db, identifier, data.device, authorization)
            if row['device'] != data.device:
                raise Problem('dispatch_claimed', 409)
            receipt = data.model_dump(mode='json', exclude={'artifact', 'device', 'coding'} if data.coding is None else {'artifact', 'device'})
            if row['receipt'] is not None:
                if row['receipt'] != receipt or (data.artifact is not None and artifact_bytes(data) != row['artifact']):
                    raise Problem('operation_conflict', 409)
                return view(row)
            if row['status'] != 'running' or data.commit != row['input']['commit']:
                raise Problem('operation_conflict', 409)
            keys = [entry.key for entry in data.checks]
            artifact = None
            if row['stage'] == 'testing':
                expected = [f'criterion:{i+1}' for i in range(len(row['input']['criteria']))]
                expected += [f'check:{i+1}' for i in range(len(row['input']['checks']))]
                if data.artifact is not None or (data.status == 'succeeded' and
                        (keys != expected or data.digest != row['input']['digest'])):
                    raise Problem('delivery_evidence_required', 409)
            elif data.status == 'succeeded':
                if keys != ['implementation'] or bool(row['input'].get('automatic')) != bool(data.coding):
                    raise Problem('delivery_evidence_required', 409)
                if row['input'].get('automatic') and data.artifact is not None:
                    raise Problem('artifact_upload_required', 409)
                artifact = artifact_bytes(data) if data.artifact is not None else assemble(db, identifier, data.digest)
            if data.artifact is not None and artifact is None:
                raise Problem('artifact_mismatch', 422)
            if data.coding and (not row['input'].get('automatic') or len(set(data.coding.files)) != len(data.coding.files)):
                raise Problem('invalid_request', 422)
            status = data.status if fresh(db, row, group) else 'historical'
            row = db.execute('''UPDATE mad_delivery_dispatches SET status=%s,receipt=%s,artifact=%s WHERE id=%s RETURNING *''',
                             (status, Jsonb(receipt), artifact, identifier)).fetchone()
            kind, message = ('executor_review', 'tebb 启动结果待核对。') if data.status == 'needs_review' else ('executor', 'tebb 执行回执已入档。')
            group_message(db, group, row['actor'], kind, message, {'dispatch_id': str(identifier), 'status': status})
            record(db, row['project_id'], row['actor'], identifier, 'tebb_receipt_recorded')
            if artifact is not None:
                db.execute('DELETE FROM mad_delivery_chunks WHERE dispatch=%s', (identifier,))
            if status == 'succeeded':
                terminal_handoff(db, service, group, row, data)
            return view(row)

    def upload_scope(db, identifier, device, authorization):
        row, _ = worker(db, identifier, device, authorization)
        if row['device'] != device or not row['desktop'] or row['stage'] != 'development':
            raise Problem('permission_denied', 403)
        return row

    @router.put('/executor/{identifier}/upload')
    def upload(identifier: UUID, data: Manifest, authorization: Annotated[str, Header()] = ''):
        with connect(settings.database_url) as db:
            return declare(db, upload_scope(db, identifier, data.device, authorization), data)

    @router.put('/executor/{identifier}/upload/{position}')
    def upload_chunk(identifier: UUID, position: int, data: Chunk, authorization: Annotated[str, Header()] = ''):
        with connect(settings.database_url) as db:
            return store_chunk(db, upload_scope(db, identifier, data.device, authorization), position, data)

    @router.get('/executor/{identifier}/candidate')
    def patch_candidate(identifier: UUID, device: UUID, authorization: Annotated[str, Header()] = ''):
        with connect(settings.database_url) as db:
            row, group = worker(db, identifier, device, authorization)
            if row['device'] != device or row['status'] != 'running' or not row['input'].get('patch_only') or not fresh(db, row, group):
                raise Problem('stage_changed', 409)
            candidate = dispatch_row(db, UUID(row['input']['candidate_id']))
            if candidate['receipt']['digest'] != row['input']['digest'] or candidate['artifact'] is None:
                raise Problem('artifact_mismatch', 409)
            return {'patch': b64encode(candidate['artifact']).decode()}

    @router.post('/groups/{group_id}/dispatches/{identifier}/cancel')
    def cancel(group_id: UUID, identifier: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            group = group_scope(db, group_id, user['id'])
            row = dispatch_row(db, identifier)
            if row['group_id'] != group_id or row['actor'] != user['id']:
                raise Problem('permission_denied', 403)
            authorize_actor(db, group, user['id'], row['stage'])
            if row['status'] not in {'queued', 'needs_review', 'cancelled'}:
                raise Problem('operation_conflict', 409)
            db.execute("UPDATE mad_delivery_dispatches SET status='cancelled' WHERE id=%s", (identifier,))
            return {'status': 'cancelled'}

    @router.get('/projects/{project_id}/delivery')
    def delivery(project_id: UUID, group_id: UUID | None = None, user=authenticated):
        with connect(settings.database_url) as db:
            member = membership(db, project_id, user['id'], write=True)
            dispatches = db.execute('''SELECT id,group_id,group_revision,actor,stage,input,status,receipt,created_at,
                                        artifact IS NOT NULL AS has_artifact FROM mad_delivery_dispatches WHERE project_id=%s
                                        AND (%s::uuid IS NULL OR group_id=%s) ORDER BY created_at DESC LIMIT 100''',
                                    (project_id, group_id, group_id)).fetchall()
            if group_id is not None:
                return {'dispatches': [view(item) for item in dispatches]}
            builds = db.execute('SELECT * FROM mad_delivery_builds WHERE project_id=%s ORDER BY approved_at DESC',
                                (project_id,)).fetchall()
            releases = db.execute('SELECT * FROM mad_delivery_releases WHERE project_id=%s ORDER BY published_at DESC',
                                  (project_id,)).fetchall()
            publishers = db.execute('SELECT user_id FROM mad_delivery_publishers WHERE project_id=%s', (project_id,)).fetchall()
            return {'dispatches': [view(item) for item in dispatches], 'builds': builds, 'releases': releases,
                    'publishers': [item['user_id'] for item in publishers], 'can_grant': member['role'] == 'owner'}

    @router.get('/projects/{project_id}/delivery/artifacts/{identifier}')
    def artifact(project_id: UUID, identifier: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user['id'], write=True)
            row = dispatch_row(db, identifier)
            if row['project_id'] != project_id or row['artifact'] is None:
                raise Problem('artifact_required', 404)
            return Response(bytes(row['artifact']), media_type='application/octet-stream',
                            headers={'Content-Disposition': f'attachment; filename="{identifier}.patch"'})

    @router.put('/projects/{project_id}/delivery/publishers/{publisher}')
    def grant(project_id: UUID, publisher: UUID, enabled: bool, user=authenticated):
        with connect(settings.database_url) as db:
            if membership(db, project_id, user['id'], write=True)['role'] != 'owner':
                raise Problem('permission_denied', 403)
            membership(db, project_id, publisher)
            if enabled:
                db.execute('INSERT INTO mad_delivery_publishers VALUES (%s,%s) ON CONFLICT DO NOTHING', (project_id, publisher))
            else:
                db.execute('DELETE FROM mad_delivery_publishers WHERE project_id=%s AND user_id=%s', (project_id, publisher))
            record(db, project_id, user['id'], publisher, 'release_permission_granted' if enabled else 'release_permission_revoked')
            return {'enabled': enabled}

    @router.post('/projects/{project_id}/delivery/{plan_id}/approve')
    def approve(project_id: UUID, plan_id: UUID, data: BuildInput, user=authenticated):
        def scope(db):
            membership(db, project_id, user['id'], write=True, manage=True)
            _, group, candidate, check = approved_candidate(db, project_id, plan_id, data.revision, data.candidate_id)
            if group['quality_id'] != user['id']:
                raise Problem('permission_denied', 403)
            return candidate, check

        with connect(settings.database_url) as db:
            candidate, _ = scope(db)
        ci = verify_ci(github, candidate, data.ci_run_id)
        with connect(settings.database_url) as db:
            _, check = scope(db)
            db.execute('''INSERT INTO mad_delivery_builds (project_id,plan_id,plan_revision,candidate_id,verification_id,quality_id,ci)
                          VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                       (project_id, plan_id, data.revision, data.candidate_id, check['id'], user['id'], Jsonb(ci)))
            row = db.execute('''SELECT * FROM mad_delivery_builds WHERE project_id=%s AND plan_id=%s
                                AND plan_revision=%s AND candidate_id=%s''', (project_id, plan_id, data.revision, data.candidate_id)).fetchone()
            if row['ci'] != ci or row['verification_id'] != check['id']:
                raise Problem('operation_conflict', 409)
            return row

    @router.post('/projects/{project_id}/delivery/{plan_id}/publish')
    def publish(project_id: UUID, plan_id: UUID, data: PublishInput, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user['id'], write=True)
            if not db.execute('SELECT 1 FROM mad_delivery_publishers WHERE project_id=%s AND user_id=%s',
                              (project_id, user['id'])).fetchone():
                raise Problem('permission_denied', 403)
            key = (project_id, plan_id, data.revision)
            prior = db.execute('SELECT * FROM mad_delivery_releases WHERE project_id=%s AND plan_id=%s AND plan_revision=%s', key).fetchone()
            if prior:
                if prior['candidate_id'] != data.candidate_id:
                    raise Problem('operation_conflict', 409)
                return prior
            plan, _, candidate, check = approved_candidate(db, project_id, plan_id, data.revision, data.candidate_id)
            build = db.execute('''SELECT * FROM mad_delivery_builds WHERE project_id=%s AND plan_id=%s
                                  AND plan_revision=%s AND candidate_id=%s''', (*key, data.candidate_id)).fetchone()
            if not build or build['verification_id'] != check['id']:
                raise Problem('delivery_evidence_required', 409)
            payload, published = local_receipt(settings.pilot_release_root, candidate, plan)
            record(db, project_id, user['id'], plan_id, 'pilot_release_published')
            return db.execute('''INSERT INTO mad_delivery_releases VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                              (*key, candidate['id'], user['id'], Jsonb(payload), published)).fetchone()

    combined = APIRouter()
    combined.include_router(router)
    combined.include_router(devices)
    return combined
