from typing import Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import APIRouter, BackgroundTasks, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from server.db import connect
from server.errors import Problem
from server.project_context import authorized_run, context_changed, event, handoff_outdated, latest_requirement
from server.projects import membership, record


class GroupInput(BaseModel):
    requirement_id: UUID
    source: str = Field(min_length=1, max_length=500)
    product_id: UUID
    development_id: UUID
    testing_id: UUID
    quality_id: UUID
    auto_run: bool = True


class GroupMessage(BaseModel):
    id: UUID
    body: str = Field(min_length=1, max_length=8000)


class StageDecision(BaseModel):
    expected_revision: int
    action: Literal['approve', 'return']
    note: str = Field(min_length=1, max_length=3000)


class AnswerInput(BaseModel):
    expected_revision: int
    answer: str = Field(min_length=1, max_length=1000)
    note: str = Field(min_length=1, max_length=3000)


class ReassignInput(BaseModel):
    member_id: UUID
    expected_revision: int


def group_scope(db, group_id, user_id):
    row = db.execute('SELECT * FROM mad_requirement_groups WHERE id=%s', (group_id,)).fetchone()
    if not row:
        raise Problem('group_not_found', 404)
    membership(db, row['project_id'], user_id, write=True)
    return db.execute('SELECT * FROM mad_requirement_groups WHERE id=%s FOR UPDATE', (group_id,)).fetchone()


def group_message(db, group, author, kind, body, data=None, identifier=None):
    db.execute('''INSERT INTO mad_group_messages (id,group_id,author,kind,body,data) VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (id) DO NOTHING''', (identifier or uuid4(), group['id'], author, kind, body, Jsonb(data or {})))


def check_group_run(db, run):
    if not run.get('group_id'):
        return None
    group = group_scope(db, run['group_id'], run['user_id'])
    if group['revision'] != run['group_revision'] or group['stage'] != run['role']:
        raise Problem('stage_changed', 409)
    if group.get(run['role'] + '_id') != run['user_id']:
        raise Problem('permission_denied', 403)
    return group


def transition(db, group, stage, state, actor, handoff=None, note=''):
    db.execute('''UPDATE mad_requirement_groups SET stage=%s,state=%s,revision=revision+1,last_handoff=%s WHERE id=%s''',
               (stage, state, handoff, group['id']))
    labels = {'product': '产品确认', 'development': '实现与自测', 'testing': '独立验收', 'quality': '质量复核', 'completed': '需求完成'}
    body = note or ('本阶段已交付，等待本人确认。' if state == 'awaiting_confirmation' else f"已交接至{labels[stage]}。")
    group_message(db, group, actor, 'transition', body,
                  {'from_stage': group['stage'], 'stage': stage, 'revision': group['revision'] + 1,
                   'run_id': str(handoff) if handoff else None})
    record(db, group['project_id'], actor, group['id'], 'requirement_stage_changed')


def group_handoff(db, run, result):
    if not run.get('group_id'):
        return
    group = check_group_run(db, run)
    handoff = db.execute('SELECT * FROM mad_handoffs WHERE run_id=%s', (run['id'],)).fetchone()
    if handoff_outdated(db, handoff):
        raise Problem('context_changed', 409)
    if run['role'] == 'product':
        stage, state = 'product', 'awaiting_confirmation'
    elif run['role'] == 'development':
        stage, state = 'testing', 'active'
    elif result['verdict'] == 'rejected':
        stage, state = 'development', 'active'
    else:
        stage, state = 'quality', 'awaiting_confirmation'
    group_message(db, group, run['user_id'], 'handoff', handoff['summary'],
                  {'run_id': str(run['id']), 'role': run['role'], 'verdict': result['verdict'],
                   'requirement_revision': run['requirement_revision'], 'evidence': handoff['evidence'],
                   'context_references': handoff['context_references'],
                   'candidate_run': str(handoff['candidate_run']) if handoff['candidate_run'] else None})
    transition(db, group, stage, state, run['user_id'], run['id'])
    db.execute("UPDATE mad_agent_runs SET status='completed' WHERE id=%s", (run['id'],))


