from typing import Literal
from uuid import UUID, uuid4

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


class AgentInput(BaseModel):
    expected_revision: int = Field(ge=1)
    model: str
    skills: list[str] = Field(max_length=4)


class Assignment(BaseModel):
    role: Literal["product", "development", "testing"]


class NewRun(BaseModel):
    id: UUID
    requirement_id: UUID


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

    def check(self, db, run):
        current = authorized_run(db, run["id"], run["user_id"])
        if current["status"] == "cancelled":
            raise Problem("run_cancelled", 409)
        if context_changed(db, current):
            raise Problem("context_changed", 409)
        return current

    def execute(self, run_id, user_id):
        with connect(self.url) as lease:
            locked = lease.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired", (str(run_id),)).fetchone()["acquired"]
            lease.commit()
            if not locked:
                return
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
                    receipts = db.execute("SELECT tool,result FROM mad_run_receipts WHERE run_id=%s AND generation=%s ORDER BY call_id",
                                          (run_id, run["generation"])).fetchall()
                    bundle = {"role": run["role"], "requirement": {key: requirement[key] for key in ("id", "revision", "title", "body", "criteria")},
                              "checkpoint": run["checkpoint"], "receipts": receipts,
                              "skills": [SKILLS[name] for name in run["config"]["skills"]]}
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
                        prior = db.execute("SELECT tool,arguments,result FROM mad_run_receipts WHERE run_id=%s AND generation=%s AND call_id=%s",
                                           (run_id, active["generation"], call_id)).fetchone()
                        if prior:
                            if prior["tool"] != name or prior["arguments"] != arguments:
                                raise Problem("operation_conflict", 409)
                            return prior["result"]
                        try:
                            result = jsonable_encoder(context_tool(db, active, name, arguments))
                        except Problem as exc:
                            event(db, run_id, "tool_refused", {"tool": name, "call_id": call_id, "reason": exc.code})
                            db.commit()
                            raise
                        db.execute("INSERT INTO mad_run_receipts VALUES (%s,%s,%s,%s,%s,%s)",
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
                status = {"context_changed": "stale", "run_cancelled": "cancelled"}.get(code, "failed")
                with connect(self.url) as db:
                    db.execute("UPDATE mad_agent_runs SET status=%s WHERE id=%s AND status<>'cancelled'", (status, run_id))
                    event(db, run_id, "stopped", {"reason": code})
            finally:
                lease.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(run_id),))


def agents_router(settings, identity):
    router = APIRouter(prefix="/api")
    authenticated = Depends(identity)
    service = AgentService(settings)

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
            role = db.execute("SELECT role FROM mad_project_roles WHERE project_id=%s AND user_id=%s", (project_id, user["id"])).fetchone()
            if not role or role["role"] not in {"development", "testing"}:
                raise Problem("permission_denied", 403)
            if role["role"] == "testing" and db.execute("""SELECT 1 FROM mad_handoffs WHERE project_id=%s AND requirement_id=%s
                                                          AND role='development' AND author=%s""", (project_id, data.requirement_id, user["id"])).fetchone():
                raise Problem("independent_review_required", 409)
            personal = personal_agent(db, user["id"], config["model"])
            if personal["model"] != config["model"]:
                raise Problem("model_configuration_changed", 409)
            snapshot = {"revision": personal["revision"], "model": personal["model"], "skills": personal["skills"], "base": config["base"]}
            db.execute("""INSERT INTO mad_agent_runs (id,project_id,requirement_id,requirement_revision,user_id,agent_id,role,config)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                       (data.id, project_id, data.requirement_id, requirement["revision"], user["id"], personal["id"], role["role"], Jsonb(snapshot)))
            row = authorized_run(db, data.id, user["id"])
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
            row["events"] = db.execute("SELECT id,kind,data,created_at FROM mad_run_events WHERE run_id=%s ORDER BY id LIMIT 500", (run_id,)).fetchall()
            return row

    @router.post("/runs/{run_id}/execute", status_code=202)
    def execute(run_id: UUID, background: BackgroundTasks, user=authenticated):
        with connect(settings.database_url) as db:
            row = authorized_run(db, run_id, user["id"])
            if context_changed(db, row):
                raise Problem("context_changed", 409)
        background.add_task(service.execute, run_id, user["id"])
        return {"id": run_id, "outcome": "accepted"}

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: UUID, user=authenticated):
        with connect(settings.database_url) as db:
            row = authorized_run(db, run_id, user["id"], lock=True)
            if row["status"] != "completed":
                db.execute("UPDATE mad_agent_runs SET status='cancelled' WHERE id=%s", (run_id,))
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
            for ref in db.execute("SELECT DISTINCT document_id FROM mad_run_references WHERE run_id=%s AND active", (run_id,)).fetchall():
                revision = db.execute("SELECT max(revision) AS revision FROM mad_context_documents WHERE project_id=%s AND id=%s",
                                      (row["project_id"], ref["document_id"])).fetchone()["revision"]
                if data.documents.get(ref["document_id"]) != revision:
                    raise Problem("version_conflict", 409)
            db.execute("UPDATE mad_run_references SET active=false WHERE run_id=%s", (run_id,))
            db.execute("UPDATE mad_agent_runs SET requirement_revision=%s,status='paused',generation=generation+1,confirmed_at=now() WHERE id=%s",
                       (requirement["revision"], run_id))
            event(db, run_id, "context_confirmed", {"from_revision": row["requirement_revision"], "revision": requirement["revision"]})
            return {"id": run_id, "outcome": "confirmed"}

    return router
