"""Small dependency-free contracts shared across SOC business domains."""

from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


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


class ActorContext(BaseModel):
    actor_id: str = "anonymous"
    actor_type: ActorType = ActorType.USER
    surface: EntrySurface = EntrySurface.CLI
    roles: list[str] = Field(default_factory=list)


class ServiceRequestContext(BaseModel):
    request_id: str = Field(default_factory=lambda: f"REQ-{uuid4().hex[:12].upper()}")
    actor: ActorContext = Field(default_factory=ActorContext)
    trace_id: str | None = None
    idempotency_key: str | None = None


__all__ = ["ActorContext", "ActorType", "EntrySurface", "ServiceRequestContext"]
