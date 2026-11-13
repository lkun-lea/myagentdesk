from dataclasses import dataclass, field
from uuid import UUID

import httpx

from server.contracts import VerifiedIdentity
from server.errors import Problem


@dataclass(frozen=True)
class ProviderSession:
    identity: VerifiedIdentity
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int


class SupabaseAuth:
    def __init__(self, url: str, key: str, transport: httpx.BaseTransport | None = None):
        self.url = url
        self.key = key
        self.transport = transport

    def _request(self, method, path, *, body=None, access=None):
        headers = {"apikey": self.key}
        if access:
            headers["Authorization"] = f"Bearer {access}"
        try:
            with httpx.Client(timeout=8, transport=self.transport, trust_env=False) as client:
                response = client.request(method, f"{self.url}/auth/v1/{path}", headers=headers, json=body)
        except httpx.RequestError:
            raise Problem("auth_result_unknown", 503) from None
        if response.is_success:
            try:
                return response.json() if response.content else {}
            except ValueError:
                raise Problem("auth_result_unknown", 503) from None
        if response.status_code == 429:
            raise Problem("rate_limited", 429)
        if response.status_code >= 500:
            raise Problem("auth_result_unknown", 503)
        try:
            code = response.json().get("error_code", "")
        except (AttributeError, ValueError):
            code = ""
        if path == "verify" and (code == "otp_expired" or response.status_code in {400, 403, 422}):
            raise Problem("invalid_or_expired_code", 400)
        if path == "otp" and code == "email_provider_disabled":
            raise Problem("auth_unavailable", 503)
        if path == "otp" and code in {"otp_disabled", "user_not_found", "signup_disabled", "email_not_confirmed"}:
            # Keep the public send response identical for unprovisioned addresses.
            return {}
        if path != "otp" and (response.status_code in {400, 401, 403, 422}):
            raise Problem("login_required", 401)
        raise Problem("auth_unavailable", 503)

    def send_code(self, email: str):
        self._request("POST", "otp", body={"email": email, "create_user": False})

    def verify_identity(self, access_token: str) -> VerifiedIdentity:
        user = self._request("GET", "user", access=access_token)
        try:
            if not user["email_confirmed_at"]:
                raise ValueError
            return VerifiedIdentity(subject=UUID(user["id"]), email=user["email"].strip().lower())
        except (ValueError, KeyError, TypeError, AttributeError):
            raise Problem("login_required", 401) from None

    def _session(self, data) -> ProviderSession:
        try:
            access = data["access_token"]
            refresh = data["refresh_token"]
            expires = int(data["expires_in"])
            if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh or expires < 1:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise Problem("auth_result_unknown", 503) from None
        return ProviderSession(self.verify_identity(access), access, refresh, expires)

    def verify(self, email: str, code: str):
        data = self._request("POST", "verify", body={"email": email, "token": code, "type": "email"})
        session = self._session(data)
        if session.identity.email != email:
            raise Problem("login_required", 401)
        return session

    def refresh(self, refresh_token: str):
        return self._session(self._request("POST", "token?grant_type=refresh_token", body={"refresh_token": refresh_token}))

    def logout(self, access_token: str):
        self._request("POST", "logout?scope=local", access=access_token)
