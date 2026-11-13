import json
import logging
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest

from server.config import Settings
from server.db import connect
from server.main import create_app

ORIGIN = "https://workbench.test"
HEADERS = {"Origin": ORIGIN, "X-MAD-Request": "1"}
OWNER = "owner@example.com"
MEMBER = "member@example.com"
CODE = "731408"


class FakeMailAuth:
    def __init__(self):
        self.users = {email: str(uuid4()) for email in (OWNER, MEMBER)}
        self.mail = []
        self.active = {}
        self.send_lost = False
        self.verify_lost = False
        self.expired = False
        self.reject_refresh = False
        self.refreshes = 0
        self.logged_out = []

    def reply(self, request):
        path = request.url.path
        data = json.loads(request.content or "{}")
        assert request.url.host == "auth.test"
        assert request.headers["apikey"] == "publishable-test"
        if path.endswith("/otp"):
            assert data["create_user"] is False
            if data["email"] not in self.users:
                return httpx.Response(422, json={"error_code": "otp_disabled"})
            self.mail.append(data["email"])
            if self.send_lost:
                raise httpx.ReadTimeout("provider detail containing " + CODE, request=request)
            return httpx.Response(200, json={})
        if path.endswith("/verify"):
            assert data["type"] == "email"
            if self.expired or data["token"] != CODE or data["email"] not in self.mail:
                return httpx.Response(403, json={"error_code": "otp_expired", "msg": data["token"]})
            if self.verify_lost:
                raise httpx.ReadTimeout("OTP " + CODE, request=request)
            return self.session(data["email"])
        if path.endswith("/token"):
            assert request.url.params["grant_type"] == "refresh_token"
            self.refreshes += 1
            if self.reject_refresh:
                return httpx.Response(400, json={"error_code": "refresh_token_not_found"})
            return self.session(self.active[data["refresh_token"]])
        token = request.headers["Authorization"].removeprefix("Bearer ")
        if path.endswith("/user"):
            email = self.active[token]
            return httpx.Response(200, json={"id": self.users[email], "email": email, "email_confirmed_at": "2026-11-13T08:00:00Z"})
        assert path.endswith("/logout") and request.url.params["scope"] == "local"
        self.logged_out.append(token)
        return httpx.Response(204)

    def session(self, email):
        access = "upstream-access-" + uuid4().hex
        refresh = "upstream-refresh-" + uuid4().hex
        self.active.update({access: email, refresh: email})
        return httpx.Response(200, json={"access_token": access, "refresh_token": refresh, "expires_in": 3600})


@pytest.fixture
def setup(database):
    fake = FakeMailAuth()
    settings = Settings(database, ORIGIN, "https://auth.test", "publishable-test", Fernet.generate_key().decode())
    app = create_app(settings, auth_transport=httpx.MockTransport(fake.reply))
    return fake, lambda: TestClient(app, base_url=ORIGIN, headers=HEADERS)


def send(client, email, operation=None):
    assert client.post("/api/auth/flow").status_code == 200
    operation = operation or str(uuid4())
    response = client.post("/api/auth/code", json={"operation_id": operation, "email": email})
    return operation, response


def verify(client, email, challenge, code=CODE, operation=None):
    operation = operation or str(uuid4())
    response = client.post("/api/auth/verify", json={
        "operation_id": operation, "challenge_id": challenge, "email": email, "code": code,
    })
    return operation, response


def login(client, email, database=None):
    if database:
        with connect(database) as db:
            db.execute("UPDATE mad_auth_operations SET started_at = now() - interval '2 minutes' WHERE kind = 'send' AND email = %s", (email,))
    challenge, sent = send(client, email)
    assert sent.json()["outcome"] == "sent"
    _, response = verify(client, email, challenge)
    assert response.json() == {"outcome": "verified"}
    assert "HttpOnly" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
    assert "upstream-" not in response.text
    assert client.get("/api/auth/session").json()["user"]["email"] == email
    return response


