"""Investigation evidence repository helpers."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import InvestigationEvidence


class InMemoryInvestigationEvidenceRepository:
    """In-memory investigation evidence store for local TUI/session tests."""

    def __init__(self, evidence: Iterable[InvestigationEvidence] | None = None) -> None:
        self._evidence: dict[str, InvestigationEvidence] = {}
        for item in evidence or ():
            self.save_evidence(item)

    def save_evidence(self, evidence: InvestigationEvidence) -> None:
        self._evidence[evidence.evidence_id] = evidence

    def get_evidence(self, evidence_id: str) -> InvestigationEvidence | None:
        evidence = self._evidence.get(evidence_id)
        return evidence.model_copy(deep=True) if evidence is not None else None

    def list_evidence(
        self,
        *,
        queue_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[InvestigationEvidence]:
        filters = {
            "queue_id": queue_id,
            "run_id": run_id,
            "alert_id": alert_id,
            "thread_id": thread_id,
        }
        active_filters = {key: value for key, value in filters.items() if value}
        items = list(self._evidence.values())
        if active_filters:
            items = [item for item in items if _matches_any_filter(item, active_filters)]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]


def _matches_any_filter(item: InvestigationEvidence, filters: dict[str, str]) -> bool:
    return any(getattr(item, key) == value for key, value in filters.items())
