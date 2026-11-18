import hashlib
import os
from pathlib import Path
import re
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from typing import Literal

from server.db import connect
from server.errors import Problem
from server.projects import membership, record


class ResourceInput(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=80)


class GrantInput(BaseModel):
    expected_revision: int = Field(ge=0)
    access: Literal['none', 'read', 'write']


def missing_resources(db, run, user_id=None):
    return db.execute('''SELECT p.id,p.name,'write' AS required_access FROM mad_run_resources rr
        JOIN mad_project_resources p ON p.id=rr.resource_id
        LEFT JOIN mad_resource_grants g ON g.resource_id=p.id AND g.user_id=%s
        WHERE rr.run_id=%s AND (g.access IS NULL OR g.access<>'write') ORDER BY p.name''',
                      (user_id or run['user_id'], run['id'])).fetchall()


def bind_resources(db, run_id, project_id, user_id, identifiers):
    for identifier in set(identifiers):
        resource = db.execute('SELECT * FROM mad_project_resources WHERE id=%s AND project_id=%s', (identifier, project_id)).fetchone()
        if not resource:
            raise Problem('resource_not_found', 404)
        grant = db.execute('SELECT access FROM mad_resource_grants WHERE resource_id=%s AND user_id=%s', (identifier, user_id)).fetchone()
        if not grant or grant['access'] != 'write':
            raise Problem('authorization_required', 409)
        db.execute('INSERT INTO mad_run_resources VALUES (%s,%s) ON CONFLICT DO NOTHING', (run_id, identifier))


def artifact_path(settings, project, resource, identifier):
    if not settings.resource_root:
        raise Problem('resource_store_unavailable', 503)
    root = Path(settings.resource_root).resolve()
    directory = root / str(project) / str(resource)
    if not directory.resolve().is_relative_to(root):
        raise Problem('permission_denied', 403)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (str(identifier) + '.txt')
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise Problem('permission_denied', 403)
    return path