def test_otp_failures_receipts_and_login_expiry(setup, database, caplog):
    fake, browser = setup
    client = browser()
    caplog.set_level(logging.INFO)
    fake.send_lost = True
    challenge, response = send(client, OWNER)
    assert response.json()["outcome"] == "auth_result_unknown"
    assert client.get("/api/auth/operations/" + challenge).json()["outcome"] == "auth_result_unknown"
    assert send(client, OWNER, challenge)[1].json()["outcome"] == "auth_result_unknown"
    assert fake.mail == [OWNER]
    assert browser().get("/api/auth/operations/" + challenge).status_code == 400
    assert verify(client, OWNER, challenge, "000000")[1].json()["outcome"] == "invalid_or_expired_code"
    fake.expired = True
    assert verify(client, OWNER, challenge)[1].json()["outcome"] == "invalid_or_expired_code"
    fake.expired = False
    fake.verify_lost = True
    operation, response = verify(client, OWNER, challenge)
    assert response.json()["outcome"] == "auth_result_unknown"
    assert verify(client, OWNER, challenge, operation=operation)[1].json()["outcome"] == "auth_result_unknown"
    assert client.get("/api/auth/session").json()["user"] is None
    fake.verify_lost = False
    operation, response = verify(client, OWNER, challenge)
    assert response.json()["outcome"] == "verified"
    session_cookie = client.cookies["__Host-mad_session"]
    # Simulate a lost verification response by removing only the session cookie.
    client.cookies.delete("__Host-mad_session")
    assert client.get("/api/auth/operations/" + operation).json()["outcome"] == "verified"
    assert client.cookies["__Host-mad_session"] == session_cookie
    with connect(database) as db:
        row = db.execute("SELECT digest, upstream FROM mad_sessions").fetchone()
        assert row["digest"] != session_cookie and b"upstream-" not in row["upstream"]
        db.execute("UPDATE mad_sessions SET access_until = now() - interval '1 second'")
    assert client.get("/api/auth/session").json()["user"]["email"] == OWNER
    assert fake.refreshes == 1
    with connect(database) as db:
        db.execute("UPDATE mad_sessions SET access_until = now() - interval '1 second'")
    fake.reject_refresh = True
    assert client.get("/api/auth/session").json()["user"] is None
    assert client.get("/api/auth/operations/" + operation).json()["outcome"] == "login_required"
    assert CODE not in caplog.text and "upstream-access-" not in caplog.text


def test_two_accounts_invite_permissions_and_revocation(setup, database):
    fake, browser = setup
    owner, member, second_owner = browser(), browser(), browser()
    login(owner, OWNER)
    login(member, MEMBER)
    login(second_owner, OWNER, database)
    created = owner.post("/api/projects", json={"name": "隔离项目"}).json()
    same_workspace = owner.post("/api/projects", json={"name": "同工作区项目"}).json()
    assert same_workspace["workspace_id"] == created["workspace_id"]
    path = "/api/projects/" + created["id"]
    assert second_owner.get(path).json()["name"] == "隔离项目"
    assert member.get("/api/projects").json() == []
    assert member.get(path).status_code == 404
    assert member.post("/api/projects", json={"name": "越权", "workspace_id": created["workspace_id"]}).status_code == 404
    assert owner.post("/api/projects", json={"name": "失败"}, headers={"Origin": "https://other.test"}).status_code == 403

    expired = {"id": str(uuid4()), "email": MEMBER}
    assert owner.post(path + "/invites", json=expired).status_code == 200
    with connect(database) as db:
        db.execute("UPDATE mad_invites SET expires_at = now() WHERE id = %s", (expired["id"],))
    assert member.post("/api/invites/" + expired["id"] + "/accept").status_code == 410
    invitation = {"id": str(uuid4()), "email": MEMBER}
    assert owner.post(path + "/invites", json=invitation).json() == owner.post(path + "/invites", json=invitation).json()
    accept = "/api/invites/" + invitation["id"] + "/accept"
    assert owner.post(accept).status_code == 404
    assert member.post(accept).json()["role"] == "member"
    assert member.post(accept).status_code == 200
    assert member.get(path).headers["Cache-Control"] == "no-store"
    assert member.post(path + "/invites", json=expired).status_code == 403
    assert member.get(path + "/activity.json").status_code == 403
    member_path = path + "/members/" + fake.users[MEMBER]
    assert owner.patch(member_path, json={"role": "admin"}).json() == {"role": "admin"}
    assert "invite_accepted" in member.get(path + "/activity").text
    assert member.get(path + "/activity.json").status_code == 200
    assert owner.delete(member_path).status_code == 200
    assert member.get(path).status_code == 404
    assert member.get(path + "/members").status_code == 404
    assert member.get(path + "/activity").status_code == 404
    assert member.get(path + "/activity.json").status_code == 404
    assert member.post(accept).status_code == 404
    assert member.post("/api/auth/logout").json() == {"outcome": "signed_out"}
    assert owner.post("/api/auth/logout").json() == {"outcome": "signed_out"}
    assert not owner.cookies.get("__Host-mad_session")
    assert owner.get(path).status_code == 401
    assert second_owner.get(path).status_code == 200
    assert len(fake.logged_out) == 2


def test_invalid_config_and_validation_never_echo_secrets(setup):
    fake, browser = setup
    client = browser()
    invalid = client.post("/api/auth/verify", json={"code": "secret-that-must-not-be-echoed"})
    assert invalid.status_code == 422 and invalid.json() == {"error": "invalid_request"}
    disabled = TestClient(create_app(Settings(session_key="malformed")), base_url=ORIGIN)
    result = disabled.get("/api/auth/configuration").json()
    assert not result["available"] and "MAD_SESSION_KEY" in result["invalid"]
    assert "malformed" not in str(result)
    assert send(client, "unprovisioned@example.com")[1].json() == {"outcome": "sent"}
    assert not fake.mail
