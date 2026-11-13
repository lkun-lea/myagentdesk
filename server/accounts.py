from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field

from server.auth_provider import SupabaseAuth
from server.errors import Problem
from server.projects import projects_router
from server.sessions import Sessions, new_flow


class SendCode(BaseModel):
    operation_id: UUID
    email: EmailStr


class VerifyCode(SendCode):
    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


def enable_accounts(app, settings, transport):
    origin = urlsplit(settings.origin)
    is_loopback = origin.scheme == "http" and origin.hostname in {"127.0.0.1", "localhost", "::1"}
    origin_valid = (origin.scheme == "https" or is_loopback) and origin.netloc and not (
        origin.path or origin.query or origin.fragment or origin.username or origin.password
    )
    secure = origin.scheme == "https"
    session_cookie = "__Host-mad_session" if secure else "mad_session"
    flow_cookie = "__Host-mad_flow" if secure else "mad_flow"
    auth = APIRouter(prefix="/api/auth")
    configuration = {
        "MAD_DATABASE_URL": settings.database_url,
        "MAD_SUPABASE_URL": settings.supabase_url,
        "MAD_SUPABASE_PUBLISHABLE_KEY": settings.publishable_key,
        "MAD_SESSION_KEY": settings.session_key,
    }
    missing = [name for name, value in configuration.items() if not value]
    upstream = urlsplit(settings.supabase_url)
    invalid = []
    if not origin_valid:
        invalid.append("MAD_ORIGIN")
    if settings.supabase_url and (upstream.scheme != "https" or not upstream.hostname or
                                  upstream.path or upstream.query or upstream.fragment or upstream.username or upstream.password):
        invalid.append("MAD_SUPABASE_URL")
    if settings.session_key:
        try:
            Fernet(settings.session_key.encode())
        except (ValueError, TypeError):
            invalid.append("MAD_SESSION_KEY")
    sessions = None
    if not missing and not invalid:
        sessions = Sessions(settings, SupabaseAuth(settings.supabase_url, settings.publishable_key, transport))

    def require_accounts():
        if not sessions:
            raise Problem("auth_unavailable", 503)
        return sessions

    def cookie(response, name, value, age):
        response.set_cookie(name, value, max_age=age, secure=secure, httponly=True, samesite="lax", path="/")

    def flow(request):
        value = request.cookies.get(flow_cookie)
        if not value or len(value) != 64:
            raise Problem("challenge_missing", 400)
        return value

    def identity(request: Request):
        return require_accounts().identity(request.cookies.get(session_cookie))

    @auth.get("/configuration")
    def configuration_status():
        return {"available": sessions is not None, "missing": missing, "invalid": invalid}

    @auth.post("/flow")
    def start(request: Request, response: Response):
        require_accounts()
        if not request.cookies.get(flow_cookie) or len(request.cookies[flow_cookie]) != 64:
            cookie(response, flow_cookie, new_flow(), 60 * 60)
        return {"outcome": "ready"}

    def result(response, value):
        payload, token = value
        if token:
            cookie(response, session_cookie, token, 7 * 24 * 60 * 60)
        return payload

    @auth.post("/code")
    def send(data: SendCode, request: Request, response: Response):
        service = require_accounts()
        browser = flow(request)
        email = data.email.lower()
        if service.begin(browser, data.operation_id, "send", email):
            try:
                service.provider.send_code(email)
            except Problem as exc:
                service.finish(browser, data.operation_id, exc.code)
            else:
                service.finish(browser, data.operation_id, "sent")
        return result(response, service.operation(browser, data.operation_id))

    @auth.post("/verify")
    def verify(data: VerifyCode, request: Request, response: Response):
        service = require_accounts()
        if request.cookies.get(session_cookie):
            # Prevent replacing a live identity without explicitly signing out.
            try:
                identity(request)
            except Problem as exc:
                if exc.code != "login_required":
                    raise
            else:
                browser = flow(request)
                operation, token = service.operation(browser, data.operation_id)
                if token and token == request.cookies[session_cookie]:
                    return operation
                raise Problem("sign_out_first", 409)
        browser = flow(request)
        email = data.email.lower()
        if service.begin(browser, data.operation_id, "verify", email, data.challenge_id):
            try:
                upstream_session = service.provider.verify(email, data.code)
            except Problem as exc:
                service.finish(browser, data.operation_id, exc.code)
            else:
                service.finish(browser, data.operation_id, "verified", upstream_session)
        return result(response, service.operation(browser, data.operation_id))

    @auth.get("/operations/{operation_id}")
    def operation(operation_id: UUID, request: Request, response: Response):
        value = require_accounts().operation(flow(request), operation_id)
        current = request.cookies.get(session_cookie)
        if current and value[1] and current != value[1]:
            raise Problem("sign_out_first", 409)
        return result(response, value)

    @auth.get("/session")
    def session(request: Request, response: Response):
        try:
            return {"user": identity(request)}
        except Problem as exc:
            if exc.code != "login_required":
                raise
            response.delete_cookie(session_cookie, path="/", secure=secure, httponly=True, samesite="lax")
            return {"user": None}

    @auth.post("/logout")
    def logout(request: Request, response: Response):
        status = require_accounts().logout(request.cookies.get(session_cookie), request.cookies.get(flow_cookie))
        response.delete_cookie(session_cookie, path="/", secure=secure, httponly=True, samesite="lax")
        response.delete_cookie(flow_cookie, path="/", secure=secure, httponly=True, samesite="lax")
        return {"outcome": status}

    app.include_router(auth)
    app.include_router(projects_router(settings.database_url, identity))
