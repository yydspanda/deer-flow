"""Memory candidate repository helpers."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import (
    SocMemoryCandidate,
    SocMemoryCandidateStatus,
    SocMemoryQuery,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMutationAuditRecord,
    SocMutationOperation,
)
from soc_agent.memory.scoring import score_memory_record


class InMemoryMemoryCandidateRepository:
    """In-memory candidate store for service tests and local smoke runs."""

    def __init__(self, candidates: Iterable[SocMemoryCandidate] | None = None) -> None:
        self._candidates: dict[str, SocMemoryCandidate] = {}
        self._records: dict[str, SocMemoryRecord] = {}
        self._mutation_audits: dict[tuple[SocMutationOperation, str], SocMutationAuditRecord] = {}
        for candidate in candidates or ():
            self.save_memory_candidate(candidate)

    def save_memory_candidate(self, candidate: SocMemoryCandidate) -> None:
        self._candidates[candidate.candidate_id] = candidate

    def get_memory_candidate(self, candidate_id: str) -> SocMemoryCandidate | None:
        return self._candidates.get(candidate_id)

    def find_memory_candidate_by_idempotency_key(self, idempotency_key: str) -> SocMemoryCandidate | None:
        for candidate in self._candidates.values():
            if candidate.idempotency_key == idempotency_key:
                return candidate
        return None

    def find_memory_candidate_by_source_id(
        self,
        source_id: str,
    ) -> SocMemoryCandidate | None:
        for candidate in self._candidates.values():
            if candidate.source.source_id == source_id:
                return candidate
        return None

    def list_memory_candidates(
        self,
        *,
        status: SocMemoryCandidateStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryCandidate]:
        items = list(self._candidates.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        if tenant_scope is not None:
            items = [item for item in items if item.tenant_scope == tenant_scope]
        if tenant_id is not None:
            items = [item for item in items if item.tenant_id == tenant_id]
        source_filters = {
            "run_id": run_id,
            "alert_id": alert_id,
            "queue_id": queue_id,
        }
        active_source_filters = {key: value for key, value in source_filters.items() if value is not None}
        if active_source_filters:
            items = [item for item in items if any(getattr(item.source, key) == value for key, value in active_source_filters.items())]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    def save_memory_record(self, record: SocMemoryRecord) -> None:
        self._records[record.memory_id] = record

    def compare_and_set_memory_record(
        self,
        record: SocMemoryRecord,
        *,
        expected_version: int,
    ) -> bool:
        current = self._records.get(record.memory_id)
        if current is None or current.version != expected_version:
            return False
        self._records[record.memory_id] = record
        return True

    def get_memory_record(self, memory_id: str) -> SocMemoryRecord | None:
        return self._records.get(memory_id)

    def get_memory_record_by_candidate_id(self, candidate_id: str) -> SocMemoryRecord | None:
        for record in self._records.values():
            if record.source_candidate_id == candidate_id:
                return record
        return None

    def list_memory_records(
        self,
        *,
        status: SocMemoryRecordStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        source_candidate_id: str | None = None,
        retrieval_enabled: bool | None = None,
        limit: int = 50,
    ) -> list[SocMemoryRecord]:
        items = list(self._records.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        if tenant_scope is not None:
            items = [item for item in items if item.tenant_scope == tenant_scope]
        if tenant_id is not None:
            items = [item for item in items if item.tenant_id == tenant_id]
        if source_candidate_id is not None:
            items = [item for item in items if item.source_candidate_id == source_candidate_id]
        if retrieval_enabled is not None:
            items = [item for item in items if item.retrieval_enabled is retrieval_enabled]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)[:limit]

    def find_memory_candidate_records(
        self,
        query: SocMemoryQuery,
    ) -> list[SocMemoryRecord]:
        """Select relevant candidates across the complete in-memory corpus."""

        items = [
            record
            for record in self._records.values()
            if (not query.statuses or record.status in query.statuses)
            and (not query.memory_types or record.memory_type in query.memory_types)
            and (query.tenant_scope is None or record.tenant_scope == query.tenant_scope)
            and (query.tenant_id is None or record.tenant_id == query.tenant_id)
        ]
        ranked = sorted(
            items,
            key=lambda record: (
                score_memory_record(record, query)[0],
                record.updated_at,
            ),
            reverse=True,
        )
        relevant = [record for record in ranked if score_memory_record(record, query)[1]]
        selected = relevant[: query.candidate_limit]
        if len(selected) < query.candidate_limit:
            selected_ids = {record.memory_id for record in selected}
            selected.extend(record for record in ranked if record.memory_id not in selected_ids)
        return selected[: query.candidate_limit]

    def append_mutation_audit(self, record: SocMutationAuditRecord) -> None:
        key = (record.operation, record.idempotency_key)
        if key in self._mutation_audits:
            raise ValueError(f"mutation audit idempotency key {record.idempotency_key} already exists")
        self._mutation_audits[key] = record

    def find_mutation_audit_by_idempotency_key(
        self,
        operation: SocMutationOperation,
        idempotency_key: str,
    ) -> SocMutationAuditRecord | None:
        return self._mutation_audits.get((operation, idempotency_key))

    def list_mutation_audits(
        self,
        *,
        operation: SocMutationOperation | None = None,
        run_id: str | None = None,
        queue_id: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[SocMutationAuditRecord]:
        items = list(self._mutation_audits.values())
        if operation is not None:
            items = [item for item in items if item.operation is operation]
        if run_id is not None:
            items = [item for item in items if item.run_id == run_id]
        if queue_id is not None:
            items = [item for item in items if item.queue_id == queue_id]
        if target_id is not None:
            items = [item for item in items if item.target_id == target_id]
        return sorted(items, key=lambda item: item.occurred_at, reverse=True)[:limit]
