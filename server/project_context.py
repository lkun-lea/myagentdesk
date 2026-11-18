from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from server.db import connect
from server.errors import Problem
from server.projects import membership, record


class DocumentInput(BaseModel):
    id: UUID
    expected_revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50000)


class RequirementContent(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=12000)
    criteria: list[str] = Field(min_length=1, max_length=20)

    @field_validator('criteria')
    @classmethod
    def validate_criteria(cls, criteria):
        if any(not item.strip() or len(item) > 1000 for item in criteria):
            raise ValueError('Invalid acceptance criterion')
        return criteria


class RequirementInput(RequirementContent):
    id: UUID
    expected_revision: int = Field(ge=0)


def save_requirement(db, project_id, data, author):
    previous = db.execute('SELECT coalesce(max(revision),0) AS revision FROM mad_requirements WHERE project_id=%s AND id=%s',
                          (project_id, data.id)).fetchone()['revision']
    if previous != data.expected_revision:
        raise Problem('version_conflict', 409)
    revision = previous + 1
    db.execute('INSERT INTO mad_requirements (project_id,id,revision,title,body,criteria,author) VALUES (%s,%s,%s,%s,%s,%s,%s)',
               (project_id, data.id, revision, data.title, data.body, Jsonb(data.criteria), author))
    if previous:
        from server.requirement_groups import restart_group
        restart_group(db, project_id, data.id, revision, author)
    record(db, project_id, author, data.id, 'requirement_confirmed')
    return {'id': data.id, 'revision': revision}


def latest_requirement(db, project, requirement):
    row = db.execute("""SELECT * FROM mad_requirements WHERE project_id=%s AND id=%s
                        ORDER BY revision DESC LIMIT 1""", (project, requirement)).fetchone()
    if not row:
        raise Problem("requirement_not_found", 404)
    return row


def context_changed(db, run):
    current = latest_requirement(db, run["project_id"], run["requirement_id"])
    if current["revision"] != run["requirement_revision"]:
        return True
    if run["role"] == "testing":
        prior = db.execute("SELECT result FROM mad_run_receipts WHERE run_id=%s AND generation=%s AND tool='read_handoff' LIMIT 1",
                           (run["id"], run["generation"])).fetchone()
        if prior:
            candidate = db.execute("SELECT * FROM mad_handoffs WHERE run_id=%s", (run['candidate_run'],)).fetchone() if run.get('candidate_run') else db.execute("""SELECT * FROM mad_handoffs WHERE project_id=%s AND requirement_id=%s
                                      AND requirement_revision=%s AND role='development' ORDER BY created_at DESC LIMIT 1""",
                                   (run["project_id"], run["requirement_id"], run["requirement_revision"])).fetchone()
            if not candidate or str(candidate["run_id"]) != prior["result"]["run_id"] or handoff_outdated(db, candidate):
                return True
    return db.execute("""SELECT 1 FROM mad_run_references ref WHERE ref.run_id=%s AND ref.active AND ref.revision <
                        (SELECT max(d.revision) FROM mad_context_documents d WHERE d.project_id=%s AND d.id=ref.document_id)
                        LIMIT 1""", (run["id"], run["project_id"])).fetchone() is not None


def event(db, run_id, kind, data):
    db.execute("INSERT INTO mad_run_events (run_id, kind, data) VALUES (%s,%s,%s)", (run_id, kind, Jsonb(data)))


def handoff_outdated(db, handoff):
    current = latest_requirement(db, handoff["project_id"], handoff["requirement_id"])
    if current["revision"] != handoff["requirement_revision"]:
        return True
    for ref in handoff["context_references"]:
        latest = db.execute("SELECT max(revision) AS revision FROM mad_context_documents WHERE project_id=%s AND id=%s",
                            (handoff["project_id"], ref["document_id"])).fetchone()["revision"]
        if latest != ref["revision"]:
            return True
    if handoff['candidate_run']:
        candidate = db.execute('SELECT * FROM mad_handoffs WHERE run_id=%s', (handoff['candidate_run'],)).fetchone()
        if not candidate or handoff_outdated(db, candidate):
            return True
    return False


