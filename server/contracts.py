from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from server.vendor.calendar_time import parse_event_time


class WorkspaceProject(BaseModel):
    workspace_id: UUID
    project_id: UUID


class VerifiedIdentity(BaseModel):
    subject: UUID
    email: str


class IdentityVerifier(Protocol):
    def verify_identity(self, access_token: str) -> VerifiedIdentity: ...


class RunEvent(WorkspaceProject):
    version: Literal[1] = 1
    run_id: UUID
    sequence: int = Field(ge=1)
    kind: Literal["queued", "started", "completed", "failed"]
    occurred_at: datetime

    @field_validator("occurred_at", mode="before")
    @classmethod
    def require_absolute_time(cls, value):
        try:
            return parse_event_time(value, "事件时间")
        except RuntimeError as exc:
            raise ValueError("事件时间必须包含时区") from exc


class RunEventSource(Protocol):
    def after(self, target: WorkspaceProject, run_id: UUID, sequence: int) -> list[RunEvent]: ...
