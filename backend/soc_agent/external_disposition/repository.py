"""In-memory external disposition repository for service tests and local smoke."""

from __future__ import annotations

from soc_agent.contracts import SocExternalDispositionRecord


class InMemoryExternalDispositionRepository:
    """Simple in-memory repository for external disposition records."""

    def __init__(self) -> None:
        self.records: dict[str, SocExternalDispositionRecord] = {}

    def save_external_disposition(self, record: SocExternalDispositionRecord) -> None:
        self.records[record.disposition_id] = record

    def find_external_disposition_by_idempotency_key(self, idempotency_key: str) -> SocExternalDispositionRecord | None:
        for record in self.records.values():
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def list_external_dispositions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        external_system: str | None = None,
        external_case_id: str | None = None,
        limit: int = 50,
    ) -> list[SocExternalDispositionRecord]:
        records = list(self.records.values())
        target_filters = {
            "target_run_id": run_id,
            "target_alert_id": alert_id,
            "target_queue_id": queue_id,
        }
        active_target_filters = {key: value for key, value in target_filters.items() if value is not None}
        if active_target_filters:
            records = [item for item in records if any(getattr(item, key) == value for key, value in active_target_filters.items())]
        if external_system is not None:
            records = [item for item in records if item.event.external_system == external_system]
        if external_case_id is not None:
            records = [item for item in records if item.event.external_case_id == external_case_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)[:limit]
