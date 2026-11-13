from mimetypes import add_type
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg

from server.config import Settings
from server.db import ready
from server.accounts import enable_accounts
from server.errors import Problem


def create_app(settings: Settings | None = None, *, static_dir: Path | None = None, auth_transport=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title="MyAgentDesk", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            request.headers.get("origin") != settings.origin or request.headers.get("x-mad-request") != "1"
        ):
            response = JSONResponse({"error": "origin_denied"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(Problem)
    async def problem(_request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(psycopg.Error)
    async def database_error(_request, _exc):
        return JSONResponse({"error": "database_unavailable"}, status_code=503)

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

    enable_accounts(app, settings, auth_transport)
    assets = static_dir or Path(__file__).resolve().parent.parent / "dist"
    if assets.is_dir():
        add_type("text/javascript", ".js")
        add_type("text/css", ".css")
        app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app
