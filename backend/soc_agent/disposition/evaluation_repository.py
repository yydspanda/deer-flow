"""In-memory append-only persistence for EV-01 evaluation artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from soc_agent.contracts import (
    SocDispositionOutcomeRecord,
    SocDispositionOutcomeReviewKind,
    SocDispositionSampleManifest,
)


class DispositionEvaluationConflictError(ValueError):
    """Raised when immutable evaluation identities conflict."""


class InMemoryDispositionEvaluationRepository:
    def __init__(
        self,
        *,
        manifests: Iterable[SocDispositionSampleManifest] = (),
        outcomes: Iterable[SocDispositionOutcomeRecord] = (),
    ) -> None:
        self._manifests: dict[str, SocDispositionSampleManifest] = {}
        self._manifest_idempotency_index: dict[str, str] = {}
        self._manifest_key_index: dict[str, str] = {}
        self._outcomes: dict[str, SocDispositionOutcomeRecord] = {}
        self._outcome_idempotency_index: dict[str, str] = {}
        self._outcome_lineage_index: dict[str, str] = {}
        for manifest in manifests:
            self.save_disposition_sample_manifest(manifest)
        for outcome in outcomes:
            self.save_disposition_outcome(outcome)

    def save_disposition_sample_manifest(self, manifest: SocDispositionSampleManifest) -> None:
        if manifest.sample_id in self._manifests:
            raise DispositionEvaluationConflictError(f"disposition sample {manifest.sample_id} already exists")
        if manifest.idempotency_key in self._manifest_idempotency_index:
            raise DispositionEvaluationConflictError("disposition sample idempotency key already exists")
        if manifest.sample_key in self._manifest_key_index:
            raise DispositionEvaluationConflictError("semantic disposition sample already exists")
        self._manifests[manifest.sample_id] = manifest
        self._manifest_idempotency_index[manifest.idempotency_key] = manifest.sample_id
        self._manifest_key_index[manifest.sample_key] = manifest.sample_id

    def get_disposition_sample_manifest(self, sample_id: str) -> SocDispositionSampleManifest | None:
        return self._manifests.get(sample_id)

    def find_disposition_sample_manifest_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionSampleManifest | None:
        sample_id = self._manifest_idempotency_index.get(idempotency_key)
        return self._manifests.get(sample_id) if sample_id is not None else None

    def find_disposition_sample_manifest_by_key(
        self,
        sample_key: str,
    ) -> SocDispositionSampleManifest | None:
        sample_id = self._manifest_key_index.get(sample_key)
        return self._manifests.get(sample_id) if sample_id is not None else None

    def list_disposition_sample_manifests(
        self,
        *,
        scope_hash: str | None = None,
        limit: int = 100,
    ) -> list[SocDispositionSampleManifest]:
        manifests = list(self._manifests.values())
        if scope_hash is not None:
            manifests = [manifest for manifest in manifests if manifest.scope_hash == scope_hash]
        return sorted(manifests, key=lambda item: (item.created_at, item.sample_id), reverse=True)[:limit]

    def save_disposition_outcome(self, outcome: SocDispositionOutcomeRecord) -> None:
        if outcome.outcome_id in self._outcomes:
            raise DispositionEvaluationConflictError(f"disposition outcome {outcome.outcome_id} already exists")
        if outcome.idempotency_key in self._outcome_idempotency_index:
            raise DispositionEvaluationConflictError("disposition outcome idempotency key already exists")
        if outcome.lineage_key in self._outcome_lineage_index:
            raise DispositionEvaluationConflictError("disposition outcome lineage position already exists")
        self._outcomes[outcome.outcome_id] = outcome
        self._outcome_idempotency_index[outcome.idempotency_key] = outcome.outcome_id
        self._outcome_lineage_index[outcome.lineage_key] = outcome.outcome_id

    def get_disposition_outcome(self, outcome_id: str) -> SocDispositionOutcomeRecord | None:
        return self._outcomes.get(outcome_id)

    def find_disposition_outcome_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionOutcomeRecord | None:
        outcome_id = self._outcome_idempotency_index.get(idempotency_key)
        return self._outcomes.get(outcome_id) if outcome_id is not None else None

    def list_disposition_outcomes(
        self,
        *,
        proposal_id: str | None = None,
        queue_id: str | None = None,
        review_kind: SocDispositionOutcomeReviewKind | None = None,
        sample_id: str | None = None,
        limit: int = 500,
    ) -> list[SocDispositionOutcomeRecord]:
        outcomes = list(self._outcomes.values())
        filters = {
            "proposal_id": proposal_id,
            "queue_id": queue_id,
            "review_kind": review_kind,
            "sample_id": sample_id,
        }
        active = {name: value for name, value in filters.items() if value is not None}
        if active:
            outcomes = [outcome for outcome in outcomes if all(getattr(outcome, name) == value for name, value in active.items())]
        return sorted(outcomes, key=lambda item: (item.observed_at, item.created_at, item.outcome_id), reverse=True)[:limit]

    def list_latest_disposition_outcomes_for_proposals(
        self,
        *,
        proposal_ids: Sequence[str],
        review_kind: SocDispositionOutcomeReviewKind,
        sample_id: str | None = None,
    ) -> list[SocDispositionOutcomeRecord]:
        ordered_ids = list(dict.fromkeys(proposal_ids))
        selected = set(ordered_ids)
        outcomes = [outcome for outcome in self._outcomes.values() if outcome.proposal_id in selected and outcome.review_kind is review_kind and (sample_id is None or outcome.sample_id == sample_id)]
        outcomes.sort(
            key=lambda item: (item.observed_at, item.created_at, item.outcome_id),
            reverse=True,
        )
        latest: dict[str, SocDispositionOutcomeRecord] = {}
        for outcome in outcomes:
            latest.setdefault(outcome.proposal_id, outcome)
        return [latest[proposal_id] for proposal_id in ordered_ids if proposal_id in latest]


__all__ = [
    "DispositionEvaluationConflictError",
    "InMemoryDispositionEvaluationRepository",
]
