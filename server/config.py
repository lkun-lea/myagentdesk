from dataclasses import dataclass, field
from os import environ


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default="", repr=False)
    origin: str = "http://127.0.0.1:4178"
    supabase_url: str = ""
    publishable_key: str = field(default="", repr=False)
    session_key: str = field(default="", repr=False)
    model_env_file: str = ""
    node_binary: str = "node"
    resource_root: str = ""
    resume_worker: bool = False
    github_read_token: str = field(default='', repr=False)
    pilot_release_root: str = ''

    @classmethod
    def from_env(cls):
        return cls(
            database_url=environ.get("MAD_DATABASE_URL", ""),
            origin=environ.get("MAD_ORIGIN", cls.origin).rstrip("/"),
            supabase_url=environ.get("MAD_SUPABASE_URL", "").rstrip("/"),
            publishable_key=environ.get("MAD_SUPABASE_PUBLISHABLE_KEY", ""),
            session_key=environ.get("MAD_SESSION_KEY", ""),
            model_env_file=environ.get("MAD_MODEL_ENV_FILE", ""),
            node_binary=environ.get("MAD_NODE_BINARY", "node"),
            resource_root=environ.get("MAD_RESOURCE_ROOT", ""),
            resume_worker=environ.get("MAD_RESUME_WORKER", "1") == "1",
            github_read_token=environ.get('MAD_GITHUB_READ_TOKEN', ''),
            pilot_release_root=environ.get('MAD_PILOT_RELEASE_ROOT', ''),
        )