def authorized_run(db, run_id, user_id, *, lock=False):
    row = db.execute("SELECT * FROM mad_agent_runs WHERE id=%s AND user_id=%s",
                     (run_id, user_id)).fetchone()
    if not row:
        raise Problem("run_not_found", 404)
    membership(db, row["project_id"], user_id, write=True)
    # Membership takes the project lock; a reassignment may have completed while waiting.
    row = db.execute("SELECT * FROM mad_agent_runs WHERE id=%s" + (" FOR UPDATE" if lock else ""), (run_id,)).fetchone()
    if str(row['user_id']) != str(user_id):
        raise Problem("run_not_found", 404)
    role = db.execute("SELECT role FROM mad_project_roles WHERE project_id=%s AND user_id=%s",
                      (row["project_id"], user_id)).fetchone()
    if not role or role["role"] != row["role"]:
        raise Problem("permission_denied", 403)
    if row['role'] == 'testing' and db.execute('''SELECT 1 FROM mad_run_contributors c JOIN mad_agent_runs r ON r.id=c.run_id
            WHERE r.project_id=%s AND r.requirement_id=%s AND c.role='development' AND c.user_id=%s AND r.checkpoint>0 LIMIT 1''',
                                             (row['project_id'], row['requirement_id'], user_id)).fetchone():
        raise Problem('independent_review_required', 409)
    return row


def document_list(db, project, query=""):
    return db.execute("""SELECT id,revision,title,source,left(body,160) AS summary FROM
                         (SELECT DISTINCT ON(id) * FROM mad_context_documents WHERE project_id=%s
                          ORDER BY id,revision DESC) d
                         WHERE strpos(lower(title || ' ' || body),lower(%s)) > 0 ORDER BY title LIMIT 20""",
                      (project, query)).fetchall()


def context_tool(db, run, tool, args, call_id=None):
    project = run["project_id"]
    if tool == 'request_decision':
        from server.decisions import request_decision
        return request_decision(db, run, args, call_id)
    if tool == 'read_group_messages':
        if not run.get('group_id'):
            raise Problem('group_not_found', 404)
        from server.requirement_groups import group_scope
        group_scope(db, run['group_id'], run['user_id'])
        after = args.get('after', 0)
        if not isinstance(after, int) or not 0 <= after < 2**63:
            raise Problem('invalid_request', 422)
        rows = db.execute('''SELECT sequence,kind,left(body,2000) AS body,data FROM mad_group_messages
                             WHERE group_id=%s AND sequence>%s ORDER BY sequence LIMIT 20''', (run['group_id'], after)).fetchall()
        return {'messages': rows, 'next_cursor': rows[-1]['sequence'] if rows else after}
    if tool == "list_context":
        return {"documents": document_list(db, project, str(args.get("query", ""))[:120])}
    if tool == "read_context":
        try:
            doc = UUID(args["id"])
            revision = int(args["revision"])
            start, end = int(args.get("start", 0)), int(args.get("end", 2000))
        except (KeyError, ValueError, TypeError):
            raise Problem("invalid_request", 422) from None
        if start < 0 or not start < end <= start + 4000:
            raise Problem("invalid_request", 422)
        row = db.execute("SELECT title,source,body FROM mad_context_documents WHERE project_id=%s AND id=%s AND revision=%s",
                         (project, doc, revision)).fetchone()
        if not row:
            raise Problem("document_not_found", 404)
        if start >= len(row["body"]):
            raise Problem("invalid_request", 422)
        newest = db.execute("SELECT max(revision) AS revision FROM mad_context_documents WHERE project_id=%s AND id=%s",
                            (project, doc)).fetchone()["revision"]
        if newest != revision:
            raise Problem("context_changed", 409)
        end = min(end, len(row["body"]))
        db.execute("""INSERT INTO mad_run_references (run_id,document_id,revision,start_offset,end_offset)
                      VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id,document_id,revision,start_offset,end_offset)
                      DO UPDATE SET active=true""",
                   (run["id"], doc, revision, start, end))
        return {"id": str(doc), "revision": revision, "source": row["source"], "title": row["title"],
                "start": start, "end": end, "text": row["body"][start:end]}
    if tool == "read_handoff":
        handoff = db.execute("SELECT * FROM mad_handoffs WHERE run_id=%s", (run['candidate_run'],)).fetchone() if run.get('candidate_run') else db.execute("""SELECT h.* FROM mad_handoffs h JOIN mad_agent_runs r ON r.id=h.run_id
                                WHERE h.project_id=%s AND h.requirement_id=%s AND h.requirement_revision=%s
                                AND h.role='development' AND h.author<>%s ORDER BY h.created_at DESC LIMIT 1""",
                             (project, run["requirement_id"], run["requirement_revision"], run["user_id"])).fetchone()
        if not handoff:
            raise Problem("handoff_not_found", 409)
        if handoff_outdated(db, handoff):
            raise Problem("context_changed", 409)
        return {"run_id": str(handoff["run_id"]), "summary": handoff["summary"], "evidence": handoff["evidence"]}
    if tool == "submit_handoff":
        summary = args.get("summary", "")
        evidence = args.get("evidence", [])
        if not isinstance(summary, str) or not 1 <= len(summary) <= 6000 or not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
            raise Problem("evidence_required", 422)
        if any(not isinstance(item, str) or not 1 <= len(item) <= 2000 for item in evidence):
            raise Problem("evidence_required", 422)
        if not db.execute("SELECT 1 FROM mad_run_references WHERE run_id=%s AND active", (run["id"],)).fetchone():
            raise Problem("evidence_required", 409)
        candidate = None
        verdict = "delivered"
        if run["role"] == "testing":
            original = context_tool(db, run, "read_handoff", {})
            candidate = UUID(original["run_id"])
            prior = db.execute("SELECT result FROM mad_run_receipts WHERE run_id=%s AND generation=%s AND tool='read_handoff' LIMIT 1",
                               (run["id"], run["generation"])).fetchone()
            if not prior:
                raise Problem("evidence_required", 409)
            if prior["result"]["run_id"] != original["run_id"]:
                raise Problem("context_changed", 409)
            verdict = args.get("verdict")
            if verdict not in {"passed", "rejected"}:
                raise Problem("invalid_request", 422)
        elif run['role'] == 'product' and run.get('group_id'):
            verdict = 'proposed'
        elif run["role"] != "development":
            raise Problem("permission_denied", 403)
        references = db.execute("SELECT document_id,revision,start_offset,end_offset FROM mad_run_references WHERE run_id=%s AND active",
                                (run["id"],)).fetchall()
        db.execute("""INSERT INTO mad_handoffs (run_id,project_id,requirement_id,requirement_revision,author,role,
                       summary,evidence,verdict,candidate_run,context_references) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (run_id) DO UPDATE SET requirement_revision=excluded.requirement_revision,
                       summary=excluded.summary,evidence=excluded.evidence,verdict=excluded.verdict,
                       candidate_run=excluded.candidate_run,context_references=excluded.context_references""",
                   (run["id"], project, run["requirement_id"], run["requirement_revision"], run["user_id"],
                    run["role"], summary, Jsonb(evidence), verdict, candidate, Jsonb([
                        {**ref, "document_id": str(ref["document_id"])} for ref in references])))
        return {"run_id": str(run["id"]), "verdict": verdict, "submitted": True}
    raise Problem("tool_unavailable", 404)


