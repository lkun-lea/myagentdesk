import json
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from server.db import connect
from server.errors import Problem
from server.sessions import now


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    workspace_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        if not value.strip():
            raise ValueError("name required")
        return value.strip()


class InviteInput(BaseModel):
    id: UUID
    email: EmailStr


class MemberInput(BaseModel):
    role: Literal["admin", "member"]


def membership(db, project_id, user_id, *, write=False, manage=False):
    if write:
        db.execute("SELECT id FROM mad_projects WHERE id = %s FOR UPDATE", (project_id,)).fetchone()
    row = db.execute(
        """SELECT p.id, p.name, p.workspace_id, m.role FROM mad_projects p
           JOIN mad_memberships m ON m.project_id = p.id WHERE p.id = %s AND m.user_id = %s""",
        (project_id, user_id),
    ).fetchone()
    if not row:
        raise Problem("project_not_found", 404)
    if manage and row["role"] not in {"owner", "admin"}:
        raise Problem("permission_denied", 403)
    return row


def record(db, project_id, user_id, target, action):
    db.execute(
        "INSERT INTO mad_project_events (project_id, actor, target, action) VALUES (%s,%s,%s,%s)",
        (project_id, user_id, str(target), action),
    )


def projects_router(url, identity):
    router = APIRouter(prefix="/api")
    authenticated = Depends(identity)

    @router.get("/projects")
    def list_projects(user=authenticated):
        with connect(url) as db:
            return db.execute(
                """SELECT p.id, p.name, p.workspace_id, w.name AS workspace_name, m.role
                   FROM mad_memberships m JOIN mad_projects p ON p.id = m.project_id
                   JOIN mad_workspaces w ON w.id = p.workspace_id WHERE m.user_id = %s ORDER BY w.name, p.name, p.id""",
                (user["id"],),
            ).fetchall()

    @router.post("/projects", status_code=201)
    def create_project(data: ProjectInput, user=authenticated):
        with connect(url) as db:
            workspace = data.workspace_id
            if workspace:
                owner = db.execute(
                    "SELECT 1 FROM mad_workspaces WHERE id = %s AND owner_id = %s",
                    (workspace, user["id"]),
                ).fetchone()
                if not owner:
                    raise Problem("workspace_not_found", 404)
            else:
                db.execute("SELECT id FROM mad_users WHERE id = %s FOR UPDATE", (user["id"],)).fetchone()
                personal = db.execute(
                    "SELECT id FROM mad_workspaces WHERE owner_id = %s AND name = %s ORDER BY id LIMIT 1",
                    (user["id"], "个人工作区"),
                ).fetchone()
                if personal:
                    workspace = personal["id"]
                else:
                    workspace = uuid4()
                    db.execute("INSERT INTO mad_workspaces (id, name, owner_id) VALUES (%s,%s,%s)",
                               (workspace, "个人工作区", user["id"]))
            project = uuid4()
            db.execute("INSERT INTO mad_projects VALUES (%s,%s,%s)", (project, workspace, data.name))
            db.execute("INSERT INTO mad_memberships VALUES (%s,%s,'owner')", (project, user["id"]))
            record(db, project, user["id"], project, "project_created")
            return {"id": project, "workspace_id": workspace, "name": data.name, "role": "owner"}

    @router.get("/projects/{project_id}")
    def detail(project_id: UUID, user=authenticated):
        with connect(url) as db:
            return membership(db, project_id, user["id"])

    @router.get("/projects/{project_id}/members")
    def members(project_id: UUID, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True)
            return db.execute(
                """SELECT u.id, u.email, m.role FROM mad_memberships m JOIN mad_users u ON u.id = m.user_id
                   WHERE m.project_id = %s ORDER BY u.email""", (project_id,)
            ).fetchall()

    @router.post("/projects/{project_id}/invites")
    def invite(project_id: UUID, data: InviteInput, user=authenticated):
        email = data.email.lower()
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True, manage=True)
            existing = db.execute("SELECT * FROM mad_invites WHERE id = %s", (data.id,)).fetchone()
            if existing:
                if (existing["project_id"], existing["creator"], existing["email"]) != (project_id, user["id"], email):
                    raise Problem("operation_conflict", 409)
                return invite_details(existing)
            if db.execute(
                """SELECT 1 FROM mad_memberships m JOIN mad_users u ON u.id = m.user_id
                   WHERE m.project_id = %s AND u.email = %s""", (project_id, email)
            ).fetchone():
                raise Problem("already_member", 409)
            created = db.execute(
                """INSERT INTO mad_invites (id, project_id, email, creator, expires_at) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING RETURNING *""",
                (data.id, project_id, email, user["id"], now() + timedelta(days=7)),
            ).fetchone()
            if not created:
                raise Problem("operation_conflict", 409)
            record(db, project_id, user["id"], data.id, "invite_created")
            return invite_details(created)

    @router.get("/projects/{project_id}/invites")
    def invites(project_id: UUID, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True, manage=True)
            rows = db.execute("SELECT * FROM mad_invites WHERE project_id = %s ORDER BY expires_at DESC", (project_id,)).fetchall()
            return [invite_details(row) for row in rows]

    @router.delete("/projects/{project_id}/invites/{invite_id}")
    def revoke_invite(project_id: UUID, invite_id: UUID, user=authenticated):
        with connect(url) as db:
            membership(db, project_id, user["id"], write=True, manage=True)
            revoked = db.execute(
                """UPDATE mad_invites SET revoked_at = now() WHERE id = %s AND project_id = %s
                   AND revoked_at IS NULL AND accepted_by IS NULL RETURNING id""", (invite_id, project_id)
            ).fetchone()
            if revoked:
                record(db, project_id, user["id"], invite_id, "invite_revoked")
        return {"outcome": "revoked"}

    @router.post("/invites/{invite_id}/accept")
    def accept(invite_id: UUID, user=authenticated):
        with connect(url) as db:
            project = db.execute("SELECT project_id FROM mad_invites WHERE id = %s AND email = %s",
                                 (invite_id, user["email"])).fetchone()
            if not project:
                raise Problem("invite_not_found", 404)
            project_id = project["project_id"]
            db.execute("SELECT id FROM mad_projects WHERE id = %s FOR UPDATE", (project_id,)).fetchone()
            row = db.execute("SELECT * FROM mad_invites WHERE id = %s", (invite_id,)).fetchone()
            if row["accepted_by"]:
                if row["accepted_by"] != user["id"]:
                    raise Problem("invite_not_found", 404)
                return membership(db, project_id, user["id"])
            if row["revoked_at"] or row["expires_at"] <= now():
                raise Problem("invite_expired_or_revoked", 410)
            # An outstanding invite requires the issuer to retain invite authority.
            membership(db, project_id, row["creator"], manage=True)
            db.execute("INSERT INTO mad_memberships VALUES (%s,%s,'member') ON CONFLICT DO NOTHING", (project_id, user["id"]))
            db.execute("UPDATE mad_invites SET accepted_by = %s WHERE id = %s", (user["id"], invite_id))
            record(db, project_id, user["id"], invite_id, "invite_accepted")
            return membership(db, project_id, user["id"])

    @router.patch("/projects/{project_id}/members/{member_id}")
    def change_role(project_id: UUID, member_id: UUID, data: MemberInput, user=authenticated):
        with connect(url) as db:
            actor = membership(db, project_id, user["id"], write=True)
            if actor["role"] != "owner":
                raise Problem("permission_denied", 403)
            target = membership(db, project_id, member_id)
            if target["role"] == "owner":
                raise Problem("owner_protected", 409)
            if target["role"] != data.role:
                db.execute("UPDATE mad_memberships SET role = %s WHERE project_id = %s AND user_id = %s",
                           (data.role, project_id, member_id))
                record(db, project_id, user["id"], member_id, f"role_{data.role}")
            return {"role": data.role}

    @router.delete("/projects/{project_id}/members/{member_id}")
    def remove(project_id: UUID, member_id: UUID, user=authenticated):
        with connect(url) as db:
            actor = membership(db, project_id, user["id"], write=True, manage=True)
            target = db.execute("SELECT role FROM mad_memberships WHERE project_id = %s AND user_id = %s",
                                (project_id, member_id)).fetchone()
            if target:
                if target["role"] == "owner" or (target["role"] == "admin" and actor["role"] != "owner"):
                    raise Problem("permission_denied", 403)
                db.execute("DELETE FROM mad_memberships WHERE project_id = %s AND user_id = %s", (project_id, member_id))
                db.execute("UPDATE mad_invites SET revoked_at = now() WHERE project_id = %s AND creator = %s AND accepted_by IS NULL",
                           (project_id, member_id))
                record(db, project_id, user["id"], member_id, "member_removed")
        return {"outcome": "removed"}

    def activity(project_id, user_id, after=0, limit=250):
        with connect(url) as db:
            membership(db, project_id, user_id, write=True, manage=True)
            return db.execute(
                """SELECT id, action, target, actor, occurred_at FROM mad_project_events
                   WHERE project_id = %s AND id > %s ORDER BY id LIMIT %s""", (project_id, after, limit)
            ).fetchall()

    @router.get("/projects/{project_id}/activity")
    def events(project_id: UUID, request: Request, user=authenticated):
        raw_cursor = request.headers.get("last-event-id", "0")
        try:
            cursor = int(raw_cursor)
            if not 0 <= cursor <= 2**63 - 1:
                raise ValueError
        except ValueError:
            raise Problem("invalid_request", 400) from None
        rows = activity(project_id, user["id"], cursor)
        body = "retry: 5000\n\n" + "".join(
            f"id: {row['id']}\ndata: {json.dumps(row, default=str)}\n\n" for row in rows
        )
        return StreamingResponse(iter([body]), media_type="text/event-stream")

    @router.get("/projects/{project_id}/activity.json")
    def download(project_id: UUID, user=authenticated):
        rows = activity(project_id, user["id"], limit=1000)
        return StreamingResponse(iter([json.dumps(rows, default=str)]), media_type="application/json",
                                 headers={"Content-Disposition": 'attachment; filename="project-activity.json"'})

    return router


def invite_details(row):
    if row["accepted_by"]:
        state = "accepted"
    elif row["revoked_at"]:
        state = "revoked"
    elif row["expires_at"] <= now():
        state = "expired"
    else:
        state = "pending"
    return {"id": row["id"], "email": row["email"], "expires_at": row["expires_at"], "state": state}
