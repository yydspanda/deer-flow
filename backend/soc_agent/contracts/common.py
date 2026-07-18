"""Small dependency-free contracts shared across SOC business domains."""

from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    SERVICE = "service"


class EntrySurface(StrEnum):
    CLI = "cli"
    API = "api"
    CHANNEL = "channel"
    DAEMON = "daemon"
    TUI = "tui"
    WEB = "web"
    TEST = "test"


class ActorAuthSource(StrEnum):
    """Credential or trust boundary that established an actor identity."""

    UNKNOWN = "unknown"
    SESSION = "session"
    INTERNAL = "internal"
    AUTH_DISABLED = "auth_disabled"
    LOCAL_CLI = "local_cli"
    LOCAL_TUI = "local_tui"
    DAEMON = "daemon"
    SYSTEM = "system"
    EXTERNAL_ADAPTER = "external_adapter"
    TEST = "test"


class ActorContext(BaseModel):
    actor_id: str = "anonymous"
    actor_type: ActorType = ActorType.USER
    surface: EntrySurface = EntrySurface.CLI
    roles: list[str] = Field(default_factory=list)
    auth_source: ActorAuthSource = ActorAuthSource.UNKNOWN

    @model_validator(mode="after")
    def infer_local_auth_source(self) -> ActorContext:
        if self.auth_source is not ActorAuthSource.UNKNOWN:
            return self
        inferred = {
            EntrySurface.CLI: ActorAuthSource.LOCAL_CLI,
            EntrySurface.TUI: ActorAuthSource.LOCAL_TUI,
            EntrySurface.DAEMON: ActorAuthSource.DAEMON,
            EntrySurface.TEST: ActorAuthSource.TEST,
        }.get(self.surface)
        if inferred is not None:
            self.auth_source = inferred
        elif self.actor_type is ActorType.SYSTEM:
            self.auth_source = ActorAuthSource.SYSTEM
        return self


class ServiceRequestContext(BaseModel):
    request_id: str = Field(default_factory=lambda: f"REQ-{uuid4().hex[:12].upper()}")
    actor: ActorContext = Field(default_factory=ActorContext)
    trace_id: str | None = None
    idempotency_key: str | None = None


__all__ = ["ActorAuthSource", "ActorContext", "ActorType", "EntrySurface", "ServiceRequestContext"]