def context_router(url, identity):
    router = APIRouter(prefix="/api/projects/{project_id}")
    authenticated = Depends(identity)

    @router.get("/context")
    def documents(project_id: UUID, q: str = "", user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True)
            return document_list(db, project_id, q[:120])

    @router.put("/context")
    def publish(project_id: UUID, data: DocumentInput, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True, manage=True)
            previous = db.execute("SELECT coalesce(max(revision),0) AS revision FROM mad_context_documents WHERE project_id=%s AND id=%s",
                                  (project_id, data.id)).fetchone()["revision"]
            if previous != data.expected_revision:
                raise Problem("version_conflict", 409)
            db.execute("INSERT INTO mad_context_documents (project_id,id,revision,title,source,body,author) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                       (project_id, data.id, previous + 1, data.title, data.source, data.body, user["id"]))
            from server.requirement_groups import invalidate_group_context
            invalidate_group_context(db, project_id, data.id, user['id'])
            record(db, project_id, user["id"], data.id, "context_published")
            return {"id": data.id, "revision": previous + 1}

    @router.get("/context/{document_id}/{revision}")
    def original(project_id: UUID, document_id: UUID, revision: int, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True)
            row = db.execute("SELECT id,revision,title,source,body FROM mad_context_documents WHERE project_id=%s AND id=%s AND revision=%s",
                             (project_id, document_id, revision)).fetchone()
            if not row:
                raise Problem("document_not_found", 404)
            return row

    @router.get("/requirements")
    def requirements(project_id: UUID, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True)
            return db.execute("SELECT DISTINCT ON(id) * FROM mad_requirements WHERE project_id=%s ORDER BY id,revision DESC",
                              (project_id,)).fetchall()

    @router.put("/requirements")
    def confirm_requirement(project_id: UUID, data: RequirementInput, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True, manage=True)
            return save_requirement(db, project_id, data, user['id'])

    @router.get("/handoffs")
    def handoffs(project_id: UUID, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True)
            rows = db.execute("SELECT * FROM mad_handoffs WHERE project_id=%s ORDER BY created_at DESC LIMIT 50", (project_id,)).fetchall()
            for row in rows:
                row["outdated"] = handoff_outdated(db, row)
            return rows

    return router
