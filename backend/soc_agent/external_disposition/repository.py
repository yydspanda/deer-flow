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
        if run_id is not None:
            records = [item for item in records if item.target_run_id == run_id]
        if alert_id is not None:
            records = [item for item in records if item.target_alert_id == alert_id]
        if queue_id is not None:
            records = [item for item in records if item.target_queue_id == queue_id]
        if external_system is not None:
            records = [item for item in records if item.event.external_system == external_system]
        if external_case_id is not None:
            records = [item for item in records if item.event.external_case_id == external_case_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)[:limit]
