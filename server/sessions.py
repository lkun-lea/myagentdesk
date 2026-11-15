import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from cryptography.fernet import Fernet

from server.config import Settings
from server.db import connect
from server.errors import Problem


def now():
    return datetime.now(timezone.utc)


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


class Sessions:
    def __init__(self, settings: Settings, provider):
        self.url = settings.database_url
        self.provider = provider
        self.key = settings.session_key.encode()
        self.box = Fernet(self.key)

    def session_token(self, flow: str, operation: UUID) -> str:
        return hmac.new(self.key, f"session:{flow}:{operation}".encode(), "sha256").hexdigest()

    def store(self, db, token, provider_session):
        identity = provider_session.identity
        db.execute(
            "INSERT INTO mad_users VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email",
            (identity.subject, identity.email),
        )
        upstream = self.box.encrypt(json.dumps([provider_session.access_token, provider_session.refresh_token]).encode())
        db.execute(
            "INSERT INTO mad_sessions VALUES (%s, %s, %s, %s, %s)",
            (digest(token), identity.subject, upstream, now() + timedelta(seconds=provider_session.expires_in), now() + timedelta(days=7)),
        )

    def identity(self, token: str | None):
        if not token or len(token) != 64:
            raise Problem("login_required", 401)
        identifier = digest(token)
        with connect(self.url) as db:
            row = db.execute(
                """SELECT s.*, u.email FROM mad_sessions s JOIN mad_users u ON u.id = s.user_id
                   WHERE digest = %s AND expires_at > now() FOR UPDATE OF s""",
                (identifier,),
            ).fetchone()
            if not row:
                raise Problem("login_required", 401)
            if row["access_until"] > now() + timedelta(seconds=15):
                return {"id": row["user_id"], "email": row["email"]}
            _, refresh_token = json.loads(self.box.decrypt(row["upstream"]))
            try:
                updated = self.provider.refresh(refresh_token)
            except Problem as exc:
                if exc.status == 401:
                    db.execute("DELETE FROM mad_sessions WHERE digest = %s", (identifier,))
                    db.commit()
                raise
            if updated.identity.subject != row["user_id"]:
                db.execute("DELETE FROM mad_sessions WHERE digest = %s", (identifier,))
                db.commit()
                raise Problem("login_required", 401)
            upstream = self.box.encrypt(json.dumps([updated.access_token, updated.refresh_token]).encode())
            db.execute("UPDATE mad_sessions SET upstream = %s, access_until = %s WHERE digest = %s",
                       (upstream, now() + timedelta(seconds=updated.expires_in), identifier))
            db.execute("UPDATE mad_users SET email = %s WHERE id = %s", (updated.identity.email, row["user_id"]))
            return {"id": row["user_id"], "email": updated.identity.email}

    def logout(self, token: str | None, flow: str | None):
        with connect(self.url) as db:
            if flow:
                db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (digest(flow),))
            row = db.execute("DELETE FROM mad_sessions WHERE digest = %s RETURNING upstream, access_until",
                             (digest(token or ""),)).fetchone()
            if flow:
                db.execute(
                    """UPDATE mad_auth_operations SET outcome = 'login_required', session_digest = NULL
                       WHERE flow = %s AND (outcome = 'processing' OR session_digest IS NOT NULL)""", (digest(flow),)
                )
        if row:
            access, refresh = json.loads(self.box.decrypt(row["upstream"]))
            try:
                if row["access_until"] <= now():
                    access = self.provider.refresh(refresh).access_token
                self.provider.logout(access)
            except Problem:
                return "remote_unconfirmed"
        return "signed_out"

    def _existing(self, db, flow, operation):
        return db.execute("SELECT * FROM mad_auth_operations WHERE flow = %s AND id = %s",
                          (digest(flow), operation)).fetchone()

    def operation(self, flow, operation):
        with connect(self.url) as db:
            row = self._existing(db, flow, operation)
            if not row:
                return {"outcome": "not_found"}, None
            if row["outcome"] == "processing" and row["started_at"] < now() - timedelta(seconds=30):
                return {"outcome": "auth_result_unknown"}, None
            if row["outcome"] == "verified" and not row["session_digest"]:
                return {"outcome": "login_required"}, None
            token = self.session_token(flow, operation) if row["session_digest"] else None
            if token and not db.execute("SELECT 1 FROM mad_sessions WHERE digest = %s AND expires_at > now()", (digest(token),)).fetchone():
                return {"outcome": "login_required"}, None
            return {"outcome": row["outcome"]}, token

    def begin(self, flow, operation, kind, email, challenge=None):
        with connect(self.url) as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mad:otp:{email}",))
            existing = self._existing(db, flow, operation)
            if existing:
                if (existing["kind"], existing["email"], existing["challenge_id"]) != (kind, email, challenge):
                    raise Problem("operation_conflict", 409)
                return False
            if kind == "send":
                previous = db.execute(
                    """SELECT 1 FROM mad_auth_operations WHERE email = %s AND kind = 'send'
                       AND started_at > now() - interval '60 seconds'""", (email,)
                ).fetchone()
                if previous:
                    raise Problem("rate_limited", 429)
            else:
                sent = self._existing(db, flow, challenge)
                if not sent or sent["kind"] != "send" or sent["email"] != email:
                    raise Problem("challenge_missing", 400)
                if sent["outcome"] not in {"sent", "auth_result_unknown", "processing"}:
                    raise Problem("challenge_missing", 400)
                if sent["started_at"] < now() - timedelta(minutes=15):
                    raise Problem("invalid_or_expired_code", 400)
                if db.execute(
                    """SELECT 1 FROM mad_auth_operations WHERE flow = %s AND email = %s
                       AND kind = 'send' AND started_at > %s""", (digest(flow), email, sent["started_at"])
                ).fetchone():
                    raise Problem("invalid_or_expired_code", 400)
                attempts = db.execute(
                    """SELECT count(*) AS attempts FROM mad_auth_operations
                       WHERE kind = 'verify' AND email = %s AND started_at > now() - interval '15 minutes'""",
                    (email,),
                ).fetchone()["attempts"]
                if attempts >= 8:
                    raise Problem("rate_limited", 429)
            db.execute(
                "INSERT INTO mad_auth_operations (flow, id, kind, email, challenge_id, outcome) VALUES (%s,%s,%s,%s,%s,'processing')",
                (digest(flow), operation, kind, email, challenge),
            )
        return True

    def finish(self, flow, operation, outcome, provider_session=None):
        with connect(self.url) as db:
            scope = digest(flow)
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (scope,))
            row = db.execute("SELECT outcome FROM mad_auth_operations WHERE flow = %s AND id = %s FOR UPDATE",
                             (scope, operation)).fetchone()
            if not row or row["outcome"] != "processing":
                raise Problem("login_required", 401)
            token = None
            if provider_session and db.execute(
                "SELECT 1 FROM mad_auth_operations WHERE flow = %s AND session_digest IS NOT NULL", (scope,)
            ).fetchone():
                outcome, provider_session = "sign_out_first", None
            if provider_session:
                token = self.session_token(flow, operation)
                self.store(db, token, provider_session)
            db.execute(
                """UPDATE mad_auth_operations SET outcome = %s, finished_at = now(), session_digest = %s
                   WHERE flow = %s AND id = %s""",
                (outcome, digest(token) if token else None, scope, operation),
            )
        return {"outcome": outcome}, token


def new_flow():
    return secrets.token_hex(32)
