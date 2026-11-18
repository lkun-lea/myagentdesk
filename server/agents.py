from typing import Literal
from uuid import UUID, uuid4
import time
from threading import Event

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.encoders import jsonable_encoder
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from server.db import connect
from server.errors import Problem
from server.pi_runtime import PiRuntime, SKILLS, TOOLS
from server.project_context import authorized_run, context_changed, context_tool, event, latest_requirement
from server.projects import membership, record
from server.requirement_groups import check_group_run, group_handoff, group_message, groups_router
from server.resources import bind_resources, missing_resources, publish_artifact, resources_router


class AgentInput(BaseModel):
    expected_revision: int = Field(ge=1)
    model: str
    skills: list[str] = Field(max_length=4)


class Assignment(BaseModel):
    role: Literal["product", "development", "testing"]


class NewRun(BaseModel):
    id: UUID
    requirement_id: UUID
    group_id: UUID | None = None
    resource_ids: list[UUID] = Field(default_factory=list, max_length=8)


class VersionConfirmation(BaseModel):
    expected_revision: int = Field(ge=1)
    documents: dict[UUID, int] = Field(default_factory=dict)


def personal_agent(db, user_id, model):
    db.execute("""INSERT INTO mad_agents (user_id,id,model,skills) VALUES (%s,%s,%s,%s) ON CONFLICT (user_id) DO NOTHING""",
               (user_id, uuid4(), model, Jsonb(list(SKILLS))))
    return db.execute("SELECT id,revision,model,skills FROM mad_agents WHERE user_id=%s", (user_id,)).fetchone()


