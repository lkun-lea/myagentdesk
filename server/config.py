from dataclasses import dataclass, field
from os import environ


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default="", repr=False)
    origin: str = "http://127.0.0.1:4178"
    supabase_url: str = ""
    publishable_key: str = field(default="", repr=False)
    session_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        return cls(
            database_url=environ.get("MAD_DATABASE_URL", ""),
            origin=environ.get("MAD_ORIGIN", cls.origin).rstrip("/"),
            supabase_url=environ.get("MAD_SUPABASE_URL", "").rstrip("/"),
            publishable_key=environ.get("MAD_SUPABASE_PUBLISHABLE_KEY", ""),
            session_key=environ.get("MAD_SESSION_KEY", ""),
        )