def request_decision(db, run, args, call_id):
    group = check_group_run(db, run)
    question, options, evidence = args.get('question'), args.get('options'), args.get('evidence')
    if not isinstance(question, str) or not 1 <= len(question) <= 3000:
        raise Problem('invalid_request', 422)
    if not isinstance(options, list) or not 2 <= len(options) <= 5 or any(not isinstance(option, str) or not 1 <= len(option) <= 1000 for option in options):
        raise Problem('invalid_request', 422)
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12 or any(not isinstance(item, str) or not 1 <= len(item) <= 2000 for item in evidence):
        raise Problem('evidence_required', 422)
    identifier = uuid5(NAMESPACE_URL, f"mad:decision:{run['id']}:{run['generation']}:{call_id}")
    db.execute('''INSERT INTO mad_run_decisions (id,run_id,generation,owner_id,question,options,evidence,checkpoint)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
               (identifier, run['id'], run['generation'], run['user_id'], question, Jsonb(options), Jsonb(evidence), run['checkpoint'] + 1))
    decision = db.execute("SELECT id FROM mad_run_decisions WHERE run_id=%s AND generation=%s AND state='pending'",
                          (run['id'], run['generation'])).fetchone()
    db.execute("UPDATE mad_agent_runs SET status='waiting_decision' WHERE id=%s", (run['id'],))
    event(db, run['id'], 'decision_requested', {'decision_id': str(decision['id']), 'checkpoint': run['checkpoint'] + 1})
    if group:
        db.execute("UPDATE mad_requirement_groups SET state='waiting_decision' WHERE id=%s", (group['id'],))
        group_message(db, group, run['user_id'], 'decision', question,
                      {'decision_id': str(decision['id']), 'owner_id': str(run['user_id']), 'run_id': str(run['id']),
                       'options': options, 'evidence': evidence, 'checkpoint': run['checkpoint'] + 1, 'generation': run['generation']})
    return {'decision_id': str(decision['id']), 'state': 'pending', 'owner_id': str(run['user_id'])}


def restart_group(db, project_id, requirement_id, revision, author):
    group = db.execute('SELECT * FROM mad_requirement_groups WHERE project_id=%s AND requirement_id=%s',
                       (project_id, requirement_id)).fetchone()
    if not group:
        return
    db.execute('UPDATE mad_requirement_groups SET requirement_revision=%s WHERE id=%s', (revision, group['id']))
    db.execute("UPDATE mad_run_decisions SET state='superseded',revision=revision+1 WHERE state='pending' AND run_id IN (SELECT id FROM mad_agent_runs WHERE group_id=%s)", (group['id'],))
    transition(db, group, 'product', 'active', author, note='需求版本已更新，重新确认产品方案。')


def invalidate_group_context(db, project_id, document_id, author):
    groups = db.execute('''SELECT g.* FROM mad_requirement_groups g WHERE g.project_id=%s AND EXISTS
                           (SELECT 1 FROM mad_agent_runs r JOIN mad_run_references ref ON ref.run_id=r.id
                            WHERE r.group_id=g.id AND ref.document_id=%s)''', (project_id, document_id)).fetchall()
    for group in groups:
        current = db.execute('''SELECT 1 FROM mad_agent_runs r JOIN mad_run_references ref ON ref.run_id=r.id
                               WHERE r.group_id=%s AND r.group_revision=%s AND r.status<>'completed'
                               AND ref.document_id=%s AND ref.active LIMIT 1''', (group['id'], group['revision'], document_id)).fetchone()
        if current:
            continue
        db.execute("UPDATE mad_run_decisions SET state='superseded',revision=revision+1 WHERE state='pending' AND run_id IN (SELECT id FROM mad_agent_runs WHERE group_id=%s)", (group['id'],))
        transition(db, group, 'product', 'active', author, note='阶段依据的项目资料已更新，请重新确认产品方案。')


def groups_router(settings, identity, service):
    router = APIRouter()
    authenticated = Depends(identity)

    @router.post('/projects/{project_id}/groups', status_code=201)
    def create(project_id: UUID, data: GroupInput, background: BackgroundTasks, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user['id'], write=True, manage=True)
            prior = db.execute('SELECT * FROM mad_requirement_groups WHERE project_id=%s AND requirement_id=%s',
                               (project_id, data.requirement_id)).fetchone()
            if prior:
                if prior['auto_run']:
                    background.add_task(service.drive, prior['id'])
                return prior
            requirement = latest_requirement(db, project_id, data.requirement_id)
            if data.development_id == data.testing_id:
                raise Problem('independent_review_required', 409)
            for role in ('product', 'development', 'testing'):
                member_id = getattr(data, role + '_id')
                membership(db, project_id, member_id)
                assigned = db.execute('SELECT role FROM mad_project_roles WHERE project_id=%s AND user_id=%s', (project_id, member_id)).fetchone()
                if not assigned or assigned['role'] != role:
                    raise Problem('role_required', 409)
            membership(db, project_id, data.quality_id, manage=True)
            group_id = uuid4()
            db.execute('''INSERT INTO mad_requirement_groups (id,project_id,requirement_id,requirement_revision,source,
                          product_id,development_id,testing_id,quality_id,auto_run) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                       (group_id, project_id, data.requirement_id, requirement['revision'], data.source,
                        data.product_id, data.development_id, data.testing_id, data.quality_id, data.auto_run))
            group = group_scope(db, group_id, user['id'])
            group_message(db, group, user['id'], 'created', '需求已立项，产品阶段待推进。', {'source': data.source})
            record(db, project_id, user['id'], group_id, 'requirement_group_created')
            if group['auto_run']:
                background.add_task(service.drive, group_id)
            return group

    @router.get('/projects/{project_id}/groups')
    def groups(project_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user['id'], write=True)
            return db.execute('SELECT * FROM mad_requirement_groups WHERE project_id=%s ORDER BY id', (project_id,)).fetchall()

    @router.get('/groups/{group_id}')
    def detail(group_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            group = group_scope(db, group_id, user['id'])
            group['runs'] = db.execute('''SELECT id,role,status,user_id,checkpoint,group_revision FROM mad_agent_runs
                                         WHERE group_id=%s ORDER BY created_at''', (group_id,)).fetchall()
            group['requirement'] = latest_requirement(db, group['project_id'], group['requirement_id'])
            group['pending_decisions'] = db.execute('''SELECT d.*,r.role FROM mad_run_decisions d JOIN mad_agent_runs r ON r.id=d.run_id
                                                      WHERE r.group_id=%s AND d.state='pending' ORDER BY d.created_at''', (group_id,)).fetchall()
            return group

    @router.get('/groups/{group_id}/messages')
    def messages(group_id: UUID, after: int = 0, user=authenticated):
        if not 0 <= after < 2**63:
            raise Problem('invalid_request', 422)
        with connect(settings.database_url) as db:
            group_scope(db, group_id, user['id'])
            return db.execute('SELECT * FROM mad_group_messages WHERE group_id=%s AND sequence>%s ORDER BY sequence LIMIT 200',
                              (group_id, after)).fetchall()

    @router.post('/groups/{group_id}/messages')
    def post_message(group_id: UUID, data: GroupMessage, user=authenticated):
        with connect(settings.database_url) as db:
            group = group_scope(db, group_id, user['id'])
            existing = db.execute('SELECT * FROM mad_group_messages WHERE id=%s', (data.id,)).fetchone()
            if existing and (existing['group_id'], existing['author'], existing['body']) != (group_id, user['id'], data.body):
                raise Problem('operation_conflict', 409)
            group_message(db, group, user['id'], 'message', data.body, identifier=data.id)
            return {'id': data.id}

    @router.post('/groups/{group_id}/advance')
    def advance(group_id: UUID, data: StageDecision, background: BackgroundTasks, user=authenticated):
        with connect(settings.database_url) as db:
            group = group_scope(db, group_id, user['id'])
            prior = db.execute('SELECT * FROM mad_group_decisions WHERE group_id=%s AND revision=%s', (group_id, data.expected_revision)).fetchone()
            if prior:
                if (prior['actor'], prior['action'], prior['note']) != (user['id'], data.action, data.note):
                    raise Problem('operation_conflict', 409)
                return prior['result']
            if group['revision'] != data.expected_revision:
                raise Problem('version_conflict', 409)
            if group['state'] != 'awaiting_confirmation' or group['stage'] not in {'product', 'quality'}:
                raise Problem('stage_changed', 409)
            if user['id'] != group[group['stage'] + '_id']:
                raise Problem('permission_denied', 403)
            if group['stage'] == 'quality':
                membership(db, group['project_id'], user['id'], manage=True)
            handoff = db.execute('SELECT * FROM mad_handoffs WHERE run_id=%s', (group['last_handoff'],)).fetchone()
            if not handoff or handoff_outdated(db, handoff):
                raise Problem('context_changed', 409)
            if data.action == 'return':
                stage = 'product' if group['stage'] == 'product' else 'development'
            else:
                stage = 'development' if group['stage'] == 'product' else 'completed'
            transition(db, group, stage, 'completed' if stage == 'completed' else 'active', user['id'],
                       group['last_handoff'], note=data.note)
            result = {'stage': stage, 'revision': group['revision'] + 1}
            db.execute('INSERT INTO mad_group_decisions VALUES (%s,%s,%s,%s,%s,%s)',
                       (group_id, data.expected_revision, user['id'], data.action, data.note, Jsonb(result)))
            if group['auto_run']:
                background.add_task(service.drive, group_id)
            return result

    @router.get('/decisions')
    def decisions(user=authenticated):
        with connect(settings.database_url) as db:
            return db.execute('''SELECT d.*,r.project_id,r.group_id,r.requirement_id FROM mad_run_decisions d
                                 JOIN mad_agent_runs r ON r.id=d.run_id JOIN mad_memberships m
                                 ON m.project_id=r.project_id AND m.user_id=d.owner_id
                                 WHERE d.owner_id=%s AND d.state='pending' ORDER BY d.created_at''', (user['id'],)).fetchall()

    @router.post('/decisions/{decision_id}/resolve')
    def resolve(decision_id: UUID, data: AnswerInput, background: BackgroundTasks, user=authenticated):
        with connect(settings.database_url) as db:
            decision = db.execute('SELECT * FROM mad_run_decisions WHERE id=%s AND owner_id=%s', (decision_id, user['id'])).fetchone()
            if not decision:
                raise Problem('decision_not_found', 404)
            run = authorized_run(db, decision['run_id'], user['id'], lock=True)
            decision = db.execute('SELECT * FROM mad_run_decisions WHERE id=%s FOR UPDATE', (decision_id,)).fetchone()
            if decision['state'] == 'resolved':
                if decision['answer'] != data.answer or decision['note'] != data.note:
                    raise Problem('operation_conflict', 409)
            else:
                check_group_run(db, run)
                if decision['state'] != 'pending' or decision['revision'] != data.expected_revision or decision['generation'] != run['generation']:
                    raise Problem('version_conflict', 409)
                if context_changed(db, run):
                    raise Problem('context_changed', 409)
                if data.answer not in decision['options']:
                    raise Problem('invalid_request', 422)
                db.execute("UPDATE mad_run_decisions SET state='resolved',answer=%s,note=%s,resolved_at=now(),revision=revision+1 WHERE id=%s", (data.answer, data.note, decision_id))
                db.execute("UPDATE mad_agent_runs SET status='paused' WHERE id=%s", (run['id'],))
                event(db, run['id'], 'decision_resolved', {'decision_id': str(decision_id), 'answer': data.answer, 'note': data.note, 'checkpoint': decision['checkpoint']})
                if run['group_id']:
                    group = group_scope(db, run['group_id'], user['id'])
                    db.execute("UPDATE mad_requirement_groups SET state='active' WHERE id=%s", (group['id'],))
                    group_message(db, group, user['id'], 'decision_resolved', data.note,
                                  {'decision_id': str(decision_id), 'run_id': str(run['id']), 'answer': data.answer,
                                   'checkpoint': decision['checkpoint'], 'generation': run['generation']})
            background.add_task(service.resume, run['id'], user['id'])
            return {'id': decision_id, 'run_id': run['id'], 'checkpoint': decision['checkpoint'], 'outcome': 'resolved'}

    @router.post('/decisions/{decision_id}/reassign')
    def reassign(decision_id: UUID, data: ReassignInput, user=authenticated):
        with connect(settings.database_url) as db:
            decision = db.execute('SELECT * FROM mad_run_decisions WHERE id=%s', (decision_id,)).fetchone()
            if not decision:
                raise Problem('decision_not_found', 404)
            run = db.execute('SELECT * FROM mad_agent_runs WHERE id=%s', (decision['run_id'],)).fetchone()
            membership(db, run['project_id'], user['id'], write=True, manage=True)
            decision = db.execute('SELECT * FROM mad_run_decisions WHERE id=%s FOR UPDATE', (decision_id,)).fetchone()
            run = db.execute('SELECT * FROM mad_agent_runs WHERE id=%s FOR UPDATE', (decision['run_id'],)).fetchone()
            membership(db, run['project_id'], data.member_id)
            role = db.execute('SELECT role FROM mad_project_roles WHERE project_id=%s AND user_id=%s', (run['project_id'], data.member_id)).fetchone()
            if not role or role['role'] != run['role'] or decision['state'] != 'pending' or decision['revision'] != data.expected_revision:
                raise Problem('role_required', 409)
            if not db.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired', (str(run['id']),)).fetchone()['acquired']:
                raise Problem('run_busy', 409)
            from server.agents import personal_agent
            agent = personal_agent(db, data.member_id, run['config']['model'])
            if run['group_id']:
                group = group_scope(db, run['group_id'], user['id'])
                if group['revision'] != run['group_revision']:
                    raise Problem('stage_changed', 409)
                opposite = {'development': 'testing_id', 'testing': 'development_id'}.get(run['role'])
                if opposite and group[opposite] == data.member_id:
                    raise Problem('independent_review_required', 409)
                from psycopg import sql
                db.execute(sql.SQL('UPDATE mad_requirement_groups SET {}=%s WHERE id=%s').format(sql.Identifier(run['role'] + '_id')),
                           (data.member_id, group['id']))
                group_message(db, group, user['id'], 'decision_reassigned', '待决策及原运行已转交同岗位替补。',
                              {'decision_id': str(decision_id), 'owner_id': str(data.member_id), 'run_id': str(run['id'])})
            db.execute('UPDATE mad_agent_runs SET user_id=%s,agent_id=%s WHERE id=%s', (data.member_id, agent['id'], run['id']))
            db.execute('INSERT INTO mad_run_contributors VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (run['id'], data.member_id, run['role']))
            db.execute('UPDATE mad_run_decisions SET owner_id=%s,revision=revision+1 WHERE id=%s', (data.member_id, decision_id))
            event(db, run['id'], 'decision_reassigned', {'from': str(run['user_id']), 'to': str(data.member_id), 'checkpoint': run['checkpoint']})
            return {'id': decision_id, 'run_id': run['id'], 'owner_id': data.member_id}

    return router
