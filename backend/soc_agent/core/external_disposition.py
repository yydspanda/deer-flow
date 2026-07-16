"""Core service boundary for external disposition feedback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import (
    ActorContext,
    AuditAction,
    CorrectionCommand,
    DecisionAuditRecord,
    EvidenceItem,
    ServiceRequestContext,
    SocDispositionOutcomeCommand,
    SocDispositionOutcomeReviewKind,
    SocDispositionOutcomeSource,
    SocEvent,
    SocEventType,
    SocExternalDispositionApplyResult,
    SocExternalDispositionApplyStatus,
    SocExternalDispositionCanonicalStatus,
    SocExternalDispositionEvent,
    SocExternalDispositionMappingConfig,
    SocExternalDispositionRecord,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.external_disposition import build_external_disposition_idempotency_key, resolve_external_disposition_status
from soc_agent.protocols import (
    AlertRepository,
    AlertSummaryRepository,
    DecisionAuditRepository,
    ReviewQueueRepository,
    SocDispositionProposalRepository,
    SocEventSink,
    SocExternalDispositionRepository,
)

from .disposition_evaluation import (
    DispositionEvaluationIneligibleError,
    SocDispositionEvaluationService,
)
from .service import (
    NoopEventSink,
    SocMemoryService,
    SocReviewService,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)


@dataclass(frozen=True)
class _LocatedExternalDispositionTarget:
    run_id: str | None = None
    alert_id: str | None = None
    queue_id: str | None = None
    matched_by: str | None = None


@dataclass(frozen=True)
class _DispositionOutcomeBridgeResult:
    recorded: bool = False
    outcome_id: str | None = None
    idempotent: bool = False
    skip_reason: str | None = None


_VERIFIED_TARGET_MATCHES = frozenset({"soc_queue_id", "soc_run_id", "external_case_binding"})


class SocExternalDispositionService:
    """Apply external ticket/case disposition feedback through one service boundary."""

    def __init__(
        self,
        *,
        repository: SocExternalDispositionRepository | None = None,
        mapping_config: SocExternalDispositionMappingConfig | None = None,
        alert_repository: AlertRepository | None = None,
        summary_repository: AlertSummaryRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        audit_repository: DecisionAuditRepository | None = None,
        event_sink: SocEventSink | None = None,
        memory_service: SocMemoryService | None = None,
        disposition_proposal_repository: SocDispositionProposalRepository | None = None,
        disposition_evaluation_service: SocDispositionEvaluationService | None = None,
    ) -> None:
        self._repository = repository
        self._mapping_config = mapping_config or SocExternalDispositionMappingConfig()
        self._alert_repository = alert_repository
        self._summary_repository = summary_repository
        self._review_queue_repository = review_queue_repository
        self._audit_repository = audit_repository
        self._event_sink = event_sink or NoopEventSink()
        self._memory_service = memory_service
        self._disposition_proposal_repository = disposition_proposal_repository
        self._disposition_evaluation_service = disposition_evaluation_service

    def apply_event(
        self,
        event: SocExternalDispositionEvent | dict[str, Any],
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocExternalDispositionApplyResult:
        """Persist one external disposition event and apply configured feedback boundaries."""

        if self._repository is None:
            raise SocServiceNotImplementedError("apply_event requires a SocExternalDispositionRepository")

        request_context = context or ServiceRequestContext()
        external_event = SocExternalDispositionEvent.model_validate(event)
        idempotency_key = build_external_disposition_idempotency_key(external_event)
        existing = self._repository.find_external_disposition_by_idempotency_key(idempotency_key)
        if existing is not None:
            bridge = self._capture_disposition_outcome(
                existing,
                target=_target_from_record(existing),
                trust_level=str(existing.metadata.get("mapping_trust_level") or "low"),
                context=request_context,
            )
            return SocExternalDispositionApplyResult(
                record=existing,
                idempotent=True,
                audit_written=False,
                disposition_outcome_recorded=bridge.recorded,
                disposition_outcome_id=bridge.outcome_id,
                disposition_outcome_idempotent=bridge.idempotent,
                disposition_outcome_skip_reason=bridge.skip_reason,
            )

        status_mapping = resolve_external_disposition_status(external_event, self._mapping_config)
        target = self._locate_target(external_event)
        apply_status, apply_reason = _apply_status_and_reason(status_mapping.canonical_status, status_mapping.apply_to_review, target)
        correction_id = self._apply_review_correction(
            external_event,
            target=target,
            trust_level=status_mapping.trust_level,
            canonical_status=status_mapping.canonical_status,
            apply_status=apply_status,
            context=request_context,
        )

        record = SocExternalDispositionRecord(
            event=external_event,
            canonical_status=status_mapping.canonical_status,
            apply_status=apply_status,
            idempotency_key=idempotency_key,
            target_run_id=target.run_id if target is not None else None,
            target_alert_id=target.alert_id if target is not None else None,
            target_queue_id=target.queue_id if target is not None else None,
            matched_by=target.matched_by if target is not None else None,
            apply_reason=apply_reason,
            correction_id=correction_id,
            metadata={
                "mapping_trust_level": status_mapping.trust_level,
                "mapping_notes": status_mapping.notes,
                "request_id": request_context.request_id,
                "correction_applied": correction_id is not None,
            },
        )
        memory_candidate_id = self._propose_memory_candidate(
            record,
            target=target,
            trust_level=status_mapping.trust_level,
            apply_status=apply_status,
            correction_id=correction_id,
            context=request_context,
        )
        record = record.model_copy(
            update={
                "memory_candidate_id": memory_candidate_id,
                "metadata": {
                    **record.metadata,
                    "memory_candidate_created": memory_candidate_id is not None,
                },
            }
        )
        audit_record = self._build_audit_record(
            external_event,
            target=target,
            actor=request_context.actor,
            idempotency_key=idempotency_key,
            canonical_status=status_mapping.canonical_status,
            apply_status=apply_status,
            correction_id=correction_id,
            memory_candidate_id=memory_candidate_id,
        )
        record = record.model_copy(update={"audit_id": audit_record.audit_id if audit_record is not None else None})
        self._repository.save_external_disposition(record)
        outcome_bridge = self._capture_disposition_outcome(
            record,
            target=target,
            trust_level=status_mapping.trust_level,
            context=request_context,
        )
        if audit_record is not None and self._audit_repository is not None:
            audit_record = audit_record.model_copy(
                update={
                    "payload": {
                        **audit_record.payload,
                        "disposition_outcome_id": outcome_bridge.outcome_id,
                        "disposition_outcome_recorded": outcome_bridge.recorded,
                        "disposition_outcome_skip_reason": outcome_bridge.skip_reason,
                    }
                }
            )
            self._audit_repository.save_audit_record(audit_record)
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.EXTERNAL_DISPOSITION_RECEIVED,
                request_id=request_context.request_id,
                run_id=record.target_run_id,
                alert_id=record.target_alert_id,
                actor=request_context.actor,
                payload={
                    "disposition_id": record.disposition_id,
                    "external_system": external_event.external_system,
                    "external_case_id": external_event.external_case_id,
                    "canonical_status": record.canonical_status.value,
                    "apply_status": record.apply_status.value,
                    "idempotency_key": record.idempotency_key,
                    "disposition_outcome_id": outcome_bridge.outcome_id,
                    "disposition_outcome_recorded": outcome_bridge.recorded,
                    "disposition_outcome_skip_reason": outcome_bridge.skip_reason,
                },
            )
        )
        return SocExternalDispositionApplyResult(
            record=record,
            idempotent=False,
            audit_written=audit_record is not None and self._audit_repository is not None,
            correction_applied=correction_id is not None,
            memory_candidate_created=memory_candidate_id is not None,
            disposition_outcome_recorded=outcome_bridge.recorded,
            disposition_outcome_id=outcome_bridge.outcome_id,
            disposition_outcome_idempotent=outcome_bridge.idempotent,
            disposition_outcome_skip_reason=outcome_bridge.skip_reason,
        )

    def _capture_disposition_outcome(
        self,
        record: SocExternalDispositionRecord,
        *,
        target: _LocatedExternalDispositionTarget | None,
        trust_level: str,
        context: ServiceRequestContext,
    ) -> _DispositionOutcomeBridgeResult:
        service = self._disposition_evaluation_service
        proposal_repository = self._disposition_proposal_repository
        if service is None or proposal_repository is None:
            return _DispositionOutcomeBridgeResult(skip_reason="disposition outcome bridge is not configured")
        if trust_level != "high":
            return _DispositionOutcomeBridgeResult(skip_reason="external disposition mapping is not high trust")
        if record.apply_status is not SocExternalDispositionApplyStatus.MAPPED:
            return _DispositionOutcomeBridgeResult(skip_reason="external disposition is not mapped")
        if record.canonical_status is SocExternalDispositionCanonicalStatus.UNKNOWN:
            return _DispositionOutcomeBridgeResult(skip_reason="external disposition has no terminal canonical status")
        if target is None or target.queue_id is None or target.matched_by not in _VERIFIED_TARGET_MATCHES:
            return _DispositionOutcomeBridgeResult(skip_reason="external disposition has no verified ReviewQueue target")

        proposals = proposal_repository.list_disposition_proposals(queue_id=target.queue_id, limit=3)
        matching = [proposal for proposal in proposals if proposal.run_id == target.run_id and proposal.alert_id == target.alert_id]
        if len(matching) != 1:
            return _DispositionOutcomeBridgeResult(skip_reason=f"expected one disposition proposal for verified target, found {len(matching)}")
        proposal = matching[0]
        idempotency_key = f"outcome:external:{record.disposition_id}"
        try:
            existing_outcomes = service.list_outcomes(
                proposal_id=proposal.proposal_id,
                review_kind=SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION,
                limit=100,
            )
        except SocServiceNotImplementedError as exc:
            return _DispositionOutcomeBridgeResult(skip_reason=str(exc))
        duplicate = next(
            (item for item in existing_outcomes if item.idempotency_key == idempotency_key),
            None,
        )
        if duplicate is not None:
            return _DispositionOutcomeBridgeResult(
                recorded=True,
                outcome_id=duplicate.outcome_id,
                idempotent=True,
            )

        latest = existing_outcomes[0] if existing_outcomes else None
        if latest is not None and latest.source is not SocDispositionOutcomeSource.EXTERNAL_DISPOSITION:
            return _DispositionOutcomeBridgeResult(skip_reason=(f"latest primary outcome {latest.outcome_id} is not external; an explicit analyst supersession is required"))
        try:
            result = service.record_outcome(
                SocDispositionOutcomeCommand(
                    proposal_id=proposal.proposal_id,
                    observed_disposition=record.canonical_status,
                    review_kind=SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION,
                    source=SocDispositionOutcomeSource.EXTERNAL_DISPOSITION,
                    source_ref=record.disposition_id,
                    reason=_external_outcome_reason(record),
                    evidence_refs=_external_outcome_evidence_refs(record),
                    supersedes_outcome_id=latest.outcome_id if latest is not None else None,
                    idempotency_key=idempotency_key,
                ),
                context=context,
            )
        except (
            DispositionEvaluationIneligibleError,
            SocServiceNotFoundError,
            SocServiceNotImplementedError,
        ) as exc:
            return _DispositionOutcomeBridgeResult(skip_reason=str(exc))
        return _DispositionOutcomeBridgeResult(
            recorded=True,
            outcome_id=result.outcome.outcome_id,
            idempotent=result.idempotent,
        )

    def _propose_memory_candidate(
        self,
        record: SocExternalDispositionRecord,
        *,
        target: _LocatedExternalDispositionTarget | None,
        trust_level: str,
        apply_status: SocExternalDispositionApplyStatus,
        correction_id: str | None,
        context: ServiceRequestContext,
    ) -> str | None:
        if self._memory_service is None:
            return None
        reason = record.event.external_reason.strip() if record.event.external_reason else ""
        if not reason:
            return None
        if apply_status is not SocExternalDispositionApplyStatus.MAPPED or target is None:
            return None
        candidate_type = _memory_candidate_type_for_external_status(record.canonical_status)
        if candidate_type is None:
            return None

        candidate = self._memory_service.propose_candidate(
            SocMemoryCandidateCreateCommand(
                candidate_type=candidate_type,
                target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
                summary=_external_memory_candidate_summary(record),
                content=reason,
                tenant_scope=record.event.tenant_id or "global",
                tenant_id=record.event.tenant_id,
                source=SocMemoryCandidateSource(
                    source_type=SocMemoryCandidateSourceType.EXTERNAL_DISPOSITION,
                    source_id=record.disposition_id,
                    run_id=target.run_id,
                    alert_id=target.alert_id,
                    queue_id=target.queue_id,
                    correction_id=correction_id,
                    metadata={
                        "external_system": record.event.external_system,
                        "external_case_id": record.event.external_case_id,
                        "external_status": record.event.external_status,
                        "canonical_status": record.canonical_status.value,
                        "apply_status": apply_status.value,
                        "mapping_trust_level": trust_level,
                    },
                ),
                evidence_refs=_memory_candidate_evidence_refs(record, target),
                validity=SocMemoryCandidateValidity(
                    notes="External disposition reason is operator feedback and must be reviewed before reuse.",
                ),
                idempotency_key=f"memory_candidate:{record.idempotency_key}",
                confidence=_memory_candidate_confidence(trust_level),
                facets=_memory_candidate_facets(record, trust_level=trust_level, apply_status=apply_status),
                decision_impact=_memory_candidate_decision_impact(record.canonical_status),
                review_owner="soc_analyst",
                labels=[
                    "external-disposition",
                    "candidate-only",
                    record.event.external_system,
                    record.canonical_status.value,
                ],
                metadata={
                    "external_system": record.event.external_system,
                    "external_case_id": record.event.external_case_id,
                    "source_event_id": record.event.source_event_id,
                    "source_version": record.event.source_version,
                    "raw_payload_hash": record.event.raw_payload_hash,
                    "matched_by": record.matched_by,
                    "correction_id": correction_id,
                    "runtime_decision_allowed": False,
                },
            ),
            context=context,
        )
        return candidate.candidate_id

    def _apply_review_correction(
        self,
        event: SocExternalDispositionEvent,
        *,
        target: _LocatedExternalDispositionTarget | None,
        trust_level: str,
        canonical_status: SocExternalDispositionCanonicalStatus,
        apply_status: SocExternalDispositionApplyStatus,
        context: ServiceRequestContext,
    ) -> str | None:
        verdict = _verdict_for_external_status(canonical_status)
        if (
            verdict is None
            or trust_level != "high"
            or apply_status is not SocExternalDispositionApplyStatus.MAPPED
            or target is None
            or target.run_id is None
            or target.matched_by not in _VERIFIED_TARGET_MATCHES
            or self._alert_repository is None
        ):
            return None
        review_service = SocReviewService(
            repository=self._alert_repository,
            summary_repository=self._summary_repository,
            audit_repository=self._audit_repository,
            review_queue_repository=self._review_queue_repository,
            event_sink=self._event_sink,
        )
        run = review_service.correct(
            CorrectionCommand(
                run_id=target.run_id,
                corrected_verdict=verdict,
                corrected_confidence=0.95,
                reason=_external_correction_reason(event, canonical_status),
                evidence=[
                    EvidenceItem(
                        source="external_disposition",
                        description=f"{event.external_system}:{event.external_case_id}",
                        value=event.external_status,
                    )
                ],
            ),
            context=context,
        )
        return run.corrections[-1].correction_id if run.corrections else None

    def _locate_target(self, event: SocExternalDispositionEvent) -> _LocatedExternalDispositionTarget | None:
        if event.soc_queue_id is not None and self._review_queue_repository is not None:
            item = self._review_queue_repository.get_review_item(event.soc_queue_id)
            if item is not None:
                return _LocatedExternalDispositionTarget(
                    run_id=item.run_id,
                    alert_id=item.alert_id,
                    queue_id=item.queue_id,
                    matched_by="soc_queue_id",
                )

        if event.soc_run_id is not None and self._review_queue_repository is not None:
            item = self._review_queue_repository.get_open_review_item_by_run(event.soc_run_id)
            if item is not None:
                return _LocatedExternalDispositionTarget(
                    run_id=item.run_id,
                    alert_id=item.alert_id,
                    queue_id=item.queue_id,
                    matched_by="soc_run_id",
                )

        if event.soc_run_id is not None and self._alert_repository is not None:
            run = self._alert_repository.get_run(event.soc_run_id)
            if run is not None:
                return _LocatedExternalDispositionTarget(
                    run_id=run.run_id,
                    alert_id=run.alert_id,
                    queue_id=event.soc_queue_id,
                    matched_by="soc_run_id",
                )

        if event.soc_run_id is not None or event.soc_alert_id is not None or event.soc_queue_id is not None:
            return _LocatedExternalDispositionTarget(
                run_id=event.soc_run_id,
                alert_id=event.soc_alert_id,
                queue_id=event.soc_queue_id,
                matched_by="explicit_unverified_refs",
            )

        previous = self._repository.list_external_dispositions(
            external_system=event.external_system,
            external_case_id=event.external_case_id,
            limit=10,
        )
        for record in previous:
            if record.target_run_id or record.target_alert_id or record.target_queue_id:
                return _LocatedExternalDispositionTarget(
                    run_id=record.target_run_id,
                    alert_id=record.target_alert_id,
                    queue_id=record.target_queue_id,
                    matched_by="external_case_binding",
                )
        return None

    def _build_audit_record(
        self,
        event: SocExternalDispositionEvent,
        *,
        target: _LocatedExternalDispositionTarget | None,
        actor: ActorContext,
        idempotency_key: str,
        canonical_status: SocExternalDispositionCanonicalStatus,
        apply_status: SocExternalDispositionApplyStatus,
        correction_id: str | None,
        memory_candidate_id: str | None,
    ) -> DecisionAuditRecord | None:
        if target is None or target.run_id is None or target.alert_id is None:
            return None
        return DecisionAuditRecord(
            action=AuditAction.EXTERNAL_DISPOSITION,
            run_id=target.run_id,
            alert_id=target.alert_id,
            actor=actor,
            payload={
                "external_system": event.external_system,
                "external_case_id": event.external_case_id,
                "external_status": event.external_status,
                "canonical_status": canonical_status.value,
                "apply_status": apply_status.value,
                "correction_id": correction_id,
                "memory_candidate_id": memory_candidate_id,
                "idempotency_key": idempotency_key,
                "external_reason_present": bool(event.external_reason),
                "source_event_id": event.source_event_id,
                "source_version": event.source_version,
                "raw_payload_hash": event.raw_payload_hash,
                "matched_by": target.matched_by,
            },
        )


def _target_from_record(record: SocExternalDispositionRecord) -> _LocatedExternalDispositionTarget:
    return _LocatedExternalDispositionTarget(
        run_id=record.target_run_id,
        alert_id=record.target_alert_id,
        queue_id=record.target_queue_id,
        matched_by=record.matched_by,
    )


def _external_outcome_reason(record: SocExternalDispositionRecord) -> str:
    reason = record.event.external_reason.strip() if record.event.external_reason else ""
    if reason:
        return reason
    return f"Trusted external disposition {record.event.external_system}:{record.event.external_case_id} reported {record.event.external_status}."


def _external_outcome_evidence_refs(record: SocExternalDispositionRecord) -> list[str]:
    refs = [
        f"external_disposition:{record.disposition_id}",
        f"external_case:{record.event.external_system}:{record.event.external_case_id}",
    ]
    if record.event.source_event_id:
        refs.append(f"external_event:{record.event.external_system}:{record.event.source_event_id}")
    return refs


def _apply_status_and_reason(
    canonical_status: SocExternalDispositionCanonicalStatus,
    apply_to_review: bool,
    target: _LocatedExternalDispositionTarget | None,
) -> tuple[SocExternalDispositionApplyStatus, str]:
    if canonical_status is SocExternalDispositionCanonicalStatus.UNKNOWN:
        return SocExternalDispositionApplyStatus.UNMATCHED, "external status is unmapped"
    if target is None:
        return SocExternalDispositionApplyStatus.UNMATCHED, "no unique local target was found"
    if not apply_to_review:
        return SocExternalDispositionApplyStatus.IGNORED, "mapping is configured as non-applying"
    return SocExternalDispositionApplyStatus.MAPPED, "external status mapped to a unique local target"


def _verdict_for_external_status(status: SocExternalDispositionCanonicalStatus) -> Verdict | None:
    if status is SocExternalDispositionCanonicalStatus.CLOSED_TRUE_POSITIVE:
        return Verdict.TRUE_POSITIVE
    if status is SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE:
        return Verdict.FALSE_POSITIVE
    if status is SocExternalDispositionCanonicalStatus.CLOSED_BENIGN_TRUE_POSITIVE:
        return Verdict.TRUE_POSITIVE
    return None


def _memory_candidate_type_for_external_status(status: SocExternalDispositionCanonicalStatus) -> SocMemoryCandidateType | None:
    if status in {
        SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE,
        SocExternalDispositionCanonicalStatus.CLOSED_BENIGN_TRUE_POSITIVE,
        SocExternalDispositionCanonicalStatus.SUPPRESSED,
        SocExternalDispositionCanonicalStatus.IGNORED,
    }:
        return SocMemoryCandidateType.BENIGN_PATTERN
    if status in {
        SocExternalDispositionCanonicalStatus.CLOSED_TRUE_POSITIVE,
        SocExternalDispositionCanonicalStatus.ESCALATED,
    }:
        return SocMemoryCandidateType.DETECTION_LESSON
    return None


def _memory_candidate_decision_impact(status: SocExternalDispositionCanonicalStatus) -> SocMemoryDecisionImpact:
    if status is SocExternalDispositionCanonicalStatus.SUPPRESSED:
        return SocMemoryDecisionImpact.SUPPRESSION_HINT
    return SocMemoryDecisionImpact.REVIEW_HINT


def _memory_candidate_confidence(trust_level: str) -> float:
    if trust_level == "high":
        return 0.65
    if trust_level == "medium":
        return 0.5
    return 0.35


def _external_memory_candidate_summary(record: SocExternalDispositionRecord) -> str:
    return f"External disposition feedback from {record.event.external_system}: {record.canonical_status.value}"


def _memory_candidate_facets(
    record: SocExternalDispositionRecord,
    *,
    trust_level: str,
    apply_status: SocExternalDispositionApplyStatus,
) -> dict[str, list[str]]:
    facets = {
        "source_type": ["external_disposition"],
        "external_system": [record.event.external_system],
        "canonical_status": [record.canonical_status.value],
        "apply_status": [apply_status.value],
        "mapping_trust_level": [trust_level],
    }
    if record.event.tenant_id:
        facets["tenant"] = [record.event.tenant_id]
    if record.target_alert_id:
        facets["alert_id"] = [record.target_alert_id]
    if record.target_run_id:
        facets["run_id"] = [record.target_run_id]
    return facets


def _memory_candidate_evidence_refs(
    record: SocExternalDispositionRecord,
    target: _LocatedExternalDispositionTarget,
) -> list[str]:
    refs = [f"external_disposition:{record.disposition_id}"]
    if target.run_id:
        refs.append(f"run:{target.run_id}")
    if target.alert_id:
        refs.append(f"alert:{target.alert_id}")
    if target.queue_id:
        refs.append(f"review_queue:{target.queue_id}")
    return refs


def _external_correction_reason(event: SocExternalDispositionEvent, canonical_status: SocExternalDispositionCanonicalStatus) -> str:
    reason = event.external_reason or "external disposition synchronized"
    return f"external disposition {event.external_system}:{event.external_case_id} -> {canonical_status.value}: {reason}"
