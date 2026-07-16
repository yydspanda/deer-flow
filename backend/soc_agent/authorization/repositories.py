"""In-memory authorization enrichment repository for tests and local flows."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import AuthorizationEnrichmentRecord


class AuthorizationEnrichmentConflictError(ValueError):
    """Raised when append-only enrichment identity constraints are violated."""


class InMemoryAuthorizationEnrichmentRepository:
    """Append-only in-memory implementation of AuthorizationEnrichmentRepository."""

    def __init__(self, records: Iterable[AuthorizationEnrichmentRecord] | None = None) -> None:
        self._records: dict[str, AuthorizationEnrichmentRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        for record in records or ():
            self.save_authorization_enrichment(record)

    def save_authorization_enrichment(self, record: AuthorizationEnrichmentRecord) -> None:
        if record.enrichment_id in self._records:
            raise AuthorizationEnrichmentConflictError(f"authorization enrichment {record.enrichment_id} already exists")
        existing_id = self._idempotency_index.get(record.idempotency_key)
        if existing_id is not None:
            raise AuthorizationEnrichmentConflictError(f"authorization enrichment idempotency key already belongs to {existing_id}")
        self._records[record.enrichment_id] = record
        self._idempotency_index[record.idempotency_key] = record.enrichment_id

    def get_authorization_enrichment(self, enrichment_id: str) -> AuthorizationEnrichmentRecord | None:
        return self._records.get(enrichment_id)

    def find_authorization_enrichment_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AuthorizationEnrichmentRecord | None:
        enrichment_id = self._idempotency_index.get(idempotency_key)
        return self._records.get(enrichment_id) if enrichment_id is not None else None

    def list_authorization_enrichments(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[AuthorizationEnrichmentRecord]:
        records = list(self._records.values())
        filters = {"run_id": run_id, "alert_id": alert_id, "queue_id": queue_id}
        active_filters = {name: value for name, value in filters.items() if value is not None}
        if active_filters:
            records = [record for record in records if any(getattr(record, name) == value for name, value in active_filters.items())]
        return sorted(records, key=lambda record: record.created_at, reverse=True)[:limit]


__all__ = [
    "AuthorizationEnrichmentConflictError",
    "InMemoryAuthorizationEnrichmentRepository",
]
