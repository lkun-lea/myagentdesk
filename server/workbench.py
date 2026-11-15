import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from server.db import connect
from server.errors import Problem
from server.vendor.calendar_time import parse_event_time


TOOLS = json.loads(Path(__file__).with_name("workbench-tools.json").read_text(encoding="utf-8"))
TOOLS_BY_ID = {tool["id"]: tool for tool in TOOLS}


class FavoriteInput(BaseModel):
    saved: bool


class TitleInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def trim_title(cls, title):
        if not title.strip():
            raise ValueError("title required")
        return title.strip()


class NewTask(TitleInput):
    id: UUID


class TaskStatus(BaseModel):
    completed: bool


class EventInput(TitleInput):
    starts_at: str
    ends_at: str
    task_id: UUID | None = None


def time_range(start, end):
    try:
        first = parse_event_time(start, "开始")
        last = parse_event_time(end, "结束")
    except RuntimeError:
        raise Problem("invalid_calendar_range", 422) from None
    if not timedelta(0) < last - first <= timedelta(days=62):
        raise Problem("invalid_calendar_range", 422)
    return first, last


def has_project(db, user_id):
    return db.execute("SELECT 1 FROM mad_memberships WHERE user_id = %s LIMIT 1", (user_id,)).fetchone() is not None


def workbench_router(url, identity):
    router = APIRouter(prefix="/api")
    authenticated = Depends(identity)

    @router.get("/workbench")
    def catalog(user=authenticated):
        with connect(url) as db:
            eligible = has_project(db, user["id"])
            available = [tool for tool in TOOLS if tool["scope"] == "account" or eligible]
            ids = {tool["id"] for tool in available}
            favorites = db.execute("SELECT tool_id FROM mad_tool_favorites WHERE user_id = %s", (user["id"],))
            return {"tools": available, "favorites": [row["tool_id"] for row in favorites if row["tool_id"] in ids]}

    @router.put("/workbench/favorites/{tool_id}")
    def favorite(tool_id: str, data: FavoriteInput, user=authenticated):
        tool = TOOLS_BY_ID.get(tool_id)
        if not tool:
            raise Problem("tool_unavailable", 404)
        with connect(url) as db:
            if data.saved:
                if not tool["enabled"]:
                    raise Problem("tool_unavailable", 409)
                if tool["scope"] == "project" and not has_project(db, user["id"]):
                    raise Problem("project_not_found", 404)
                db.execute("INSERT INTO mad_tool_favorites VALUES (%s, %s) ON CONFLICT DO NOTHING",
                           (user["id"], tool_id))
            else:
                db.execute("DELETE FROM mad_tool_favorites WHERE user_id = %s AND tool_id = %s",
                           (user["id"], tool_id))
        return data

    @router.get("/tasks")
    def tasks(user=authenticated):
        with connect(url) as db:
            return db.execute("""SELECT id, title, completed FROM mad_personal_tasks WHERE user_id = %s
                                 ORDER BY completed, created_at, id""", (user["id"],)).fetchall()

    @router.post("/tasks", status_code=201)
    def create_task(data: NewTask, user=authenticated):
        with connect(url) as db:
            db.execute("""INSERT INTO mad_personal_tasks (user_id, id, title) VALUES (%s, %s, %s)
                          ON CONFLICT DO NOTHING""", (user["id"], data.id, data.title))
            row = db.execute("SELECT id, title, completed FROM mad_personal_tasks WHERE user_id = %s AND id = %s",
                             (user["id"], data.id)).fetchone()
            if row["title"] != data.title:
                raise Problem("operation_conflict", 409)
            return row

    @router.patch("/tasks/{task_id}")
    def complete_task(task_id: UUID, data: TaskStatus, user=authenticated):
        with connect(url) as db:
            row = db.execute("""UPDATE mad_personal_tasks SET completed = %s WHERE user_id = %s AND id = %s
                                RETURNING id, title, completed""", (data.completed, user["id"], task_id)).fetchone()
            if not row:
                raise Problem("task_not_found", 404)
            return row

    @router.get("/calendar")
    def events(start: str, end: str, user=authenticated):
        first, last = time_range(start, end)
        with connect(url) as db:
            return db.execute("""SELECT id, title, starts_at, ends_at, task_id FROM mad_calendar_events
                                 WHERE user_id = %s AND starts_at < %s AND ends_at > %s ORDER BY starts_at, id""",
                              (user["id"], last, first)).fetchall()

    @router.put("/calendar/{event_id}")
    def save_event(event_id: UUID, data: EventInput, user=authenticated):
        start, end = time_range(data.starts_at, data.ends_at)
        with connect(url) as db:
            if data.task_id is not None and not db.execute(
                "SELECT 1 FROM mad_personal_tasks WHERE user_id = %s AND id = %s", (user["id"], data.task_id)
            ).fetchone():
                raise Problem("task_not_found", 404)
            return db.execute("""INSERT INTO mad_calendar_events (user_id, id, title, starts_at, ends_at, task_id)
                                 VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id, id) DO UPDATE
                                 SET title = excluded.title, starts_at = excluded.starts_at,
                                     ends_at = excluded.ends_at, task_id = excluded.task_id
                                 RETURNING id, title, starts_at, ends_at, task_id""",
                              (user["id"], event_id, data.title, start, end, data.task_id)).fetchone()

    @router.delete("/calendar/{event_id}")
    def delete_event(event_id: UUID, user=authenticated):
        with connect(url) as db:
            db.execute("DELETE FROM mad_calendar_events WHERE user_id = %s AND id = %s", (user["id"], event_id))
        return {"outcome": "removed"}

    return router