def publish_artifact(db, run, args, settings):
    try:
        resource = UUID(args['resource_id'])
        key, content = args['operation_key'], args['content']
    except (ValueError, KeyError, TypeError):
        raise Problem('invalid_request', 422) from None
    if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', key) or not isinstance(content, str) or not 1 <= len(content) <= 16000:
        raise Problem('invalid_request', 422)
    if not db.execute('SELECT 1 FROM mad_run_resources WHERE run_id=%s AND resource_id=%s', (run['id'], resource)).fetchone():
        raise Problem('permission_denied', 403)
    if missing_resources(db, run):
        raise Problem('authorization_required', 409)
    identifier = uuid5(NAMESPACE_URL, f"mad:artifact:{run['id']}:{run['generation']}:{resource}:{key}")
    data = content.encode('utf-8')
    digest = hashlib.sha256(data).hexdigest()
    path = artifact_path(settings, run['project_id'], resource, identifier)
    reused = path.exists()
    if not reused:
        staging = path.with_name('.' + uuid4().hex + '.tmp')
        try:
            with staging.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(staging, path)
            except FileExistsError:
                reused = True
        finally:
            staging.unlink(missing_ok=True)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise Problem('operation_conflict', 409)
    db.execute('''INSERT INTO mad_artifacts VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
               (identifier, run['id'], run['generation'], resource, key, digest, len(data)))
    return {'artifact_id': str(identifier), 'resource_id': str(resource), 'operation_key':key, 'digest': digest, 'bytes': len(data), 'reused': reused}


def resources_router(settings, identity, service):
    router = APIRouter()
    authenticated = Depends(identity)

    @router.get('/projects/{project_id}/resources')
    def resources(project_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user['id'], write=True)
            rows = db.execute('SELECT * FROM mad_project_resources WHERE project_id=%s ORDER BY name', (project_id,)).fetchall()
            for row in rows:
                row['grants'] = db.execute('SELECT user_id,access,revision FROM mad_resource_grants WHERE resource_id=%s', (row['id'],)).fetchall()
            return {'available': bool(settings.resource_root), 'resources': rows}

    @router.post('/projects/{project_id}/resources', status_code=201)
    def create(project_id: UUID, data: ResourceInput, user=authenticated):
        if not settings.resource_root:
            raise Problem('resource_store_unavailable', 503)
        with connect(settings.database_url) as db:
            membership(db, project_id, user['id'], write=True, manage=True)
            prior = db.execute('SELECT * FROM mad_project_resources WHERE id=%s', (data.id,)).fetchone()
            if prior:
                if prior['project_id'] != project_id or prior['name'] != data.name:
                    raise Problem('operation_conflict', 409)
                return prior
            if db.execute('SELECT 1 FROM mad_project_resources WHERE project_id=%s AND name=%s', (project_id, data.name)).fetchone():
                raise Problem('operation_conflict', 409)
            db.execute('INSERT INTO mad_project_resources VALUES (%s,%s,%s,%s)', (data.id, project_id, data.name, user['id']))
            record(db, project_id, user['id'], data.id, 'resource_created')
            return data

    @router.put('/projects/{project_id}/resources/{resource_id}/grants/{member_id}')
    def grant(project_id: UUID, resource_id: UUID, member_id: UUID, data: GrantInput, background: BackgroundTasks, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user['id'], write=True, manage=True)
            membership(db, project_id, member_id)
            if not db.execute('SELECT 1 FROM mad_project_resources WHERE id=%s AND project_id=%s', (resource_id, project_id)).fetchone():
                raise Problem('resource_not_found', 404)
            prior = db.execute('SELECT * FROM mad_resource_grants WHERE resource_id=%s AND user_id=%s', (resource_id, member_id)).fetchone()
            if prior and prior['access'] == data.access:
                return prior
            if (prior['revision'] if prior else 0) != data.expected_revision:
                raise Problem('version_conflict', 409)
            db.execute('''INSERT INTO mad_resource_grants (resource_id,user_id,access) VALUES (%s,%s,%s)
                ON CONFLICT(resource_id,user_id) DO UPDATE SET access=excluded.access,revision=mad_resource_grants.revision+1''',
                       (resource_id, member_id, data.access))
            if data.access == 'write':
                db.execute('''UPDATE mad_resume_jobs j SET state='queued',reason=NULL,updated_at=now()
                    WHERE j.user_id=%s AND j.state='blocked' AND j.reason='authorization_required'
                    AND EXISTS(SELECT 1 FROM mad_run_resources rr WHERE rr.run_id=j.run_id AND rr.resource_id=%s)''', (member_id, resource_id))
                background.add_task(service.drain_jobs)
            record(db, project_id, user['id'], resource_id, 'resource_grant_' + data.access)
            return db.execute('SELECT * FROM mad_resource_grants WHERE resource_id=%s AND user_id=%s', (resource_id, member_id)).fetchone()

    @router.get('/artifacts/{artifact_id}')
    def download(artifact_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            row = db.execute('''SELECT a.*,r.project_id FROM mad_artifacts a JOIN mad_agent_runs r ON r.id=a.run_id WHERE a.id=%s''', (artifact_id,)).fetchone()
            if not row:
                raise Problem('resource_not_found', 404)
            membership(db, row['project_id'], user['id'], write=True)
            grant = db.execute('SELECT access FROM mad_resource_grants WHERE resource_id=%s AND user_id=%s', (row['resource_id'], user['id'])).fetchone()
            if not grant or grant['access'] not in {'read','write'}:
                raise Problem('permission_denied', 403)
            try:
                data=artifact_path(settings, row['project_id'], row['resource_id'], row['id']).read_bytes()
            except OSError:
                raise Problem('artifact_unavailable',503) from None
            if hashlib.sha256(data).hexdigest()!=row['digest']:
                raise Problem('artifact_unavailable',503)
            content=data.decode('utf-8')
            return PlainTextResponse(content, headers={'Content-Disposition': f'attachment; filename="{row["id"]}.txt"'})

    return router
