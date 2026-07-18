"""Shared helpers for atomic SOC mutations and their durable audit records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import (
    ServiceRequestContext,
    SocEvent,
    SocMutationAuditRecord,
    SocMutationOperation,
)
from soc_agent.protocols import SocEventSink, SocMutationAuditRepository, SocMutationUnitOfWork
from soc_agent.utils.hashing import stable_hash

from .errors import SocServiceConflictError

_SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|credential|api[_-]?key|pwd)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|cookie|password|passwd|secret|token|credential|api[_-]?key|pwd)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_MAX_REASON_CHARS = 2000
_MAX_STRING_CHARS = 512
_MAX_ITEMS = 20
_MAX_DEPTH = 3


class BufferedSocEventSink:
    """Hold process-local events until the enclosing database transaction commits."""

    def __init__(self, downstream: SocEventSink) -> None:
        self._downstream = downstream
        self._events: list[SocEvent] = []

    def emit(self, event: SocEvent) -> None:
        self._events.append(event)

    def flush(self) -> None:
        for event in self._events:
            self._downstream.emit(event)
        self._events.clear()


def mutation_uow_from(*dependencies: object | None) -> SocMutationUnitOfWork | None:
    """Return the first repository exposing the explicit mutation UoW contract."""

    for dependency in dependencies:
        if dependency is not None and callable(getattr(dependency, "mutation_transaction", None)):
            return dependency  # type: ignore[return-value]
    return None


def mutation_audit_repository_from(*dependencies: object | None) -> SocMutationAuditRepository | None:
    """Return the first repository exposing durable mutation-audit persistence."""

    for dependency in dependencies:
        if dependency is not None and callable(getattr(dependency, "append_mutation_audit", None)):
            return dependency  # type: ignore[return-value]
    return None


def mutation_idempotency_key(context: ServiceRequestContext) -> str:
    """Use the caller key when supplied and the unique request id otherwise."""

    if context.idempotency_key and context.idempotency_key.strip():
        return context.idempotency_key.strip()
    return context.request_id


def build_mutation_audit(
    *,
    operation: SocMutationOperation,
    target_type: str,
    target_id: str,
    context: ServiceRequestContext,
    reason: str,
    command: object,
    result_status: str = "succeeded",
    result_ref: str | None = None,
    run_id: str | None = None,
    alert_id: str | None = None,
    queue_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> SocMutationAuditRecord:
    """Build one secret-safe append-only mutation record."""

    return SocMutationAuditRecord(
        operation=operation,
        target_type=target_type,
        target_id=target_id,
        run_id=run_id,
        alert_id=alert_id,
        queue_id=queue_id,
        actor=context.actor,
        request_id=context.request_id,
        idempotency_key=mutation_idempotency_key(context),
        command_hash=stable_hash(command),
        reason=sanitize_audit_reason(reason),
        result_status=result_status,
        result_ref=result_ref,
        payload=sanitize_audit_payload(payload or {}),
    )


def validate_mutation_retry(
    existing: SocMutationAuditRecord,
    *,
    command: object,
    target_type: str,
    target_id: str,
) -> None:
    """Accept an exact retry and reject reuse of an idempotency key."""

    if existing.command_hash == stable_hash(command) and existing.target_type == target_type and existing.target_id == target_id:
        return
    raise SocServiceConflictError(f"mutation idempotency key {existing.idempotency_key} was already used for different content")


def sanitize_audit_reason(value: str) -> str:
    """Redact common inline credentials and bound analyst-provided text."""

    text = _BEARER_RE.sub("Bearer [REDACTED]", value.strip())
    text = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    if not text:
        text = "reason not provided"
    return text[:_MAX_REASON_CHARS]


def sanitize_audit_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Bound and redact an explicitly allowlisted audit projection."""

    sanitized = _sanitize_value(value, depth=0)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_value(value: Any, *, depth: int) -> Any:
    if depth >= _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["_truncated"] = True
                break
            normalized_key = str(key)[:128]
            if _SENSITIVE_KEY_RE.search(normalized_key):
                result[normalized_key] = "[REDACTED]"
            else:
                result[normalized_key] = _sanitize_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        sanitized = [_sanitize_value(item, depth=depth + 1) for item in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            sanitized.append("[TRUNCATED]")
        return sanitized
    if isinstance(value, str):
        return sanitize_audit_reason(value)[:_MAX_STRING_CHARS]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_audit_reason(str(value))[:_MAX_STRING_CHARS]


__all__ = [
    "BufferedSocEventSink",
    "build_mutation_audit",
    "mutation_audit_repository_from",
    "mutation_idempotency_key",
    "mutation_uow_from",
    "sanitize_audit_payload",
    "sanitize_audit_reason",
    "validate_mutation_retry",
]
