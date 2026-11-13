from mimetypes import add_type
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg

from server.config import Settings
from server.db import ready


def create_app(settings: Settings | None = None, *, static_dir: Path | None = None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="MyAgentDesk", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    def readiness():
        if not settings.database_url:
            return JSONResponse({"status": "unavailable", "missing": ["MAD_DATABASE_URL"]}, status_code=503)
        try:
            if ready(settings.database_url):
                return {"status": "ok"}
        except psycopg.Error:
            return JSONResponse({"status": "unavailable", "reason": "database_unreachable"}, status_code=503)
        return JSONResponse({"status": "unavailable", "reason": "migration_required"}, status_code=503)

    assets = static_dir or Path(__file__).resolve().parent.parent / "dist"
    if assets.is_dir():
        add_type("text/javascript", ".js")
        add_type("text/css", ".css")
        app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app
