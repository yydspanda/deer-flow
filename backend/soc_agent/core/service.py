"""Stable public service entry points for SOC Agent use cases."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Collection, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from soc_agent.context_bridge import skill_context_from_investigation_context
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertInput,
    AlertSourceType,
    AlertSummary,
    AnalysisRun,
    AnalysisRunStatus,
    AuditAction,
    CorrectionCommand,
    CorrectionRecord,
    CorrelationQuery,
    CorrelationResult,
    Decision,
    DecisionAuditRecord,
    DecisionReviewReason,
    EntrySurface,
    ExtractionReport,
    InvestigationContext,
    InvestigationEvidence,
    InvestigationTimelineItem,
    MessageSchemaStatus,
    NormalizationDriftReport,
    NormalizationDriftSample,
    NormalizationInspectionResult,
    NormalizationMonitoringResult,
    NormalizationReport,
    ReviewNoteCommand,
    ReviewNoteResult,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueuePriority,
    ReviewQueueStatus,
    ServiceRequestContext,
    SimilarAlertQuery,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentApprovedActionCommand,
    SocAgentChatRequest,
    SocAgentChatResponse,
    SocAgentPermissionDecision,
    SocAgentRiskLevel,
    SocAgentRouteDecision,
    SocAgentStreamEvent,
    SocDaemonMessage,
    SocDaemonProcessResult,
    SocDispositionProposalRecord,
    SocDomainTriageRequest,
    SocDomainTriageResult,
    SocEvent,
    SocEventType,
    SocMemoryCandidate,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateReviewResult,
    SocMemoryCandidateStatus,
    SocMemoryMatch,
    SocMemoryQuery,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMemoryRetrievalResult,
    SocSkillResolution,
    UnifiedInvestigationView,
    Verdict,
)
from soc_agent.core.runtime import analyze_alert, build_analysis_request_for_payload, inspect_alert_normalization
from soc_agent.memory import SocMemoryCandidateSourceBridge
from soc_agent.normalizers import load_mapping_config, normalize_alert_payload
from soc_agent.protocols import (
    AlertRepository,
    AlertSummaryRepository,
    AnalysisPersistence,
    AnalysisRuntime,
    AuthorizationEnrichmentRepository,
    DecisionAuditRepository,
    DecisionPolicy,
    InvestigationEvidenceRepository,
    LLMAnalyzer,
    MemoryCandidateRepository,
    MemoryRecordRepository,
    NormalizationMaintenanceMonitor,
    ReviewQueueRepository,
    SocActionAdapterRegistryPort,
    SocAgentApprovalGrantRepository,
    SocAgentApprovalRequestRepository,
    SocDispositionProposalRepository,
    SocEventSink,
    SocExternalDispositionRepository,
)
from soc_agent.skills import SocSkillResolver


class SocServiceError(RuntimeError):
    """Base error for service-layer failures."""


class SocServiceNotImplementedError(SocServiceError):
    """Raised when a planned service operation has no Phase 1 implementation."""


class SocServiceNotFoundError(SocServiceError):
    """Raised when a requested SOC resource does not exist."""


class DeterministicAnalysisRuntime:
    """Adapter that exposes the current deterministic runtime as a protocol."""

    def __init__(
        self,
        *,
        analyzer: LLMAnalyzer | None = None,
        decision_policy: DecisionPolicy | None = None,
    ) -> None:
        self._analyzer = analyzer
        self._decision_policy = decision_policy

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun:
        return analyze_alert(
            payload,
            analyzer=self._analyzer,
            decision_policy=self._decision_policy,
        )


class NoopEventSink:
    """Default event sink used until an entry adapter attaches subscribers."""

    def emit(self, event: SocEvent) -> None:
        return None


class SocAnalysisService:
    """Application service used by DeerFlow-aligned SOC entry adapters.

    TUI/headless CLI, Gateway API, Web UI, IM channels, and background ingestion
    call this service instead of directly assembling pipeline steps or touching
    repositories/adapters.
    """

    def __init__(
        self,
        *,
        runtime: AnalysisRuntime | None = None,
        repository: AlertRepository | None = None,
        summary_repository: AlertSummaryRepository | None = None,
        audit_repository: DecisionAuditRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        analysis_persistence: AnalysisPersistence | None = None,
        normalization_maintenance_monitor: NormalizationMaintenanceMonitor | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._runtime = runtime or DeterministicAnalysisRuntime()
        self._repository = repository
        self._summary_repository = summary_repository
        self._audit_repository = audit_repository
        self._review_queue_repository = review_queue_repository
        self._analysis_persistence = analysis_persistence
        self._normalization_maintenance_monitor = normalization_maintenance_monitor
        self._event_sink = event_sink or NoopEventSink()

    def analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        request_context = context or ServiceRequestContext()
        return self._analyze(payload, context=request_context)

    def get_run(self, run_id: str) -> AnalysisRun | None:
        if self._repository is None:
            raise SocServiceNotImplementedError("get_run requires an AlertRepository")
        return self._repository.get_run(run_id)

    def replay(
        self,
        run_id: str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        if self._repository is None:
            raise SocServiceNotImplementedError("replay requires an AlertRepository")
        previous = self._repository.get_run(run_id)
        if previous is None:
            raise SocServiceNotFoundError(f"run {run_id} not found")
        if previous.input_payload is None:
            raise SocServiceNotImplementedError(f"run {run_id} has no replayable input payload")

        request_context = context or ServiceRequestContext()
        return self._analyze(previous.input_payload, context=request_context, replay_of_run_id=run_id)

    def _analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext,
        replay_of_run_id: str | None = None,
    ) -> AnalysisRun:
        audit_action = AuditAction.REPLAY if replay_of_run_id else AuditAction.ANALYSIS
        self._emit(
            SocEvent(
                event_type=SocEventType.ANALYSIS_REQUESTED,
                request_id=context.request_id,
                actor=context.actor,
                payload={
                    "surface": context.actor.surface.value,
                    "replay_of_run_id": replay_of_run_id,
                },
            )
        )

        if existing_run := self._find_existing_idempotent_run(context, action=audit_action):
            self._emit_analysis_completion(existing_run, context=context, replay_of_run_id=replay_of_run_id, idempotent_replay=True)
            return existing_run

        run = self._runtime.analyze(payload)
        run.replay_of_run_id = replay_of_run_id
        summary = _alert_summary_from_run(run)
        audit_record = _analysis_audit_record(
            run,
            actor=context.actor,
            action=audit_action,
            idempotency_key=context.idempotency_key,
        )
        review_item = _review_queue_item_from_summary(summary)
        if self._analysis_persistence is not None:
            self._analysis_persistence.save_analysis_bundle(
                run=run,
                summary=summary,
                review_item=review_item,
                audit_record=audit_record,
            )
        else:
            if self._repository is not None:
                self._repository.save_run(run)
            if self._summary_repository is not None:
                self._summary_repository.save_alert_summary(summary)
            if self._review_queue_repository is not None:
                _upsert_review_queue_item(self._review_queue_repository, summary)
            if self._audit_repository is not None:
                self._audit_repository.save_audit_record(audit_record)

        if self._normalization_maintenance_monitor is not None:
            try:
                run.normalization_monitoring_result = self._normalization_maintenance_monitor.monitor_run(
                    run,
                    context=context,
                )
            except Exception as exc:  # noqa: BLE001 - maintenance monitoring must not fail alert analysis
                run.normalization_monitoring_result = NormalizationMonitoringResult(
                    run_id=run.run_id,
                    alert_id=run.alert_id,
                    warnings=[f"normalization monitoring failed: {type(exc).__name__}"],
                )
            if self._repository is not None:
                self._repository.save_run(run)

        self._emit_analysis_completion(run, context=context, replay_of_run_id=replay_of_run_id, idempotent_replay=False)
        return run

    def _find_existing_idempotent_run(self, context: ServiceRequestContext, *, action: AuditAction) -> AnalysisRun | None:
        if not context.idempotency_key or self._audit_repository is None or self._repository is None:
            return None
        audit_record = self._audit_repository.find_audit_record_by_idempotency_key(context.idempotency_key, action=action.value)
        if audit_record is None:
            return None
        run = self._repository.get_run(audit_record.run_id)
        if run is not None and run.status is AnalysisRunStatus.FAILED and run.failure is not None and run.failure.retryable:
            return None
        return run

    def _emit_analysis_completion(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
        replay_of_run_id: str | None,
        idempotent_replay: bool,
    ) -> None:
        self._emit(
            SocEvent(
                event_type=_completion_event_type(run),
                request_id=context.request_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                actor=context.actor,
                payload={
                    "status": run.status.value,
                    "trace_id": context.trace_id,
                    "idempotency_key": context.idempotency_key,
                    "replay_of_run_id": replay_of_run_id,
                    "idempotent_replay": idempotent_replay,
                },
            )
        )

    def _emit(self, event: SocEvent) -> None:
        self._event_sink.emit(event)


class SocNormalizationService:
    """Inspect-only normalization service for vendor onboarding and drift triage."""

    def __init__(self, *, repository: AlertRepository | None = None) -> None:
        self._repository = repository

    def inspect(
        self,
        payload: Mapping[str, Any],
        *,
        mapping_path: str | Path | None = None,
        mapping_config: Mapping[str, Any] | None = None,
    ) -> NormalizationInspectionResult:
        if mapping_path is not None and mapping_config is not None:
            raise SocServiceError("mapping_path and mapping_config cannot both be provided")
        loaded_mapping = load_mapping_config(mapping_path) if mapping_path is not None else mapping_config
        return inspect_alert_normalization(payload, mapping_config=loaded_mapping)

    def drift(
        self,
        samples: list[tuple[str, Mapping[str, Any]]],
        *,
        mapping_path: str | Path | None = None,
        mapping_config: Mapping[str, Any] | None = None,
        known_schema_fingerprints: Collection[str] | None = None,
    ) -> NormalizationDriftReport:
        if mapping_path is not None and mapping_config is not None:
            raise SocServiceError("mapping_path and mapping_config cannot both be provided")

        loaded_mapping = load_mapping_config(mapping_path) if mapping_path is not None else mapping_config
        sample_reports: list[NormalizationDriftSample] = []
        for sample_path, payload in samples:
            try:
                inspection = self.inspect(payload, mapping_config=loaded_mapping)
            except Exception as exc:  # noqa: BLE001 - preserve per-sample failures in batch report
                sample_reports.append(_drift_failure_sample(sample_path, str(exc)))
                continue

            sample_reports.append(
                _drift_sample_from_reports(
                    path=sample_path,
                    alert_id=inspection.alert.alert_id,
                    normalization=inspection.normalization_report,
                    extraction=inspection.extraction_report,
                )
            )

        return _normalization_drift_report(
            sample_reports,
            known_schema_fingerprints=known_schema_fingerprints,
        )

    def drift_recent(
        self,
        *,
        limit: int = 50,
        known_schema_fingerprints: Collection[str] | None = None,
    ) -> NormalizationDriftReport:
        if self._repository is None:
            raise SocServiceNotImplementedError("drift_recent requires an AlertRepository")

        sample_reports: list[NormalizationDriftSample] = []
        for run in self._repository.list_runs(limit=limit):
            if run.normalization_report is None or run.extraction_report is None:
                sample_reports.append(
                    _drift_failure_sample(
                        f"run:{run.run_id}",
                        "run is missing normalization or extraction reports",
                        run_id=run.run_id,
                        alert_id=run.alert_id,
                    )
                )
                continue
            sample_reports.append(
                _drift_sample_from_reports(
                    path=f"run:{run.run_id}",
                    run_id=run.run_id,
                    alert_id=run.alert_id,
                    normalization=run.normalization_report,
                    extraction=run.extraction_report,
                )
            )

        return _normalization_drift_report(
            sample_reports,
            known_schema_fingerprints=known_schema_fingerprints,
        )


class SocSkillResolutionService:
    """Resolve DeerFlow SOC domain skills through the core service boundary."""

    def __init__(self, *, resolver: SocSkillResolver | None = None) -> None:
        self._resolver = resolver or SocSkillResolver()

    def resolve_payload(self, payload: Mapping[str, Any]) -> SocSkillResolution:
        request = build_analysis_request_for_payload(payload)
        return self._resolver.resolve_for_analysis_request(request)


def _normalization_drift_report(
    sample_reports: list[NormalizationDriftSample],
    *,
    known_schema_fingerprints: Collection[str] | None = None,
) -> NormalizationDriftReport:
    known_fingerprints = None if known_schema_fingerprints is None else set(known_schema_fingerprints)
    adapter_counts: Counter[str] = Counter()
    source_type_counts: Counter[str] = Counter()
    missing_field_counts: Counter[str] = Counter()
    unmapped_field_counts: Counter[str] = Counter()
    entity_kind_counts: Counter[str] = Counter()
    missing_entity_kind_counts: Counter[str] = Counter()
    schema_fingerprint_counts: Counter[str] = Counter()
    novel_schema_fingerprint_counts: Counter[str] = Counter()
    schema_status_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()

    for sample in sample_reports:
        if known_fingerprints is not None:
            sample.novel_schema_fingerprints = sorted(fingerprint for fingerprint in sample.schema_fingerprints if fingerprint not in known_fingerprints)
        if sample.adapter:
            adapter_counts.update([sample.adapter])
        source_type_counts.update([sample.source_type.value])
        missing_field_counts.update(sample.missing_fields)
        unmapped_field_counts.update(sample.unmapped_fields)
        entity_kind_counts.update(sample.entity_counts)
        missing_entity_kind_counts.update(sample.missing_entity_kinds)
        schema_fingerprint_counts.update(sample.schema_fingerprints)
        novel_schema_fingerprint_counts.update(sample.novel_schema_fingerprints)
        schema_status_counts.update(status.value for status in sample.schema_statuses)
        warning_counts.update(sample.warnings)

    suspicious_samples = [
        sample
        for sample in sample_reports
        if sample.status == "failed" or sample.missing_fields or sample.unmapped_fields or sample.novel_schema_fingerprints or any(status is not MessageSchemaStatus.RECOGNIZED for status in sample.schema_statuses)
    ]

    success_count = sum(1 for sample in sample_reports if sample.status == "success")
    return NormalizationDriftReport(
        sample_count=len(sample_reports),
        success_count=success_count,
        failure_count=len(sample_reports) - success_count,
        adapter_counts=dict(adapter_counts),
        source_type_counts=dict(source_type_counts),
        missing_field_counts=dict(missing_field_counts),
        unmapped_field_counts=dict(unmapped_field_counts),
        entity_kind_counts=dict(entity_kind_counts),
        missing_entity_kind_counts=dict(missing_entity_kind_counts),
        schema_fingerprint_counts=dict(schema_fingerprint_counts),
        schema_baseline_applied=known_fingerprints is not None,
        known_schema_fingerprint_count=len(known_fingerprints or ()),
        novel_schema_fingerprint_counts=dict(novel_schema_fingerprint_counts),
        schema_status_counts=dict(schema_status_counts),
        warning_counts=dict(warning_counts),
        suspicious_samples=suspicious_samples,
        samples=sample_reports,
    )


def _drift_sample_from_reports(
    *,
    path: str,
    alert_id: str,
    normalization: NormalizationReport,
    extraction: ExtractionReport,
    run_id: str | None = None,
) -> NormalizationDriftSample:
    return NormalizationDriftSample(
        path=path,
        status="success",
        run_id=run_id,
        alert_id=alert_id,
        adapter=normalization.adapter,
        source_type=normalization.source_type,
        source_system=normalization.source_system,
        missing_fields=normalization.missing_fields,
        unmapped_fields=normalization.unmapped_fields,
        entity_counts=extraction.entity_counts,
        missing_entity_kinds=extraction.missing_entity_kinds,
        schema_fingerprints=[observation.schema_fingerprint for observation in normalization.message_schemas if observation.schema_fingerprint],
        schema_statuses=[observation.status for observation in normalization.message_schemas],
        warnings=[*normalization.warnings, *extraction.warnings],
    )


def _drift_failure_sample(
    path: str,
    error: str,
    *,
    run_id: str | None = None,
    alert_id: str | None = None,
) -> NormalizationDriftSample:
    return NormalizationDriftSample(
        path=path,
        status="failed",
        run_id=run_id,
        alert_id=alert_id,
        warnings=[error],
        error=error,
    )


class SocReviewService:
    """Review queue and correction service."""

    def __init__(
        self,
        *,
        repository: AlertRepository | None = None,
        summary_repository: AlertSummaryRepository | None = None,
        audit_repository: DecisionAuditRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        evidence_repository: InvestigationEvidenceRepository | None = None,
        authorization_enrichment_repository: AuthorizationEnrichmentRepository | None = None,
        disposition_proposal_repository: SocDispositionProposalRepository | None = None,
        external_disposition_repository: SocExternalDispositionRepository | None = None,
        memory_candidate_repository: MemoryCandidateRepository | None = None,
        memory_record_repository: MemoryRecordRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._repository = repository
        self._summary_repository = summary_repository
        self._audit_repository = audit_repository
        self._review_queue_repository = review_queue_repository
        self._evidence_repository = evidence_repository
        self._authorization_enrichment_repository = authorization_enrichment_repository
        self._disposition_proposal_repository = disposition_proposal_repository
        self._external_disposition_repository = external_disposition_repository
        self._memory_candidate_repository = memory_candidate_repository
        self._memory_record_repository = memory_record_repository
        self._event_sink = event_sink or NoopEventSink()

    def correct(
        self,
        command: CorrectionCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        if self._repository is None:
            raise SocServiceNotImplementedError("correct requires an AlertRepository")

        run = self._repository.get_run(command.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {command.run_id} not found")

        request_context = context or ServiceRequestContext()
        previous_verdict = _current_verdict(run)
        record = CorrectionRecord(
            run_id=run.run_id,
            previous_verdict=previous_verdict,
            corrected_verdict=command.corrected_verdict,
            reason=command.reason,
            corrected_confidence=command.corrected_confidence,
            actor=request_context.actor,
            evidence=command.evidence,
            candidate_knowledge_status="pending_review",
        )
        run.corrections.append(record)
        run.decision = Decision(
            verdict=command.corrected_verdict,
            confidence=command.corrected_confidence if command.corrected_confidence is not None else 1.0,
            suggested_action=run.decision.suggested_action if run.decision is not None else "manual correction recorded",
            needs_review=False,
            reason=command.reason,
            automation_allowed=False,
        )
        review_item = self._review_queue_repository.get_open_review_item_by_run(run.run_id) if self._review_queue_repository is not None else None
        self._repository.save_run(run)
        if self._summary_repository is not None:
            self._summary_repository.save_alert_summary(_alert_summary_from_run(run))
        if self._review_queue_repository is not None:
            _close_open_review_item_for_run(
                self._review_queue_repository,
                run_id=run.run_id,
                actor=request_context.actor,
                reason=f"manual correction: {command.reason}",
            )
        memory_candidate = self._propose_correction_memory_candidate(
            run,
            record,
            queue_item=review_item,
            context=request_context,
        )
        if memory_candidate is not None:
            record.memory_candidate_id = memory_candidate.candidate_id
            run.corrections[-1] = record
            self._repository.save_run(run)
        if self._audit_repository is not None:
            self._audit_repository.save_audit_record(_correction_audit_record(run, record))
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.REVIEW_CORRECTED,
                request_id=request_context.request_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                actor=request_context.actor,
                payload={
                    "correction_id": record.correction_id,
                    "previous_verdict": previous_verdict.value if previous_verdict is not None else None,
                    "corrected_verdict": command.corrected_verdict.value,
                    "candidate_knowledge_status": record.candidate_knowledge_status,
                    "memory_candidate_id": record.memory_candidate_id,
                },
            )
        )
        return run

    def _propose_correction_memory_candidate(
        self,
        run: AnalysisRun,
        record: CorrectionRecord,
        *,
        queue_item: ReviewQueueItem | None,
        context: ServiceRequestContext,
    ) -> SocMemoryCandidate | None:
        if self._memory_candidate_repository is None:
            return None
        memory_service = SocMemoryService(
            candidate_repository=self._memory_candidate_repository,
            event_sink=self._event_sink,
        )
        return SocMemoryCandidateSourceBridge(memory_service).propose_from_correction(
            run,
            record,
            queue_item=queue_item,
            context=context,
        )

    def list_queue(
        self,
        *,
        status: ReviewQueueStatus | None = ReviewQueueStatus.OPEN,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("list_queue requires a ReviewQueueRepository")
        return self._review_queue_repository.list_review_items(status=status, limit=limit)

    def close_queue_item(
        self,
        command: ReviewQueueCloseCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> ReviewQueueItem:
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("close_queue_item requires a ReviewQueueRepository")

        item = self._review_queue_repository.get_review_item(command.queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {command.queue_id} not found")

        request_context = context or ServiceRequestContext()
        item.status = ReviewQueueStatus.CLOSED
        item.closed_at = _utc_now()
        item.closed_by = request_context.actor
        item.close_reason = command.reason
        item.updated_at = item.closed_at
        self._review_queue_repository.save_review_item(item)
        return item

    def add_note(
        self,
        command: ReviewNoteCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> ReviewNoteResult:
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("add_note requires a ReviewQueueRepository")
        if self._repository is None:
            raise SocServiceNotImplementedError("add_note requires an AlertRepository")
        if self._memory_candidate_repository is None:
            raise SocServiceNotImplementedError("add_note requires a MemoryCandidateRepository")

        item = self._review_queue_repository.get_review_item(command.queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {command.queue_id} not found")

        run = self._repository.get_run(item.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {item.run_id} not found")

        request_context = context or ServiceRequestContext()
        memory_service = SocMemoryService(
            candidate_repository=self._memory_candidate_repository,
            event_sink=self._event_sink,
        )
        candidate = SocMemoryCandidateSourceBridge(memory_service).propose_from_review_note(
            run,
            command,
            queue_item=item,
            context=request_context,
        )
        return ReviewNoteResult(queue_item=item, memory_candidate=candidate)

    def get_investigation_context(self, queue_id: str) -> InvestigationContext:
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("get_investigation_context requires a ReviewQueueRepository")
        if self._repository is None:
            raise SocServiceNotImplementedError("get_investigation_context requires an AlertRepository")

        item = self._review_queue_repository.get_review_item(queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {queue_id} not found")

        run = self._repository.get_run(item.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {item.run_id} not found")

        summary = self._summary_repository.get_alert_summary(item.run_id) if self._summary_repository is not None else None
        audit_records = self._audit_repository.list_audit_records(item.run_id) if self._audit_repository is not None else []
        similar_alerts = self._summary_repository.find_similar_alert_summaries(_similar_alert_query_from_summary(summary)) if self._summary_repository is not None and summary is not None else []
        action_evidence = (
            self._evidence_repository.list_evidence(
                queue_id=item.queue_id,
                run_id=item.run_id,
                alert_id=item.alert_id,
                limit=20,
            )
            if self._evidence_repository is not None
            else []
        )
        authorization_enrichments = (
            self._authorization_enrichment_repository.list_authorization_enrichments(
                run_id=item.run_id,
                limit=20,
            )
            if self._authorization_enrichment_repository is not None
            else []
        )
        disposition_proposals = (
            self._disposition_proposal_repository.list_disposition_proposals(
                run_id=item.run_id,
                queue_id=item.queue_id,
                limit=20,
            )
            if self._disposition_proposal_repository is not None
            else []
        )
        external_dispositions = (
            self._external_disposition_repository.list_external_dispositions(
                queue_id=item.queue_id,
                run_id=item.run_id,
                alert_id=item.alert_id,
                limit=20,
            )
            if self._external_disposition_repository is not None
            else []
        )
        memory_candidates = (
            self._memory_candidate_repository.list_memory_candidates(
                queue_id=item.queue_id,
                run_id=item.run_id,
                alert_id=item.alert_id,
                limit=20,
            )
            if self._memory_candidate_repository is not None
            else []
        )
        correlation_result = _correlation_result_for_context(
            run_id=item.run_id,
            summary=summary,
            summary_repository=self._summary_repository,
            evidence_repository=self._evidence_repository,
        )
        context = InvestigationContext(
            queue_item=item,
            run=run,
            summary=summary,
            audit_records=audit_records,
            similar_alerts=similar_alerts,
            action_evidence=action_evidence,
            authorization_enrichments=authorization_enrichments,
            disposition_proposals=disposition_proposals,
            external_dispositions=external_dispositions,
            memory_candidates=memory_candidates,
            correlation_result=correlation_result,
        )
        if self._memory_record_repository is not None:
            relevant_memories = SocMemoryService(record_repository=self._memory_record_repository).find_relevant_records(_memory_query_from_investigation_context(context))
            context = context.model_copy(update={"relevant_memories": relevant_memories})
        domain_triage_results = _domain_triage_results_for_context(context)
        context = context.model_copy(update={"domain_triage_results": domain_triage_results})
        return context.model_copy(update={"investigation_view": _unified_investigation_view_from_context(context)})


class SocMemoryService:
    """Facts, lessons, and reviewable candidate knowledge service."""

    def __init__(
        self,
        *,
        candidate_repository: MemoryCandidateRepository | None = None,
        record_repository: MemoryRecordRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._candidate_repository = candidate_repository
        self._record_repository = record_repository
        self._event_sink = event_sink or NoopEventSink()

    def propose_candidate(
        self,
        command: SocMemoryCandidateCreateCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryCandidate:
        """Persist candidate knowledge as pending review only."""

        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("propose_candidate requires a MemoryCandidateRepository")

        request_context = context or ServiceRequestContext()
        if command.idempotency_key:
            existing = self._candidate_repository.find_memory_candidate_by_idempotency_key(command.idempotency_key)
            if existing is not None:
                return existing

        source = command.source.model_copy(update={"source_surface": command.source.source_surface or request_context.actor.surface})
        candidate = SocMemoryCandidate(
            candidate_type=command.candidate_type,
            target_artifact=command.target_artifact,
            summary=command.summary,
            content=command.content,
            tenant_scope=command.tenant_scope,
            tenant_id=command.tenant_id,
            status=SocMemoryCandidateStatus.PENDING_REVIEW,
            source=source,
            evidence_refs=command.evidence_refs,
            validity=command.validity,
            idempotency_key=command.idempotency_key,
            confidence=command.confidence,
            facets=command.facets,
            decision_impact=command.decision_impact,
            review_owner=command.review_owner,
            labels=command.labels,
            metadata={
                **command.metadata,
                "request_id": request_context.request_id,
            },
            proposed_by=request_context.actor,
        )
        self._candidate_repository.save_memory_candidate(candidate)
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.MEMORY_UPDATED,
                request_id=request_context.request_id,
                run_id=candidate.source.run_id,
                alert_id=candidate.source.alert_id,
                actor=request_context.actor,
                payload={
                    "operation": "memory_candidate.proposed",
                    "candidate_id": candidate.candidate_id,
                    "candidate_status": candidate.status.value,
                    "candidate_type": candidate.candidate_type.value,
                    "target_artifact": candidate.target_artifact.value,
                    "tenant_scope": candidate.tenant_scope,
                    "idempotency_key": candidate.idempotency_key,
                    "runtime_decision_allowed": candidate.runtime_decision_allowed,
                },
            )
        )
        return candidate

    def get_candidate(self, candidate_id: str) -> SocMemoryCandidate:
        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("get_candidate requires a MemoryCandidateRepository")

        candidate = self._candidate_repository.get_memory_candidate(candidate_id)
        if candidate is None:
            raise SocServiceNotFoundError(f"memory candidate {candidate_id} not found")
        return candidate

    def review_candidate(
        self,
        command: SocMemoryCandidateReviewCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryCandidateReviewResult:
        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("review_candidate requires a MemoryCandidateRepository")

        request_context = context or ServiceRequestContext()
        candidate = self.get_candidate(command.candidate_id)
        previous_status = candidate.status
        reviewed_at = datetime.now(UTC)
        memory_record: SocMemoryRecord | None = None

        if command.decision is SocMemoryCandidateReviewDecision.CONFIRM_CANDIDATE:
            _validate_memory_candidate_transition(candidate.status, command.decision)
            candidate = self._transition_candidate(
                candidate,
                status=SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
                command=command,
                actor=request_context.actor,
                reviewed_at=reviewed_at,
            )
        elif command.decision is SocMemoryCandidateReviewDecision.CONFIRM:
            _validate_memory_candidate_transition(candidate.status, command.decision)
            if self._record_repository is None:
                raise SocServiceNotImplementedError("confirming a memory candidate requires a MemoryRecordRepository")
            candidate = self._transition_candidate(
                candidate,
                status=SocMemoryCandidateStatus.CONFIRMED,
                command=command,
                actor=request_context.actor,
                reviewed_at=reviewed_at,
            )
            memory_record = self._record_repository.get_memory_record_by_candidate_id(candidate.candidate_id)
            if memory_record is None:
                memory_record = _memory_record_from_candidate(candidate, command=command, actor=request_context.actor, created_at=reviewed_at)
                self._record_repository.save_memory_record(memory_record)
        elif command.decision is SocMemoryCandidateReviewDecision.REJECT:
            _validate_memory_candidate_transition(candidate.status, command.decision)
            candidate = self._transition_candidate(
                candidate,
                status=SocMemoryCandidateStatus.REJECTED,
                command=command,
                actor=request_context.actor,
                reviewed_at=reviewed_at,
            )
        elif command.decision in {SocMemoryCandidateReviewDecision.DEPRECATE, SocMemoryCandidateReviewDecision.EXPIRE}:
            _validate_memory_candidate_transition(candidate.status, command.decision)
            candidate_status = SocMemoryCandidateStatus.EXPIRED if command.decision is SocMemoryCandidateReviewDecision.EXPIRE else SocMemoryCandidateStatus.DEPRECATED
            record_status = SocMemoryRecordStatus.EXPIRED if command.decision is SocMemoryCandidateReviewDecision.EXPIRE else SocMemoryRecordStatus.DEPRECATED
            candidate = self._transition_candidate(
                candidate,
                status=candidate_status,
                command=command,
                actor=request_context.actor,
                reviewed_at=reviewed_at,
            )
            if self._record_repository is not None:
                memory_record = self._record_repository.get_memory_record_by_candidate_id(candidate.candidate_id)
                if memory_record is not None:
                    memory_record = memory_record.model_copy(
                        update={
                            "status": record_status,
                            "updated_at": reviewed_at,
                            "deprecated_by": request_context.actor,
                            "deprecated_at": reviewed_at,
                            "deprecation_reason": command.reason,
                            "metadata": {**memory_record.metadata, **command.metadata},
                        }
                    )
                    self._record_repository.save_memory_record(memory_record)
        else:
            raise SocServiceError(f"unsupported memory review decision: {command.decision}")

        self._candidate_repository.save_memory_candidate(candidate)
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.MEMORY_UPDATED,
                request_id=request_context.request_id,
                run_id=candidate.source.run_id,
                alert_id=candidate.source.alert_id,
                actor=request_context.actor,
                payload={
                    "operation": "memory_candidate.reviewed",
                    "candidate_id": candidate.candidate_id,
                    "previous_status": previous_status.value,
                    "candidate_status": candidate.status.value,
                    "decision": command.decision.value,
                    "memory_id": memory_record.memory_id if memory_record is not None else None,
                    "retrieval_enabled": memory_record.retrieval_enabled if memory_record is not None else None,
                },
            )
        )
        return SocMemoryCandidateReviewResult(
            candidate=candidate,
            memory_record=memory_record,
            previous_status=previous_status,
            decision=command.decision,
            reviewed_at=reviewed_at,
        )

    def _transition_candidate(
        self,
        candidate: SocMemoryCandidate,
        *,
        status: SocMemoryCandidateStatus,
        command: SocMemoryCandidateReviewCommand,
        actor: ActorContext,
        reviewed_at: datetime,
    ) -> SocMemoryCandidate:
        return candidate.model_copy(
            update={
                "status": status,
                "reviewed_by": actor,
                "reviewed_at": reviewed_at,
                "review_reason": command.reason,
                "updated_at": reviewed_at,
                "metadata": {**candidate.metadata, **command.metadata},
            }
        )

    def list_candidates(
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
        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("list_candidates requires a MemoryCandidateRepository")

        return self._candidate_repository.list_memory_candidates(
            status=status,
            tenant_scope=tenant_scope,
            tenant_id=tenant_id,
            run_id=run_id,
            alert_id=alert_id,
            queue_id=queue_id,
            limit=limit,
        )

    def get_record(self, memory_id: str) -> SocMemoryRecord:
        if self._record_repository is None:
            raise SocServiceNotImplementedError("get_record requires a MemoryRecordRepository")

        record = self._record_repository.get_memory_record(memory_id)
        if record is None:
            raise SocServiceNotFoundError(f"memory record {memory_id} not found")
        return record

    def list_records(
        self,
        *,
        status: SocMemoryRecordStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        source_candidate_id: str | None = None,
        retrieval_enabled: bool | None = None,
        limit: int = 50,
    ) -> list[SocMemoryRecord]:
        if self._record_repository is None:
            raise SocServiceNotImplementedError("list_records requires a MemoryRecordRepository")

        return self._record_repository.list_memory_records(
            status=status,
            tenant_scope=tenant_scope,
            tenant_id=tenant_id,
            source_candidate_id=source_candidate_id,
            retrieval_enabled=retrieval_enabled,
            limit=limit,
        )

    def find_relevant_records(self, query: SocMemoryQuery) -> SocMemoryRetrievalResult:
        """Return retrieval-enabled confirmed memory records with scoring metadata."""

        if self._record_repository is None:
            raise SocServiceNotImplementedError("find_relevant_records requires a MemoryRecordRepository")

        candidate_records: list[SocMemoryRecord] = []
        for status in query.statuses:
            candidate_records.extend(
                self._record_repository.list_memory_records(
                    status=status,
                    tenant_scope=query.tenant_scope,
                    tenant_id=query.tenant_id,
                    retrieval_enabled=None,
                    limit=query.candidate_limit,
                )
            )
        if not query.statuses:
            candidate_records = self._record_repository.list_memory_records(
                status=None,
                tenant_scope=query.tenant_scope,
                tenant_id=query.tenant_id,
                retrieval_enabled=None,
                limit=query.candidate_limit,
            )

        deduped_records: dict[str, SocMemoryRecord] = {}
        for record in candidate_records:
            deduped_records[record.memory_id] = record

        scored_matches: list[SocMemoryMatch] = []
        skipped_retrieval_disabled = 0
        skipped_status = 0
        skipped_expired = 0
        skipped_below_min_score = 0
        now = datetime.now(UTC)

        for record in deduped_records.values():
            if query.statuses and record.status not in query.statuses:
                skipped_status += 1
                continue
            if query.memory_types and record.memory_type not in query.memory_types:
                skipped_status += 1
                continue
            if query.require_retrieval_enabled and not record.retrieval_enabled:
                skipped_retrieval_disabled += 1
                continue
            if record.status != SocMemoryRecordStatus.CONFIRMED:
                skipped_status += 1
                continue
            if record.validity.valid_until is not None and record.validity.valid_until <= now:
                skipped_expired += 1
                continue

            score, match_reasons, matched_facets = _score_memory_record(record, query)
            if score < query.min_score:
                skipped_below_min_score += 1
                continue
            token_estimate = _estimate_memory_tokens(record)
            scored_matches.append(
                SocMemoryMatch(
                    memory_id=record.memory_id,
                    version=record.version,
                    record=record,
                    score=score,
                    match_reasons=match_reasons,
                    matched_facets=matched_facets,
                    token_estimate=token_estimate,
                    content_hash=record.content_hash,
                    facets_hash=record.facets_hash,
                    retrieval_enabled=True,
                )
            )

        selected_matches: list[SocMemoryMatch] = []
        token_total = 0
        for match in sorted(scored_matches, key=lambda item: (item.score, item.record.updated_at), reverse=True):
            if len(selected_matches) >= query.limit:
                break
            if token_total + match.token_estimate > query.max_tokens and selected_matches:
                break
            selected_matches.append(match)
            token_total += match.token_estimate

        return SocMemoryRetrievalResult(
            query=query,
            matches=selected_matches,
            total_candidate_count=len(deduped_records),
            skipped_retrieval_disabled=skipped_retrieval_disabled,
            skipped_status=skipped_status,
            skipped_expired=skipped_expired,
            skipped_below_min_score=skipped_below_min_score,
            returned_count=len(selected_matches),
            total_token_estimate=token_total,
            max_tokens=query.max_tokens,
        )

    def list_facts(self) -> list[Any]:
        raise SocServiceNotImplementedError("list_facts is replaced by find_relevant_records(SocMemoryQuery)")


def _memory_record_from_candidate(
    candidate: SocMemoryCandidate,
    *,
    command: SocMemoryCandidateReviewCommand,
    actor: ActorContext,
    created_at: datetime,
) -> SocMemoryRecord:
    summary = command.record_summary or candidate.summary
    content = command.record_content or candidate.content
    facets_hash = _stable_sha256(candidate.facets)
    return SocMemoryRecord(
        memory_type=candidate.candidate_type,
        target_artifact=candidate.target_artifact,
        tenant_scope=candidate.tenant_scope,
        tenant_id=candidate.tenant_id,
        source_candidate_id=candidate.candidate_id,
        source=candidate.source,
        summary=summary,
        content=content,
        facets=candidate.facets,
        evidence_refs=candidate.evidence_refs,
        validity=candidate.validity,
        confidence=candidate.confidence,
        decision_impact=candidate.decision_impact,
        content_hash=f"sha256:{_stable_sha256(content)}",
        facets_hash=f"sha256:{facets_hash}",
        created_by=actor,
        created_at=created_at,
        updated_at=created_at,
        labels=sorted(set(candidate.labels + ["confirmed-memory", "retrieval-disabled"])),
        metadata={
            **candidate.metadata,
            **command.metadata,
            "review_reason": command.reason,
            "retrieval_enabled": False,
        },
    )


def _memory_query_from_investigation_context(context: InvestigationContext) -> SocMemoryQuery:
    facets: dict[str, list[str]] = {}
    text_terms: list[str] = []
    evidence_refs: list[str] = []

    item = context.queue_item
    _add_memory_query_facet(facets, "source_type", item.source_type.value)
    _add_memory_query_facet(facets, "source_system", item.source_system)
    _add_memory_query_facet(facets, "rule_code", item.rule_code)
    _add_memory_query_facet(facets, "rule_name", item.rule_name)
    _add_memory_query_facet(facets, "severity", item.severity)
    _add_memory_query_facet(facets, "category", item.category)
    _add_memory_query_facet(facets, "verdict", item.verdict.value if item.verdict is not None else None)
    for entity_key in item.entity_keys:
        _add_memory_query_facet(facets, "entity", entity_key)

    if context.summary is not None:
        summary = context.summary
        _add_memory_query_facet(facets, "detection_key", summary.detection_key)
        _add_memory_query_facet(facets, "rule_code", summary.rule_code)
        _add_memory_query_facet(facets, "rule_name", summary.rule_name)
        _add_memory_query_facet(facets, "source_type", summary.source_type.value)
        _add_memory_query_facet(facets, "source_system", summary.source_system)
        _add_memory_query_facet(facets, "category", summary.category)
        for entity_key in summary.entity_keys:
            _add_memory_query_facet(facets, "entity", entity_key)
        if summary.summary:
            text_terms.extend(_memory_text_terms(summary.summary))

    if context.run.analysis is not None:
        text_terms.extend(_memory_text_terms(context.run.analysis.summary))
        text_terms.extend(_memory_text_terms(context.run.analysis.reason))
        for candidate in context.run.analysis.knowledge_candidates:
            _add_memory_query_facet(facets, "knowledge_candidate", candidate)

    request = context.run.llm_analysis_request
    if request is not None:
        for skill in request.skill_context.selected_skills:
            _add_memory_query_facet(facets, "skill", skill.skill_name)
            _add_memory_query_facet(facets, "skill_reason", skill.reason)
            for matched_field in skill.matched_fields:
                _add_memory_query_facet(facets, "skill_matched_field", matched_field)
        for conflict_type in request.conflict_types:
            _add_memory_query_facet(facets, "conflict_type", conflict_type)

    for evidence in context.action_evidence:
        evidence_refs.append(evidence.evidence_id)
        _add_memory_query_facet(facets, "action", evidence.action)
        _add_memory_query_facet(facets, "route", evidence.route)
    for candidate in context.memory_candidates:
        _add_memory_query_facet(facets, "candidate_type", candidate.candidate_type.value)
        _add_memory_query_facet(facets, "target_artifact", candidate.target_artifact.value)

    return SocMemoryQuery(
        tenant_id=item.tenant_id,
        facets=facets,
        text_terms=text_terms,
        evidence_refs=evidence_refs,
        limit=5,
        max_tokens=900,
        metadata={
            "source": "investigation_context",
            "queue_id": item.queue_id,
            "run_id": item.run_id,
            "alert_id": item.alert_id,
        },
    )


def _add_memory_query_facet(facets: dict[str, list[str]], key: str, value: str | None) -> None:
    if value is None:
        return
    normalized = str(value).strip()
    if not normalized:
        return
    values = facets.setdefault(key, [])
    if normalized not in values:
        values.append(normalized)


def _memory_text_terms(text: str | None) -> list[str]:
    if not text:
        return []
    terms: list[str] = []
    for token in str(text).replace("/", " ").replace(",", " ").replace("，", " ").replace(":", " ").split():
        normalized = token.strip()
        if len(normalized) >= 3 and normalized not in terms:
            terms.append(normalized[:80])
        if len(terms) >= 12:
            break
    return terms


def _score_memory_record(record: SocMemoryRecord, query: SocMemoryQuery) -> tuple[float, list[str], dict[str, list[str]]]:
    score = float(record.confidence)
    match_reasons: list[str] = []
    matched_facets: dict[str, list[str]] = {}

    if query.memory_types and record.memory_type in query.memory_types:
        score += 2.0
        match_reasons.append(f"memory_type:{record.memory_type.value}")

    record_facets = _normalized_memory_facets(record.facets)
    query_facets = _normalized_memory_facets(query.facets)
    for key, query_values in query_facets.items():
        record_values = record_facets.get(key, set())
        overlap = sorted(query_values & record_values)
        if not overlap:
            continue
        weight = _memory_facet_weight(key)
        score += weight * len(overlap)
        matched_facets[key] = overlap
        match_reasons.append(f"facet:{key}={','.join(overlap[:3])}")

    haystack = f"{record.summary}\n{record.content}".lower()
    for term in query.text_terms:
        normalized_term = term.lower()
        if normalized_term and normalized_term in haystack:
            score += 1.25
            match_reasons.append(f"text:{term[:40]}")

    evidence_overlap = sorted(set(record.evidence_refs) & set(query.evidence_refs))
    if evidence_overlap:
        score += 3.0 * len(evidence_overlap)
        match_reasons.append(f"evidence:{','.join(evidence_overlap[:3])}")

    if not match_reasons and not query.facets and not query.text_terms and not query.evidence_refs:
        match_reasons.append("broad:policy_allowed")

    return round(score, 3), match_reasons, matched_facets


def _normalized_memory_facets(facets: dict[str, list[str]]) -> dict[str, set[str]]:
    normalized: dict[str, set[str]] = {}
    for key, values in facets.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        normalized_values = {str(value).strip().lower() for value in values if str(value).strip()}
        if normalized_values:
            normalized[normalized_key] = normalized_values
    return normalized


def _memory_facet_weight(key: str) -> float:
    if key in {"detection_key", "rule_code", "canonical_detection", "vendor_alias"}:
        return 4.0
    if key in {"topic", "skill", "skill_reason", "category", "candidate_type"}:
        return 2.5
    if key in {"entity", "asset", "host", "user", "ip"}:
        return 2.0
    if key in {"source_type", "source_system", "severity", "conflict_type", "action"}:
        return 1.5
    return 1.0


def _estimate_memory_tokens(record: SocMemoryRecord) -> int:
    # Conservative text-size estimate; the later prompt builder can replace this
    # with model-specific tokenization without changing retrieval contracts.
    text = f"{record.summary}\n{record.content}"
    return max(1, (len(text) + 3) // 4)


def _validate_memory_candidate_transition(
    status: SocMemoryCandidateStatus,
    decision: SocMemoryCandidateReviewDecision,
) -> None:
    allowed: dict[SocMemoryCandidateReviewDecision, set[SocMemoryCandidateStatus]] = {
        SocMemoryCandidateReviewDecision.CONFIRM_CANDIDATE: {
            SocMemoryCandidateStatus.PENDING_REVIEW,
            SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
        },
        SocMemoryCandidateReviewDecision.CONFIRM: {
            SocMemoryCandidateStatus.PENDING_REVIEW,
            SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
            SocMemoryCandidateStatus.CONFIRMED,
        },
        SocMemoryCandidateReviewDecision.REJECT: {
            SocMemoryCandidateStatus.PENDING_REVIEW,
            SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
            SocMemoryCandidateStatus.REJECTED,
        },
        SocMemoryCandidateReviewDecision.DEPRECATE: {
            SocMemoryCandidateStatus.CONFIRMED,
            SocMemoryCandidateStatus.DEPRECATED,
        },
        SocMemoryCandidateReviewDecision.EXPIRE: {
            SocMemoryCandidateStatus.PENDING_REVIEW,
            SocMemoryCandidateStatus.CONFIRMED_CANDIDATE,
            SocMemoryCandidateStatus.CONFIRMED,
            SocMemoryCandidateStatus.EXPIRED,
        },
    }
    if status not in allowed[decision]:
        raise SocServiceError(f"cannot apply memory review decision {decision.value} to candidate in status {status.value}")


def _stable_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SocDaemonService:
    """Kafka worker orchestration service placeholder."""

    def __init__(
        self,
        *,
        analysis_service: SocAnalysisService | None = None,
        approval_service: SocAgentApprovalService | None = None,
    ) -> None:
        self._analysis_service = analysis_service
        self._approval_service = approval_service

    def start(self) -> None:
        raise SocServiceNotImplementedError("daemon mode is planned for Phase 4")

    def process_message(self, message: SocDaemonMessage | Mapping[str, Any]) -> SocDaemonProcessResult:
        """Process one decoded daemon message through stable core services."""

        daemon_message = SocDaemonMessage.model_validate(message)
        if daemon_message.kind == "alert":
            return self._process_alert_message(daemon_message)
        if daemon_message.kind == "approval_request":
            approval_request = SocAgentApprovalRequest.model_validate(daemon_message.payload)
            submitted = self.submit_approval_request(approval_request)
            return SocDaemonProcessResult(
                message_id=daemon_message.message_id,
                kind=daemon_message.kind,
                status="processed",
                approval_request_id=submitted.approval_request_id,
                payload={
                    "route": submitted.route,
                    "action": submitted.action,
                    "risk_level": submitted.risk_level.value,
                    "idempotency_key": _daemon_idempotency_key(daemon_message),
                },
            )
        raise SocServiceError(f"unsupported daemon message kind: {daemon_message.kind}")

    def submit_approval_request(self, approval_request: SocAgentApprovalRequest) -> SocAgentApprovalRequest:
        """Daemon-side boundary for writing high-risk requests to the shared inbox."""

        if self._approval_service is None:
            raise SocServiceNotImplementedError("submit_approval_request requires a SocAgentApprovalService")
        return self._approval_service.submit_request(approval_request)

    def _process_alert_message(self, message: SocDaemonMessage) -> SocDaemonProcessResult:
        if self._analysis_service is None:
            raise SocServiceNotImplementedError("process alert message requires a SocAnalysisService")
        run = self._analysis_service.analyze(message.payload, context=_daemon_request_context(message))
        failed = run.status is AnalysisRunStatus.FAILED
        return SocDaemonProcessResult(
            message_id=message.message_id,
            kind=message.kind,
            status="failed" if failed else "processed",
            run_id=run.run_id,
            alert_id=run.alert_id,
            analysis_status=run.status.value,
            normalization_issue_count=(len(run.normalization_monitoring_result.issues) if run.normalization_monitoring_result is not None else 0),
            normalization_issue_ids=([item.issue_id for item in run.normalization_monitoring_result.issues] if run.normalization_monitoring_result is not None else []),
            normalization_warnings=(run.normalization_monitoring_result.warnings if run.normalization_monitoring_result is not None else []),
            error=run.failure.message if run.failure is not None else None,
            payload={
                "topic": message.topic,
                "partition": message.partition,
                "offset": message.offset,
                "key": message.key,
                "idempotency_key": _daemon_idempotency_key(message),
                "failure_kind": run.failure.kind.value if run.failure is not None else None,
                "retryable": run.failure.retryable if run.failure is not None else False,
            },
        )


def _daemon_request_context(message: SocDaemonMessage) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(actor_id="soc-daemon", actor_type=ActorType.SERVICE, surface=EntrySurface.DAEMON),
        trace_id=message.message_id,
        idempotency_key=_daemon_idempotency_key(message),
    )


def _daemon_idempotency_key(message: SocDaemonMessage) -> str:
    if message.topic is not None and message.partition is not None and message.offset is not None:
        return f"kafka:{message.topic}:{message.partition}:{message.offset}"
    return f"daemon:{message.message_id}"


def _merge_approval_action_payload(
    approval_request: SocAgentApprovalRequest,
    command_payload: Mapping[str, Any],
) -> dict[str, Any]:
    payload = dict(approval_request.action_payload)
    request_context_refs = dict(approval_request.context_refs)
    command_payload_copy = dict(command_payload)
    command_context_refs = command_payload_copy.pop("context_refs", None)

    payload.update(command_payload_copy)
    if isinstance(command_context_refs, Mapping):
        request_context_refs.update(command_context_refs)
    if request_context_refs:
        payload["context_refs"] = request_context_refs
    elif command_context_refs is not None:
        payload["context_refs"] = command_context_refs
    return payload


class SocAgentApprovalService:
    """Human approval boundary for high-risk SOC Agent actions.

    This service creates an execution grant only. It does not execute the action,
    call external tools, or write business state.
    """

    APPROVER_ROLES = frozenset({"soc_approver", "soc_admin"})

    def __init__(
        self,
        *,
        grant_repository: SocAgentApprovalGrantRepository | None = None,
        request_repository: SocAgentApprovalRequestRepository | None = None,
        action_adapter_registry: SocActionAdapterRegistryPort | None = None,
    ) -> None:
        self._grant_repository = grant_repository
        self._request_repository = request_repository
        self._action_adapter_registry = action_adapter_registry

    def submit_request(self, approval_request: SocAgentApprovalRequest) -> SocAgentApprovalRequest:
        """Persist a pending approval request for human review."""

        if approval_request.status != "pending":
            raise SocServiceError(f"approval request {approval_request.approval_request_id} is not pending")
        if self._request_repository is None:
            raise SocServiceNotImplementedError("submit_request requires a SocAgentApprovalRequestRepository")
        self._request_repository.save_approval_request(approval_request)
        return approval_request

    def get_request(self, approval_request_id: str) -> SocAgentApprovalRequest:
        if self._request_repository is None:
            raise SocServiceNotImplementedError("get_request requires a SocAgentApprovalRequestRepository")
        approval_request = self._request_repository.get_approval_request(approval_request_id)
        if approval_request is None:
            raise SocServiceNotFoundError(f"approval request {approval_request_id} not found")
        return approval_request

    def list_requests(
        self,
        *,
        status: str | None = "pending",
        limit: int = 50,
    ) -> list[SocAgentApprovalRequest]:
        if self._request_repository is None:
            raise SocServiceNotImplementedError("list_requests requires a SocAgentApprovalRequestRepository")
        return self._request_repository.list_approval_requests(status=status, limit=limit)

    def approve(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        context: ServiceRequestContext,
        reason: str,
        expires_in_seconds: int = 900,
    ) -> SocAgentApprovalGrant:
        if approval_request.status != "pending":
            raise SocServiceError(f"approval request {approval_request.approval_request_id} is not pending")
        if not reason.strip():
            raise SocServiceError("approval reason is required")
        if expires_in_seconds <= 0:
            raise SocServiceError("approval grant expiry must be positive")
        if not self._can_approve(context.actor):
            raise SocServiceError("approval requires actor role soc_approver or soc_admin")

        approved_at = datetime.now(UTC)
        grant = SocAgentApprovalGrant(
            approval_request_id=approval_request.approval_request_id,
            permission_decision_id=approval_request.permission_decision_id,
            route=approval_request.route,
            action=approval_request.action,
            risk_level=approval_request.risk_level,
            requested_by=approval_request.requested_by,
            approved_by=context.actor,
            approval_reason=reason.strip(),
            idempotency_key=context.idempotency_key,
            approved_at=approved_at,
            expires_at=approved_at + timedelta(seconds=expires_in_seconds),
        )
        if self._request_repository is not None:
            self._request_repository.save_approval_request(approval_request)
        if self._grant_repository is not None:
            self._grant_repository.save_approval_grant(grant)
        return grant

    def dry_run_approved_action(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        """Validate an approval grant and return a non-side-effecting action result."""

        if self._grant_repository is None:
            raise SocServiceNotImplementedError("dry_run_approved_action requires a SocAgentApprovalGrantRepository")
        if not command.dry_run:
            raise SocServiceError("dry_run_approved_action requires dry_run=true")

        grant = self._grant_repository.get_approval_grant_by_token(command.execution_token_id)
        if grant is None:
            raise SocServiceNotFoundError(f"approval execution token {command.execution_token_id} not found")
        self._validate_grant_for_command(grant, command)

        if self._action_adapter_registry is not None:
            adapter_command = self._adapter_action_command_with_approval_payload(command, grant)
            try:
                adapter_result = self._action_adapter_registry.dry_run(adapter_command, context=context)
            except (LookupError, ValueError) as exc:
                raise SocServiceError(f"approved action dry-run adapter validation failed: {exc}") from exc
            payload = self._approval_dry_run_payload(grant, adapter_command, context)
            payload.update(adapter_result.payload)
            payload["adapter_validated"] = True
            return SocAgentActionResult(
                route=grant.route,
                action=grant.action,
                status=adapter_result.status,
                message=adapter_result.message,
                payload=payload,
                requires_human_approval=adapter_result.requires_human_approval,
            )

        return SocAgentActionResult(
            route=grant.route,
            action=grant.action,
            status="success",
            message="Approved action dry-run validated; no external side effect executed.",
            payload=self._approval_dry_run_payload(grant, command, context),
        )

    def execute_approved_action(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        """Consume an approved action token at the execution boundary.

        Phase 1 intentionally stops at the boundary: it consumes the one-time
        token and records the deterministic execution result, but it does not
        call an external response tool or mutate production systems.
        """

        if self._grant_repository is None:
            raise SocServiceNotImplementedError("execute_approved_action requires a SocAgentApprovalGrantRepository")
        if command.dry_run:
            raise SocServiceError("execute_approved_action requires dry_run=false")
        if not context.idempotency_key:
            raise SocServiceError("execute_approved_action requires an idempotency_key")

        grant = self._grant_repository.get_approval_grant_by_token(command.execution_token_id)
        if grant is None:
            raise SocServiceNotFoundError(f"approval execution token {command.execution_token_id} not found")
        if grant.status == "consumed":
            return self._replay_consumed_grant(grant, context.idempotency_key)

        self._validate_grant_for_command(grant, command)
        execution_command = command
        adapter_preflight_result: SocAgentActionResult | None = None
        if self._action_adapter_registry is not None:
            execution_command = self._adapter_action_command_with_approval_payload(command, grant)
            try:
                adapter_preflight_result = self._action_adapter_registry.preflight_execute(execution_command, context=context)
            except (LookupError, ValueError) as exc:
                raise SocServiceError(f"approved action execute adapter preflight failed: {exc}") from exc
            if adapter_preflight_result.status != "success":
                raise SocServiceError(f"approved action execute adapter preflight failed: {adapter_preflight_result.message}")

        executed_at = datetime.now(UTC)
        execution_result_id = f"AXR-{uuid4().hex[:12].upper()}"
        execution_payload: dict[str, Any] = {
            "dry_run": execution_command.dry_run,
            "adapter_preflight_validated": adapter_preflight_result is not None,
            "execution_result_id": execution_result_id,
            "approval_grant_id": grant.approval_grant_id,
            "approval_request_id": grant.approval_request_id,
            "execution_token_id": grant.execution_token_id,
            "requested_by": grant.requested_by.model_dump(mode="json"),
            "approved_by": grant.approved_by.model_dump(mode="json"),
            "executed_by": context.actor.model_dump(mode="json"),
            "idempotency_key": context.idempotency_key,
            "executed_at": executed_at.isoformat(),
            "external_side_effect": "not_executed",
            "payload": execution_command.payload,
        }
        if adapter_preflight_result is not None:
            execution_payload.update(adapter_preflight_result.payload)
            execution_payload["adapter_preflight_validated"] = True

        result = SocAgentActionResult(
            route=grant.route,
            action=grant.action,
            status="success",
            message="Approved action execution boundary consumed token; no external side effect adapter executed.",
            payload=execution_payload,
        )

        grant.status = "consumed"
        grant.consumed_at = executed_at
        grant.consumed_by = context.actor
        grant.consume_idempotency_key = context.idempotency_key
        grant.execution_result_id = execution_result_id
        grant.execution_result_payload = result.model_dump(mode="json")
        self._grant_repository.save_approval_grant(grant)
        return result

    def _can_approve(self, actor: ActorContext) -> bool:
        return bool(self.APPROVER_ROLES.intersection(actor.roles))

    def _replay_consumed_grant(self, grant: SocAgentApprovalGrant, idempotency_key: str) -> SocAgentActionResult:
        if grant.consume_idempotency_key != idempotency_key:
            raise SocServiceError(f"approval grant {grant.approval_grant_id} has already been consumed")
        if grant.execution_result_payload is None:
            raise SocServiceError(f"approval grant {grant.approval_grant_id} was consumed without result payload")
        return SocAgentActionResult.model_validate(grant.execution_result_payload)

    def _adapter_action_command_with_approval_payload(
        self,
        command: SocAgentApprovedActionCommand,
        grant: SocAgentApprovalGrant,
    ) -> SocAgentApprovedActionCommand:
        if self._request_repository is None:
            return command
        approval_request = self._request_repository.get_approval_request(grant.approval_request_id)
        if approval_request is None:
            return command
        return command.model_copy(update={"payload": _merge_approval_action_payload(approval_request, command.payload)})

    def _approval_dry_run_payload(
        self,
        grant: SocAgentApprovalGrant,
        command: SocAgentApprovedActionCommand,
        context: ServiceRequestContext,
    ) -> dict[str, Any]:
        return {
            "dry_run": command.dry_run,
            "adapter_validated": False,
            "approval_grant_id": grant.approval_grant_id,
            "approval_request_id": grant.approval_request_id,
            "execution_token_id": grant.execution_token_id,
            "requested_by": grant.requested_by.model_dump(mode="json"),
            "approved_by": grant.approved_by.model_dump(mode="json"),
            "executed_by": context.actor.model_dump(mode="json"),
            "idempotency_key": context.idempotency_key,
            "expires_at": grant.expires_at.isoformat(),
            "payload": command.payload,
        }

    def _validate_grant_for_command(
        self,
        grant: SocAgentApprovalGrant,
        command: SocAgentApprovedActionCommand,
    ) -> None:
        now = datetime.now(UTC)
        if grant.status != "approved":
            raise SocServiceError(f"approval grant {grant.approval_grant_id} is {grant.status}")
        if grant.expires_at <= now:
            raise SocServiceError(f"approval grant {grant.approval_grant_id} is expired")
        if grant.route != command.route:
            raise SocServiceError("approval grant route does not match requested action")
        if grant.action != command.action:
            raise SocServiceError("approval grant action does not match requested action")


class SocAgentChatService:
    """Interactive investigation service for TUI/Web/Channels.

    This Phase 1 version is intentionally deterministic. It establishes the
    DeerFlow-compatible stream contract and can load review context, but it does
    not run the future SOC Lead Agent or call LLM tools yet.
    """

    def __init__(
        self,
        *,
        review_service: SocReviewService | None = None,
        capability_router: SocAgentCapabilityRouter | None = None,
        action_dispatcher: SocAgentActionDispatcher | None = None,
        approval_service: SocAgentApprovalService | None = None,
    ) -> None:
        self._review_service = review_service
        self._capability_router = capability_router or SocAgentCapabilityRouter()
        self._action_dispatcher = action_dispatcher or SocAgentActionDispatcher(review_service=review_service)
        self._approval_service = approval_service

    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]:
        chat_request = _coerce_chat_request(request)
        request_context = context or ServiceRequestContext()
        thread_id = chat_request.thread_id or _new_chat_thread_id()

        yield SocAgentStreamEvent(
            type="values",
            data={
                "title": _chat_title(chat_request),
                "messages": [],
                "artifacts": [],
                "thread_id": thread_id,
            },
        )

        route_decision = self._capability_router.route(chat_request)
        yield _route_decision_event(route_decision)
        if not route_decision.allowed:
            yield _assistant_event(f"Route denied: {route_decision.reason}")
            yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})
            return

        permission_decision = self._action_dispatcher.check_permission(chat_request, route_decision, context=request_context)
        yield _permission_decision_event(permission_decision)
        if not permission_decision.allowed:
            if permission_decision.requires_human_approval:
                approval_request = _approval_request_from_permission(permission_decision, context=request_context)
                if self._approval_service is not None:
                    self._approval_service.submit_request(approval_request)
                yield _approval_request_event(approval_request)
            yield _assistant_event(_permission_denied_message(permission_decision))
            yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})
            return

        action_result = self._action_dispatcher.dispatch(chat_request, route_decision, context=request_context, permission_decision=permission_decision)
        yield _action_result_event(action_result)
        if action_result.status != "success":
            yield _assistant_event(action_result.message)
            yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})
            return

        if action_result.action == "review.open_context":
            review_payload = dict(action_result.payload)
            skill_context = review_payload.pop("skill_context", None)
            yield SocAgentStreamEvent(type="custom", data={"kind": "soc.review_context", **review_payload})
            if isinstance(skill_context, dict):
                yield SocAgentStreamEvent(type="custom", data={"kind": "soc.skill_context", **skill_context})
        yield _assistant_event(action_result.message)

        yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})

    def send_message(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocAgentChatResponse:
        events = list(self.stream(request, context=context))
        thread_id = _thread_id_from_events(events)
        return SocAgentChatResponse(
            thread_id=thread_id,
            events=events,
            final_text=_final_text_from_events(events),
        )


class SocAgentCapabilityRouter:
    """Deterministic whitelist router for SOC chat capabilities."""

    DEFAULT_ALLOWED_ROUTES = frozenset({"chat.freeform", "review.open_context"})

    def __init__(self, *, allowed_routes: set[str] | None = None) -> None:
        self._allowed_routes = frozenset(allowed_routes or self.DEFAULT_ALLOWED_ROUTES)

    def route(self, request: SocAgentChatRequest) -> SocAgentRouteDecision:
        route = _route_name(request)
        allowed = route in self._allowed_routes and (not request.allowed_routes or route in set(request.allowed_routes))
        if allowed:
            return SocAgentRouteDecision(
                route=route,
                allowed=True,
                reason=f"route {route} is allowed by whitelist",
                input_text=request.message,
            )
        return SocAgentRouteDecision(
            route=route,
            allowed=False,
            reason=f"route {route} is not allowed",
            input_text=request.message,
        )


class SocAgentActionPolicy:
    """Permission policy for routed SOC Agent service actions."""

    POLICY_VERSION = "soc.agent_action_policy.v1"
    READ_ONLY_ACTIONS = frozenset(
        {
            "asset.lookup",
            "asset.locate",
            "chat.ready_message",
            "endpoint.process_tree.lookup",
            "host.event_context.lookup",
            "review.open_context",
            "security_tag.lookup",
            "threat_intel.ip_reputation.lookup",
        }
    )
    ANALYST_WRITE_ACTIONS = frozenset({"review.correct", "analysis.replay"})
    HIGH_RISK_ACTIONS = frozenset({"response.block_ip", "endpoint.isolate_host", "mcp.invoke"})

    def check(
        self,
        *,
        action: str,
        route: str,
        request: SocAgentChatRequest,
        context: ServiceRequestContext,
    ) -> SocAgentPermissionDecision:
        risk_level = self._risk_level(action)
        if risk_level is SocAgentRiskLevel.READ_ONLY:
            return self._decision(
                action=action,
                route=route,
                allowed=True,
                risk_level=risk_level,
                reason=f"action {action} is read-only",
                context=context,
            )
        if risk_level is SocAgentRiskLevel.ANALYST_WRITE:
            allowed = "analyst" in context.actor.roles
            reason = f"actor has analyst role for action {action}" if allowed else f"action {action} requires analyst role"
            return self._decision(
                action=action,
                route=route,
                allowed=allowed,
                risk_level=risk_level,
                reason=reason,
                context=context,
            )
        if risk_level is SocAgentRiskLevel.HIGH_RISK:
            return self._decision(
                action=action,
                route=route,
                allowed=False,
                risk_level=risk_level,
                reason=f"action {action} requires human approval",
                context=context,
                requires_human_approval=True,
            )
        return self._decision(
            action=action,
            route=route,
            allowed=False,
            risk_level=SocAgentRiskLevel.UNKNOWN,
            reason=f"action {action} is not registered in policy",
            context=context,
        )

    def _risk_level(self, action: str) -> SocAgentRiskLevel:
        if action in self.READ_ONLY_ACTIONS:
            return SocAgentRiskLevel.READ_ONLY
        if action in self.ANALYST_WRITE_ACTIONS:
            return SocAgentRiskLevel.ANALYST_WRITE
        if action in self.HIGH_RISK_ACTIONS:
            return SocAgentRiskLevel.HIGH_RISK
        return SocAgentRiskLevel.UNKNOWN

    def _decision(
        self,
        *,
        action: str,
        route: str,
        allowed: bool,
        risk_level: SocAgentRiskLevel,
        reason: str,
        context: ServiceRequestContext,
        requires_human_approval: bool = False,
    ) -> SocAgentPermissionDecision:
        return SocAgentPermissionDecision(
            route=route,
            action=action,
            allowed=allowed,
            risk_level=risk_level,
            reason=reason,
            requires_human_approval=requires_human_approval,
            approval_request_id=f"APR-{uuid4().hex[:12].upper()}" if requires_human_approval else None,
            policy_version=self.POLICY_VERSION,
            actor=context.actor,
        )


class SocAgentActionDispatcher:
    """Dispatch allowed SOC Agent routes to explicit service actions."""

    def __init__(
        self,
        *,
        review_service: SocReviewService | None = None,
        action_policy: SocAgentActionPolicy | None = None,
        action_adapter_registry: SocActionAdapterRegistryPort | None = None,
        evidence_repository: InvestigationEvidenceRepository | None = None,
    ) -> None:
        self._review_service = review_service
        self._action_policy = action_policy or SocAgentActionPolicy()
        self._action_adapter_registry = action_adapter_registry
        self._evidence_repository = evidence_repository

    def check_permission(
        self,
        request: SocAgentChatRequest,
        route_decision: SocAgentRouteDecision,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentPermissionDecision:
        action = _action_name_for_route(route_decision.route)
        if not route_decision.allowed:
            return SocAgentPermissionDecision(
                route=route_decision.route,
                action=action,
                allowed=False,
                risk_level=SocAgentRiskLevel.UNKNOWN,
                reason=route_decision.reason,
                actor=context.actor,
            )
        return self._action_policy.check(action=action, route=route_decision.route, request=request, context=context)

    def dispatch(
        self,
        request: SocAgentChatRequest,
        route_decision: SocAgentRouteDecision,
        *,
        context: ServiceRequestContext,
        permission_decision: SocAgentPermissionDecision | None = None,
    ) -> SocAgentActionResult:
        if not route_decision.allowed:
            return SocAgentActionResult(
                route=route_decision.route,
                action="route.denied",
                status="denied",
                message=route_decision.reason,
            )
        permission = permission_decision or self.check_permission(request, route_decision, context=context)
        if not permission.allowed:
            return SocAgentActionResult(
                route=route_decision.route,
                action=permission.action,
                status="denied",
                message=permission.reason,
                requires_human_approval=permission.requires_human_approval,
            )
        if permission.action == "chat.ready_message":
            return SocAgentActionResult(
                route=route_decision.route,
                action=permission.action,
                status="success",
                message="SOC investigation chat is ready. Phase 1 supports deterministic review context loading; future SOC Lead Agent routing will attach skills, MCP tools, and bounded LLM reasoning here.",
            )
        if permission.action == "review.open_context":
            return self._open_review_context(request, route_decision=route_decision, context=context)
        if permission.risk_level is SocAgentRiskLevel.READ_ONLY:
            return self._dispatch_read_only_adapter(request, permission=permission, context=context)
        return SocAgentActionResult(
            route=route_decision.route,
            action=permission.action,
            status="denied",
            message=f"action {permission.action} has no service action mapping",
        )

    def _open_review_context(
        self,
        request: SocAgentChatRequest,
        *,
        route_decision: SocAgentRouteDecision,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if self._review_service is None:
            raise SocServiceNotImplementedError("agent chat review context requires SocReviewService")
        if not request.queue_id:
            return SocAgentActionResult(
                route=route_decision.route,
                action="review.open_context",
                status="failed",
                message="review.open_context requires queue_id",
            )
        investigation_context = self._review_service.get_investigation_context(request.queue_id)
        payload = {
            "queue_id": investigation_context.queue_item.queue_id,
            "run_id": investigation_context.run.run_id,
            "alert_id": investigation_context.run.alert_id,
            "actor_surface": context.actor.surface.value,
        }
        skill_context = skill_context_from_investigation_context(investigation_context)
        if skill_context is not None:
            payload["skill_context"] = skill_context.model_dump(mode="json", exclude_none=True)
        return SocAgentActionResult(
            route=route_decision.route,
            action="review.open_context",
            status="success",
            message=_review_context_loaded_message(
                queue_id=investigation_context.queue_item.queue_id,
                run_id=investigation_context.run.run_id,
                alert_id=investigation_context.run.alert_id,
            ),
            payload=payload,
        )

    def _dispatch_read_only_adapter(
        self,
        request: SocAgentChatRequest,
        *,
        permission: SocAgentPermissionDecision,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if self._action_adapter_registry is None:
            return SocAgentActionResult(
                route=permission.route,
                action=permission.action,
                status="failed",
                message=f"read-only action {permission.action} requires an action adapter registry",
            )
        command = SocAgentActionCommand(
            route=permission.route,
            action=permission.action,
            dry_run=False,
            payload=_action_adapter_payload_from_request(request),
        )
        try:
            result = self._action_adapter_registry.execute(command, context=context)
        except (LookupError, ValueError) as exc:
            return SocAgentActionResult(
                route=permission.route,
                action=permission.action,
                status="failed",
                message=f"read-only action adapter execution failed: {exc}",
            )
        return self._record_read_only_action_evidence(
            result,
            request=request,
            command=command,
            context=context,
        )

    def _record_read_only_action_evidence(
        self,
        result: SocAgentActionResult,
        *,
        request: SocAgentChatRequest,
        command: SocAgentActionCommand,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if self._evidence_repository is None or result.status != "success":
            return result
        evidence = _investigation_evidence_from_action_result(
            result,
            request=request,
            command=command,
            context=context,
        )
        self._evidence_repository.save_evidence(evidence)
        payload = dict(result.payload)
        payload["evidence_id"] = evidence.evidence_id
        return result.model_copy(update={"payload": payload})


def _coerce_chat_request(request: SocAgentChatRequest | str) -> SocAgentChatRequest:
    if isinstance(request, SocAgentChatRequest):
        return request
    return SocAgentChatRequest(message=request)


def _route_name(request: SocAgentChatRequest) -> str:
    metadata_route = _metadata_soc_route(request)
    if metadata_route:
        return metadata_route
    if request.queue_id:
        return "review.open_context"
    if request.message.strip().startswith("/"):
        return "command.unknown"
    return "chat.freeform"


def _action_name_for_route(route: str) -> str:
    if route == "chat.freeform":
        return "chat.ready_message"
    if route == "review.open_context":
        return "review.open_context"
    if route == "command.unknown":
        return "command.unknown"
    if route in SocAgentActionPolicy.READ_ONLY_ACTIONS | SocAgentActionPolicy.ANALYST_WRITE_ACTIONS | SocAgentActionPolicy.HIGH_RISK_ACTIONS:
        return route
    return "route.unsupported"


def _metadata_soc_route(request: SocAgentChatRequest) -> str | None:
    route = request.metadata.get("soc_route")
    if isinstance(route, str) and route.strip():
        return route.strip()
    return None


def _action_adapter_payload_from_request(request: SocAgentChatRequest) -> dict[str, Any]:
    raw_payload = request.metadata.get("action_payload")
    payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    context_refs = dict(payload.get("context_refs")) if isinstance(payload.get("context_refs"), Mapping) else {}
    if request.thread_id:
        context_refs.setdefault("thread_id", request.thread_id)
    if request.queue_id:
        context_refs.setdefault("queue_id", request.queue_id)
    if request.run_id:
        context_refs.setdefault("run_id", request.run_id)
    if context_refs:
        payload["context_refs"] = context_refs
    return payload


def _investigation_evidence_from_action_result(
    result: SocAgentActionResult,
    *,
    request: SocAgentChatRequest,
    command: SocAgentActionCommand,
    context: ServiceRequestContext,
) -> InvestigationEvidence:
    context_refs = command.payload.get("context_refs")
    refs = dict(context_refs) if isinstance(context_refs, Mapping) else {}
    return InvestigationEvidence(
        route=result.route,
        action=result.action,
        status=result.status,
        message=result.message,
        result_payload=result.payload,
        mocked=_contains_mock_marker(result.payload),
        queue_id=_string_ref(refs, "queue_id") or request.queue_id,
        run_id=_string_ref(refs, "run_id") or request.run_id,
        alert_id=_string_ref(refs, "alert_id"),
        thread_id=_string_ref(refs, "thread_id") or request.thread_id,
        source_proposal_id=_string_ref(refs, "proposal_id"),
        context_hash=_string_ref(refs, "context_hash"),
        actor=context.actor,
    )


def _contains_mock_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("mocked") is True:
            return True
        return any(_contains_mock_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_mock_marker(item) for item in value)
    return False


def _string_ref(refs: Mapping[str, Any], key: str) -> str | None:
    value = refs.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _route_decision_event(decision: SocAgentRouteDecision) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.route_decision",
            "route": decision.route,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "requires_human_approval": decision.requires_human_approval,
        },
    )


def _permission_decision_event(decision: SocAgentPermissionDecision) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.permission_decision",
            "decision_id": decision.decision_id,
            "route": decision.route,
            "action": decision.action,
            "allowed": decision.allowed,
            "risk_level": decision.risk_level.value,
            "reason": decision.reason,
            "requires_human_approval": decision.requires_human_approval,
            "approval_request_id": decision.approval_request_id,
            "policy_version": decision.policy_version,
        },
    )


def _approval_request_from_permission(
    decision: SocAgentPermissionDecision,
    *,
    context: ServiceRequestContext,
) -> SocAgentApprovalRequest:
    return SocAgentApprovalRequest(
        approval_request_id=decision.approval_request_id or f"APR-{uuid4().hex[:12].upper()}",
        permission_decision_id=decision.decision_id,
        route=decision.route,
        action=decision.action,
        risk_level=decision.risk_level,
        reason=decision.reason,
        requested_by=decision.actor or context.actor,
    )


def _approval_request_event(request: SocAgentApprovalRequest) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.approval_request",
            "approval_request_id": request.approval_request_id,
            "permission_decision_id": request.permission_decision_id,
            "route": request.route,
            "action": request.action,
            "risk_level": request.risk_level.value,
            "reason": request.reason,
            "requested_by": request.requested_by.model_dump(mode="json"),
            "source_proposal_id": request.source_proposal_id,
            "action_payload": request.action_payload,
            "context_refs": request.context_refs,
            "status": request.status,
            "created_at": request.created_at.isoformat(),
        },
    )


def _action_result_event(result: SocAgentActionResult) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.action_result",
            "route": result.route,
            "action": result.action,
            "status": result.status,
            "message": result.message,
            "requires_human_approval": result.requires_human_approval,
            "payload": result.payload,
        },
    )


def _permission_denied_message(decision: SocAgentPermissionDecision) -> str:
    if decision.requires_human_approval:
        return f"Action requires human approval: {decision.reason}"
    return f"Permission denied: {decision.reason}"


def _new_chat_thread_id() -> str:
    return f"SOC-TH-{uuid4().hex[:12].upper()}"


def _chat_title(request: SocAgentChatRequest) -> str:
    if request.queue_id:
        return f"SOC Review {request.queue_id}"
    if request.run_id:
        return f"SOC Run {request.run_id}"
    text = " ".join(request.message.split())
    if not text:
        return "SOC Investigation"
    return text[:60]


def _assistant_event(text: str) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="messages-tuple",
        data={
            "type": "ai",
            "id": f"soc-ai-{uuid4().hex[:8]}",
            "content": text,
        },
    )


def _review_context_loaded_message(*, queue_id: str, run_id: str, alert_id: str) -> str:
    return f"Loaded review context {queue_id} for alert {alert_id} / run {run_id}. Next steps should be expressed as bounded SOC actions such as inspect evidence, compare similar alerts, record correction, or request human approval."


def _correlation_result_for_context(
    *,
    run_id: str,
    summary: AlertSummary | None,
    summary_repository: AlertSummaryRepository | None,
    evidence_repository: InvestigationEvidenceRepository | None,
) -> CorrelationResult | None:
    if summary is None or summary_repository is None:
        return None
    from soc_agent.core.correlation import SocCorrelationService

    try:
        return SocCorrelationService(
            summary_repository=summary_repository,
            evidence_repository=evidence_repository,
        ).correlate(CorrelationQuery(run_id=run_id, limit=10, candidate_limit=200, evidence_limit_per_match=5))
    except (SocServiceNotFoundError, SocServiceNotImplementedError):
        return None


def _domain_triage_results_for_context(context: InvestigationContext) -> list[SocDomainTriageResult]:
    from soc_agent.domain import SocDomainTriageService

    skill_context = skill_context_from_investigation_context(context)
    available_action_routes = sorted({route for evidence in context.action_evidence for route in (evidence.route, evidence.action) if route})
    request = SocDomainTriageRequest(
        run=context.run,
        investigation_evidence=context.action_evidence,
        metadata={
            "source": "review_context",
            "queue_id": context.queue_item.queue_id,
            "similar_alert_count": len(context.similar_alerts),
            "correlation_match_count": len(context.correlation_result.matches) if context.correlation_result is not None else 0,
            "external_disposition_count": len(context.external_dispositions),
            "memory_candidate_count": len(context.memory_candidates),
            "relevant_memory_count": context.relevant_memories.returned_count if context.relevant_memories is not None else 0,
            "available_action_routes": available_action_routes,
            "handler_output_only": True,
            "writes_db": False,
        },
    )
    if skill_context is not None:
        request = request.model_copy(update={"skill_context": skill_context})
    return [SocDomainTriageService().triage(request)]


def _unified_investigation_view_from_context(context: InvestigationContext) -> UnifiedInvestigationView:
    run = context.run
    decision = run.decision
    analysis = run.analysis
    timeline = _investigation_timeline_from_context(context)
    return UnifiedInvestigationView(
        queue_id=context.queue_item.queue_id,
        run_id=run.run_id,
        alert_id=run.alert_id,
        runtime_verdict=decision.verdict if decision is not None else _current_verdict(run),
        runtime_confidence=decision.confidence if decision is not None else _current_confidence(run),
        needs_review=decision.needs_review if decision is not None else run.status is AnalysisRunStatus.NEEDS_REVIEW,
        automation_allowed=decision.automation_allowed if decision is not None else False,
        primary_summary=analysis.summary if analysis is not None else context.queue_item.summary,
        primary_reason=decision.reason if decision is not None else (analysis.reason if analysis is not None else context.queue_item.reason),
        correlation_result=context.correlation_result,
        domain_triage_results=context.domain_triage_results,
        evidence_timeline=timeline,
        counts={
            "similar_alerts": len(context.similar_alerts),
            "correlation_matches": len(context.correlation_result.matches) if context.correlation_result is not None else 0,
            "reusable_evidence": context.correlation_result.reusable_evidence_count if context.correlation_result is not None else 0,
            "domain_findings": sum(len(result.findings) for result in context.domain_triage_results),
            "action_evidence": len(context.action_evidence),
            "authorization_enrichments": len(context.authorization_enrichments),
            "exact_authorization_matches": sum(item.match_result.status.value == "exact" for item in context.authorization_enrichments),
            "disposition_proposals": len(context.disposition_proposals),
            "external_dispositions": len(context.external_dispositions),
            "memory_candidates": len(context.memory_candidates),
            "relevant_memories": context.relevant_memories.returned_count if context.relevant_memories is not None else 0,
            "audit_records": len(context.audit_records),
            "corrections": len(run.corrections),
            "timeline_items": len(timeline),
        },
        metadata={
            "source": "SocReviewService.get_investigation_context",
            "view_only": True,
            "writes_db": False,
            "executes_actions": False,
        },
    )


def _investigation_timeline_from_context(context: InvestigationContext) -> list[InvestigationTimelineItem]:
    run = context.run
    items: list[InvestigationTimelineItem] = []
    if run.analysis is not None:
        items.append(
            InvestigationTimelineItem(
                kind="analysis",
                title="Runtime analysis completed",
                summary=run.analysis.summary,
                status=run.analysis.verdict.value,
                severity=context.summary.severity if context.summary is not None else None,
                source_id=run.run_id,
                source_refs={"run_id": run.run_id, "alert_id": run.alert_id},
                occurred_at=run.ended_at or run.started_at,
                payload={
                    "confidence": run.analysis.confidence,
                    "reason": run.analysis.reason,
                    "recommended_action": run.analysis.recommended_action,
                },
            )
        )
    if run.decision is not None:
        items.append(
            InvestigationTimelineItem(
                kind="decision",
                title="Operational decision",
                summary=run.decision.reason,
                status=run.decision.verdict.value,
                source_id=run.run_id,
                source_refs={"run_id": run.run_id, "alert_id": run.alert_id},
                occurred_at=run.ended_at or run.started_at,
                payload={
                    "confidence": run.decision.confidence,
                    "confidence_source": run.decision.confidence_source.value,
                    "confidence_is_calibrated": run.decision.confidence_is_calibrated,
                    "calibrated_probability": run.decision.calibrated_probability,
                    "calibration_profile_version": run.decision.calibration_profile_version,
                    "evidence_state": run.decision.evidence_state.value,
                    "needs_review": run.decision.needs_review,
                    "review_reasons": [item.value for item in run.decision.review_reasons],
                    "policy_version": run.decision.policy_version,
                    "automation_allowed": run.decision.automation_allowed,
                    "suggested_action": run.decision.suggested_action,
                },
            )
        )
    for correction in run.corrections:
        items.append(
            InvestigationTimelineItem(
                kind="correction",
                title="Manual correction recorded",
                summary=correction.reason,
                status=correction.corrected_verdict.value,
                source_id=correction.correction_id,
                source_refs={"run_id": run.run_id, "correction_id": correction.correction_id},
                occurred_at=correction.created_at,
                payload={
                    "previous_verdict": correction.previous_verdict.value if correction.previous_verdict is not None else None,
                    "corrected_confidence": correction.corrected_confidence,
                    "candidate_knowledge_status": correction.candidate_knowledge_status,
                },
            )
        )
    if context.correlation_result is not None:
        for match in context.correlation_result.matches[:5]:
            items.append(
                InvestigationTimelineItem(
                    kind="correlation",
                    title="Correlated historical alert",
                    summary=match.summary.summary,
                    status=match.summary.verdict.value if match.summary.verdict is not None else None,
                    severity=match.summary.severity,
                    source_id=match.summary.run_id,
                    source_refs={"run_id": match.summary.run_id, "alert_id": match.summary.alert_id},
                    occurred_at=match.summary.updated_at,
                    payload={
                        "score": match.score,
                        "matched_reasons": match.matched_reasons,
                        "reusable_evidence_count": len(match.reusable_evidence),
                    },
                )
            )
    for result in context.domain_triage_results:
        for finding in result.findings:
            items.append(
                InvestigationTimelineItem(
                    kind="domain_finding",
                    title=finding.title,
                    summary=finding.summary,
                    status=finding.disposition.value,
                    severity=finding.severity.value,
                    source_id=finding.finding_id,
                    source_refs={"run_id": result.run_id, "finding_id": finding.finding_id, "domain": result.domain.value},
                    occurred_at=result.created_at,
                    payload={
                        "handler_id": result.handler_id,
                        "scenario_key": finding.scenario_key,
                        "scenario_name": finding.scenario_name,
                        "confidence": finding.confidence,
                        "evidence_profile": finding.evidence_profile.model_dump(mode="json"),
                        "current_conclusion": finding.current_conclusion.model_dump(mode="json"),
                        "evidence_refs": finding.evidence_refs,
                        "recommendations": finding.recommendations,
                        "limitations": finding.limitations,
                        "human_checklist": finding.human_checklist,
                    },
                )
            )
    for evidence in context.action_evidence:
        items.append(
            InvestigationTimelineItem(
                kind="read_only_evidence",
                title=evidence.action,
                summary=evidence.message,
                status=evidence.status,
                source_id=evidence.evidence_id,
                source_refs={"evidence_id": evidence.evidence_id, "route": evidence.route},
                occurred_at=evidence.created_at,
                payload={
                    "result_payload": evidence.result_payload,
                    "source_proposal_id": evidence.source_proposal_id,
                },
            )
        )
    for enrichment in context.authorization_enrichments:
        result = enrichment.match_result
        items.append(
            InvestigationTimelineItem(
                kind="authorization_enrichment",
                title="Authorized-activity match",
                summary=(f"{len(result.matched_fact_refs)} exact governed fact match(es)" if result.status.value == "exact" else "; ".join(result.warnings[:2]) or "No exact authorized-activity match"),
                status=result.status.value,
                source_id=enrichment.enrichment_id,
                source_refs={
                    "enrichment_id": enrichment.enrichment_id,
                    "query_id": enrichment.query.query_id,
                    "run_id": enrichment.run_id,
                },
                occurred_at=enrichment.created_at,
                payload={
                    "query_hash": enrichment.query_hash,
                    "matcher_policy_version": enrichment.matcher_policy_version,
                    "matched_fact_version_ids": [fact.fact_version_id for fact in result.matched_fact_refs],
                    "matched_dimensions": [item.value for item in result.matched_dimensions],
                    "missing_dimensions": [item.value for item in result.missing_dimensions],
                    "out_of_scope_dimensions": [item.value for item in result.out_of_scope_dimensions],
                    "replay_of_enrichment_id": enrichment.replay_of_enrichment_id,
                    "shadow_only": enrichment.shadow_only,
                    "decision_impact": enrichment.decision_impact,
                },
            )
        )
    for proposal in context.disposition_proposals:
        items.append(_disposition_proposal_timeline_item(proposal))
    for record in context.external_dispositions:
        items.append(
            InvestigationTimelineItem(
                kind="external_disposition",
                title=f"{record.event.external_system} disposition",
                summary=record.event.external_reason or record.apply_reason,
                status=f"{record.canonical_status.value}/{record.apply_status.value}",
                source_id=record.disposition_id,
                source_refs={"disposition_id": record.disposition_id, "external_case_id": record.event.external_case_id},
                occurred_at=record.event.updated_at,
                payload={
                    "external_status": record.event.external_status,
                    "matched_by": record.matched_by,
                    "correction_id": record.correction_id,
                    "memory_candidate_id": record.memory_candidate_id,
                },
            )
        )
    for candidate in context.memory_candidates:
        items.append(
            InvestigationTimelineItem(
                kind="memory_candidate",
                title="Memory candidate",
                summary=candidate.summary,
                status=candidate.status.value,
                source_id=candidate.candidate_id,
                source_refs={"candidate_id": candidate.candidate_id},
                occurred_at=candidate.created_at,
                payload={
                    "candidate_type": candidate.candidate_type.value,
                    "target_artifact": candidate.target_artifact.value,
                    "runtime_decision_allowed": candidate.runtime_decision_allowed,
                    "confidence": candidate.confidence,
                },
            )
        )
    if context.relevant_memories is not None:
        for match in context.relevant_memories.matches[:5]:
            items.append(
                InvestigationTimelineItem(
                    kind="relevant_memory",
                    title="Relevant confirmed memory",
                    summary=match.record.summary,
                    status=match.record.status.value,
                    source_id=match.memory_id,
                    source_refs={"memory_id": match.memory_id, "source_candidate_id": match.record.source_candidate_id},
                    occurred_at=match.record.updated_at,
                    payload={
                        "score": match.score,
                        "match_reasons": match.match_reasons,
                        "retrieval_enabled": match.retrieval_enabled,
                        "token_estimate": match.token_estimate,
                    },
                )
            )
    for audit in context.audit_records[:10]:
        items.append(
            InvestigationTimelineItem(
                kind="audit",
                title=f"Audit {audit.action.value}",
                summary=audit.payload.get("reason") if isinstance(audit.payload.get("reason"), str) else None,
                status=audit.final_verdict.value if audit.final_verdict is not None else None,
                source_id=audit.audit_id,
                source_refs={"audit_id": audit.audit_id, "run_id": audit.run_id},
                occurred_at=audit.occurred_at,
                payload={
                    "input_hash": audit.input_hash,
                    "confidence": audit.confidence,
                    "correction_id": audit.correction_id,
                },
            )
        )
    return sorted(items, key=lambda item: item.occurred_at or datetime.min.replace(tzinfo=UTC), reverse=True)


def _disposition_proposal_timeline_item(
    proposal: SocDispositionProposalRecord,
) -> InvestigationTimelineItem:
    return InvestigationTimelineItem(
        kind="disposition_proposal",
        title="Shadow disposition proposal",
        summary="; ".join(proposal.rationale[:2]),
        status=f"{proposal.proposed_disposition.value}/{proposal.proposal_mode}",
        source_id=proposal.proposal_id,
        source_refs={
            "proposal_id": proposal.proposal_id,
            "enrichment_id": proposal.source_enrichment_id,
            "run_id": proposal.run_id,
        },
        occurred_at=proposal.created_at,
        payload={
            "reason_code": proposal.reason_code.value,
            "policy_version": proposal.policy_version,
            "detection_verdict": proposal.detection_truth.verdict.value,
            "source_fact_version_ids": [item.fact_version_id for item in proposal.source_fact_refs],
            "application_status": proposal.application_status,
            "requires_human_review": proposal.requires_human_review,
            "auto_close_allowed": proposal.auto_close_allowed,
            "detection_truth_impact": proposal.detection_truth_impact,
            "review_queue_impact": proposal.review_queue_impact,
        },
    )


def _thread_id_from_events(events: list[SocAgentStreamEvent]) -> str:
    for event in events:
        thread_id = event.data.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return _new_chat_thread_id()


def _final_text_from_events(events: list[SocAgentStreamEvent]) -> str:
    parts: list[str] = []
    for event in events:
        if event.type != "messages-tuple":
            continue
        if event.data.get("type") != "ai":
            continue
        content = event.data.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)


def _completion_event_type(run: AnalysisRun) -> SocEventType:
    if run.status is AnalysisRunStatus.FAILED:
        return SocEventType.ANALYSIS_FAILED
    return SocEventType.ANALYSIS_COMPLETED


def _current_verdict(run: AnalysisRun) -> Verdict | None:
    if run.decision is not None:
        return run.decision.verdict
    if run.analysis is not None:
        return run.analysis.verdict
    return None


def _current_confidence(run: AnalysisRun) -> float | None:
    if run.decision is not None:
        return run.decision.confidence
    if run.analysis is not None:
        return run.analysis.confidence
    return None


def _alert_summary_from_run(run: AnalysisRun) -> AlertSummary:
    alert = _normalized_alert_from_run(run)
    decision = run.decision
    analysis = run.analysis
    verdict = _current_verdict(run)
    confidence = _current_confidence(run)
    analysis_failed = run.status is AnalysisRunStatus.FAILED
    failed_requires_review = analysis_failed and (run.failure is None or not run.failure.retryable)

    return AlertSummary(
        run_id=run.run_id,
        alert_id=run.alert_id,
        tenant_id=alert.tenant_id if alert is not None else None,
        source_type=alert.source.source_type if alert is not None else AlertSourceType.UNKNOWN,
        source_system=alert.source.source_system if alert is not None else None,
        detection_key=alert.detection.detection_key if alert is not None else None,
        rule_code=alert.detection.rule_code if alert is not None else None,
        rule_name=alert.detection.rule_name if alert is not None else None,
        severity=alert.classification.severity if alert is not None else None,
        category=alert.classification.category if alert is not None else None,
        entity_keys=_entity_keys(run),
        status=run.status,
        verdict=verdict,
        confidence=confidence,
        needs_review=(decision.needs_review if decision is not None else run.status is AnalysisRunStatus.NEEDS_REVIEW or failed_requires_review),
        review_reasons=(list(decision.review_reasons) if decision is not None else [DecisionReviewReason.ANALYSIS_FAILED] if failed_requires_review else []),
        summary=(analysis.summary if analysis is not None else run.failure.message if run.failure is not None else None),
        recommended_action=decision.suggested_action if decision is not None else None,
        input_hash=run.input_hash,
        replay_of_run_id=run.replay_of_run_id,
        created_at=run.started_at,
        updated_at=run.ended_at or run.started_at,
    )


def _similar_alert_query_from_summary(summary: AlertSummary) -> SimilarAlertQuery:
    return SimilarAlertQuery(
        run_id=summary.run_id,
        detection_key=summary.detection_key,
        rule_code=summary.rule_code,
        source_type=summary.source_type,
        category=summary.category,
        entity_keys=summary.entity_keys,
    )


def _normalized_alert_from_run(run: AnalysisRun) -> AlertInput | None:
    if run.input_payload is None:
        return None
    try:
        return normalize_alert_payload(run.input_payload)
    except Exception:  # noqa: BLE001 - summary generation should preserve failed runs
        return None


def _entity_keys(run: AnalysisRun) -> list[str]:
    if run.entities is None:
        return []

    if run.entities.mentions:
        return _dedupe([mention.key for mention in run.entities.mentions])

    values = [
        *(f"ip:{value}" for value in run.entities.ips),
        *(f"domain:{value}" for value in run.entities.domains),
        *(f"url:{value}" for value in run.entities.urls),
        *(f"process:{value}" for value in run.entities.processes),
        *(f"user:{value}" for value in run.entities.users),
        *(f"host:{value}" for value in run.entities.hosts),
        *(f"rule_code:{value}" for value in run.entities.rule_codes),
        *(f"rule_name:{value}" for value in run.entities.rule_names),
        *(f"rule:{value}" for value in run.entities.rules if value),
    ]
    return _dedupe(values)


def _upsert_review_queue_item(repository: ReviewQueueRepository, summary: AlertSummary) -> None:
    item = _review_queue_item_from_summary(
        summary,
        existing=repository.get_open_review_item_by_run(summary.run_id),
    )
    if item is None:
        return
    repository.save_review_item(item)


def _review_queue_item_from_summary(
    summary: AlertSummary,
    *,
    existing: ReviewQueueItem | None = None,
) -> ReviewQueueItem | None:
    if summary.status is AnalysisRunStatus.FAILED and not summary.needs_review:
        return None
    reason = _review_reason(summary)
    if reason is None:
        return None
    item = existing or ReviewQueueItem(
        run_id=summary.run_id,
        alert_id=summary.alert_id,
        reason=reason,
    )
    item.tenant_id = summary.tenant_id
    item.priority = _review_priority(summary)
    item.reason = reason
    item.source_type = summary.source_type
    item.source_system = summary.source_system
    item.rule_code = summary.rule_code
    item.rule_name = summary.rule_name
    item.severity = summary.severity
    item.category = summary.category
    item.verdict = summary.verdict
    item.confidence = summary.confidence
    item.review_reasons = list(summary.review_reasons)
    item.entity_keys = summary.entity_keys
    item.summary = summary.summary
    item.updated_at = _utc_now()
    return item


def _close_open_review_item_for_run(
    repository: ReviewQueueRepository,
    *,
    run_id: str,
    actor: ActorContext,
    reason: str,
) -> None:
    item = repository.get_open_review_item_by_run(run_id)
    if item is None:
        return
    item.status = ReviewQueueStatus.CLOSED
    item.closed_at = _utc_now()
    item.closed_by = actor
    item.close_reason = reason
    item.updated_at = item.closed_at
    repository.save_review_item(item)


def _review_reason(summary: AlertSummary) -> str | None:
    if summary.review_reasons:
        return summary.review_reasons[0].value
    if summary.needs_review:
        return "summary.needs_review"
    if summary.confidence is not None and summary.confidence < 0.75:
        return "low_confidence"
    if summary.verdict in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW, Verdict.SUSPICIOUS}:
        return "uncertain_verdict"
    if _severity_level(summary.severity) >= 2:
        return "high_severity"
    return None


def _review_priority(summary: AlertSummary) -> ReviewQueuePriority:
    if _severity_level(summary.severity) >= 2 or summary.verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return ReviewQueuePriority.HIGH
    if summary.confidence is not None and summary.confidence < 0.6:
        return ReviewQueuePriority.HIGH
    if summary.needs_review:
        return ReviewQueuePriority.MEDIUM
    return ReviewQueuePriority.LOW


def _severity_level(value: str | None) -> int:
    if value is None:
        return 0
    normalized = value.strip().lower()
    if normalized in {"critical", "high", "高危", "严重"}:
        return 2
    if normalized in {"medium", "中危"}:
        return 1
    return 0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _analysis_audit_record(
    run: AnalysisRun,
    *,
    actor: ActorContext,
    action: AuditAction,
    idempotency_key: str | None = None,
) -> DecisionAuditRecord:
    return DecisionAuditRecord(
        action=action,
        run_id=run.run_id,
        alert_id=run.alert_id,
        actor=actor,
        input_hash=run.input_hash,
        final_verdict=_current_verdict(run),
        confidence=_current_confidence(run),
        replay_of_run_id=run.replay_of_run_id,
        payload={
            "status": run.status.value,
            "pipeline_version": run.pipeline_version,
            "model_name": run.model_name,
            "prompt_version": run.prompt_version,
            "step_count": len(run.steps),
            "decision_policy_version": run.decision.policy_version if run.decision is not None else None,
            "confidence_source": run.decision.confidence_source.value if run.decision is not None else None,
            "confidence_is_calibrated": run.decision.confidence_is_calibrated if run.decision is not None else False,
            "calibrated_probability": run.decision.calibrated_probability if run.decision is not None else None,
            "calibration_profile_version": run.decision.calibration_profile_version if run.decision is not None else None,
            "evidence_state": run.decision.evidence_state.value if run.decision is not None else None,
            "review_reasons": [item.value for item in run.decision.review_reasons] if run.decision is not None else [],
            "evidence_grounded_count": (run.analysis_evidence_grounding.grounded_count if run.analysis_evidence_grounding is not None else None),
            "evidence_ungrounded_count": (run.analysis_evidence_grounding.ungrounded_count if run.analysis_evidence_grounding is not None else None),
            "failure_kind": run.failure.kind.value if run.failure is not None else None,
            "failure_retryable": run.failure.retryable if run.failure is not None else None,
            "idempotency_key": idempotency_key,
        },
    )


def _correction_audit_record(run: AnalysisRun, record: CorrectionRecord) -> DecisionAuditRecord:
    return DecisionAuditRecord(
        action=AuditAction.CORRECTION,
        run_id=run.run_id,
        alert_id=run.alert_id,
        actor=record.actor,
        input_hash=run.input_hash,
        previous_verdict=record.previous_verdict,
        final_verdict=record.corrected_verdict,
        confidence=record.corrected_confidence,
        replay_of_run_id=run.replay_of_run_id,
        correction_id=record.correction_id,
        payload={
            "reason": record.reason,
            "candidate_knowledge_status": record.candidate_knowledge_status,
            "memory_candidate_id": record.memory_candidate_id,
            "evidence_count": len(record.evidence),
            "automation_allowed": False,
        },
    )
