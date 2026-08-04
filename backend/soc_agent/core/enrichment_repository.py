"""In-memory PI-01D3 execution repository for focused tests and local demos."""

from __future__ import annotations

from collections.abc import Iterable
from threading import RLock

from soc_agent.contracts import SocEnrichmentActionAttempt, SocEnrichmentExecution


class InMemorySocEnrichmentExecutionRepository:
    """Thread-safe execution ledger with the same CAS semantics as SQL storage."""

    def __init__(
        self,
        executions: Iterable[SocEnrichmentExecution] = (),
        attempts: Iterable[SocEnrichmentActionAttempt] = (),
    ) -> None:
        self._lock = RLock()
        self._executions: dict[str, SocEnrichmentExecution] = {}
        self._execution_ids_by_idempotency_key: dict[str, str] = {}
        self._attempts: dict[str, SocEnrichmentActionAttempt] = {}
        self._attempt_ids_by_identity: dict[tuple[str, str, int], str] = {}
        self._attempt_ids_by_idempotency_key: dict[str, str] = {}
        for execution in executions:
            if not self.create_enrichment_execution(execution):
                raise ValueError(f"duplicate enrichment execution {execution.execution_id}")
        for attempt in attempts:
            if not self.create_enrichment_action_attempt(attempt):
                raise ValueError(f"duplicate enrichment action attempt {attempt.attempt_id}")

    def create_enrichment_execution(self, execution: SocEnrichmentExecution) -> bool:
        with self._lock:
            if execution.execution_id in self._executions:
                return False
            if execution.idempotency_key in self._execution_ids_by_idempotency_key:
                return False
            stored = execution.model_copy(deep=True)
            self._executions[stored.execution_id] = stored
            self._execution_ids_by_idempotency_key[stored.idempotency_key] = stored.execution_id
            return True

    def get_enrichment_execution(self, execution_id: str) -> SocEnrichmentExecution | None:
        with self._lock:
            execution = self._executions.get(execution_id)
            return execution.model_copy(deep=True) if execution is not None else None

    def find_enrichment_execution_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocEnrichmentExecution | None:
        with self._lock:
            execution_id = self._execution_ids_by_idempotency_key.get(idempotency_key)
            if execution_id is None:
                return None
            return self._executions[execution_id].model_copy(deep=True)

    def compare_and_set_enrichment_execution(
        self,
        execution: SocEnrichmentExecution,
        *,
        expected_version: int,
    ) -> bool:
        with self._lock:
            current = self._executions.get(execution.execution_id)
            if current is None or current.version != expected_version:
                return False
            if execution.idempotency_key != current.idempotency_key:
                raise ValueError("enrichment execution idempotency key is immutable")
            if execution.version != expected_version + 1:
                raise ValueError("enrichment execution CAS must increment version by one")
            self._executions[execution.execution_id] = execution.model_copy(deep=True)
            return True

    def create_enrichment_action_attempt(self, attempt: SocEnrichmentActionAttempt) -> bool:
        identity = (attempt.execution_id, attempt.plan_action_id, attempt.attempt_number)
        with self._lock:
            if attempt.attempt_id in self._attempts:
                return False
            if identity in self._attempt_ids_by_identity:
                return False
            if attempt.action_idempotency_key in self._attempt_ids_by_idempotency_key:
                return False
            stored = attempt.model_copy(deep=True)
            self._attempts[stored.attempt_id] = stored
            self._attempt_ids_by_identity[identity] = stored.attempt_id
            self._attempt_ids_by_idempotency_key[stored.action_idempotency_key] = stored.attempt_id
            return True

    def get_enrichment_action_attempt(
        self,
        attempt_id: str,
    ) -> SocEnrichmentActionAttempt | None:
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            return attempt.model_copy(deep=True) if attempt is not None else None

    def compare_and_set_enrichment_action_attempt(
        self,
        attempt: SocEnrichmentActionAttempt,
        *,
        expected_version: int,
    ) -> bool:
        with self._lock:
            current = self._attempts.get(attempt.attempt_id)
            if current is None or current.version != expected_version:
                return False
            if attempt.action_idempotency_key != current.action_idempotency_key:
                raise ValueError("enrichment attempt idempotency key is immutable")
            if attempt.version != expected_version + 1:
                raise ValueError("enrichment attempt CAS must increment version by one")
            self._attempts[attempt.attempt_id] = attempt.model_copy(deep=True)
            return True

    def list_enrichment_action_attempts(
        self,
        execution_id: str,
    ) -> list[SocEnrichmentActionAttempt]:
        with self._lock:
            attempts = [attempt.model_copy(deep=True) for attempt in self._attempts.values() if attempt.execution_id == execution_id]
        return sorted(
            attempts,
            key=lambda item: (item.plan_action_id, item.attempt_number, item.started_at, item.attempt_id),
        )


__all__ = ["InMemorySocEnrichmentExecutionRepository"]
