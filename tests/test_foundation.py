from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from server.config import Settings
from server.contracts import RunEvent, VerifiedIdentity, WorkspaceProject
from server.db import connect, migrate
from server.main import create_app
from server.vendor.calendar_time import day_range


def test_clean_install_home_health_and_migrations(isolated_database, tmp_path):
    (tmp_path / "index.html").write_text("<h1>MyAgentDesk</h1>", encoding="utf-8")
    (tmp_path / "index.js").write_text("export {}", encoding="utf-8")
    client = TestClient(create_app(Settings(database_url=isolated_database), static_dir=tmp_path))
    assert client.get("/").text == "<h1>MyAgentDesk</h1>"
    assert client.get("/index.js").headers["content-type"].split(";")[0] == "text/javascript"
    assert client.get("/api/health/live").json() == {"status": "ok"}
    assert client.get("/api/health/ready").json()["reason"] == "migration_required"
    migrate(isolated_database)
    migrate(isolated_database)
    assert client.get("/api/health/ready").json() == {"status": "ok"}
    target = WorkspaceProject(workspace_id=uuid4(), project_id=uuid4())
    with connect(isolated_database) as db:
        db.execute("INSERT INTO mad_workspaces VALUES (%s, %s)", (target.workspace_id, "Test"))
        db.execute("INSERT INTO mad_projects VALUES (%s, %s, %s)", (target.project_id, target.workspace_id, "Project"))
        assert db.execute("SELECT id FROM mad_projects").fetchone()["id"] == target.project_id


def test_missing_or_wrong_config_never_falls_back_to_tebb(tmp_path, monkeypatch):
    for key in ("MAD_DATABASE_URL", "MAD_SUPABASE_URL", "MAD_SUPABASE_PUBLISHABLE_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DESK_COMPANION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://other-project.invalid")
    settings = Settings.from_env()
    assert not settings.database_url and not settings.supabase_url
    assert TestClient(create_app(settings)).get("/api/health/ready").json()["missing"] == ["MAD_DATABASE_URL"]
    bad = TestClient(create_app(Settings(database_url="postgresql://127.0.0.1:1/absent")))
    assert bad.get("/api/health/ready").json()["reason"] == "database_unreachable"
    assert not list(tmp_path.iterdir())


def test_upstream_time_example_and_fake_adapters():
    start, end = day_range("2026-11-18")
    assert start.utcoffset() == timedelta(hours=8) and end - start == timedelta(days=1)
    target = WorkspaceProject(workspace_id=uuid4(), project_id=uuid4())
    run = uuid4()
    event = dict(**target.model_dump(), run_id=run, sequence=1, kind="started")
    received = RunEvent(**event, occurred_at="2026-11-18T15:00:00+08:00")
    assert RunEvent.model_validate_json(received.model_dump_json()) == received
    with pytest.raises(ValidationError):
        RunEvent(**event, occurred_at="2026-11-18T15:00:00")

    class FakeIdentity:
        def verify_identity(self, access_token):
            assert access_token == "fake"
            return VerifiedIdentity(subject=uuid4(), email="test@example.com")

    class FakeConnector:
        def after(self, scope, run_id, sequence):
            return [received] if (scope, run_id, sequence) == (target, run, 0) else []

    assert FakeIdentity().verify_identity("fake").email == "test@example.com"
    assert FakeConnector().after(target, run, 0) == [received]
    assert not FakeConnector().after(target, run, 1)
