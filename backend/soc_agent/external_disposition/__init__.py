"""External disposition feedback adapters and repositories."""

from soc_agent.external_disposition.mapping import (
    build_external_disposition_event,
    build_external_disposition_idempotency_key,
    resolve_external_disposition_status,
)
from soc_agent.external_disposition.repository import InMemoryExternalDispositionRepository

__all__ = [
    "InMemoryExternalDispositionRepository",
    "build_external_disposition_event",
    "build_external_disposition_idempotency_key",
    "resolve_external_disposition_status",
]
