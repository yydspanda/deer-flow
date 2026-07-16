"""EV-01 shadow outcome, sampling, and evaluation service."""

from __future__ import annotations

from datetime import UTC, datetime

from soc_agent.contracts import (
    AuthorizationEnrichmentRecord,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocDispositionEvaluationGatePolicy,
    SocDispositionEvaluationReport,
    SocDispositionOutcomeApplyResult,
    SocDispositionOutcomeCommand,
    SocDispositionOutcomeRecord,
    SocDispositionOutcomeReviewKind,
    SocDispositionOutcomeStatus,
    SocDispositionProposalRecord,
    SocDispositionSampleCreateCommand,
    SocDispositionSampleCreateResult,
    SocDispositionSampleManifest,
    SocDispositionSampleManifestListResponse,
    SocDispositionSampleReviewInbox,
    SocDispositionSampleReviewItem,
    SocDispositionSampleReviewReadiness,
    SocEvent,
    SocEventType,
    SocOperationalDisposition,
)
from soc_agent.core.service import NoopEventSink, SocServiceNotFoundError, SocServiceNotImplementedError
from soc_agent.disposition import DispositionEvaluationConflictError
from soc_agent.disposition.evaluation import (
    disposition_evaluation_scope_hash,
    evaluate_disposition_gate,
    scoped_disposition_proposals,
    select_disposition_review_sample,
)
from soc_agent.protocols import (
    AuthorizationEnrichmentRepository,
    ReviewQueueRepository,
    SocDispositionEvaluationRepository,
    SocDispositionProposalRepository,
    SocEventSink,
)
from soc_agent.utils.hashing import stable_hash


class DispositionEvaluationIdempotencyConflictError(ValueError):
    """Raised when immutable EV-01 identities are reused with other semantics."""


class DispositionEvaluationIneligibleError(ValueError):
    """Raised when an outcome or sample cannot satisfy the evaluation contract."""


