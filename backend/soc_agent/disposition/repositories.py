"""In-memory repository for immutable shadow disposition proposals."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import SocDispositionProposalRecord


class DispositionProposalConflictError(ValueError):
    """Raised when append-only proposal identity constraints are violated."""


class InMemoryDispositionProposalRepository:
    """Append-only implementation used by focused tests and local composition."""

    def __init__(self, proposals: Iterable[SocDispositionProposalRecord] | None = None) -> None:
        self._proposals: dict[str, SocDispositionProposalRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        self._proposal_key_index: dict[str, str] = {}
        for proposal in proposals or ():
            self.save_disposition_proposal(proposal)

    def save_disposition_proposal(self, proposal: SocDispositionProposalRecord) -> None:
        if proposal.proposal_id in self._proposals:
            raise DispositionProposalConflictError(f"disposition proposal {proposal.proposal_id} already exists")
        existing_id = self._idempotency_index.get(proposal.idempotency_key)
        if existing_id is not None:
            raise DispositionProposalConflictError(f"disposition proposal idempotency key already belongs to {existing_id}")
        existing_id = self._proposal_key_index.get(proposal.proposal_key)
        if existing_id is not None:
            raise DispositionProposalConflictError(f"semantic disposition proposal already exists as {existing_id}")
        self._proposals[proposal.proposal_id] = proposal
        self._idempotency_index[proposal.idempotency_key] = proposal.proposal_id
        self._proposal_key_index[proposal.proposal_key] = proposal.proposal_id

    def get_disposition_proposal(self, proposal_id: str) -> SocDispositionProposalRecord | None:
        return self._proposals.get(proposal_id)

    def find_disposition_proposal_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionProposalRecord | None:
        proposal_id = self._idempotency_index.get(idempotency_key)
        return self._proposals.get(proposal_id) if proposal_id is not None else None

    def find_disposition_proposal_by_key(
        self,
        proposal_key: str,
    ) -> SocDispositionProposalRecord | None:
        proposal_id = self._proposal_key_index.get(proposal_key)
        return self._proposals.get(proposal_id) if proposal_id is not None else None

    def list_disposition_proposals(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        enrichment_id: str | None = None,
        limit: int = 50,
    ) -> list[SocDispositionProposalRecord]:
        proposals = list(self._proposals.values())
        filters = {
            "run_id": run_id,
            "alert_id": alert_id,
            "queue_id": queue_id,
            "source_enrichment_id": enrichment_id,
        }
        active_filters = {name: value for name, value in filters.items() if value is not None}
        if active_filters:
            proposals = [proposal for proposal in proposals if all(getattr(proposal, name) == value for name, value in active_filters.items())]
        return sorted(proposals, key=lambda proposal: proposal.created_at, reverse=True)[:limit]


__all__ = [
    "DispositionProposalConflictError",
    "InMemoryDispositionProposalRepository",
]
