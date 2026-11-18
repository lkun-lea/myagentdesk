from typing import Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import APIRouter, BackgroundTasks, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from server.db import connect
from server.errors import Problem
from server.projects import membership
from server.project_context import authorized_run, context_changed, event
from server.requirement_groups import check_group_run, group_scope, group_message
from server.resources import missing_resources


class AnswerInput(BaseModel):
    expected_revision: int
    answer: str = Field(min_length=1, max_length=1000)
    note: str = Field(min_length=1, max_length=3000)


class ReassignInput(BaseModel):
    member_id: UUID
    expected_revision: int


class EscalationInput(BaseModel):
    expected_revision: int
    reason: str = Field(min_length=1, max_length=3000)


class ResumeInput(BaseModel):
    expected_revision: int = Field(ge=1)


class ReplaceInput(BaseModel):
    id: UUID
    expected_revision: int = Field(ge=1)
    question: str = Field(min_length=1,max_length=3000)
    options: list[str] = Field(min_length=2,max_length=5)
    evidence: list[str] = Field(min_length=1,max_length=12)


def decision_candidates(db, decision, run):
    rows = db.execute('''SELECT m.user_id,u.email FROM mad_memberships m JOIN mad_users u ON u.id=m.user_id
        JOIN mad_project_roles p ON p.project_id=m.project_id AND p.user_id=m.user_id
        WHERE m.project_id=%s AND p.role=%s AND m.user_id<>%s ORDER BY u.email''',
                      (run['project_id'],run['role'],run['user_id'])).fetchall()
    eligible = []
    for row in rows:
        if missing_resources(db, run, row['user_id']):
            continue
        if run['role']=='testing' and db.execute('''SELECT 1 FROM mad_run_contributors c JOIN mad_agent_runs r ON r.id=c.run_id
            WHERE r.project_id=%s AND r.requirement_id=%s AND c.user_id=%s AND c.role='development' AND r.checkpoint>0''',
                                               (run['project_id'],run['requirement_id'],row['user_id'])).fetchone():
            continue
        if run['group_id']:
            group = db.execute('SELECT * FROM mad_requirement_groups WHERE id=%s', (run['group_id'],)).fetchone()
            opposite = {'development':'testing_id','testing':'development_id'}.get(run['role'])
            if opposite and group[opposite]==row['user_id']:
                continue
        eligible.append(row)
    return eligible


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
    references=[{**ref,'document_id':str(ref['document_id'])} for ref in db.execute(
        'SELECT document_id,revision,start_offset,end_offset FROM mad_run_references WHERE run_id=%s AND active',(run['id'],))]
    db.execute('''INSERT INTO mad_run_decisions (id,run_id,generation,owner_id,question,options,evidence,checkpoint,context_references)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
               (identifier, run['id'], run['generation'], run['user_id'], question, Jsonb(options), Jsonb(evidence), run['checkpoint'] + 1,Jsonb(references)))
    decision = db.execute("SELECT id FROM mad_run_decisions WHERE run_id=%s AND generation=%s AND state='pending'",
                          (run['id'], run['generation'])).fetchone()
    db.execute("UPDATE mad_agent_runs SET status='waiting_decision',execution_revision=execution_revision+1 WHERE id=%s", (run['id'],))
    event(db, run['id'], 'decision_requested', {'decision_id': str(decision['id']), 'checkpoint': run['checkpoint'] + 1})
    if group:
        db.execute("UPDATE mad_requirement_groups SET state='waiting_decision' WHERE id=%s", (group['id'],))
        group_message(db, group, run['user_id'], 'decision', question,
                      {'decision_id': str(decision['id']), 'owner_id': str(run['user_id']), 'run_id': str(run['id']),
                       'options': options, 'evidence': evidence, 'checkpoint': run['checkpoint'] + 1, 'generation': run['generation']})
    return {'decision_id': str(decision['id']), 'state': 'pending', 'owner_id': str(run['user_id'])}


def decisions_router(settings,identity,service):
    router=APIRouter()
    authenticated=Depends(identity)

    @router.get('/decisions')
    def decisions(history: bool = False, user=authenticated):
        with connect(settings.database_url) as db:
            return db.execute('''SELECT d.*,r.project_id,r.group_id,r.requirement_id,r.status AS execution_state,
                                 r.execution_revision,p.name AS project_name,q.title AS requirement_title,
                                 j.reason AS blocked_reason,
                                 (d.generation<>r.generation OR EXISTS(SELECT 1 FROM mad_requirements v
                                    WHERE v.project_id=r.project_id AND v.id=r.requirement_id AND v.revision>r.requirement_revision)
                                  OR (r.status<>'completed' AND EXISTS(SELECT 1 FROM mad_requirement_groups g WHERE g.id=r.group_id AND g.revision<>r.group_revision))
                                  OR EXISTS(SELECT 1 FROM mad_run_references ref JOIN mad_context_documents v ON v.project_id=r.project_id
                                    AND v.id=ref.document_id AND v.revision>ref.revision WHERE ref.run_id=r.id AND ref.active)) AS outdated
                                 FROM mad_run_decisions d
                                 JOIN mad_agent_runs r ON r.id=d.run_id JOIN mad_memberships m
                                 ON m.project_id=r.project_id AND m.user_id=d.owner_id
                                 JOIN mad_projects p ON p.id=r.project_id JOIN mad_requirements q
                                 ON q.project_id=r.project_id AND q.id=r.requirement_id AND q.revision=r.requirement_revision
                                 LEFT JOIN mad_resume_jobs j ON j.run_id=r.id
                                 WHERE d.owner_id=%s AND (%s OR d.state='pending' OR
                                     (d.state='resolved' AND r.status NOT IN ('completed','cancelled')))
                                 ORDER BY d.created_at DESC LIMIT 100''', (user['id'],history)).fetchall()

    @router.get('/decisions/{decision_id}')
    def decision_detail(decision_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            row=db.execute('SELECT * FROM mad_run_decisions WHERE id=%s AND owner_id=%s',(decision_id,user['id'])).fetchone()
            if not row:
                raise Problem('decision_not_found',404)
            run=authorized_run(db,row['run_id'],user['id'])
            row['execution_state']=run['status']
            group=db.execute('SELECT revision FROM mad_requirement_groups WHERE id=%s',(run['group_id'],)).fetchone() if run['group_id'] else None
            row['outdated']=row['generation']!=run['generation'] or context_changed(db,run) or bool(run['status']!='completed' and group and group['revision']!=run['group_revision'])
            row['group_id']=run['group_id']
            row['project_id']=run['project_id']
            row['missing_resources']=missing_resources(db,run)
            row['job']=db.execute('SELECT state,reason,attempts FROM mad_resume_jobs WHERE run_id=%s',(run['id'],)).fetchone()
            row['escalations']=db.execute('SELECT reason,created_at FROM mad_decision_escalations WHERE decision_id=%s ORDER BY created_at',(decision_id,)).fetchall()
            row['references']=[]
            for ref in row['context_references'][:20]:
                original=db.execute('''SELECT title,source,revision,substring(body FROM %s FOR %s) AS excerpt
                    FROM mad_context_documents WHERE project_id=%s AND id=%s AND revision=%s''',
                    (ref['start_offset']+1,min(ref['end_offset']-ref['start_offset'],1200),run['project_id'],ref['document_id'],ref['revision'])).fetchone()
                if original:
                    row['references'].append(original)
            return row

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
                if decision['receipt']:
                    background.add_task(service.process_job, run['id'])
                    return decision['receipt']
            else:
                check_group_run(db, run)
                if decision['state'] != 'pending' or decision['revision'] != data.expected_revision or decision['generation'] != run['generation']:
                    raise Problem('version_conflict', 409)
                if context_changed(db, run):
                    raise Problem('context_changed', 409)
                if data.answer not in decision['options']:
                    raise Problem('invalid_request', 422)
                db.execute("UPDATE mad_run_decisions SET state='resolved',answer=%s,note=%s,resolved_at=now(),revision=revision+1 WHERE id=%s", (data.answer, data.note, decision_id))
                run=db.execute("UPDATE mad_agent_runs SET status='paused' WHERE id=%s RETURNING *", (run['id'],)).fetchone()
                event(db, run['id'], 'decision_resolved', {'decision_id': str(decision_id), 'answer': data.answer, 'note': data.note, 'checkpoint': decision['checkpoint']})
                if run['group_id']:
                    group = group_scope(db, run['group_id'], user['id'])
                    db.execute("UPDATE mad_requirement_groups SET state='active' WHERE id=%s", (group['id'],))
                    group_message(db, group, user['id'], 'decision_resolved', data.note,
                                  {'decision_id': str(decision_id), 'run_id': str(run['id']), 'answer': data.answer,
                                   'checkpoint': decision['checkpoint'], 'generation': run['generation']})
            from server.recovery import enqueue, block_run
            enqueue(db,run)
            missing=missing_resources(db,run)
            if missing:
                block_run(db,run,'authorization_required')
                db.execute("UPDATE mad_resume_jobs SET state='blocked',reason='authorization_required' WHERE run_id=%s",(run['id'],))
            receipt={'id':str(decision_id),'run_id':str(run['id']),'checkpoint':decision['checkpoint'],'outcome':'resolved',
                     'execution_state':'waiting_authorization' if missing else 'queued'}
            db.execute('UPDATE mad_run_decisions SET receipt=%s WHERE id=%s',(Jsonb(receipt),decision_id))
            background.add_task(service.process_job, run['id'])
            return receipt

    @router.post('/decisions/{decision_id}/resume')
    def resume_decision(decision_id:UUID,data:ResumeInput,background:BackgroundTasks,user=authenticated):
        with connect(settings.database_url) as db:
            decision=db.execute('SELECT * FROM mad_run_decisions WHERE id=%s AND owner_id=%s',(decision_id,user['id'])).fetchone()
            if not decision:
                raise Problem('decision_not_found',404)
            run=authorized_run(db,decision['run_id'],user['id'],lock=True)
            if run['status']=='completed':
                return {'outcome':'completed','run_id':run['id'],'checkpoint':run['checkpoint']}
            check_group_run(db,run)
            if decision['state']!='resolved' or decision['revision']!=data.expected_revision or context_changed(db,run):
                raise Problem('version_conflict',409)
            from server.recovery import enqueue
            enqueue(db,run)
            background.add_task(service.process_job,run['id'])
            return {'outcome':'queued','run_id':run['id'],'checkpoint':run['checkpoint']}

    @router.post('/decisions/{decision_id}/replace')
    def replace_decision(decision_id:UUID,data:ReplaceInput,user=authenticated):
        with connect(settings.database_url) as db:
            original=db.execute('SELECT * FROM mad_run_decisions WHERE id=%s AND owner_id=%s',(decision_id,user['id'])).fetchone()
            if not original:
                raise Problem('decision_not_found',404)
            run=authorized_run(db,original['run_id'],user['id'],lock=True)
            prior=db.execute('SELECT * FROM mad_run_decisions WHERE id=%s',(data.id,)).fetchone()
            if prior:
                if prior['run_id']!=run['id'] or prior['question']!=data.question or prior['options']!=data.options or prior['evidence']!=data.evidence:
                    raise Problem('operation_conflict',409)
                return prior
            check_group_run(db,run)
            original=db.execute('SELECT * FROM mad_run_decisions WHERE id=%s FOR UPDATE',(decision_id,)).fetchone()
            if original['state']!='pending' or original['revision']!=data.expected_revision or context_changed(db,run):
                raise Problem('version_conflict',409)
            if any(not 1<=len(s)<=1000 for s in data.options) or any(not 1<=len(s)<=2000 for s in data.evidence):
                raise Problem('invalid_request',422)
            db.execute("UPDATE mad_run_decisions SET state='superseded',revision=revision+1 WHERE id=%s",(decision_id,))
            db.execute('''INSERT INTO mad_run_decisions (id,run_id,generation,owner_id,question,options,evidence,checkpoint,context_references)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(data.id,run['id'],run['generation'],user['id'],data.question,Jsonb(data.options),Jsonb(data.evidence),original['checkpoint'],Jsonb(original['context_references'])))
            db.execute("UPDATE mad_agent_runs SET execution_revision=execution_revision+1,status='waiting_decision' WHERE id=%s",(run['id'],))
            event(db,run['id'],'decision_replaced',{'from':str(decision_id),'decision_id':str(data.id),'checkpoint':original['checkpoint']})
            if run['group_id']:
                group=group_scope(db,run['group_id'],user['id'])
                group_message(db,group,user['id'],'decision_replaced',data.question,{'from':str(decision_id),'decision_id':str(data.id),'run_id':str(run['id']),'checkpoint':original['checkpoint']})
            return db.execute('SELECT * FROM mad_run_decisions WHERE id=%s',(data.id,)).fetchone()

    @router.get('/decisions/{decision_id}/candidates')
    def candidates(decision_id:UUID,user=authenticated):
        with connect(settings.database_url) as db:
            decision=db.execute('SELECT * FROM mad_run_decisions WHERE id=%s',(decision_id,)).fetchone()
            if not decision:
                raise Problem('decision_not_found',404)
            run=db.execute('SELECT * FROM mad_agent_runs WHERE id=%s',(decision['run_id'],)).fetchone()
            membership(db,run['project_id'],user['id'],write=True,manage=True)
            return decision_candidates(db,decision,run)

    @router.post('/decisions/{decision_id}/escalate')
    def escalate(decision_id:UUID,data:EscalationInput,user=authenticated):
        with connect(settings.database_url) as db:
            decision=db.execute('SELECT * FROM mad_run_decisions WHERE id=%s',(decision_id,)).fetchone()
            if not decision:
                raise Problem('decision_not_found',404)
            run=db.execute('SELECT * FROM mad_agent_runs WHERE id=%s',(decision['run_id'],)).fetchone()
            group=group_scope(db,run['group_id'],user['id']) if run['group_id'] else None
            membership(db,run['project_id'],user['id'],write=True,manage=True)
            if decision['revision']!=data.expected_revision or decision['state'] not in {'pending','resolved'} or run['status']=='completed':
                raise Problem('version_conflict',409)
            added=db.execute('INSERT INTO mad_decision_escalations VALUES (%s,%s,%s,%s,now()) ON CONFLICT DO NOTHING RETURNING *',
                             (decision_id,decision['revision'],user['id'],data.reason)).fetchone()
            if added and group:
                group_message(db,group,user['id'],'decision_escalated',data.reason,{'decision_id':str(decision_id),'run_id':str(run['id'])})
            return {'outcome':'waiting','decision_id':decision_id}

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
            if not role or role['role'] != run['role'] or decision['state'] not in {'pending','resolved'} or decision['revision'] != data.expected_revision or run['status']=='completed':
                raise Problem('role_required', 409)
            if context_changed(db,run):
                raise Problem('context_changed',409)
            if data.member_id not in [row['user_id'] for row in decision_candidates(db,decision,run)]:
                raise Problem('replacement_ineligible',409)
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
            updated=db.execute('''UPDATE mad_agent_runs SET user_id=%s,agent_id=%s,execution_revision=execution_revision+1,
                status=%s WHERE id=%s RETURNING *''', (data.member_id, agent['id'],'waiting_decision' if decision['state']=='pending' else 'paused',run['id'])).fetchone()
            db.execute('INSERT INTO mad_run_contributors VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (run['id'], data.member_id, run['role']))
            db.execute('UPDATE mad_run_decisions SET owner_id=%s,revision=revision+1 WHERE id=%s', (data.member_id, decision_id))
            db.execute('''UPDATE mad_resume_jobs SET user_id=%s,execution_revision=%s,revision=revision+1,
                state='blocked',reason='reassignment_review',updated_at=now() WHERE run_id=%s''',
                       (data.member_id,updated['execution_revision'],run['id']))
            if run['group_id'] and decision['state']=='resolved':
                db.execute("UPDATE mad_requirement_groups SET state='awaiting_takeover' WHERE id=%s",(run['group_id'],))
            event(db, run['id'], 'decision_reassigned', {'from': str(run['user_id']), 'to': str(data.member_id), 'checkpoint': run['checkpoint']})
            return {'id': decision_id, 'run_id': run['id'], 'owner_id': data.member_id}

    return router