class AgentService:
    def __init__(self, settings):
        self.url = settings.database_url
        self.runtime = PiRuntime(settings)
        self.settings = settings
        self.stopping = Event()

    def drain_jobs(self):
        from server.recovery import drain_jobs
        drain_jobs(self)

    def process_job(self, run_id):
        from server.recovery import process_job
        return process_job(self, run_id)

    def check(self, db, run):
        current = authorized_run(db, run["id"], run["user_id"])
        if current['execution_revision'] != run['execution_revision']:
            raise Problem('execution_replaced', 409)
        if self.stopping.is_set():
            raise Problem('worker_stopping', 409)
        if current["status"] == "cancelled":
            raise Problem("run_cancelled", 409)
        if current['status'] == 'waiting_decision':
            raise Problem('decision_pending', 409)
        if current['status'] != 'completed':
            check_group_run(db, current)
        if context_changed(db, current):
            raise Problem("context_changed", 409)
        if missing_resources(db, current):
            raise Problem('authorization_required', 409)
        return current

    def execute(self, run_id, user_id):
        run = None
        with connect(self.url) as lease:
            locked = lease.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired", (str(run_id),)).fetchone()["acquired"]
            lease.commit()
            if not locked:
                return False
            try:
                with connect(self.url) as db:
                    run = authorized_run(db, run_id, user_id, lock=True)
                    if run["status"] == "completed":
                        return
                    if run["status"] == "cancelled":
                        db.execute("UPDATE mad_agent_runs SET status='paused' WHERE id=%s", (run_id,))
                    self.check(db, run)
                    config = self.runtime.configuration()
                    if config["model"] != run["config"]["model"] or config["base"] != run["config"]["base"]:
                        raise Problem("model_configuration_changed", 409)
                    requirement = latest_requirement(db, run["project_id"], run["requirement_id"])
                    receipts = db.execute("SELECT tool,result FROM mad_run_receipts WHERE run_id=%s AND generation=%s ORDER BY created_at,call_id",
                                          (run_id, run["generation"])).fetchall()
                    bundle = {"run_id": str(run['id']), "generation": run['generation'], "execution_revision":run['execution_revision'],"role": run["role"], "requirement": {key: requirement[key] for key in ("id", "revision", "title", "body", "criteria")},
                              "checkpoint": run["checkpoint"], "receipts": receipts,
                              "skills": [SKILLS[name] for name in run["config"]["skills"]]}
                    if requirement['specification']:
                        bundle['requirement']['specification'] = requirement['specification']
                    bundle['decisions'] = db.execute('''SELECT question,answer,note,checkpoint FROM mad_run_decisions
                                                       WHERE run_id=%s AND generation=%s AND state='resolved' ORDER BY created_at''',
                                                    (run_id, run['generation'])).fetchall()
                    bundle['resources'] = db.execute('''SELECT p.id,p.name FROM mad_run_resources rr JOIN mad_project_resources p
                        ON p.id=rr.resource_id WHERE rr.run_id=%s''', (run_id,)).fetchall()
                    if run['group_id']:
                        group = check_group_run(db, run)
                        bundle['group'] = {'id': str(group['id']), 'stage': group['stage'], 'revision': group['revision'],
                                           'source': group['source']}
                        bundle['group']['recent_messages'] = db.execute('''SELECT kind,body,data FROM
                            (SELECT sequence,kind,left(body,2000) AS body,data FROM mad_group_messages WHERE group_id=%s
                             ORDER BY sequence DESC LIMIT 8) m ORDER BY sequence''', (group['id'],)).fetchall()
                    db.execute("UPDATE mad_agent_runs SET status='running' WHERE id=%s", (run_id,))
                    event(db, run_id, "started", {"checkpoint": run["checkpoint"]})

                def check():
                    with connect(self.url) as db:
                        self.check(db, run)

                def callback(call_id, name, arguments):
                    with connect(self.url) as db:
                        active = self.check(db, run)
                        if name == 'runtime_check':
                            return {"authorized": True}
                        if name == "runtime_event":
                            if arguments.get("kind") == "text_delta":
                                event(db, run_id, "text_delta", {"text": arguments.get("text", "")[:2000]})
                            return None
                        if active['status'] == 'completed':
                            raise Problem('run_completed', 409)
                        prior = db.execute("SELECT tool,arguments,result FROM mad_run_receipts WHERE run_id=%s AND generation=%s AND call_id=%s",
                                           (run_id, active["generation"], call_id)).fetchone()
                        if prior:
                            if prior["tool"] != name or prior["arguments"] != arguments:
                                raise Problem("operation_conflict", 409)
                            return prior["result"]
                        try:
                            result = jsonable_encoder(publish_artifact(db, active, arguments, self.settings) if name=='publish_artifact'
                                                      else context_tool(db, active, name, arguments, call_id))
                            if name == 'submit_handoff':
                                group_handoff(db, active, result)
                        except Problem as exc:
                            event(db, run_id, "tool_refused", {"tool": name, "call_id": call_id, "reason": exc.code})
                            db.commit()
                            if exc.code in {'invalid_request', 'evidence_required', 'handoff_not_found'}:
                                return {'error': exc.code, 'message': {
                                    'invalid_request': '工具参数无效。read_context 需 0 <= start < end <= start + 4000，按目录中真实 id 和版本分段读取；其他工具请核对参数定义。',
                                    'evidence_required': '交接须先读取原文并给出具体证据。独立测试还须读取当前研发交接；固定规格的需求须逐条提交 case_results。',
                                    'handoff_not_found': '当前需求尚无可供独立测试读取的研发交接，请核对阶段及已有工具结果。',
                                }[exc.code]}
                            raise
                        db.execute("INSERT INTO mad_run_receipts (run_id,call_id,generation,tool,arguments,result) VALUES (%s,%s,%s,%s,%s,%s)",
                                   (run_id, call_id, active["generation"], name, Jsonb(arguments), Jsonb(result)))
                        db.execute("UPDATE mad_agent_runs SET checkpoint=checkpoint+1 WHERE id=%s", (run_id,))
                        event(db, run_id, "tool_result", {"tool": name, "call_id": call_id, "result": result})
                        return result

                self.runtime.run(bundle, TOOLS, callback, check)
                with connect(self.url) as db:
                    self.check(db, run)
                    handoff = db.execute("SELECT 1 FROM mad_handoffs WHERE run_id=%s AND requirement_revision=%s",
                                         (run_id, run["requirement_revision"])).fetchone()
                    status = "completed" if handoff else "paused"
                    db.execute("UPDATE mad_agent_runs SET status=%s WHERE id=%s", (status, run_id))
                    event(db, run_id, status, {})
            except (Problem, OSError, ValueError, KeyError, TypeError, psycopg.Error) as exc:
                code = exc.code if isinstance(exc, Problem) else "runtime_unavailable"
                status = {"context_changed": "stale", "run_cancelled": "cancelled", 'decision_pending': 'waiting_decision',
                          'run_completed': 'completed', 'stage_changed': 'stale', 'authorization_required': 'waiting_authorization',
                          'worker_stopping': 'paused'}.get(code, "failed")
                with connect(self.url) as db:
                    changed = db.execute('''UPDATE mad_agent_runs SET status=%s WHERE id=%s AND status<>'cancelled'
                        AND execution_revision=%s RETURNING *''', (status, run_id, run['execution_revision'] if run else -1)).fetchone()
                    if changed and code=='authorization_required':
                        from server.recovery import block_run
                        block_run(db, changed, code)
                    event(db, run_id, "stopped", {"reason": code,'execution_revision':run['execution_revision'] if run else None,'state_changed':bool(changed)})
            finally:
                lease.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(run_id),))

    def resume(self, run_id, user_id):
        for _ in range(20):
            if self.execute(run_id, user_id) is not False:
                with connect(self.url) as db:
                    row = db.execute('SELECT group_id FROM mad_agent_runs WHERE id=%s', (run_id,)).fetchone()
                if row and row['group_id']:
                    self.drive(row['group_id'])
                return
            time.sleep(0.25)

    def group_run(self, db, group, identifier=None, *, external=False):
        role = group['stage']
        if role not in {'product', 'development', 'testing'} or group['state'] != 'active':
            raise Problem('stage_changed', 409)
        if not external and role != 'product' and db.execute(
            'SELECT 1 FROM mad_delivery_dispatches WHERE group_id=%s LIMIT 1', (group['id'],)
        ).fetchone():
            raise Problem('external_execution_required', 409)
        owner = group[role + '_id']
        membership(db, group['project_id'], owner, write=True)
        position = db.execute('SELECT role FROM mad_project_roles WHERE project_id=%s AND user_id=%s', (group['project_id'], owner)).fetchone()
        if not position or position['role'] != role:
            raise Problem('role_required', 409)
        prior = db.execute('SELECT * FROM mad_agent_runs WHERE group_id=%s AND group_revision=%s', (group['id'], group['revision'])).fetchone()
        if prior:
            return prior
        config = self.runtime.configuration()
        personal = personal_agent(db, owner, config['model'])
        if personal['model'] != config['model']:
            raise Problem('model_configuration_changed', 409)
        if role == 'testing' and db.execute('''SELECT 1 FROM mad_run_contributors c JOIN mad_agent_runs r ON r.id=c.run_id
                WHERE r.project_id=%s AND r.requirement_id=%s AND c.role='development' AND c.user_id=%s AND r.checkpoint>0 LIMIT 1''',
                                           (group['project_id'], group['requirement_id'], owner)).fetchone():
            raise Problem('independent_review_required', 409)
        identifier = identifier or uuid4()
        snapshot = {'revision': personal['revision'], 'model': personal['model'], 'skills': personal['skills'], 'base': config['base']}
        db.execute('''INSERT INTO mad_agent_runs (id,project_id,requirement_id,requirement_revision,user_id,agent_id,role,config,group_id,group_revision,candidate_run)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                   (identifier, group['project_id'], group['requirement_id'], group['requirement_revision'], owner, personal['id'], role,
                    Jsonb(snapshot), group['id'], group['revision'], group['last_handoff'] if role == 'testing' else None))
        run = db.execute('SELECT * FROM mad_agent_runs WHERE id=%s', (identifier,)).fetchone()
        if not run or run['group_id'] != group['id'] or run['group_revision'] != group['revision']:
            raise Problem('operation_conflict', 409)
        db.execute('INSERT INTO mad_run_contributors VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (identifier, owner, role))
        if role=='development':
            identifiers = [r['resource_id'] for r in db.execute('SELECT resource_id FROM mad_group_resources WHERE group_id=%s', (group['id'],))]
            bind_resources(db, identifier, group['project_id'], owner, identifiers)
        return run

    def drive(self, group_id):
        with connect(self.url) as lease:
            key = 'mad:group:' + str(group_id)
            if not lease.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired', (key,)).fetchone()['acquired']:
                return
            lease.commit()
            try:
                for _ in range(12):
                    with connect(self.url) as db:
                        group = db.execute('SELECT * FROM mad_requirement_groups WHERE id=%s', (group_id,)).fetchone()
                        if not group or not group['auto_run'] or group['state'] != 'active' or group['stage'] not in {'product','development','testing'}:
                            return
                        from server.requirement_groups import group_scope
                        group = group_scope(db, group_id, group[group['stage'] + '_id'])
                        run = self.group_run(db, group)
                    if self.execute(run['id'], run['user_id']) is False:
                        return
                    with connect(self.url) as db:
                        current = db.execute('SELECT status FROM mad_agent_runs WHERE id=%s', (run['id'],)).fetchone()
                        if current['status'] != 'completed':
                            return
                with connect(self.url) as db:
                    group_message(db, group, run['user_id'], 'attention', '本次自动推进已达 12 个阶段，请核对返工原因后继续。')
            except Problem as exc:
                with connect(self.url) as db:
                    group_message(db, group, group['product_id'], 'attention', '自动接力等待处理。', {'reason': exc.code})
            finally:
                lease.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def agents_router(settings, identity, service=None):
    router = APIRouter(prefix="/api")
    authenticated = Depends(identity)
    service = service or AgentService(settings)

    @router.get("/agent")
    def agent(user=authenticated):
        config = service.runtime.configuration()
        with connect(settings.database_url) as db:
            return {"agent": personal_agent(db, user["id"], config["model"]), "models": [config["model"]] if config["model"] else [],
                    "skills": list(SKILLS), "available": bool(all(config.values()))}

    @router.put("/agent")
    def configure(data: AgentInput, user=authenticated):
        if data.model != service.runtime.configuration()["model"] or any(name not in SKILLS for name in data.skills):
            raise Problem("invalid_request", 422)
        with connect(settings.database_url) as db:
            row = db.execute("""UPDATE mad_agents SET revision=revision+1,model=%s,skills=%s WHERE user_id=%s AND revision=%s
                                RETURNING id,revision,model,skills""",
                             (data.model, Jsonb(data.skills), user["id"], data.expected_revision)).fetchone()
            if not row:
                raise Problem("version_conflict", 409)
            return row

    @router.put("/projects/{project_id}/roles/{member_id}")
    def assign(project_id: UUID, member_id: UUID, data: Assignment, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user["id"], write=True, manage=True)
            membership(db, project_id, member_id)
            db.execute("INSERT INTO mad_project_roles VALUES (%s,%s,%s) ON CONFLICT (project_id,user_id) DO UPDATE SET role=excluded.role",
                       (project_id, member_id, data.role))
            record(db, project_id, user["id"], member_id, "agent_role_assigned")
            return data

    @router.get("/projects/{project_id}/roles")
    def roles(project_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user["id"], write=True)
            return db.execute("SELECT user_id,role FROM mad_project_roles WHERE project_id=%s", (project_id,)).fetchall()

    @router.post("/projects/{project_id}/runs", status_code=201)
    def new_run(project_id: UUID, data: NewRun, user=authenticated):
        config = service.runtime.configuration()
        with connect(settings.database_url) as db:
            membership(db, project_id, user["id"], write=True)
            requirement = latest_requirement(db, project_id, data.requirement_id)
            if data.group_id is None:
                linked = db.execute('SELECT id FROM mad_requirement_groups WHERE project_id=%s AND requirement_id=%s',
                                    (project_id, data.requirement_id)).fetchone()
                if linked:
                    data.group_id = linked['id']
            role = db.execute("SELECT role FROM mad_project_roles WHERE project_id=%s AND user_id=%s", (project_id, user["id"])).fetchone()
            if not role or role["role"] not in ({'product','development','testing'} if data.group_id else {'development','testing'}):
                raise Problem("permission_denied", 403)
            group = None
            if data.group_id:
                from server.requirement_groups import group_scope
                group = group_scope(db, data.group_id, user['id'])
                if group['project_id'] != project_id or group['requirement_id'] != data.requirement_id:
                    raise Problem('group_not_found', 404)
                if group['stage'] != role['role'] or group.get(role['role'] + '_id') != user['id'] or group['state'] != 'active':
                    raise Problem('stage_changed', 409)
                return service.group_run(db, group, data.id)
            if role["role"] == "testing" and db.execute("""SELECT 1 FROM mad_handoffs WHERE project_id=%s AND requirement_id=%s
                                                          AND role='development' AND author=%s""", (project_id, data.requirement_id, user["id"])).fetchone():
                raise Problem("independent_review_required", 409)
            existing=db.execute('SELECT id FROM mad_agent_runs WHERE id=%s',(data.id,)).fetchone()
            if existing:
                prior=authorized_run(db,data.id,user['id'])
                bound={row['resource_id'] for row in db.execute('SELECT resource_id FROM mad_run_resources WHERE run_id=%s',(data.id,))}
                if prior['project_id']!=project_id or prior['requirement_id']!=data.requirement_id or bound!=set(data.resource_ids):
                    raise Problem('operation_conflict',409)
                return prior
            personal = personal_agent(db, user["id"], config["model"])
            if personal["model"] != config["model"]:
                raise Problem("model_configuration_changed", 409)
            snapshot = {"revision": personal["revision"], "model": personal["model"], "skills": personal["skills"], "base": config["base"]}
            db.execute("""INSERT INTO mad_agent_runs (id,project_id,requirement_id,requirement_revision,user_id,agent_id,role,config,group_id,group_revision,candidate_run)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                       (data.id, project_id, data.requirement_id, requirement["revision"], user["id"], personal["id"], role["role"], Jsonb(snapshot),
                        data.group_id, group['revision'] if group else None, group['last_handoff'] if group and role['role']=='testing' else None))
            row = authorized_run(db, data.id, user["id"])
            bind_resources(db, data.id, project_id, user['id'], data.resource_ids)
            db.execute('INSERT INTO mad_run_contributors VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (data.id, user['id'], role['role']))
            if row["project_id"] != project_id or row["requirement_id"] != data.requirement_id:
                raise Problem("operation_conflict", 409)
            return row

    @router.get("/projects/{project_id}/runs")
    def runs(project_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            membership(db, project_id, user["id"], write=True)
            return db.execute("SELECT * FROM mad_agent_runs WHERE project_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 30",
                              (project_id, user["id"])).fetchall()

    @router.get("/runs/{run_id}")
    def inspect(run_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            row = authorized_run(db, run_id, user["id"])
            if row["status"] == "running":
                abandoned = db.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired", (str(run_id),)).fetchone()["acquired"]
                if abandoned:
                    db.execute("UPDATE mad_agent_runs SET status='paused' WHERE id=%s", (run_id,))
                    row["status"] = "paused"
                    event(db, run_id, "worker_interrupted", {"checkpoint": row["checkpoint"]})
            row["context_changed"] = context_changed(db, row)
            row["references"] = db.execute("SELECT document_id,revision,start_offset,end_offset,active FROM mad_run_references WHERE run_id=%s", (run_id,)).fetchall()
            row['missing_resources'] = missing_resources(db, row)
            row['resume_job'] = db.execute('SELECT state,reason,attempts FROM mad_resume_jobs WHERE run_id=%s', (run_id,)).fetchone()
            row["events"] = db.execute("SELECT id,kind,data,created_at FROM mad_run_events WHERE run_id=%s ORDER BY id LIMIT 500", (run_id,)).fetchall()
            return row

    @router.post("/runs/{run_id}/execute", status_code=202)
    def execute(run_id: UUID, background: BackgroundTasks, user=authenticated):
        with connect(settings.database_url) as db:
            row = authorized_run(db, run_id, user["id"])
            if context_changed(db, row):
                raise Problem("context_changed", 409)
            check_group_run(db, row)
            if row['status'] == 'waiting_decision':
                raise Problem('decision_pending', 409)
            if row['status']=='cancelled':
                row=db.execute("UPDATE mad_agent_runs SET status='paused' WHERE id=%s RETURNING *",(run_id,)).fetchone()
            from server.recovery import enqueue
            enqueue(db, row)
        background.add_task(service.process_job, run_id)
        return {"id": run_id, "outcome": "accepted"}

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            row = authorized_run(db, run_id, user["id"], lock=True)
            if row["status"] != "completed":
                db.execute("UPDATE mad_agent_runs SET status='cancelled',execution_revision=execution_revision+1 WHERE id=%s", (run_id,))
                event(db, run_id, "cancelled", {})
            return {"outcome": "completed" if row["status"] == "completed" else "cancelled"}

    @router.post("/runs/{run_id}/confirm")
    def confirm(run_id: UUID, data: VersionConfirmation, user=authenticated):
        with connect(settings.database_url) as db:
            row = authorized_run(db, run_id, user["id"], lock=True)
            acquired = db.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired", (str(run_id),)).fetchone()["acquired"]
            if not acquired:
                raise Problem("run_busy", 409)
            requirement = latest_requirement(db, row["project_id"], row["requirement_id"])
            if requirement["revision"] != data.expected_revision:
                raise Problem("version_conflict", 409)
            if requirement['specification'] and db.execute(
                'SELECT 1 FROM mad_handoffs WHERE run_id=%s', (run_id,)
            ).fetchone():
                raise Problem('run_completed', 409)
            for ref in db.execute("SELECT DISTINCT document_id FROM mad_run_references WHERE run_id=%s AND active", (run_id,)).fetchall():
                revision = db.execute("SELECT max(revision) AS revision FROM mad_context_documents WHERE project_id=%s AND id=%s",
                                      (row["project_id"], ref["document_id"])).fetchone()["revision"]
                if data.documents.get(ref["document_id"]) != revision:
                    raise Problem("version_conflict", 409)
            db.execute("UPDATE mad_run_references SET active=false WHERE run_id=%s", (run_id,))
            db.execute("UPDATE mad_run_decisions SET state='superseded',revision=revision+1 WHERE run_id=%s AND state='pending'", (run_id,))
            if row['group_id']:
                group = check_group_run(db, row)
                db.execute("UPDATE mad_requirement_groups SET state='active' WHERE id=%s", (group['id'],))
            db.execute("UPDATE mad_agent_runs SET requirement_revision=%s,status='paused',generation=generation+1,execution_revision=execution_revision+1,confirmed_at=now() WHERE id=%s",
                       (requirement["revision"], run_id))
            event(db, run_id, "context_confirmed", {"from_revision": row["requirement_revision"], "revision": requirement["revision"]})
            return {"id": run_id, "outcome": "confirmed"}

    router.include_router(groups_router(settings, identity, service))
    router.include_router(resources_router(settings, identity, service))
    return router
