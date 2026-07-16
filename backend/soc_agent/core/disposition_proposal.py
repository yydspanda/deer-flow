"""Shadow-only operational disposition proposal service."""

from __future__ import annotations

from soc_agent.contracts import (
    AnalysisRun,
    AuthorizationEnrichmentRecord,
    AuthorizationMatchStatus,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocDetectionTruthSnapshot,
    SocDispositionProposalApplyResult,
    SocDispositionProposalCommand,
    SocDispositionProposalReasonCode,
    SocDispositionProposalRecord,
    SocEvent,
    SocEventType,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core.service import NoopEventSink, SocServiceNotFoundError, SocServiceNotImplementedError
from soc_agent.disposition import DispositionProposalConflictError
from soc_agent.protocols import (
    AlertRepository,
    AuthorizationEnrichmentRepository,
    ReviewQueueRepository,
    SocDispositionProposalRepository,
    SocEventSink,
)
from soc_agent.utils.hashing import stable_hash

SOC_DISPOSITION_PROPOSAL_POLICY_VERSION = "soc.disposition_proposal_policy.v1"


class DispositionProposalIdempotencyConflictError(ValueError):
    """Raised when a retry key is reused for a different semantic proposal."""


class DispositionProposalIneligibleError(ValueError):
    """Raised when persisted evidence cannot support a DP-01 proposal."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class SocDispositionProposalService:
    """Create immutable shadow proposals without mutating review or detection state."""

    def __init__(
        self,
        *,
        repository: SocDispositionProposalRepository | None = None,
        authorization_enrichment_repository: AuthorizationEnrichmentRepository | None = None,
        alert_repository: AlertRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._repository = repository
        self._authorization_enrichment_repository = authorization_enrichment_repository
        self._alert_repository = alert_repository
        self._review_queue_repository = review_queue_repository
        self._event_sink = event_sink or NoopEventSink()

    def propose(
        self,
        command: SocDispositionProposalCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocDispositionProposalApplyResult:
        """Create or deduplicate one exact-authorization shadow proposal."""

        repository = self._require_repository()
        enrichment = self._get_enrichment(command.enrichment_id)
        run = self._get_run(enrichment.run_id)
        if run.alert_id != enrichment.alert_id:
            raise ValueError("authorization enrichment does not belong to its referenced analysis run")
        review_item = self._get_open_review_item(enrichment)

        _validate_eligibility(enrichment)
        detection_truth = _detection_truth_snapshot(run)
        if detection_truth.verdict is not Verdict.TRUE_POSITIVE:
            raise DispositionProposalIneligibleError(
                "detection_truth_not_true_positive",
                "closed_benign_true_positive requires current detection truth=true_positive",
            )

        proposal_key = disposition_proposal_key(
            enrichment_id=enrichment.enrichment_id,
            query_hash=enrichment.query_hash,
            matcher_policy_version=enrichment.matcher_policy_version,
            fact_refs=[item.model_dump(mode="json") for item in enrichment.match_result.matched_fact_refs],
            detection_truth=detection_truth,
        )
        existing = repository.find_disposition_proposal_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            _validate_idempotent_proposal(existing, command, proposal_key=proposal_key)
            return SocDispositionProposalApplyResult(proposal=existing, idempotent=True)
        semantic_existing = repository.find_disposition_proposal_by_key(proposal_key)
        if semantic_existing is not None:
            raise DispositionProposalIdempotencyConflictError("semantic disposition proposal already exists under a different idempotency key")

        request_context = context or ServiceRequestContext()
        proposal = SocDispositionProposalRecord(
            proposal_key=proposal_key,
            run_id=run.run_id,
            alert_id=run.alert_id,
            queue_id=review_item.queue_id,
            source_enrichment_id=enrichment.enrichment_id,
            source_query_hash=enrichment.query_hash,
            source_matcher_policy_version=enrichment.matcher_policy_version,
            source_fact_refs=enrichment.match_result.matched_fact_refs,
            source_evidence_refs=enrichment.match_result.evidence_refs,
            detection_truth=detection_truth,
            proposed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            reason_code=SocDispositionProposalReasonCode.AUTHORIZED_ACTIVITY_EXACT_MATCH,
            rationale=[
                "A persisted deterministic authorization enrichment matched every required scope exactly.",
                "Detection truth remains true_positive; only the operational closure category is proposed.",
                "Shadow mode requires an analyst to review and close the case manually.",
            ],
            idempotency_key=command.idempotency_key,
            created_by=request_context.actor,
        )
        try:
            repository.save_disposition_proposal(proposal)
        except DispositionProposalConflictError:
            concurrent = repository.find_disposition_proposal_by_idempotency_key(command.idempotency_key)
            if concurrent is not None:
                _validate_idempotent_proposal(concurrent, command, proposal_key=proposal_key)
                return SocDispositionProposalApplyResult(proposal=concurrent, idempotent=True)
            concurrent = repository.find_disposition_proposal_by_key(proposal_key)
            if concurrent is not None:
                raise DispositionProposalIdempotencyConflictError("semantic disposition proposal already exists under a different idempotency key")
            raise

        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.DISPOSITION_PROPOSAL_RECORDED,
                request_id=request_context.request_id,
                run_id=proposal.run_id,
                alert_id=proposal.alert_id,
                actor=request_context.actor,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "queue_id": proposal.queue_id,
                    "source_enrichment_id": proposal.source_enrichment_id,
                    "proposed_disposition": proposal.proposed_disposition.value,
                    "reason_code": proposal.reason_code.value,
                    "policy_version": proposal.policy_version,
                    "proposal_mode": proposal.proposal_mode,
                    "auto_close_allowed": False,
                    "detection_truth_impact": "none",
                    "review_queue_impact": "none",
                },
            )
        )
        return SocDispositionProposalApplyResult(
            proposal=proposal,
            idempotent=False,
            event_written=True,
        )

    def get(self, proposal_id: str) -> SocDispositionProposalRecord:
        proposal = self._require_repository().get_disposition_proposal(proposal_id)
        if proposal is None:
            raise SocServiceNotFoundError(f"disposition proposal {proposal_id} not found")
        return proposal

    def list(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        enrichment_id: str | None = None,
        limit: int = 50,
    ) -> list[SocDispositionProposalRecord]:
        return self._require_repository().list_disposition_proposals(
            run_id=run_id,
            alert_id=alert_id,
            queue_id=queue_id,
            enrichment_id=enrichment_id,
            limit=limit,
        )

    def _require_repository(self) -> SocDispositionProposalRepository:
        if self._repository is None:
            raise SocServiceNotImplementedError("disposition proposal requires a SocDispositionProposalRepository")
        return self._repository

    def _get_enrichment(self, enrichment_id: str) -> AuthorizationEnrichmentRecord:
        if self._authorization_enrichment_repository is None:
            raise SocServiceNotImplementedError("disposition proposal requires an AuthorizationEnrichmentRepository")
        enrichment = self._authorization_enrichment_repository.get_authorization_enrichment(enrichment_id)
        if enrichment is None:
            raise SocServiceNotFoundError(f"authorization enrichment {enrichment_id} not found")
        return enrichment

    def _get_open_review_item(
        self,
        enrichment: AuthorizationEnrichmentRecord,
    ) -> ReviewQueueItem:
        if enrichment.queue_id is None:
            raise DispositionProposalIneligibleError(
                "review_queue_missing",
                "shadow disposition proposal requires an explicit review queue lineage",
            )
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("disposition proposal requires a ReviewQueueRepository")
        item = self._review_queue_repository.get_review_item(enrichment.queue_id)
        if item is None:
            raise DispositionProposalIneligibleError(
                "review_queue_not_found",
                f"review queue item {enrichment.queue_id} was not found",
            )
        if item.run_id != enrichment.run_id or item.alert_id != enrichment.alert_id:
            raise DispositionProposalIneligibleError(
                "review_queue_lineage_mismatch",
                "review queue item does not belong to the authorization enrichment run and alert",
            )
        if item.status is not ReviewQueueStatus.OPEN:
            raise DispositionProposalIneligibleError(
                "review_queue_not_open",
                "shadow disposition proposal requires an open review queue item",
            )
        return item

    def _get_run(self, run_id: str) -> AnalysisRun:
        if self._alert_repository is None:
            raise SocServiceNotImplementedError("disposition proposal requires an AlertRepository")
        run = self._alert_repository.get_run(run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {run_id} not found")
        return run


def disposition_proposal_key(
    *,
    enrichment_id: str,
    query_hash: str,
    matcher_policy_version: str,
    fact_refs: list[dict[str, object]],
    detection_truth: SocDetectionTruthSnapshot,
) -> str:
    """Hash the evidence and detection snapshot that support one proposal."""

    return stable_hash(
        {
            "source_enrichment_id": enrichment_id,
            "source_query_hash": query_hash,
            "source_matcher_policy_version": matcher_policy_version,
            "source_fact_refs": fact_refs,
            "detection_truth": detection_truth.model_dump(mode="json"),
            "proposed_disposition": SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE.value,
            "reason_code": SocDispositionProposalReasonCode.AUTHORIZED_ACTIVITY_EXACT_MATCH.value,
            "policy_version": SOC_DISPOSITION_PROPOSAL_POLICY_VERSION,
            "proposal_mode": "shadow",
        }
    )


def _validate_eligibility(enrichment: AuthorizationEnrichmentRecord) -> None:
    if enrichment.shadow_only is not True or enrichment.decision_impact != "none":
        raise DispositionProposalIneligibleError(
            "invalid_enrichment_boundary",
            "authorization enrichment must retain shadow_only=true and decision_impact=none",
        )
    if enrichment.match_result.status is not AuthorizationMatchStatus.EXACT:
        raise DispositionProposalIneligibleError(
            "authorization_match_not_exact",
            f"authorization status {enrichment.match_result.status.value} cannot produce a disposition proposal",
        )
    if not enrichment.match_result.matched_fact_refs:
        raise DispositionProposalIneligibleError(
            "authorization_fact_refs_missing",
            "exact authorization enrichment must reference at least one governed fact version",
        )


def _detection_truth_snapshot(run: AnalysisRun) -> SocDetectionTruthSnapshot:
    if run.decision is not None:
        return SocDetectionTruthSnapshot(
            verdict=run.decision.verdict,
            confidence=run.decision.confidence,
            source="decision",
            decision_policy_version=run.decision.policy_version,
            latest_correction_id=run.corrections[-1].correction_id if run.corrections else None,
        )
    if run.analysis is not None:
        return SocDetectionTruthSnapshot(
            verdict=run.analysis.verdict,
            confidence=run.analysis.confidence,
            source="analysis",
            latest_correction_id=run.corrections[-1].correction_id if run.corrections else None,
        )
    raise DispositionProposalIneligibleError(
        "detection_truth_unavailable",
        "analysis run has no detection decision or analysis result",
    )


def _validate_idempotent_proposal(
    existing: SocDispositionProposalRecord,
    command: SocDispositionProposalCommand,
    *,
    proposal_key: str,
) -> None:
    if existing.source_enrichment_id != command.enrichment_id or existing.proposal_key != proposal_key:
        raise DispositionProposalIdempotencyConflictError("disposition proposal idempotency key was reused for different input")


__all__ = [
    "DispositionProposalIdempotencyConflictError",
    "DispositionProposalIneligibleError",
    "SOC_DISPOSITION_PROPOSAL_POLICY_VERSION",
    "SocDispositionProposalService",
    "disposition_proposal_key",
]
