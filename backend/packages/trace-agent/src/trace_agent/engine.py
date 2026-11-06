"""The implementer-owned engine boundary. There is deliberately no fake engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import TraceCreateRequest, TraceCreateResponse, TraceHealth, TraceOperation, TraceTaskResponse


@dataclass(frozen=True)
class TraceReply[T]:
    """One engine snapshot: a body and its durable, atomically read revision."""

    body: T
    revision: int
    created: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("Trace revision must be a positive integer")


class TraceEngine(Protocol):
    """A real implementation owns persistence, admission, workers and fencing.

    ``create`` compares canonical input per task id before dispatching. Operations
    authenticate ownership before resolving durable operation-id deduplication and
    expected revisions. An unavailable implementation must raise, never invent
    task ids, pending states, reports or successful health.
    """

    async def create(self, request: TraceCreateRequest) -> TraceReply[TraceCreateResponse]: ...

    async def get(self, task_id: str) -> TraceReply[TraceTaskResponse]: ...

    async def resume(self, task_id: str, operation: TraceOperation) -> TraceReply[TraceCreateResponse]: ...

    async def cancel(self, task_id: str, operation: TraceOperation) -> TraceReply[TraceCreateResponse]: ...

    async def health(self) -> TraceHealth: ...


class TraceServiceError(Exception):
    """Stable public errors; raw implementation exception messages stay private."""

    status_code = 503
    code = "TRACE_ENGINE_UNAVAILABLE"
    public_message = "Trace investigation engine is not installed or ready."


class TraceConflict(TraceServiceError):
    status_code = 409
    code = "TRACE_CONFLICT"
    public_message = "The task input, operation or expected revision conflicts with current state."


class TraceNotFound(TraceServiceError):
    status_code = 404
    code = "TRACE_NOT_FOUND"
    public_message = "Trace task does not exist."


class UnavailableEngine:
    """The development shell's default: no queues, database, models or state."""

    async def create(self, request: TraceCreateRequest) -> TraceReply[TraceCreateResponse]:
        raise TraceServiceError()

    async def get(self, task_id: str) -> TraceReply[TraceTaskResponse]:
        raise TraceServiceError()

    async def resume(self, task_id: str, operation: TraceOperation) -> TraceReply[TraceCreateResponse]:
        raise TraceServiceError()

    async def cancel(self, task_id: str, operation: TraceOperation) -> TraceReply[TraceCreateResponse]:
        raise TraceServiceError()

    async def health(self) -> TraceHealth:
        return TraceHealth(status="degraded", detail="not_ready", worker={"status": "not_configured"})
