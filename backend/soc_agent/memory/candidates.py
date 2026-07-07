"""Memory candidate repository helpers."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import SocMemoryCandidate, SocMemoryCandidateStatus


class InMemoryMemoryCandidateRepository:
    """In-memory candidate store for service tests and local smoke runs."""

    def __init__(self, candidates: Iterable[SocMemoryCandidate] | None = None) -> None:
        self._candidates: dict[str, SocMemoryCandidate] = {}
        for candidate in candidates or ():
            self.save_memory_candidate(candidate)

    def save_memory_candidate(self, candidate: SocMemoryCandidate) -> None:
        self._candidates[candidate.candidate_id] = candidate

    def get_memory_candidate(self, candidate_id: str) -> SocMemoryCandidate | None:
        return self._candidates.get(candidate_id)

    def list_memory_candidates(
        self,
        *,
        status: SocMemoryCandidateStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryCandidate]:
        items = list(self._candidates.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        if tenant_scope is not None:
            items = [item for item in items if item.tenant_scope == tenant_scope]
        if tenant_id is not None:
            items = [item for item in items if item.tenant_id == tenant_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]