class SocDispositionEvaluationService:
    """Persist explicit labels and expose read-only shadow evaluation views."""

    def __init__(
        self,
        *,
        repository: SocDispositionEvaluationRepository | None = None,
        proposal_repository: SocDispositionProposalRepository | None = None,
        authorization_enrichment_repository: AuthorizationEnrichmentRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._repository = repository
        self._proposal_repository = proposal_repository
        self._authorization_enrichment_repository = authorization_enrichment_repository
        self._review_queue_repository = review_queue_repository
        self._event_sink = event_sink or NoopEventSink()

    def create_sample(
        self,
        command: SocDispositionSampleCreateCommand,
        *,
        context: ServiceRequestContext | None = None,
        proposal_limit: int = 10_000,
    ) -> SocDispositionSampleCreateResult:
        repository = self._require_repository()
        seed_hash = stable_hash({"selection_seed": command.selection_seed})
        existing = repository.find_disposition_sample_manifest_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            _validate_sample_retry(existing, command, selection_seed_hash=seed_hash)
            return SocDispositionSampleCreateResult(manifest=existing, idempotent=True)
        proposals, enrichments, complete = self._load_population(proposal_limit=proposal_limit)
        scoped, warnings = scoped_disposition_proposals(proposals, enrichments, command.scope)
        if not complete:
            raise DispositionEvaluationIneligibleError("cannot sample a truncated proposal population")
        if warnings:
            raise DispositionEvaluationIneligibleError("cannot sample a population with broken enrichment lineage")
        if command.sample_size > len(scoped):
            raise DispositionEvaluationIneligibleError("sample_size exceeds the scoped proposal population")

        selected = select_disposition_review_sample(
            scoped,
            sample_size=command.sample_size,
            selection_seed_hash=seed_hash,
        )
        scope_hash = disposition_evaluation_scope_hash(command.scope)
        population_hash = stable_hash([{"proposal_id": item.proposal_id, "proposal_key": item.proposal_key} for item in scoped])
        sample_key = stable_hash(
            {
                "scope_hash": scope_hash,
                "population_hash": population_hash,
                "sample_size": command.sample_size,
                "selection_seed_hash": seed_hash,
                "sampling_method": "sha256_rank_v1",
            }
        )
        if repository.find_disposition_sample_manifest_by_key(sample_key) is not None:
            raise DispositionEvaluationIdempotencyConflictError("semantic disposition sample already exists under a different idempotency key")

        request_context = context or ServiceRequestContext()
        manifest = SocDispositionSampleManifest(
            sample_key=sample_key,
            scope=command.scope,
            scope_hash=scope_hash,
            population_count=len(scoped),
            population_hash=population_hash,
            selected_proposal_ids=[item.proposal_id for item in selected],
            sample_size=command.sample_size,
            selection_seed_hash=seed_hash,
            idempotency_key=command.idempotency_key,
            created_by=request_context.actor,
        )
        try:
            repository.save_disposition_sample_manifest(manifest)
        except DispositionEvaluationConflictError as exc:
            concurrent = repository.find_disposition_sample_manifest_by_idempotency_key(command.idempotency_key)
            if concurrent is not None:
                _validate_sample_retry(concurrent, command, selection_seed_hash=seed_hash)
                return SocDispositionSampleCreateResult(manifest=concurrent, idempotent=True)
            raise DispositionEvaluationIdempotencyConflictError(str(exc)) from exc
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.DISPOSITION_SAMPLE_CREATED,
                request_id=request_context.request_id,
                actor=request_context.actor,
                payload={
                    "sample_id": manifest.sample_id,
                    "scope_hash": manifest.scope_hash,
                    "population_count": manifest.population_count,
                    "sample_size": manifest.sample_size,
                    "sampling_method": manifest.sampling_method,
                    "decision_impact": "none",
                },
            )
        )
        return SocDispositionSampleCreateResult(manifest=manifest)

    def record_outcome(
        self,
        command: SocDispositionOutcomeCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocDispositionOutcomeApplyResult:
        repository = self._require_repository()
        proposal = self._get_proposal(command.proposal_id)
        queue_item = self._get_closed_queue(proposal)
        observed_at = command.observed_at or datetime.now(UTC)
        if observed_at < proposal.created_at:
            raise DispositionEvaluationIneligibleError("outcome observed_at cannot precede the proposal")
        if queue_item.closed_at is not None and observed_at < queue_item.closed_at:
            raise DispositionEvaluationIneligibleError("outcome observed_at cannot precede ReviewQueue closure")

        request_context = context or ServiceRequestContext()
        existing = repository.find_disposition_outcome_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            _validate_outcome_retry(existing, command, proposal=proposal, actor_id=request_context.actor.actor_id)
            return SocDispositionOutcomeApplyResult(outcome=existing, idempotent=True)
        if command.review_kind is SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW:
            self._validate_sampled_review(command, request_context=request_context)
        latest = self._latest_outcome(proposal.proposal_id, command.review_kind)
        _validate_outcome_supersession(latest, command, observed_at=observed_at)

        outcome = SocDispositionOutcomeRecord(
            lineage_key=stable_hash(
                {
                    "proposal_id": proposal.proposal_id,
                    "review_kind": command.review_kind.value,
                    "supersedes_outcome_id": command.supersedes_outcome_id or "root",
                }
            ),
            proposal_id=proposal.proposal_id,
            proposal_key=proposal.proposal_key,
            run_id=proposal.run_id,
            alert_id=proposal.alert_id,
            queue_id=proposal.queue_id,
            proposed_disposition=proposal.proposed_disposition,
            observed_disposition=command.observed_disposition,
            outcome_status=_outcome_status(proposal, command.observed_disposition),
            review_kind=command.review_kind,
            source=command.source,
            source_ref=command.source_ref,
            sample_id=command.sample_id,
            reason=command.reason,
            evidence_refs=command.evidence_refs,
            proposal_policy_version=proposal.policy_version,
            supersedes_outcome_id=command.supersedes_outcome_id,
            idempotency_key=command.idempotency_key,
            reviewed_by=request_context.actor,
            observed_at=observed_at,
        )
        try:
            repository.save_disposition_outcome(outcome)
        except DispositionEvaluationConflictError as exc:
            concurrent = repository.find_disposition_outcome_by_idempotency_key(command.idempotency_key)
            if concurrent is not None:
                _validate_outcome_retry(
                    concurrent,
                    command,
                    proposal=proposal,
                    actor_id=request_context.actor.actor_id,
                )
                return SocDispositionOutcomeApplyResult(outcome=concurrent, idempotent=True)
            raise DispositionEvaluationIdempotencyConflictError(str(exc)) from exc
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.DISPOSITION_OUTCOME_RECORDED,
                request_id=request_context.request_id,
                run_id=outcome.run_id,
                alert_id=outcome.alert_id,
                actor=request_context.actor,
                payload={
                    "outcome_id": outcome.outcome_id,
                    "proposal_id": outcome.proposal_id,
                    "queue_id": outcome.queue_id,
                    "review_kind": outcome.review_kind.value,
                    "outcome_status": outcome.outcome_status.value,
                    "sample_id": outcome.sample_id,
                    "decision_impact": "none",
                    "review_queue_impact": "none",
                },
            )
        )
        return SocDispositionOutcomeApplyResult(outcome=outcome, event_written=True)

    def evaluate(
        self,
        policy: SocDispositionEvaluationGatePolicy,
        *,
        proposal_limit: int = 10_000,
    ) -> SocDispositionEvaluationReport:
        repository = self._require_repository()
        proposals, enrichments, proposals_complete = self._load_population(proposal_limit=proposal_limit)
        outcome_limit = max(500, proposal_limit * 4)
        outcomes = repository.list_disposition_outcomes(limit=outcome_limit + 1)
        manifest_limit = max(100, proposal_limit)
        manifests = repository.list_disposition_sample_manifests(
            scope_hash=disposition_evaluation_scope_hash(policy.scope),
            limit=manifest_limit + 1,
        )
        complete = proposals_complete and len(outcomes) <= outcome_limit and len(manifests) <= manifest_limit
        return evaluate_disposition_gate(
            proposals,
            enrichments,
            outcomes[:outcome_limit],
            manifests[:manifest_limit],
            policy,
            dataset_complete=complete,
        )

    def get_sample(self, sample_id: str) -> SocDispositionSampleManifest:
        manifest = self._require_repository().get_disposition_sample_manifest(sample_id)
        if manifest is None:
            raise SocServiceNotFoundError(f"disposition sample {sample_id} not found")
        return manifest

    def list_samples(self, *, scope_hash: str | None = None, limit: int = 100) -> list[SocDispositionSampleManifest]:
        return self._require_repository().list_disposition_sample_manifests(scope_hash=scope_hash, limit=limit)

    def list_sample_review_campaigns(
        self,
        *,
        limit: int = 50,
    ) -> SocDispositionSampleManifestListResponse:
        if not 1 <= limit <= 500:
            raise ValueError("sample review campaign limit must be between 1 and 500")
        manifests = self._require_repository().list_disposition_sample_manifests(limit=limit + 1)
        return SocDispositionSampleManifestListResponse(
            items=manifests[:limit],
            limit=limit,
            has_more=len(manifests) > limit,
        )

    def get_sample_review_inbox(
        self,
        sample_id: str,
        *,
        reviewer_actor_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> SocDispositionSampleReviewInbox:
        if not reviewer_actor_id.strip():
            raise ValueError("sample review inbox requires reviewer_actor_id")
        if offset < 0:
            raise ValueError("sample review inbox offset must be non-negative")
        if not 1 <= limit <= 200:
            raise ValueError("sample review inbox limit must be between 1 and 200")
        repository = self._require_repository()
        proposal_repository = self._proposal_repository
        review_queue_repository = self._review_queue_repository
        if proposal_repository is None:
            raise SocServiceNotImplementedError("sample review inbox requires a SocDispositionProposalRepository")
        if review_queue_repository is None:
            raise SocServiceNotImplementedError("sample review inbox requires a ReviewQueueRepository")

        manifest = self.get_sample(sample_id)
        selected_ids = manifest.selected_proposal_ids
        if offset > len(selected_ids):
            raise ValueError("sample review inbox offset exceeds manifest size")
        primary_by_proposal = {
            outcome.proposal_id: outcome
            for outcome in repository.list_latest_disposition_outcomes_for_proposals(
                proposal_ids=selected_ids,
                review_kind=SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION,
            )
        }
        sampled_by_proposal = {
            outcome.proposal_id: outcome
            for outcome in repository.list_latest_disposition_outcomes_for_proposals(
                proposal_ids=selected_ids,
                review_kind=SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW,
                sample_id=manifest.sample_id,
            )
        }
        completed_ids = {proposal_id for proposal_id, sampled in sampled_by_proposal.items() if _sampled_outcome_is_independent(primary_by_proposal.get(proposal_id), sampled)}
        remaining_ids = set(selected_ids) - completed_ids
        reviewer_id = reviewer_actor_id.strip()
        reviewer_conflict_count = sum(1 for proposal_id in remaining_ids if (primary := primary_by_proposal.get(proposal_id)) is not None and primary.reviewed_by.actor_id == reviewer_id)

        page_ids = selected_ids[offset : offset + limit]
        items: list[SocDispositionSampleReviewItem] = []
        for page_index, proposal_id in enumerate(page_ids, start=offset + 1):
            proposal = proposal_repository.get_disposition_proposal(proposal_id)
            primary = primary_by_proposal.get(proposal_id)
            sampled = sampled_by_proposal.get(proposal_id)
            sampled_independent = _sampled_outcome_is_independent(primary, sampled) if sampled is not None else None
            reviewer_independent = primary is None or primary.reviewed_by.actor_id != reviewer_id
            queue_item = review_queue_repository.get_review_item(proposal.queue_id) if proposal is not None else None
            readiness, blocking_reasons = _sample_review_readiness(
                proposal_id=proposal_id,
                proposal=proposal,
                queue_item=queue_item,
                sampled_outcome=sampled,
                sampled_outcome_independent=sampled_independent,
            )
            if not reviewer_independent:
                blocking_reasons.append("current reviewer is not independent from the primary analyst")
            can_record = (
                readiness
                in {
                    SocDispositionSampleReviewReadiness.READY,
                    SocDispositionSampleReviewReadiness.COMPLETED,
                }
                and reviewer_independent
            )
            items.append(
                SocDispositionSampleReviewItem(
                    sample_id=manifest.sample_id,
                    selection_rank=page_index,
                    proposal_id=proposal_id,
                    proposal=proposal,
                    queue_item=queue_item,
                    primary_outcome=primary,
                    sampled_outcome=sampled,
                    sampled_outcome_independent=sampled_independent,
                    reviewer_independent=reviewer_independent,
                    readiness=readiness,
                    can_record_outcome=can_record,
                    blocking_reasons=blocking_reasons,
                )
            )

        completed_count = len(completed_ids)
        return SocDispositionSampleReviewInbox(
            manifest=manifest,
            reviewer_actor_id=reviewer_id,
            total_count=manifest.sample_size,
            completed_count=completed_count,
            remaining_count=manifest.sample_size - completed_count,
            reviewer_conflict_count=reviewer_conflict_count,
            completion_rate=completed_count / manifest.sample_size,
            offset=offset,
            limit=limit,
            has_more=offset + len(items) < manifest.sample_size,
            items=items,
        )

    def get_outcome(self, outcome_id: str) -> SocDispositionOutcomeRecord:
        outcome = self._require_repository().get_disposition_outcome(outcome_id)
        if outcome is None:
            raise SocServiceNotFoundError(f"disposition outcome {outcome_id} not found")
        return outcome

    def list_outcomes(
        self,
        *,
        proposal_id: str | None = None,
        queue_id: str | None = None,
        review_kind: SocDispositionOutcomeReviewKind | None = None,
        sample_id: str | None = None,
        limit: int = 500,
    ) -> list[SocDispositionOutcomeRecord]:
        return self._require_repository().list_disposition_outcomes(
            proposal_id=proposal_id,
            queue_id=queue_id,
            review_kind=review_kind,
            sample_id=sample_id,
            limit=limit,
        )

    def _load_population(
        self,
        *,
        proposal_limit: int,
    ) -> tuple[list[SocDispositionProposalRecord], dict[str, AuthorizationEnrichmentRecord], bool]:
        if proposal_limit < 1:
            raise ValueError("proposal_limit must be positive")
        if self._proposal_repository is None:
            raise SocServiceNotImplementedError("disposition evaluation requires a SocDispositionProposalRepository")
        if self._authorization_enrichment_repository is None:
            raise SocServiceNotImplementedError("disposition evaluation requires an AuthorizationEnrichmentRepository")
        proposals = self._proposal_repository.list_disposition_proposals(limit=proposal_limit + 1)
        complete = len(proposals) <= proposal_limit
        selected = proposals[:proposal_limit]
        enrichments: dict[str, AuthorizationEnrichmentRecord] = {}
        for proposal in selected:
            enrichment = self._authorization_enrichment_repository.get_authorization_enrichment(proposal.source_enrichment_id)
            if enrichment is not None:
                enrichments[enrichment.enrichment_id] = enrichment
        return selected, enrichments, complete

    def _validate_sampled_review(
        self,
        command: SocDispositionOutcomeCommand,
        *,
        request_context: ServiceRequestContext,
    ) -> None:
        manifest = self._require_repository().get_disposition_sample_manifest(command.sample_id or "")
        if manifest is None:
            raise DispositionEvaluationIneligibleError(f"sample manifest {command.sample_id} not found")
        if command.proposal_id not in manifest.selected_proposal_ids:
            raise DispositionEvaluationIneligibleError("proposal is not part of the referenced sample manifest")
        primary = self._latest_outcome(
            command.proposal_id,
            SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION,
        )
        if primary is not None and primary.reviewed_by.actor_id == request_context.actor.actor_id:
            raise DispositionEvaluationIneligibleError("sampled quality review must be performed by an independent reviewer")

    def _latest_outcome(
        self,
        proposal_id: str,
        review_kind: SocDispositionOutcomeReviewKind,
    ) -> SocDispositionOutcomeRecord | None:
        items = self._require_repository().list_disposition_outcomes(
            proposal_id=proposal_id,
            review_kind=review_kind,
            limit=1,
        )
        return items[0] if items else None

    def _get_proposal(self, proposal_id: str) -> SocDispositionProposalRecord:
        if self._proposal_repository is None:
            raise SocServiceNotImplementedError("disposition evaluation requires a SocDispositionProposalRepository")
        proposal = self._proposal_repository.get_disposition_proposal(proposal_id)
        if proposal is None:
            raise SocServiceNotFoundError(f"disposition proposal {proposal_id} not found")
        return proposal

    def _get_closed_queue(self, proposal: SocDispositionProposalRecord):
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("disposition outcome requires a ReviewQueueRepository")
        item = self._review_queue_repository.get_review_item(proposal.queue_id)
        if item is None:
            raise DispositionEvaluationIneligibleError(f"review queue item {proposal.queue_id} not found")
        if item.run_id != proposal.run_id or item.alert_id != proposal.alert_id:
            raise DispositionEvaluationIneligibleError("review queue lineage does not match disposition proposal")
        if item.status is not ReviewQueueStatus.CLOSED or item.closed_at is None or item.closed_by is None:
            raise DispositionEvaluationIneligibleError("final disposition outcome requires a closed ReviewQueue item")
        return item

    def _require_repository(self) -> SocDispositionEvaluationRepository:
        if self._repository is None:
            raise SocServiceNotImplementedError("disposition evaluation requires a SocDispositionEvaluationRepository")
        return self._repository


def _outcome_status(
    proposal: SocDispositionProposalRecord,
    observed: SocOperationalDisposition,
) -> SocDispositionOutcomeStatus:
    if observed is SocOperationalDisposition.UNKNOWN:
        return SocDispositionOutcomeStatus.INCONCLUSIVE
    if observed is proposal.proposed_disposition:
        return SocDispositionOutcomeStatus.CONFIRMED
    return SocDispositionOutcomeStatus.OVERRIDDEN


def _sampled_outcome_is_independent(
    primary: SocDispositionOutcomeRecord | None,
    sampled: SocDispositionOutcomeRecord,
) -> bool:
    return primary is None or primary.reviewed_by.actor_id != sampled.reviewed_by.actor_id


def _sample_review_readiness(
    *,
    proposal_id: str,
    proposal: SocDispositionProposalRecord | None,
    queue_item: ReviewQueueItem | None,
    sampled_outcome: SocDispositionOutcomeRecord | None,
    sampled_outcome_independent: bool | None,
) -> tuple[SocDispositionSampleReviewReadiness, list[str]]:
    if proposal is None:
        return (
            SocDispositionSampleReviewReadiness.UNAVAILABLE,
            [f"manifest proposal {proposal_id} is unavailable"],
        )
    if queue_item is None:
        return (
            SocDispositionSampleReviewReadiness.UNAVAILABLE,
            [f"ReviewQueue item {proposal.queue_id} is unavailable"],
        )
    if queue_item.run_id != proposal.run_id or queue_item.alert_id != proposal.alert_id:
        return (
            SocDispositionSampleReviewReadiness.UNAVAILABLE,
            ["ReviewQueue lineage does not match the selected proposal"],
        )
    if queue_item.status is not ReviewQueueStatus.CLOSED or queue_item.closed_at is None or queue_item.closed_by is None:
        return (
            SocDispositionSampleReviewReadiness.WAITING_FOR_QUEUE_CLOSE,
            ["primary ReviewQueue item is not closed"],
        )
    if sampled_outcome is not None and sampled_outcome_independent:
        return SocDispositionSampleReviewReadiness.COMPLETED, []
    if sampled_outcome is not None:
        return (
            SocDispositionSampleReviewReadiness.READY,
            ["latest sampled outcome is not independent and must be superseded"],
        )
    return SocDispositionSampleReviewReadiness.READY, []


def _validate_sample_retry(
    existing: SocDispositionSampleManifest,
    command: SocDispositionSampleCreateCommand,
    *,
    selection_seed_hash: str,
) -> None:
    if existing.scope != command.scope or existing.sample_size != command.sample_size or existing.selection_seed_hash != selection_seed_hash or existing.idempotency_key != command.idempotency_key:
        raise DispositionEvaluationIdempotencyConflictError("disposition sample idempotency key was reused for different semantics")


def _validate_outcome_retry(
    existing: SocDispositionOutcomeRecord,
    command: SocDispositionOutcomeCommand,
    *,
    proposal: SocDispositionProposalRecord,
    actor_id: str,
) -> None:
    if (
        existing.proposal_id != proposal.proposal_id
        or existing.observed_disposition is not command.observed_disposition
        or existing.review_kind is not command.review_kind
        or existing.source is not command.source
        or existing.source_ref != command.source_ref
        or existing.sample_id != command.sample_id
        or existing.reason != command.reason
        or existing.evidence_refs != command.evidence_refs
        or (command.observed_at is not None and existing.observed_at != command.observed_at)
        or existing.supersedes_outcome_id != command.supersedes_outcome_id
        or existing.reviewed_by.actor_id != actor_id
    ):
        raise DispositionEvaluationIdempotencyConflictError("disposition outcome idempotency key was reused for different semantics")


def _validate_outcome_supersession(
    latest: SocDispositionOutcomeRecord | None,
    command: SocDispositionOutcomeCommand,
    *,
    observed_at: datetime,
) -> None:
    if latest is None:
        if command.supersedes_outcome_id is not None:
            raise DispositionEvaluationIneligibleError("supersedes_outcome_id requires an existing outcome")
        return
    if command.supersedes_outcome_id != latest.outcome_id:
        raise DispositionEvaluationIneligibleError(f"new outcome must explicitly supersede latest outcome {latest.outcome_id}")
    if observed_at < latest.observed_at:
        raise DispositionEvaluationIneligibleError("superseding outcome cannot move observed_at backwards")


__all__ = [
    "DispositionEvaluationIdempotencyConflictError",
    "DispositionEvaluationIneligibleError",
    "SocDispositionEvaluationService",
]
