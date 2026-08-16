"""Stable public service entry points for SOC Agent use cases."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from soc_agent.context_bridge import skill_context_from_investigation_context
from soc_agent.contracts import (
    SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION,
    ActorAuthSource,
    ActorContext,
    ActorType,
    AlertInput,
    AlertSourceType,
    AlertSummary,
    AnalysisProviderInvocation,
    AnalysisRequestJournal,
    AnalysisRequestJournalStatus,
    AnalysisRun,
    AnalysisRunRecoveryCommand,
    AnalysisRunStatus,
    AuditAction,
    CorrectionCommand,
    CorrectionRecord,
    CorrelationQuery,
    CorrelationResult,
    Decision,
    DecisionAuditRecord,
    DecisionConfidenceSource,
    DecisionEvidenceState,
    DecisionReviewReason,
    EntrySurface,
    ExtractionReport,
    InvestigationContext,
    InvestigationEvidence,
    InvestigationTimelineItem,
    LLMAnalysisRequest,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    MessageSchemaStatus,
    NormalizationDriftReport,
    NormalizationDriftSample,
    NormalizationInspectionResult,
    NormalizationMonitoringResult,
    NormalizationReport,
    PipelineStepStatus,
    ReviewNoteCommand,
    ReviewNoteOrigin,
    ReviewNoteResult,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueuePriority,
    ReviewQueueStatus,
    RoleAdjudicationConfirmationCommand,
    RoleAdjudicationRevisionRecord,
    RuntimeFailureKind,
    SensitiveEvidenceMode,
    ServiceRequestContext,
    SimilarAlertQuery,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentApprovalRequestStatus,
    SocAgentApprovedActionCommand,
    SocAgentChatRequest,
    SocAgentChatResponse,
    SocAgentPermissionDecision,
    SocAgentRiskLevel,
    SocAgentRouteDecision,
    SocAgentStreamEvent,
    SocDaemonMessage,
    SocDaemonProcessResult,
    SocDispositionOutcomeRecord,
    SocDispositionProposalRecord,
    SocDomainTriageRequest,
    SocDomainTriageResult,
    SocEnrichmentExecutionCommand,
    SocEnrichmentExecutionStatus,
    SocEnrichmentExecutionTrigger,
    SocEvent,
    SocEventType,
    SocMemoryApplicabilitySpec,
    SocMemoryApplicabilityStatus,
    SocMemoryCandidate,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateReviewResult,
    SocMemoryCandidateStatus,
    SocMemoryDecisionDirective,
    SocMemoryDecisionEffect,
    SocMemoryDecisionImpact,
    SocMemoryMatch,
    SocMemoryQuery,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryRetrievalActivationResult,
    SocMemoryRetrievalResult,
    SocMemoryReviewEffect,
    SocMutationOperation,
    SocSkillResolution,
    UnifiedInvestigationView,
    Verdict,
)
from soc_agent.core.runtime import analyze_alert, build_analysis_request_for_payload, inspect_alert_normalization
from soc_agent.memory import (
    MemoryAdmissionOutcome,
    MemoryAdmissionService,
    MemoryPatternIneligibleError,
    SocMemoryCandidateSourceBridge,
    SocMemoryProfileRegistry,
    memory_candidate_command_from_review_note,
    memory_query_from_analysis_request,
)
from soc_agent.memory.scoring import (
    evaluate_memory_anchor_gate,
    evaluate_memory_applicability,
    score_memory_record,
)
from soc_agent.normalizers import load_mapping_config, normalize_alert_payload
from soc_agent.protocols import (
    AlertRepository,
    AlertSummaryRepository,
    AnalysisBeforeProviderHook,
    AnalysisPersistence,
    AnalysisRequestEnricher,
    AnalysisRuntime,
    AuthorizationEnrichmentRepository,
    DecisionAuditRepository,
    DecisionPolicy,
    InvestigationEvidenceRepository,
    LLMAnalyzer,
    MemoryCandidateRepository,
    MemoryEvolutionRepository,
    MemoryFeedbackObserver,
    MemoryPatternObserver,
    MemoryRecordRepository,
    NormalizationMaintenanceMonitor,
    PostAnalysisObserver,
    ReviewQueueRepository,
    RoleAdjudicationVerifier,
    SocActionAdapterRegistryPort,
    SocAgentApprovalGrantRepository,
    SocAgentApprovalRequestRepository,
    SocAutomationRepository,
    SocDispositionEvaluationRepository,
    SocDispositionProposalRepository,
    SocEnrichmentExecutionRepository,
    SocEventSink,
    SocExternalDispositionRepository,
    SocInvestigationWorkflowPort,
    SocMutationAuditRepository,
    SocMutationRepository,
    SocMutationUnitOfWork,
)
from soc_agent.skills import SocSkillResolver
from soc_agent.utils.hashing import stable_hash

from .access_control import require_actor_roles
from .errors import (
    SocEnrichmentWorkflowError,
    SocServiceAuthorizationError,
    SocServiceConflictError,
    SocServiceError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)
from .investigation_reporting import SocInvestigationReportingService
from .mutation_audit import (
    BufferedSocEventSink,
    build_mutation_audit,
    mutation_audit_repository_from,
    mutation_idempotency_key,
    mutation_uow_from,
    validate_mutation_retry,
)

logger = logging.getLogger(__name__)


class DeterministicAnalysisRuntime:
    """Adapter that exposes the current deterministic runtime as a protocol."""

    def __init__(
        self,
        *,
        analyzer: LLMAnalyzer | None = None,
        role_verifier: RoleAdjudicationVerifier | None = None,
        decision_policy: DecisionPolicy | None = None,
        analysis_request_enricher: AnalysisRequestEnricher | None = None,
        sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.REDACT,
    ) -> None:
        self._analyzer = analyzer
        self._role_verifier = role_verifier
        self._decision_policy = decision_policy
        self._analysis_request_enricher = analysis_request_enricher
        self._sensitive_evidence_mode = sensitive_evidence_mode

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun:
        return analyze_alert(
            payload,
            analyzer=self._analyzer,
            role_verifier=self._role_verifier,
            decision_policy=self._decision_policy,
            analysis_request_enricher=self._analysis_request_enricher,
            sensitive_evidence_mode=self._sensitive_evidence_mode,
        )

    def analyze_journaled(
        self,
        payload: Mapping[str, Any],
        *,
        before_provider: AnalysisBeforeProviderHook,
    ) -> AnalysisRun:
        return analyze_alert(
            payload,
            analyzer=self._analyzer,
            role_verifier=self._role_verifier,
            decision_policy=self._decision_policy,
            before_provider=before_provider,
            analysis_request_enricher=self._analysis_request_enricher,
            sensitive_evidence_mode=self._sensitive_evidence_mode,
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
        post_analysis_observers: Sequence[PostAnalysisObserver] = (),
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._runtime = runtime or DeterministicAnalysisRuntime()
        self._repository = repository
        self._summary_repository = summary_repository
        self._audit_repository = audit_repository
        self._review_queue_repository = review_queue_repository
        self._analysis_persistence = analysis_persistence
        self._normalization_maintenance_monitor = normalization_maintenance_monitor
        self._post_analysis_observers = tuple(post_analysis_observers)
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
        if previous.status is AnalysisRunStatus.RUNNING:
            raise SocServiceConflictError(f"run {run_id} is still running; use recover after its stale window")
        if previous.input_payload is None:
            raise SocServiceNotImplementedError(f"run {run_id} has no replayable input payload")

        request_context = context or ServiceRequestContext()
        return self._analyze(previous.input_payload, context=request_context, replay_of_run_id=run_id)

    def recover(
        self,
        command: AnalysisRunRecoveryCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        """Claim a stale running journal and replay its preserved source input."""

        if self._repository is None:
            raise SocServiceNotImplementedError("recover requires an AlertRepository")
        previous = self._repository.get_run(command.run_id)
        if previous is None:
            raise SocServiceNotFoundError(f"run {command.run_id} not found")
        if previous.input_payload is None:
            raise SocServiceNotImplementedError(f"run {command.run_id} has no replayable input payload")
        journal = previous.request_journal
        if journal is None:
            raise SocServiceConflictError(f"run {command.run_id} has no durable request journal")
        if previous.status not in {AnalysisRunStatus.RUNNING, AnalysisRunStatus.INTERRUPTED}:
            raise SocServiceConflictError(f"run {command.run_id} is {previous.status.value}, not recoverable")

        request_context = context or ServiceRequestContext()
        recovery_context = request_context.model_copy(update={"idempotency_key": request_context.idempotency_key or f"analysis_recovery:{command.run_id}"})
        if journal.recovery_run_id is not None:
            recovered = self._repository.get_run(journal.recovery_run_id)
            if recovered is not None:
                return recovered

        now = _utc_now()
        stale_before = now - timedelta(seconds=command.stale_after_seconds)
        if previous.status is AnalysisRunStatus.RUNNING:
            if journal.provider_started_at > stale_before:
                raise SocServiceConflictError(f"run {command.run_id} has not exceeded the {command.stale_after_seconds}s recovery window")
            previous.status = AnalysisRunStatus.INTERRUPTED
            previous.ended_at = now
            _set_active_request_journal(
                previous,
                journal.model_copy(
                    update={
                        "status": AnalysisRequestJournalStatus.INTERRUPTED,
                        "finalized_at": now,
                        "recovered_at": now,
                        "recovered_by": recovery_context.actor,
                        "recovery_reason": command.reason,
                    }
                ),
            )
            claim_run_recovery = getattr(self._repository, "claim_run_recovery", None)
            if callable(claim_run_recovery):
                if not claim_run_recovery(previous, expected_status=AnalysisRunStatus.RUNNING):
                    raise SocServiceConflictError(f"run {command.run_id} recovery was already claimed")
            else:
                self._repository.save_run(previous)
        elif journal.recovered_at is not None and journal.recovered_at > stale_before:
            raise SocServiceConflictError(f"run {command.run_id} recovery is already in progress")
        else:
            _set_active_request_journal(
                previous,
                journal.model_copy(
                    update={
                        "recovered_at": now,
                        "recovered_by": recovery_context.actor,
                        "recovery_reason": command.reason,
                    }
                ),
            )
            self._repository.save_run(previous)

        recovered = self._analyze(
            previous.input_payload,
            context=recovery_context,
            replay_of_run_id=previous.run_id,
        )
        refreshed = self._repository.get_run(previous.run_id) or previous
        if refreshed.request_journal is not None:
            _set_active_request_journal(
                refreshed,
                refreshed.request_journal.model_copy(update={"recovery_run_id": recovered.run_id}),
            )
            self._repository.save_run(refreshed)
        return recovered

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
            self._observe_post_analysis(existing_run, context=context)
            self._emit_analysis_completion(existing_run, context=context, replay_of_run_id=replay_of_run_id, idempotent_replay=True)
            return existing_run

        run = self._run_runtime_with_journal(
            payload,
            context=context,
            action=audit_action,
            replay_of_run_id=replay_of_run_id,
        )
        run.replay_of_run_id = replay_of_run_id
        _finalize_request_journal(run)
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

        self._observe_post_analysis(run, context=context)

        self._emit_analysis_completion(run, context=context, replay_of_run_id=replay_of_run_id, idempotent_replay=False)
        return run

    def _observe_post_analysis(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> None:
        for observer in self._post_analysis_observers:
            try:
                observer.observe(run, context=context)
            except Exception:  # noqa: BLE001 - post-analysis shadow work must not fail the persisted run
                logger.exception(
                    "post-analysis observer %s failed for run %s",
                    type(observer).__name__,
                    run.run_id,
                )

    def _run_runtime_with_journal(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext,
        action: AuditAction,
        replay_of_run_id: str | None,
    ) -> AnalysisRun:
        analyze_journaled = getattr(self._runtime, "analyze_journaled", None)
        if self._repository is None or not callable(analyze_journaled):
            return self._runtime.analyze(payload)

        def persist_before_provider(
            run: AnalysisRun,
            request: LLMAnalysisRequest,
            invocation: AnalysisProviderInvocation,
        ) -> None:
            run.replay_of_run_id = replay_of_run_id
            _complete_active_request_journal(run)
            journal = _request_journal_from_analysis_request(
                run,
                request,
                context=context,
                action=action,
                invocation=invocation,
            )
            _set_active_request_journal(run, journal)
            self._repository.save_run(run.model_copy(deep=True))

        return analyze_journaled(payload, before_provider=persist_before_provider)

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

    HUMAN_MUTATION_ROLES = frozenset({"analyst", "soc_analyst", "soc_admin"})

    def __init__(
        self,
        *,
        repository: AlertRepository | None = None,
        summary_repository: AlertSummaryRepository | None = None,
        audit_repository: DecisionAuditRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        evidence_repository: InvestigationEvidenceRepository | None = None,
        enrichment_execution_repository: SocEnrichmentExecutionRepository | None = None,
        authorization_enrichment_repository: AuthorizationEnrichmentRepository | None = None,
        disposition_proposal_repository: SocDispositionProposalRepository | None = None,
        disposition_evaluation_repository: SocDispositionEvaluationRepository | None = None,
        external_disposition_repository: SocExternalDispositionRepository | None = None,
        memory_candidate_repository: MemoryCandidateRepository | None = None,
        memory_record_repository: MemoryRecordRepository | None = None,
        memory_feedback_observer: MemoryFeedbackObserver | None = None,
        memory_profile_registry: SocMemoryProfileRegistry | None = None,
        mutation_audit_repository: SocMutationAuditRepository | None = None,
        mutation_uow: SocMutationUnitOfWork | None = None,
        event_sink: SocEventSink | None = None,
        _transaction_active: bool = False,
    ) -> None:
        self._repository = repository
        self._summary_repository = summary_repository
        self._audit_repository = audit_repository
        self._review_queue_repository = review_queue_repository
        self._evidence_repository = evidence_repository
        self._enrichment_execution_repository = enrichment_execution_repository
        self._authorization_enrichment_repository = authorization_enrichment_repository
        self._disposition_proposal_repository = disposition_proposal_repository
        self._disposition_evaluation_repository = disposition_evaluation_repository
        self._external_disposition_repository = external_disposition_repository
        self._memory_candidate_repository = memory_candidate_repository
        self._memory_record_repository = memory_record_repository
        self._memory_profile_registry = memory_profile_registry or SocMemoryProfileRegistry()
        self._event_sink = event_sink or NoopEventSink()
        self._mutation_audit_repository = mutation_audit_repository or mutation_audit_repository_from(
            repository,
            review_queue_repository,
            memory_candidate_repository,
        )
        self._mutation_uow = mutation_uow or mutation_uow_from(
            repository,
            review_queue_repository,
            memory_candidate_repository,
        )
        self._transaction_active = _transaction_active
        self._memory_feedback_observer = memory_feedback_observer
        if self._memory_feedback_observer is None and self._memory_record_repository is not None and _supports_memory_evolution_repository(repository):
            from .memory_evolution import SocMemoryEvolutionService

            self._memory_feedback_observer = SocMemoryEvolutionService(
                repository=cast(MemoryEvolutionRepository, repository),
                memory_record_repository=self._memory_record_repository,
                automation_repository=(cast(SocAutomationRepository, repository) if callable(getattr(repository, "list_decision_transitions", None)) else None),
                mutation_audit_repository=self._mutation_audit_repository,
                mutation_uow=self._mutation_uow,
                transaction_active=self._transaction_active,
            )

    def correct(
        self,
        command: CorrectionCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        return self._correct(
            command,
            context=context or ServiceRequestContext(),
            confidence_source=DecisionConfidenceSource.HUMAN_CONFIRMATION,
        )

    def correct_external(
        self,
        command: CorrectionCommand,
        *,
        context: ServiceRequestContext,
    ) -> AnalysisRun:
        """Apply a correction already admitted by the external-disposition service."""

        return self._correct(
            command,
            context=context,
            confidence_source=DecisionConfidenceSource.EXTERNAL_DISPOSITION,
        )

    def _correct(
        self,
        command: CorrectionCommand,
        *,
        context: ServiceRequestContext,
        confidence_source: DecisionConfidenceSource,
    ) -> AnalysisRun:
        request_context = context or ServiceRequestContext()
        allowed_roles = self.HUMAN_MUTATION_ROLES
        if confidence_source is DecisionConfidenceSource.EXTERNAL_DISPOSITION:
            allowed_roles = frozenset({"external_disposition_adapter", "soc_admin"})
        require_actor_roles(
            request_context,
            allowed_roles,
            operation="correcting an analysis run",
        )
        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as repository:
                result = self._transactional_clone(repository, event_sink=buffered_events)._correct(
                    command,
                    context=request_context,
                    confidence_source=confidence_source,
                )
            buffered_events.flush()
            return result
        if self._repository is None:
            raise SocServiceNotImplementedError("correct requires an AlertRepository")

        existing_audit = self._find_mutation_audit(
            SocMutationOperation.REVIEW_CORRECT,
            request_context,
        )
        command_payload = {
            **command.model_dump(mode="json"),
            "confidence_source": confidence_source.value,
        }
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="analysis_run",
                target_id=command.run_id,
            )
            existing_run = self._repository.get_run(command.run_id)
            if existing_run is None:
                raise SocServiceConflictError(f"mutation audit {existing_audit.audit_id} references missing run {command.run_id}")
            return existing_run

        run = self._repository.get_run(command.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {command.run_id} not found")

        memory_uses = self._memory_feedback_observer.capture_run_usage(run) if self._memory_feedback_observer is not None else []
        previous_verdict = _current_verdict(run)
        effective_confidence = command.corrected_confidence if command.corrected_confidence is not None else 1.0
        confidence_explanation = _correction_confidence_explanation(
            confidence_source,
            explicit=command.corrected_confidence is not None,
        )
        record = CorrectionRecord(
            run_id=run.run_id,
            previous_verdict=previous_verdict,
            corrected_verdict=command.corrected_verdict,
            reason=command.reason,
            corrected_confidence=effective_confidence,
            confidence_source=confidence_source,
            confidence_was_explicit=command.corrected_confidence is not None,
            confidence_policy_version="soc.correction_policy.v1",
            confidence_explanation=confidence_explanation,
            actor=request_context.actor,
            evidence=command.evidence,
            promote_to_memory=command.promote_to_memory,
            memory_use_ids=[item.use_id for item in memory_uses],
            candidate_knowledge_status="not_created",
        )
        run.corrections.append(record)
        run.decision = Decision(
            verdict=command.corrected_verdict,
            confidence=effective_confidence,
            confidence_source=confidence_source,
            confidence_is_calibrated=False,
            calibrated_probability=None,
            calibration_profile_version=None,
            evidence_state=(run.decision.evidence_state if run.decision is not None else DecisionEvidenceState.PARTIAL),
            suggested_action=run.decision.suggested_action if run.decision is not None else "manual correction recorded",
            needs_review=False,
            reason=command.reason,
            policy_version="soc.correction_policy.v1",
            confidence_explanation=confidence_explanation,
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
        memory_outcome = self._admit_correction_memory_candidate(
            run,
            record,
            queue_item=review_item,
            context=request_context,
        )
        if memory_outcome is not None:
            record.memory_admission = memory_outcome.decision
            record.candidate_knowledge_status = "pending_review" if memory_outcome.candidate is not None else "observed_only"
            record.memory_candidate_id = memory_outcome.candidate.candidate_id if memory_outcome.candidate is not None else None
        if self._memory_feedback_observer is not None:
            feedback = self._memory_feedback_observer.record_correction_feedback(
                run,
                record,
                context=request_context,
            )
            record = record.model_copy(
                update={
                    "memory_feedback_ids": [item.feedback_id for item in feedback.feedback_events],
                    "memory_revision_proposal_ids": [item.proposal_id for item in feedback.revision_proposals],
                    "suspended_memory_ids": feedback.suspended_memory_ids,
                }
            )
        run.corrections[-1] = record
        self._repository.save_run(run)
        if self._audit_repository is not None:
            self._audit_repository.save_audit_record(_correction_audit_record(run, record))
        self._append_mutation_audit(
            build_mutation_audit(
                operation=SocMutationOperation.REVIEW_CORRECT,
                target_type="analysis_run",
                target_id=run.run_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                queue_id=review_item.queue_id if review_item is not None else None,
                context=request_context,
                reason=command.reason,
                command=command_payload,
                result_ref=record.correction_id,
                payload={
                    "previous_verdict": previous_verdict.value if previous_verdict is not None else None,
                    "corrected_verdict": command.corrected_verdict.value,
                    "confidence_source": confidence_source.value,
                    "confidence_policy_version": record.confidence_policy_version,
                    "correction_id": record.correction_id,
                    "memory_candidate_id": record.memory_candidate_id,
                    "memory_admission_status": (record.memory_admission.status.value if record.memory_admission is not None else None),
                    "memory_admission_policy_version": (record.memory_admission.policy_version if record.memory_admission is not None else None),
                    "memory_use_ids": record.memory_use_ids,
                    "memory_feedback_ids": record.memory_feedback_ids,
                    "memory_revision_proposal_ids": record.memory_revision_proposal_ids,
                    "suspended_memory_ids": record.suspended_memory_ids,
                },
            )
        )
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
                    "confidence_source": record.confidence_source.value,
                    "confidence_policy_version": record.confidence_policy_version,
                    "candidate_knowledge_status": record.candidate_knowledge_status,
                    "memory_candidate_id": record.memory_candidate_id,
                    "memory_admission_status": (record.memory_admission.status.value if record.memory_admission is not None else None),
                    "memory_feedback_ids": record.memory_feedback_ids,
                    "memory_revision_proposal_ids": record.memory_revision_proposal_ids,
                    "suspended_memory_ids": record.suspended_memory_ids,
                },
            )
        )
        return run

    def _admit_correction_memory_candidate(
        self,
        run: AnalysisRun,
        record: CorrectionRecord,
        *,
        queue_item: ReviewQueueItem | None,
        context: ServiceRequestContext,
    ) -> MemoryAdmissionOutcome | None:
        if self._memory_candidate_repository is None:
            return None
        memory_service = SocMemoryService(
            candidate_repository=self._memory_candidate_repository,
            event_sink=self._event_sink,
        )
        return SocMemoryCandidateSourceBridge(
            memory_service,
            profile_registry=self._memory_profile_registry,
        ).admit_from_correction(
            run,
            record,
            queue_item=queue_item,
            context=context,
        )

    def confirm_role_adjudication(
        self,
        command: RoleAdjudicationConfirmationCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> RoleAdjudicationRevisionRecord:
        """Append one human role/response-target revision without mutating model output."""

        request_context = context or ServiceRequestContext()
        require_actor_roles(
            request_context,
            self.HUMAN_MUTATION_ROLES,
            operation="confirming alert roles",
        )
        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as repository:
                record = self._transactional_clone(
                    repository,
                    event_sink=buffered_events,
                ).confirm_role_adjudication(command, context=request_context)
            buffered_events.flush()
            return record
        if self._repository is None:
            raise SocServiceNotImplementedError("confirm_role_adjudication requires an AlertRepository")

        command_payload = command.model_dump(mode="json")
        existing_audit = self._find_mutation_audit(
            SocMutationOperation.REVIEW_ROLE_CONFIRM,
            request_context,
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="analysis_run",
                target_id=command.run_id,
            )
            existing_run = self._repository.get_run(command.run_id)
            if existing_run is None:
                raise SocServiceConflictError(f"mutation audit {existing_audit.audit_id} references missing run {command.run_id}")
            record = next(
                (item for item in existing_run.role_adjudication_revisions if item.revision_id == existing_audit.result_ref),
                None,
            )
            if record is None:
                raise SocServiceConflictError(f"mutation audit {existing_audit.audit_id} references missing role revision")
            return record

        run = self._repository.get_run(command.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {command.run_id} not found")
        if run.analysis is None:
            raise SocServiceConflictError(f"run {command.run_id} has no analyzer role result to confirm")
        current_revision = len(run.role_adjudication_revisions)
        if command.expected_revision != current_revision:
            raise SocServiceConflictError(f"role adjudication expected revision {command.expected_revision}, found {current_revision}")
        previous = run.role_adjudication_revisions[-1] if run.role_adjudication_revisions else None
        record = RoleAdjudicationRevisionRecord(
            run_id=run.run_id,
            revision=current_revision + 1,
            previous_revision_id=(previous.revision_id if previous is not None else None),
            base_model_adjudication_hash=stable_hash(run.analysis.role_adjudication.model_dump(mode="json")),
            previous_effective_hash=(
                stable_hash(
                    {
                        "roles": previous.roles,
                        "response_targets": previous.response_targets,
                    }
                )
                if previous is not None
                else None
            ),
            roles=command.roles,
            response_targets=command.response_targets,
            reason=command.reason,
            actor=request_context.actor,
        )
        run.role_adjudication_revisions.append(record)
        self._repository.save_run(run)
        self._append_mutation_audit(
            build_mutation_audit(
                operation=SocMutationOperation.REVIEW_ROLE_CONFIRM,
                target_type="analysis_run",
                target_id=run.run_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                context=request_context,
                reason=command.reason,
                command=command_payload,
                result_ref=record.revision_id,
                payload={
                    "revision": record.revision,
                    "base_model_adjudication_hash": record.base_model_adjudication_hash,
                    "previous_revision_id": record.previous_revision_id,
                    "role_count": len(record.roles),
                    "response_target_count": len(record.response_targets),
                    "automation_allowed": False,
                },
            )
        )
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.REVIEW_ROLE_CONFIRMED,
                request_id=request_context.request_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                actor=request_context.actor,
                payload={
                    "revision_id": record.revision_id,
                    "revision": record.revision,
                    "role_count": len(record.roles),
                    "response_target_count": len(record.response_targets),
                },
            )
        )
        return record

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
        request_context = context or ServiceRequestContext()
        require_actor_roles(
            request_context,
            self.HUMAN_MUTATION_ROLES,
            operation="closing a review queue item",
        )
        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return self._transactional_clone(repository, event_sink=self._event_sink).close_queue_item(
                    command,
                    context=request_context,
                )
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("close_queue_item requires a ReviewQueueRepository")

        command_payload = command.model_dump(mode="json")
        existing_audit = self._find_mutation_audit(
            SocMutationOperation.REVIEW_CLOSE,
            request_context,
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="review_queue_item",
                target_id=command.queue_id,
            )
            existing_item = self._review_queue_repository.get_review_item(command.queue_id)
            if existing_item is None:
                raise SocServiceConflictError(f"mutation audit {existing_audit.audit_id} references missing queue item {command.queue_id}")
            return existing_item

        item = self._review_queue_repository.get_review_item(command.queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {command.queue_id} not found")

        item.status = ReviewQueueStatus.CLOSED
        item.closed_at = _utc_now()
        item.closed_by = request_context.actor
        item.close_reason = command.reason
        item.updated_at = item.closed_at
        self._review_queue_repository.save_review_item(item)
        self._append_mutation_audit(
            build_mutation_audit(
                operation=SocMutationOperation.REVIEW_CLOSE,
                target_type="review_queue_item",
                target_id=item.queue_id,
                run_id=item.run_id,
                alert_id=item.alert_id,
                queue_id=item.queue_id,
                context=request_context,
                reason=command.reason,
                command=command_payload,
                result_ref=item.queue_id,
                payload={"previous_status": ReviewQueueStatus.OPEN.value, "status": item.status.value},
            )
        )
        return item

    def add_note(
        self,
        command: ReviewNoteCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> ReviewNoteResult:
        request_context = context or ServiceRequestContext()
        require_actor_roles(
            request_context,
            self.HUMAN_MUTATION_ROLES,
            operation="adding a review note",
        )
        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as repository:
                result = self._transactional_clone(repository, event_sink=buffered_events).add_note(
                    command,
                    context=request_context,
                )
            buffered_events.flush()
            return result
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("add_note requires a ReviewQueueRepository")
        if self._repository is None:
            raise SocServiceNotImplementedError("add_note requires an AlertRepository")
        if self._memory_candidate_repository is None:
            raise SocServiceNotImplementedError("add_note requires a MemoryCandidateRepository")

        command_payload = command.model_dump(mode="json")
        existing_audit = self._find_mutation_audit(
            SocMutationOperation.REVIEW_NOTE,
            request_context,
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="review_queue_item",
                target_id=command.queue_id,
            )
            item = self._review_queue_repository.get_review_item(command.queue_id)
            if item is None:
                raise SocServiceConflictError(f"mutation audit {existing_audit.audit_id} references missing queue item {command.queue_id}")
            run = self._repository.get_run(item.run_id)
            if run is None:
                raise SocServiceConflictError(f"mutation audit {existing_audit.audit_id} references missing run {item.run_id}")
            candidate_id = existing_audit.payload.get("candidate_id")
            candidate = self._memory_candidate_repository.get_memory_candidate(candidate_id) if isinstance(candidate_id, str) else None
            admission = MemoryAdmissionService().evaluate(
                memory_candidate_command_from_review_note(
                    run,
                    command,
                    queue_item=item,
                    source_surface=request_context.actor.surface,
                )
            )
            if candidate is not None:
                admission = admission.model_copy(update={"candidate_id": candidate.candidate_id})
            return ReviewNoteResult(
                queue_item=item,
                memory_candidate=candidate,
                memory_admission=admission,
            )

        item = self._review_queue_repository.get_review_item(command.queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {command.queue_id} not found")
        if command.origin is ReviewNoteOrigin.ACCEPTED_LEAD_AGENT_CONCLUSION and item.status is not ReviewQueueStatus.OPEN:
            raise SocServiceConflictError(f"review queue item {command.queue_id} is closed; Lead Agent conclusions can only be accepted for open review work")

        run = self._repository.get_run(item.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {item.run_id} not found")

        memory_service = SocMemoryService(
            candidate_repository=self._memory_candidate_repository,
            event_sink=self._event_sink,
        )
        outcome = SocMemoryCandidateSourceBridge(memory_service).admit_from_review_note(
            run,
            command,
            queue_item=item,
            context=request_context,
        )
        candidate = outcome.candidate
        self._append_mutation_audit(
            build_mutation_audit(
                operation=SocMutationOperation.REVIEW_NOTE,
                target_type="review_queue_item",
                target_id=item.queue_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                queue_id=item.queue_id,
                context=request_context,
                reason="analyst review note recorded",
                command=command_payload,
                result_ref=(candidate.candidate_id if candidate is not None else f"admission:{outcome.decision.command_hash[:16]}"),
                payload={
                    "candidate_id": (candidate.candidate_id if candidate is not None else None),
                    "memory_admission_status": outcome.decision.status.value,
                    "memory_admission_policy_version": outcome.decision.policy_version,
                    "memory_admission_quality_score": outcome.decision.quality_score,
                    "memory_admission_command_hash": outcome.decision.command_hash,
                    "scenario_key": command.scenario_key,
                    "domain": command.domain.value if command.domain is not None else None,
                },
            )
        )
        return ReviewNoteResult(
            queue_item=item,
            memory_candidate=candidate,
            memory_admission=outcome.decision,
        )

    def _transactional_clone(
        self,
        repository: SocMutationRepository,
        *,
        event_sink: SocEventSink,
    ) -> SocReviewService:
        return SocReviewService(
            repository=repository if self._repository is not None else None,
            summary_repository=repository if self._summary_repository is not None else None,
            audit_repository=repository if self._audit_repository is not None else None,
            review_queue_repository=(repository if self._review_queue_repository is not None else None),
            evidence_repository=repository if self._evidence_repository is not None else None,
            enrichment_execution_repository=(repository if self._enrichment_execution_repository is not None else None),
            authorization_enrichment_repository=(repository if self._authorization_enrichment_repository is not None else None),
            disposition_proposal_repository=(repository if self._disposition_proposal_repository is not None else None),
            disposition_evaluation_repository=(repository if self._disposition_evaluation_repository is not None else None),
            external_disposition_repository=(repository if self._external_disposition_repository is not None else None),
            memory_candidate_repository=(repository if self._memory_candidate_repository is not None else None),
            memory_record_repository=(repository if self._memory_record_repository is not None else None),
            memory_profile_registry=self._memory_profile_registry,
            mutation_audit_repository=repository,
            mutation_uow=self._mutation_uow,
            event_sink=event_sink,
            _transaction_active=True,
        )

    def _find_mutation_audit(
        self,
        operation: SocMutationOperation,
        context: ServiceRequestContext,
    ):
        if self._mutation_audit_repository is None:
            return None
        return self._mutation_audit_repository.find_mutation_audit_by_idempotency_key(
            operation,
            mutation_idempotency_key(context),
        )

    def _append_mutation_audit(self, record) -> None:
        if self._mutation_audit_repository is not None:
            self._mutation_audit_repository.append_mutation_audit(record)

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
        investigation_addenda = (
            SocInvestigationReportingService(
                run_repository=self._repository,
                execution_repository=self._enrichment_execution_repository,
                evidence_repository=self._evidence_repository,
            ).list_addenda_for_run(item.run_id, limit=10)
            if self._enrichment_execution_repository is not None and self._evidence_repository is not None
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
        disposition_outcomes = (
            self._disposition_evaluation_repository.list_disposition_outcomes(
                queue_id=item.queue_id,
                limit=50,
            )
            if self._disposition_evaluation_repository is not None
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
            investigation_addenda=investigation_addenda,
            authorization_enrichments=authorization_enrichments,
            disposition_proposals=disposition_proposals,
            disposition_outcomes=disposition_outcomes,
            external_dispositions=external_dispositions,
            memory_candidates=memory_candidates,
            correlation_result=correlation_result,
        )
        if self._memory_record_repository is not None:
            relevant_memories = SocMemoryService(record_repository=self._memory_record_repository).find_relevant_records(
                _memory_query_from_investigation_context(
                    context,
                    profile_registry=self._memory_profile_registry,
                )
            )
            context = context.model_copy(update={"relevant_memories": relevant_memories})
        domain_triage_results = _domain_triage_results_for_context(context)
        context = context.model_copy(update={"domain_triage_results": domain_triage_results})
        return context.model_copy(update={"investigation_view": _unified_investigation_view_from_context(context)})


class SocMemoryService:
    """Facts, lessons, and reviewable candidate knowledge service."""

    REVIEWER_ROLES = frozenset({"analyst", "soc_analyst", "soc_memory_reviewer", "soc_admin"})
    RETRIEVAL_ENABLE_ROLES = frozenset({"soc_memory_reviewer", "soc_admin"})
    RETRIEVAL_GOVERNOR_ROLES = frozenset({*RETRIEVAL_ENABLE_ROLES, "soc_memory_safety_monitor"})

    def __init__(
        self,
        *,
        candidate_repository: MemoryCandidateRepository | None = None,
        record_repository: MemoryRecordRepository | None = None,
        mutation_audit_repository: SocMutationAuditRepository | None = None,
        mutation_uow: SocMutationUnitOfWork | None = None,
        event_sink: SocEventSink | None = None,
        now_provider: Callable[[], datetime] | None = None,
        _transaction_active: bool = False,
    ) -> None:
        self._candidate_repository = candidate_repository
        self._record_repository = record_repository
        self._event_sink = event_sink or NoopEventSink()
        self._mutation_uow = mutation_uow or mutation_uow_from(
            candidate_repository,
            record_repository,
        )
        self._mutation_audit_repository = mutation_audit_repository
        if self._mutation_audit_repository is None and self._mutation_uow is not None:
            self._mutation_audit_repository = mutation_audit_repository_from(
                candidate_repository,
                record_repository,
            )
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._transaction_active = _transaction_active

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
            applicability=command.applicability,
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
        request_context = context or ServiceRequestContext()
        require_actor_roles(
            request_context,
            self.REVIEWER_ROLES,
            operation="reviewing a memory candidate",
        )
        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as repository:
                result = SocMemoryService(
                    candidate_repository=repository,
                    record_repository=repository,
                    mutation_audit_repository=repository,
                    mutation_uow=self._mutation_uow,
                    event_sink=buffered_events,
                    now_provider=self._now_provider,
                    _transaction_active=True,
                ).review_candidate(command, context=request_context)
            buffered_events.flush()
            return result
        if self._candidate_repository is None:
            raise SocServiceNotImplementedError("review_candidate requires a MemoryCandidateRepository")

        command_payload = command.model_dump(mode="json")
        existing_audit = (
            self._mutation_audit_repository.find_mutation_audit_by_idempotency_key(
                SocMutationOperation.MEMORY_REVIEW,
                mutation_idempotency_key(request_context),
            )
            if self._mutation_audit_repository is not None
            else None
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="memory_candidate",
                target_id=command.candidate_id,
            )
            candidate = self.get_candidate(command.candidate_id)
            memory_record = self._record_repository.get_memory_record_by_candidate_id(candidate.candidate_id) if self._record_repository is not None else None
            previous_status_value = existing_audit.payload.get("previous_status")
            previous_status = SocMemoryCandidateStatus(previous_status_value) if isinstance(previous_status_value, str) else candidate.status
            return SocMemoryCandidateReviewResult(
                candidate=candidate,
                memory_record=memory_record,
                previous_status=previous_status,
                decision=command.decision,
                reviewed_at=candidate.reviewed_at or existing_audit.occurred_at,
            )

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
            _validate_memory_record_applicability(
                candidate,
                command.record_applicability,
            )
            effective_command = _materialize_memory_review_directive(
                candidate,
                command,
            )
            _validate_memory_decision_directive(candidate, effective_command)
            if self._record_repository is None:
                raise SocServiceNotImplementedError("confirming a memory candidate requires a MemoryRecordRepository")
            candidate = self._transition_candidate(
                candidate,
                status=SocMemoryCandidateStatus.CONFIRMED,
                command=effective_command,
                actor=request_context.actor,
                reviewed_at=reviewed_at,
            )
            memory_record = self._record_repository.get_memory_record_by_candidate_id(candidate.candidate_id)
            if memory_record is None:
                memory_record = _memory_record_from_candidate(
                    candidate,
                    command=effective_command,
                    actor=request_context.actor,
                    created_at=reviewed_at,
                )
                self._record_repository.save_memory_record(memory_record)
            if command.activate_retrieval:
                activation = self.set_retrieval_activation(
                    SocMemoryRetrievalActivationCommand(
                        memory_id=memory_record.memory_id,
                        action=SocMemoryRetrievalActivationAction.ENABLE,
                        expected_record_version=memory_record.version,
                        reason=command.reason,
                        activation_valid_until=command.activation_valid_until,
                        review_after_days=command.activation_review_after_days,
                        metadata={
                            **command.metadata,
                            "source": "memory_candidate_confirmation",
                            "candidate_id": candidate.candidate_id,
                        },
                    ),
                    context=request_context,
                )
                memory_record = activation.record
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
                    previous_record_version = memory_record.version
                    memory_record = memory_record.model_copy(
                        update={
                            "version": previous_record_version + 1,
                            "status": record_status,
                            "retrieval_enabled": False,
                            "retrieval_policy_version": SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION,
                            "retrieval_valid_until": None,
                            "retrieval_review_due_at": None,
                            "retrieval_updated_by": request_context.actor,
                            "retrieval_updated_at": reviewed_at,
                            "retrieval_reason": command.reason,
                            "updated_at": reviewed_at,
                            "deprecated_by": request_context.actor,
                            "deprecated_at": reviewed_at,
                            "deprecation_reason": command.reason,
                            "metadata": {**memory_record.metadata, **command.metadata},
                        }
                    )
                    if not self._record_repository.compare_and_set_memory_record(
                        memory_record,
                        expected_version=previous_record_version,
                    ):
                        raise SocServiceConflictError(f"memory record {memory_record.memory_id} changed during candidate review")
        else:
            raise SocServiceError(f"unsupported memory review decision: {command.decision}")

        self._candidate_repository.save_memory_candidate(candidate)
        if self._mutation_audit_repository is not None:
            self._mutation_audit_repository.append_mutation_audit(
                build_mutation_audit(
                    operation=SocMutationOperation.MEMORY_REVIEW,
                    target_type="memory_candidate",
                    target_id=candidate.candidate_id,
                    run_id=candidate.source.run_id,
                    alert_id=candidate.source.alert_id,
                    queue_id=candidate.source.queue_id,
                    context=request_context,
                    reason=command.reason,
                    command=command_payload,
                    result_ref=(memory_record.memory_id if memory_record is not None else candidate.candidate_id),
                    payload={
                        "previous_status": previous_status.value,
                        "status": candidate.status.value,
                        "decision": command.decision.value,
                        "memory_id": memory_record.memory_id if memory_record is not None else None,
                        "retrieval_enabled": (memory_record.retrieval_enabled if memory_record is not None else None),
                    },
                )
            )
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

    def set_retrieval_activation(
        self,
        command: SocMemoryRetrievalActivationCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocMemoryRetrievalActivationResult:
        """Apply one governed, version-controlled retrieval transition."""

        request_context = context or ServiceRequestContext()
        require_actor_roles(
            request_context,
            self.RETRIEVAL_GOVERNOR_ROLES,
            operation="changing SOC memory retrieval activation",
        )
        if command.action is SocMemoryRetrievalActivationAction.ENABLE and not self.RETRIEVAL_ENABLE_ROLES.intersection(request_context.actor.roles):
            raise SocServiceAuthorizationError("enabling SOC memory retrieval requires soc_memory_reviewer or soc_admin")
        if self._mutation_uow is not None and not self._transaction_active:
            buffered_events = BufferedSocEventSink(self._event_sink)
            with self._mutation_uow.mutation_transaction() as repository:
                result = SocMemoryService(
                    candidate_repository=(repository if self._candidate_repository is not None else None),
                    record_repository=repository,
                    mutation_audit_repository=repository,
                    mutation_uow=self._mutation_uow,
                    event_sink=buffered_events,
                    now_provider=self._now_provider,
                    _transaction_active=True,
                ).set_retrieval_activation(command, context=request_context)
            buffered_events.flush()
            return result
        if self._record_repository is None:
            raise SocServiceNotImplementedError("set_retrieval_activation requires a MemoryRecordRepository")
        if self._mutation_audit_repository is None:
            raise SocServiceNotImplementedError("set_retrieval_activation requires a SocMutationAuditRepository")

        command_payload = command.model_dump(mode="json")
        existing_audit = self._mutation_audit_repository.find_mutation_audit_by_idempotency_key(
            SocMutationOperation.MEMORY_RETRIEVAL_ACTIVATION,
            mutation_idempotency_key(request_context),
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="memory_record",
                target_id=command.memory_id,
            )
            record = self.get_record(command.memory_id)
            result_version = existing_audit.payload.get("result_record_version")
            if not isinstance(result_version, int) or record.version != result_version:
                raise SocServiceConflictError(f"memory retrieval retry for {command.memory_id} no longer references the current version")
            previous_version = existing_audit.payload.get("previous_record_version")
            previous_enabled = existing_audit.payload.get("previous_retrieval_enabled")
            if not isinstance(previous_version, int) or not isinstance(previous_enabled, bool):
                raise SocServiceConflictError("memory retrieval audit is missing transition provenance")
            return SocMemoryRetrievalActivationResult(
                record=record,
                action=command.action,
                previous_record_version=previous_version,
                previous_retrieval_enabled=previous_enabled,
                audit_id=existing_audit.audit_id,
                policy_version=command.policy_version,
                changed_at=record.retrieval_updated_at or existing_audit.occurred_at,
            )

        record = self.get_record(command.memory_id)
        if record.version != command.expected_record_version:
            raise SocServiceConflictError(f"memory record {record.memory_id} expected version {command.expected_record_version}, found {record.version}")

        now = self._now_provider()
        if now.utcoffset() is None:
            raise SocServiceError("memory service clock must be timezone-aware")
        previous_version = record.version
        previous_enabled = record.retrieval_enabled
        if command.action is SocMemoryRetrievalActivationAction.ENABLE:
            if record.status is not SocMemoryRecordStatus.CONFIRMED:
                raise SocServiceError(f"cannot enable retrieval for memory in status {record.status.value}")
            if record.retrieval_enabled:
                raise SocServiceConflictError(f"memory record {record.memory_id} retrieval is already enabled")
            if record.validity.valid_from > now:
                raise SocServiceError("cannot enable retrieval before the memory validity window starts")
            if record.validity.valid_until is not None and record.validity.valid_until <= now:
                raise SocServiceError("cannot enable retrieval for expired memory")

            activation_valid_until = command.activation_valid_until
            review_after_days = command.review_after_days
            if activation_valid_until is None or review_after_days is None:
                raise SocServiceError("enable requires activation validity and review scheduling")
            if activation_valid_until <= now:
                raise SocServiceError("activation_valid_until must be in the future")
            if record.validity.valid_until is not None and activation_valid_until > record.validity.valid_until:
                raise SocServiceError("retrieval activation cannot outlive the confirmed memory validity window")
            review_due_at = now + timedelta(days=review_after_days)
            if review_due_at > activation_valid_until:
                raise SocServiceError("retrieval review must be due no later than activation_valid_until")
            retrieval_enabled = True
        else:
            if not record.retrieval_enabled:
                raise SocServiceConflictError(f"memory record {record.memory_id} retrieval is already disabled")
            activation_valid_until = None
            review_due_at = None
            retrieval_enabled = False

        updated_record = record.model_copy(
            update={
                "version": previous_version + 1,
                "retrieval_enabled": retrieval_enabled,
                "retrieval_policy_version": command.policy_version,
                "retrieval_valid_until": activation_valid_until,
                "retrieval_review_due_at": review_due_at,
                "retrieval_updated_by": request_context.actor,
                "retrieval_updated_at": now,
                "retrieval_reason": command.reason,
                "updated_at": now,
                "labels": _retrieval_activation_labels(record.labels, enabled=retrieval_enabled),
                "metadata": {
                    **record.metadata,
                    **command.metadata,
                    "retrieval_enabled": retrieval_enabled,
                },
            }
        )
        if not self._record_repository.compare_and_set_memory_record(
            updated_record,
            expected_version=previous_version,
        ):
            raise SocServiceConflictError(f"memory record {record.memory_id} changed while retrieval activation was being applied")

        audit_record = build_mutation_audit(
            operation=SocMutationOperation.MEMORY_RETRIEVAL_ACTIVATION,
            target_type="memory_record",
            target_id=record.memory_id,
            run_id=record.source.run_id,
            alert_id=record.source.alert_id,
            queue_id=record.source.queue_id,
            context=request_context,
            reason=command.reason,
            command=command_payload,
            result_ref=f"{record.memory_id}:v{updated_record.version}",
            payload={
                "action": command.action.value,
                "previous_record_version": previous_version,
                "result_record_version": updated_record.version,
                "previous_retrieval_enabled": previous_enabled,
                "retrieval_enabled": updated_record.retrieval_enabled,
                "policy_version": command.policy_version,
                "retrieval_valid_until": updated_record.retrieval_valid_until,
                "retrieval_review_due_at": updated_record.retrieval_review_due_at,
            },
        )
        self._mutation_audit_repository.append_mutation_audit(audit_record)
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.MEMORY_UPDATED,
                request_id=request_context.request_id,
                run_id=record.source.run_id,
                alert_id=record.source.alert_id,
                actor=request_context.actor,
                payload={
                    "operation": "memory_record.retrieval_activation_changed",
                    "memory_id": record.memory_id,
                    "action": command.action.value,
                    "previous_record_version": previous_version,
                    "record_version": updated_record.version,
                    "retrieval_enabled": updated_record.retrieval_enabled,
                    "policy_version": command.policy_version,
                    "audit_id": audit_record.audit_id,
                },
            )
        )
        return SocMemoryRetrievalActivationResult(
            record=updated_record,
            action=command.action,
            previous_record_version=previous_version,
            previous_retrieval_enabled=previous_enabled,
            audit_id=audit_record.audit_id,
            policy_version=command.policy_version,
            changed_at=now,
        )

    def find_relevant_records(self, query: SocMemoryQuery) -> SocMemoryRetrievalResult:
        """Return retrieval-enabled confirmed memory records with scoring metadata."""

        if self._record_repository is None:
            raise SocServiceNotImplementedError("find_relevant_records requires a MemoryRecordRepository")

        find_candidates = getattr(
            self._record_repository,
            "find_memory_candidate_records",
            None,
        )
        if callable(find_candidates):
            candidate_records = find_candidates(query)
        else:
            candidate_records = []
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
        skipped_ungoverned_activation = 0
        skipped_activation_expired = 0
        skipped_review_overdue = 0
        skipped_status = 0
        skipped_expired = 0
        skipped_missing_strong_anchor = 0
        skipped_not_applicable = 0
        skipped_below_min_score = 0
        now = self._now_provider()

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
            if record.retrieval_enabled and (
                record.retrieval_policy_version != SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION
                or record.retrieval_valid_until is None
                or record.retrieval_review_due_at is None
                or record.retrieval_updated_by is None
                or record.retrieval_updated_at is None
                or not record.retrieval_reason
            ):
                skipped_ungoverned_activation += 1
                continue
            if record.retrieval_enabled and record.retrieval_valid_until is not None and record.retrieval_valid_until <= now:
                skipped_activation_expired += 1
                continue
            if record.retrieval_enabled and record.retrieval_review_due_at is not None and record.retrieval_review_due_at <= now:
                skipped_review_overdue += 1
                continue
            if record.status != SocMemoryRecordStatus.CONFIRMED:
                skipped_status += 1
                continue
            if record.validity.valid_from > now:
                skipped_status += 1
                continue
            if record.validity.valid_until is not None and record.validity.valid_until <= now:
                skipped_expired += 1
                continue

            score, match_reasons, matched_facets = score_memory_record(record, query)
            anchor_allowed, anchor_reasons, matched_anchor_facets = evaluate_memory_anchor_gate(record, query, matched_facets)
            if not anchor_allowed:
                skipped_missing_strong_anchor += 1
                continue
            applicability_report = evaluate_memory_applicability(
                record,
                query,
                matched_facets,
            )
            exact_or_legacy = applicability_report.status in {
                SocMemoryApplicabilityStatus.APPLICABLE,
                SocMemoryApplicabilityStatus.LEGACY_ANCHOR_ONLY,
            }
            if not exact_or_legacy and not (applicability_report.status is SocMemoryApplicabilityStatus.PARTIAL and applicability_report.context_only_allowed):
                skipped_not_applicable += 1
                continue
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
                    anchor_match_reasons=anchor_reasons,
                    matched_anchor_facets=matched_anchor_facets,
                    applicability_report=applicability_report,
                    token_estimate=token_estimate,
                    content_hash=record.content_hash,
                    facets_hash=record.facets_hash,
                    retrieval_enabled=True,
                )
            )

        selected_matches: list[SocMemoryMatch] = []
        token_total = 0
        for match in sorted(
            scored_matches,
            key=lambda item: (
                _memory_applicability_priority(item),
                item.score,
                item.record.updated_at,
            ),
            reverse=True,
        ):
            if len(selected_matches) >= query.limit:
                break
            if token_total + match.token_estimate > query.max_tokens and selected_matches:
                break
            selected_matches.append(match)
            token_total += match.token_estimate

        return SocMemoryRetrievalResult(
            policy_version=query.policy_version,
            query=query,
            matches=selected_matches,
            total_candidate_count=len(deduped_records),
            skipped_retrieval_disabled=skipped_retrieval_disabled,
            skipped_ungoverned_activation=skipped_ungoverned_activation,
            skipped_activation_expired=skipped_activation_expired,
            skipped_review_overdue=skipped_review_overdue,
            skipped_status=skipped_status,
            skipped_expired=skipped_expired,
            skipped_missing_strong_anchor=skipped_missing_strong_anchor,
            skipped_not_applicable=skipped_not_applicable,
            skipped_below_min_score=skipped_below_min_score,
            returned_count=len(selected_matches),
            returned_context_only_count=sum(item.applicability_report is not None and item.applicability_report.context_only_allowed for item in selected_matches),
            total_token_estimate=token_total,
            max_tokens=query.max_tokens,
        )

    def list_facts(self) -> list[Any]:
        raise SocServiceNotImplementedError("list_facts is replaced by find_relevant_records(SocMemoryQuery)")


def _memory_applicability_priority(match: SocMemoryMatch) -> int:
    report = match.applicability_report
    if report is None:
        return 1
    if report.status is SocMemoryApplicabilityStatus.APPLICABLE:
        return 2
    if report.status is SocMemoryApplicabilityStatus.LEGACY_ANCHOR_ONLY:
        return 1
    return 0


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
        applicability=command.record_applicability or candidate.applicability,
        evidence_refs=candidate.evidence_refs,
        validity=candidate.validity,
        confidence=candidate.confidence,
        decision_impact=(SocMemoryDecisionImpact.DETECTION_DECISION if command.decision_directive is not None else candidate.decision_impact),
        decision_directive=command.decision_directive,
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


def _validate_memory_decision_directive(
    candidate: SocMemoryCandidate,
    command: SocMemoryCandidateReviewCommand,
) -> None:
    directive = command.decision_directive
    if directive is None:
        return
    if candidate.decision_impact is not SocMemoryDecisionImpact.DETECTION_DECISION:
        raise SocServiceError("this memory candidate is context-only and cannot create a future decision directive")
    candidate_keys = {str(key).strip() for key, values in candidate.facets.items() if str(key).strip() and any(str(value).strip() for value in values)}
    missing = sorted(set(directive.required_facet_keys) - candidate_keys)
    if missing:
        raise SocServiceError("memory decision directive requires missing candidate facets: " + ", ".join(missing))
    applicability = command.record_applicability or candidate.applicability
    if applicability is None:
        raise SocServiceError("memory decision directive requires a typed applicability contract")
    omitted_required = sorted(set(applicability.required_facets) - set(directive.required_facet_keys))
    if omitted_required:
        raise SocServiceError("memory decision directive must retain every reviewed required facet: " + ", ".join(omitted_required))


def _materialize_memory_review_directive(
    candidate: SocMemoryCandidate,
    command: SocMemoryCandidateReviewCommand,
) -> SocMemoryCandidateReviewCommand:
    if not command.apply_to_future_matches:
        return command
    applicability = command.record_applicability or candidate.applicability
    if applicability is None:
        raise SocServiceError("apply_to_future_matches requires a typed candidate applicability contract")
    if candidate.decision_impact is not SocMemoryDecisionImpact.DETECTION_DECISION:
        raise SocServiceError("apply_to_future_matches requires a behavior-scoped decision-eligible candidate")
    assert command.confirmed_verdict is not None
    directive = SocMemoryDecisionDirective(
        effect=SocMemoryDecisionEffect.OVERRIDE,
        target_verdict=command.confirmed_verdict,
        review_effect=(SocMemoryReviewEffect.CLEAR if command.clear_review_on_match else SocMemoryReviewEffect.PRESERVE),
        required_facet_keys=sorted(applicability.required_facets),
        rationale=command.reason,
    )
    return command.model_copy(update={"decision_directive": directive})


def _validate_memory_record_applicability(
    candidate: SocMemoryCandidate,
    reviewed: SocMemoryApplicabilitySpec | None,
) -> None:
    if reviewed is None:
        return
    base = candidate.applicability
    if base is None:
        raise SocServiceError("record_applicability cannot be supplied for a candidate without typed applicability")
    if (
        reviewed.profile_id,
        reviewed.profile_version,
        reviewed.feature_schema_version,
    ) != (
        base.profile_id,
        base.profile_version,
        base.feature_schema_version,
    ):
        raise SocServiceError("record_applicability must retain the candidate profile and feature schema versions")

    base_required = _normalized_applicability_values(base.required_facets)
    base_optional = _normalized_applicability_values(base.optional_facets)
    reviewed_required = _normalized_applicability_values(reviewed.required_facets)
    reviewed_optional = _normalized_applicability_values(reviewed.optional_facets)
    missing_required_keys = sorted(set(base_required) - set(reviewed_required))
    if missing_required_keys:
        raise SocServiceError("record_applicability cannot remove candidate required facets: " + ", ".join(missing_required_keys))
    for key, values in reviewed_required.items():
        allowed_values = base_required.get(key) or base_optional.get(key)
        if allowed_values is None or not values <= allowed_values:
            raise SocServiceError(f"record_applicability required facet {key} must narrow candidate facet values")
    if not set(reviewed_optional) <= set(base_optional):
        raise SocServiceError("record_applicability optional facets must come from candidate optional facets")
    for key, values in reviewed_optional.items():
        if not values <= base_optional[key]:
            raise SocServiceError(f"record_applicability optional facet {key} must narrow candidate facet values")
    if reviewed.excluded_facets != base.excluded_facets:
        raise SocServiceError("record_applicability cannot change candidate exclusions in this review contract")
    if reviewed.minimum_optional_matches < base.minimum_optional_matches:
        raise SocServiceError("record_applicability cannot lower the optional match threshold")
    if reviewed.minimum_strong_anchor_matches < base.minimum_strong_anchor_matches:
        raise SocServiceError("record_applicability cannot lower the strong-anchor threshold")

    base_context_missing = set(base.context_only_missing_facet_keys)
    reviewed_context_missing = set(reviewed.context_only_missing_facet_keys)
    base_context_similarity = set(base.context_only_similarity_facet_keys)
    reviewed_context_similarity = set(reviewed.context_only_similarity_facet_keys)
    if not reviewed_context_missing <= base_context_missing:
        raise SocServiceError("record_applicability cannot widen context-only missing facets")
    if not reviewed_context_similarity <= base_context_similarity:
        raise SocServiceError("record_applicability cannot widen context-only similarity facets")


def _normalized_applicability_values(
    facets: dict[str, list[str]],
) -> dict[str, set[str]]:
    return {str(key).strip().casefold(): {str(value).strip().casefold() for value in values if str(value).strip()} for key, values in facets.items() if str(key).strip()}


def _retrieval_activation_labels(labels: list[str], *, enabled: bool) -> list[str]:
    state_label = "retrieval-enabled" if enabled else "retrieval-disabled"
    result = set(labels) - {"retrieval-enabled", "retrieval-disabled"}
    result.add(state_label)
    return sorted(result)


def _memory_query_from_investigation_context(
    context: InvestigationContext,
    *,
    profile_registry: SocMemoryProfileRegistry,
) -> SocMemoryQuery:
    facets: dict[str, list[str]] = {}
    text_terms: list[str] = []
    evidence_refs: list[str] = []
    profile_metadata: dict[str, Any] = {
        "memory_profile_id": "soc.generic",
        "memory_profile_version": "1",
        "memory_feature_schema_version": "soc.memory_features.generic.v1",
    }
    policy_version = "soc.memory_retrieval_policy.v2"
    tenant_scope = context.queue_item.tenant_id or "global"
    tenant_id = context.queue_item.tenant_id

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

    request = context.run.llm_analysis_request
    if request is not None:
        profile = profile_registry.resolve_request(request)
        canonical_query = memory_query_from_analysis_request(
            request,
            profile=profile,
        )
        for key, values in canonical_query.facets.items():
            for value in values:
                _add_memory_query_facet(facets, key, value)
        text_terms.extend(canonical_query.text_terms)
        evidence_refs.extend(canonical_query.evidence_refs)
        profile_metadata = dict(canonical_query.metadata)
        policy_version = canonical_query.policy_version
        tenant_scope = canonical_query.tenant_scope or tenant_scope
        tenant_id = canonical_query.tenant_id
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
        policy_version=policy_version,
        tenant_scope=tenant_scope,
        tenant_id=tenant_id,
        facets=facets,
        text_terms=text_terms,
        evidence_refs=evidence_refs,
        limit=5,
        max_tokens=900,
        metadata={
            **profile_metadata,
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
    """Per-message Kafka daemon boundary used by the consumer runner."""

    def __init__(
        self,
        *,
        analysis_service: SocAnalysisService | None = None,
        approval_service: SocAgentApprovalService | None = None,
        investigation_service: SocInvestigationWorkflowPort | None = None,
        memory_pattern_observer: MemoryPatternObserver | None = None,
        memory_pattern_environment: str | None = None,
        memory_pattern_data_class: MemoryPatternDataClass | None = None,
    ) -> None:
        if memory_pattern_observer is not None and (not memory_pattern_environment or memory_pattern_data_class is None):
            raise ValueError("memory pattern observer requires an explicit environment and data class")
        self._analysis_service = analysis_service
        self._approval_service = approval_service
        self._investigation_service = investigation_service
        self._memory_pattern_observer = memory_pattern_observer
        self._memory_pattern_environment = memory_pattern_environment
        self._memory_pattern_data_class = memory_pattern_data_class

    def start(self) -> None:
        raise SocServiceNotImplementedError("daemon lifecycle is owned by SocKafkaConsumerRunner")

    def process_message(self, message: SocDaemonMessage | Mapping[str, Any]) -> SocDaemonProcessResult:
        """Process one decoded daemon message through stable core services."""

        daemon_message = SocDaemonMessage.model_validate(message)
        if daemon_message.kind == "alert":
            return self._process_alert_message(daemon_message)
        if daemon_message.kind == "approval_request":
            approval_request = SocAgentApprovalRequest.model_validate(daemon_message.payload)
            submitted = self.submit_approval_request(
                approval_request,
                context=_daemon_request_context(daemon_message),
            )
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

    def submit_approval_request(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentApprovalRequest:
        """Daemon-side boundary for writing high-risk requests to the shared inbox."""

        if self._approval_service is None:
            raise SocServiceNotImplementedError("submit_approval_request requires a SocAgentApprovalService")
        return self._approval_service.submit_request(approval_request, context=context)

    def _process_alert_message(self, message: SocDaemonMessage) -> SocDaemonProcessResult:
        if self._analysis_service is None:
            raise SocServiceNotImplementedError("process alert message requires a SocAnalysisService")
        request_context = _daemon_request_context(message)
        run = self._analysis_service.analyze(message.payload, context=request_context)
        failed = run.status is AnalysisRunStatus.FAILED
        memory_pattern_payload = self._observe_memory_pattern(
            message,
            run,
            context=request_context,
        )
        investigation_payload: dict[str, Any] = {}
        investigation_error: str | None = None
        investigation_retryable = False
        if not failed and self._investigation_service is not None:
            try:
                workflow_result = self._investigation_service.execute(
                    SocEnrichmentExecutionCommand(
                        run_id=run.run_id,
                        thread_id=f"THR-{run.run_id}",
                        trigger=(SocEnrichmentExecutionTrigger.KAFKA if message.topic is not None else SocEnrichmentExecutionTrigger.MANUAL),
                    ),
                    context=request_context,
                )
                execution = workflow_result.execution
                investigation_payload = {
                    "investigation_execution_id": execution.execution_id,
                    "investigation_status": execution.status.value,
                    "investigation_attempt_count": execution.attempt_count,
                    "investigation_evidence_count": execution.evidence_count,
                    "investigation_provider_invocation_count": workflow_result.provider_invocation_count,
                    "investigation_idempotent_replay": workflow_result.idempotent_replay,
                }
                if execution.status in {
                    SocEnrichmentExecutionStatus.RETRYABLE_FAILED,
                    SocEnrichmentExecutionStatus.FAILED,
                }:
                    failed = True
                    investigation_retryable = execution.retryable
                    investigation_error = execution.last_error or (f"persistent investigation ended as {execution.status.value}")
            except SocEnrichmentWorkflowError as exc:
                failed = True
                investigation_retryable = exc.retryable
                investigation_error = str(exc)
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
            error=(run.failure.message if run.failure is not None else investigation_error),
            payload={
                "topic": message.topic,
                "partition": message.partition,
                "offset": message.offset,
                "key": message.key,
                "idempotency_key": _daemon_idempotency_key(message),
                "failure_kind": (run.failure.kind.value if run.failure is not None else ("investigation_workflow" if investigation_error is not None else None)),
                "retryable": (run.failure.retryable if run.failure is not None else investigation_retryable),
                **memory_pattern_payload,
                **investigation_payload,
            },
        )

    def _observe_memory_pattern(
        self,
        message: SocDaemonMessage,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> dict[str, Any]:
        if self._memory_pattern_observer is None:
            return {}
        if run.status not in {AnalysisRunStatus.SUCCESS, AnalysisRunStatus.NEEDS_REVIEW}:
            return {"memory_pattern_status": "skipped_runtime_failed"}
        if message.topic is None or message.partition is None or message.offset is None:
            return {"memory_pattern_status": "skipped_non_kafka_source"}
        assert self._memory_pattern_environment is not None
        assert self._memory_pattern_data_class is not None
        transport_ref = f"kafka:{message.topic}:{message.partition}:{message.offset}"
        try:
            result = self._memory_pattern_observer.observe_run(
                run,
                source_type=MemoryPatternSourceType.KAFKA_ALERT,
                transport_ref=transport_ref,
                environment=self._memory_pattern_environment,
                data_class=self._memory_pattern_data_class,
                context=context,
            )
        except MemoryPatternIneligibleError as exc:
            return {
                "memory_pattern_status": "skipped_ineligible",
                "memory_pattern_reason": str(exc)[:500],
            }
        except Exception as exc:  # noqa: BLE001 - learning must not fail alert handling
            return {
                "memory_pattern_status": "failed_non_blocking",
                "memory_pattern_error_type": type(exc).__name__,
                "memory_pattern_error": str(exc)[:500],
            }
        return {
            "memory_pattern_status": "observed",
            "memory_pattern_observation_id": result.observation.observation_id,
            "memory_pattern_aggregation_key": result.observation.aggregation_key,
            "memory_pattern_support_count": result.support_count,
            "memory_pattern_distinct_source_count": result.distinct_source_count,
            "memory_pattern_threshold_met": result.threshold_met,
            "memory_pattern_candidate_id": (result.candidate.candidate_id if result.candidate is not None else None),
            "memory_pattern_candidate_created": result.candidate_created,
            "memory_pattern_candidate_frozen": result.candidate_frozen,
            "memory_pattern_idempotent": result.idempotent,
        }


def _daemon_request_context(message: SocDaemonMessage) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-daemon",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.DAEMON,
            roles=["soc_daemon"],
            auth_source=ActorAuthSource.DAEMON,
        ),
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


def _resolved_approval_request(
    approval_request: SocAgentApprovalRequest,
    *,
    status: SocAgentApprovalRequestStatus,
    context: ServiceRequestContext,
    reason: str,
    resolved_at: datetime,
    approval_grant_id: str | None = None,
    expires_in_seconds: int | None = None,
) -> SocAgentApprovalRequest:
    return SocAgentApprovalRequest.model_validate(
        {
            **approval_request.model_dump(mode="python"),
            "status": status,
            "resolved_at": resolved_at,
            "resolved_by": context.actor,
            "resolution_reason": reason.strip(),
            "resolution_idempotency_key": context.idempotency_key,
            "resolution_expires_in_seconds": expires_in_seconds,
            "approval_grant_id": approval_grant_id,
        }
    )


class SocAgentApprovalService:
    """Human approval boundary for high-risk SOC Agent actions.

    This service creates an execution grant only. It does not execute the action,
    call external tools, or write business state.
    """

    SUBMITTER_ROLES = frozenset({"analyst", "soc_analyst", "soc_agent", "soc_daemon", "soc_admin"})
    DELEGATED_SUBMITTER_ROLES = frozenset({"soc_agent", "soc_daemon"})
    APPROVER_ROLES = frozenset({"soc_approver", "soc_admin"})
    OPERATOR_ROLES = frozenset({"analyst", "soc_analyst", "soc_operator", "soc_approver", "soc_admin"})

    def __init__(
        self,
        *,
        grant_repository: SocAgentApprovalGrantRepository | None = None,
        request_repository: SocAgentApprovalRequestRepository | None = None,
        action_adapter_registry: SocActionAdapterRegistryPort | None = None,
        mutation_audit_repository: SocMutationAuditRepository | None = None,
        mutation_uow: SocMutationUnitOfWork | None = None,
        _transaction_active: bool = False,
    ) -> None:
        self._grant_repository = grant_repository
        self._request_repository = request_repository
        self._action_adapter_registry = action_adapter_registry
        self._mutation_audit_repository = mutation_audit_repository or mutation_audit_repository_from(
            request_repository,
            grant_repository,
        )
        self._mutation_uow = mutation_uow or mutation_uow_from(
            request_repository,
            grant_repository,
        )
        self._transaction_active = _transaction_active

    def submit_request(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentApprovalRequest:
        """Persist a pending approval request for human review."""

        require_actor_roles(context, self.SUBMITTER_ROLES, operation="submitting an approval request")
        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return self._transactional_clone(repository).submit_request(
                    approval_request,
                    context=context,
                )
        if approval_request.status is not SocAgentApprovalRequestStatus.PENDING:
            raise SocServiceError(f"approval request {approval_request.approval_request_id} is not pending")
        if self._request_repository is None:
            raise SocServiceNotImplementedError("submit_request requires a SocAgentApprovalRequestRepository")

        delegated = bool(self.DELEGATED_SUBMITTER_ROLES.intersection(context.actor.roles))
        if not delegated and approval_request.requested_by.actor_id != context.actor.actor_id:
            raise SocServiceAuthorizationError("approval requested_by must match the authenticated actor")
        requested_by = approval_request.requested_by if delegated else context.actor
        submitted = approval_request.model_copy(
            update={
                "requested_by": requested_by,
                "submitted_by": context.actor,
            }
        )
        # ``created_at`` is server-generated observation metadata, not caller
        # intent. Excluding it keeps a replayed, semantically identical action
        # proposal idempotent across process restarts.
        command_payload = submitted.model_dump(mode="json", exclude={"created_at"})
        existing_audit = self._find_mutation_audit(
            SocMutationOperation.APPROVAL_REQUEST_SUBMIT,
            context,
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="approval_request",
                target_id=submitted.approval_request_id,
            )
            existing_request = self.get_request(submitted.approval_request_id)
            return self._validate_request_submission_retry(existing_request, submitted)
        existing = self._request_repository.get_approval_request(submitted.approval_request_id)
        if existing is not None:
            result = self._validate_request_submission_retry(existing, submitted)
            self._append_approval_audit(
                operation=SocMutationOperation.APPROVAL_REQUEST_SUBMIT,
                target_type="approval_request",
                target_id=result.approval_request_id,
                context=context,
                reason=result.reason,
                command=command_payload,
                result_ref=result.approval_request_id,
                payload=self._approval_request_audit_payload(result),
            )
            return result
        if self._request_repository.create_approval_request(submitted):
            self._append_approval_audit(
                operation=SocMutationOperation.APPROVAL_REQUEST_SUBMIT,
                target_type="approval_request",
                target_id=submitted.approval_request_id,
                context=context,
                reason=submitted.reason,
                command=command_payload,
                result_ref=submitted.approval_request_id,
                payload=self._approval_request_audit_payload(submitted),
            )
            return submitted
        concurrent = self._request_repository.get_approval_request(submitted.approval_request_id)
        if concurrent is None:
            raise SocServiceConflictError(f"approval request {submitted.approval_request_id} could not be persisted")
        result = self._validate_request_submission_retry(concurrent, submitted)
        self._append_approval_audit(
            operation=SocMutationOperation.APPROVAL_REQUEST_SUBMIT,
            target_type="approval_request",
            target_id=result.approval_request_id,
            context=context,
            reason=result.reason,
            command=command_payload,
            result_ref=result.approval_request_id,
            payload=self._approval_request_audit_payload(result),
        )
        return result

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
        approval_request_id: str,
        *,
        context: ServiceRequestContext,
        reason: str,
        expires_in_seconds: int = 900,
    ) -> SocAgentApprovalGrant:
        require_actor_roles(context, self.APPROVER_ROLES, operation="approving an action")
        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return self._transactional_clone(repository).approve(
                    approval_request_id,
                    context=context,
                    reason=reason,
                    expires_in_seconds=expires_in_seconds,
                )
        if not reason.strip():
            raise SocServiceError("approval reason is required")
        if expires_in_seconds <= 0:
            raise SocServiceError("approval grant expiry must be positive")
        idempotency_key = self._require_resolution_idempotency_key(context)
        if self._request_repository is None:
            raise SocServiceNotImplementedError("approve requires a SocAgentApprovalRequestRepository")
        if self._grant_repository is None:
            raise SocServiceNotImplementedError("approve requires a SocAgentApprovalGrantRepository")
        if self._request_repository is not self._grant_repository:
            raise SocServiceNotImplementedError("approve requires one shared atomic approval repository")

        command_payload = {
            "approval_request_id": approval_request_id,
            "reason": reason.strip(),
            "expires_in_seconds": expires_in_seconds,
        }
        existing_audit = self._find_mutation_audit(
            SocMutationOperation.APPROVAL_REQUEST_APPROVE,
            context,
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="approval_request",
                target_id=approval_request_id,
            )
            return self._replay_approved_request(
                self.get_request(approval_request_id),
                context=context,
                reason=reason,
                expires_in_seconds=expires_in_seconds,
            )

        approval_request = self.get_request(approval_request_id)
        if approval_request.status is not SocAgentApprovalRequestStatus.PENDING:
            return self._replay_approved_request(
                approval_request,
                context=context,
                reason=reason,
                expires_in_seconds=expires_in_seconds,
            )

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
            idempotency_key=idempotency_key,
            approved_at=approved_at,
            expires_at=approved_at + timedelta(seconds=expires_in_seconds),
        )
        resolved = _resolved_approval_request(
            approval_request,
            status=SocAgentApprovalRequestStatus.APPROVED,
            context=context,
            reason=reason,
            resolved_at=approved_at,
            approval_grant_id=grant.approval_grant_id,
            expires_in_seconds=expires_in_seconds,
        )
        if self._request_repository.resolve_approval_request(
            resolved,
            expected_status=SocAgentApprovalRequestStatus.PENDING,
            grant=grant,
        ):
            self._append_approval_audit(
                operation=SocMutationOperation.APPROVAL_REQUEST_APPROVE,
                target_type="approval_request",
                target_id=resolved.approval_request_id,
                context=context,
                reason=reason,
                command=command_payload,
                result_ref=grant.approval_grant_id,
                payload={
                    **self._approval_request_audit_payload(resolved),
                    "approval_grant_id": grant.approval_grant_id,
                    "grant_status": grant.status,
                    "expires_in_seconds": expires_in_seconds,
                },
            )
            return grant
        concurrent = self.get_request(approval_request_id)
        return self._replay_approved_request(
            concurrent,
            context=context,
            reason=reason,
            expires_in_seconds=expires_in_seconds,
        )

    def reject(
        self,
        approval_request_id: str,
        *,
        context: ServiceRequestContext,
        reason: str,
    ) -> SocAgentApprovalRequest:
        return self._resolve_without_grant(
            approval_request_id,
            status=SocAgentApprovalRequestStatus.REJECTED,
            context=context,
            reason=reason,
        )

    def expire(
        self,
        approval_request_id: str,
        *,
        context: ServiceRequestContext,
        reason: str,
    ) -> SocAgentApprovalRequest:
        return self._resolve_without_grant(
            approval_request_id,
            status=SocAgentApprovalRequestStatus.EXPIRED,
            context=context,
            reason=reason,
        )

    def dry_run_approved_action(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        """Validate an approval grant and return a non-side-effecting action result."""

        require_actor_roles(context, self.OPERATOR_ROLES, operation="dry-running an approved action")
        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return self._transactional_clone(repository).dry_run_approved_action(
                    command,
                    context=context,
                )
        if self._grant_repository is None:
            raise SocServiceNotImplementedError("dry_run_approved_action requires a SocAgentApprovalGrantRepository")
        if not command.dry_run:
            raise SocServiceError("dry_run_approved_action requires dry_run=true")

        command_payload = command.model_dump(mode="json")
        existing_audit = self._find_mutation_audit(
            SocMutationOperation.APPROVAL_ACTION_DRY_RUN,
            context,
        )
        audit_already_written = existing_audit is not None
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="approval_grant",
                target_id=existing_audit.target_id,
            )

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
            result = SocAgentActionResult(
                route=grant.route,
                action=grant.action,
                status=adapter_result.status,
                message=adapter_result.message,
                payload=payload,
                requires_human_approval=adapter_result.requires_human_approval,
            )
            if not audit_already_written:
                self._audit_approved_action(
                    operation=SocMutationOperation.APPROVAL_ACTION_DRY_RUN,
                    command=command_payload,
                    context=context,
                    grant=grant,
                    result=result,
                    reason="approved action dry-run",
                )
            return result

        result = SocAgentActionResult(
            route=grant.route,
            action=grant.action,
            status="success",
            message="Approved action dry-run validated; no external side effect executed.",
            payload=self._approval_dry_run_payload(grant, command, context),
        )
        if not audit_already_written:
            self._audit_approved_action(
                operation=SocMutationOperation.APPROVAL_ACTION_DRY_RUN,
                command=command_payload,
                context=context,
                grant=grant,
                result=result,
                reason="approved action dry-run",
            )
        return result

    def execute_approved_action(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        """Consume an approved action token at the execution boundary.

        The current Alpha boundary consumes the one-time token and records the
        deterministic execution result, but it does not call an external
        response tool or mutate production systems.
        """

        require_actor_roles(context, self.OPERATOR_ROLES, operation="executing an approved action")
        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return self._transactional_clone(repository).execute_approved_action(
                    command,
                    context=context,
                )
        if self._grant_repository is None:
            raise SocServiceNotImplementedError("execute_approved_action requires a SocAgentApprovalGrantRepository")
        if command.dry_run:
            raise SocServiceError("execute_approved_action requires dry_run=false")
        if not context.idempotency_key:
            raise SocServiceError("execute_approved_action requires an idempotency_key")

        command_payload = command.model_dump(mode="json")
        existing_audit = self._find_mutation_audit(
            SocMutationOperation.APPROVAL_ACTION_EXECUTE,
            context,
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="approval_grant",
                target_id=existing_audit.target_id,
            )

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
        self._audit_approved_action(
            operation=SocMutationOperation.APPROVAL_ACTION_EXECUTE,
            command=command_payload,
            context=context,
            grant=grant,
            result=result,
            reason="approved action execution boundary",
        )
        return result

    def _transactional_clone(self, repository: SocMutationRepository) -> SocAgentApprovalService:
        return SocAgentApprovalService(
            grant_repository=repository,
            request_repository=repository,
            action_adapter_registry=self._action_adapter_registry,
            mutation_audit_repository=repository,
            mutation_uow=self._mutation_uow,
            _transaction_active=True,
        )

    def _find_mutation_audit(
        self,
        operation: SocMutationOperation,
        context: ServiceRequestContext,
    ):
        if self._mutation_audit_repository is None:
            return None
        return self._mutation_audit_repository.find_mutation_audit_by_idempotency_key(
            operation,
            mutation_idempotency_key(context),
        )

    def _append_approval_audit(
        self,
        *,
        operation: SocMutationOperation,
        target_type: str,
        target_id: str,
        context: ServiceRequestContext,
        reason: str,
        command: object,
        result_ref: str | None,
        payload: Mapping[str, Any],
    ) -> None:
        if self._mutation_audit_repository is None:
            return
        self._mutation_audit_repository.append_mutation_audit(
            build_mutation_audit(
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                context=context,
                reason=reason,
                command=command,
                result_ref=result_ref,
                payload=payload,
            )
        )

    def _audit_approved_action(
        self,
        *,
        operation: SocMutationOperation,
        command: object,
        context: ServiceRequestContext,
        grant: SocAgentApprovalGrant,
        result: SocAgentActionResult,
        reason: str,
    ) -> None:
        self._append_approval_audit(
            operation=operation,
            target_type="approval_grant",
            target_id=grant.approval_grant_id,
            context=context,
            reason=reason,
            command=command,
            result_ref=(grant.execution_result_id if operation is SocMutationOperation.APPROVAL_ACTION_EXECUTE else grant.approval_grant_id),
            payload={
                "approval_request_id": grant.approval_request_id,
                "approval_grant_id": grant.approval_grant_id,
                "route": result.route,
                "action": result.action,
                "result_status": result.status,
                "grant_status": grant.status,
                "dry_run": operation is SocMutationOperation.APPROVAL_ACTION_DRY_RUN,
                "external_side_effect": "not_executed",
            },
        )

    def _approval_request_audit_payload(
        self,
        approval_request: SocAgentApprovalRequest,
    ) -> dict[str, Any]:
        return {
            "approval_request_id": approval_request.approval_request_id,
            "permission_decision_id": approval_request.permission_decision_id,
            "route": approval_request.route,
            "action": approval_request.action,
            "risk_level": approval_request.risk_level.value,
            "status": approval_request.status.value,
            "approval_grant_id": approval_request.approval_grant_id,
        }

    def _resolve_without_grant(
        self,
        approval_request_id: str,
        *,
        status: SocAgentApprovalRequestStatus,
        context: ServiceRequestContext,
        reason: str,
    ) -> SocAgentApprovalRequest:
        require_actor_roles(
            context,
            self.APPROVER_ROLES,
            operation=f"marking an approval request {status.value}",
        )
        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return self._transactional_clone(repository)._resolve_without_grant(
                    approval_request_id,
                    status=status,
                    context=context,
                    reason=reason,
                )
        if status not in {SocAgentApprovalRequestStatus.REJECTED, SocAgentApprovalRequestStatus.EXPIRED}:
            raise SocServiceError(f"unsupported approval resolution status: {status.value}")
        if not reason.strip():
            raise SocServiceError("approval resolution reason is required")
        self._require_resolution_idempotency_key(context)
        if self._request_repository is None:
            raise SocServiceNotImplementedError("approval resolution requires a SocAgentApprovalRequestRepository")

        operation = SocMutationOperation.APPROVAL_REQUEST_REJECT if status is SocAgentApprovalRequestStatus.REJECTED else SocMutationOperation.APPROVAL_REQUEST_EXPIRE
        command_payload = {
            "approval_request_id": approval_request_id,
            "status": status.value,
            "reason": reason.strip(),
        }
        existing_audit = self._find_mutation_audit(operation, context)
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="approval_request",
                target_id=approval_request_id,
            )
            return self._validate_terminal_request_retry(
                self.get_request(approval_request_id),
                status=status,
                context=context,
                reason=reason,
            )

        approval_request = self.get_request(approval_request_id)
        if approval_request.status is not SocAgentApprovalRequestStatus.PENDING:
            return self._validate_terminal_request_retry(approval_request, status=status, context=context, reason=reason)
        resolved = _resolved_approval_request(
            approval_request,
            status=status,
            context=context,
            reason=reason,
            resolved_at=datetime.now(UTC),
        )
        if self._request_repository.resolve_approval_request(
            resolved,
            expected_status=SocAgentApprovalRequestStatus.PENDING,
        ):
            self._append_approval_audit(
                operation=operation,
                target_type="approval_request",
                target_id=resolved.approval_request_id,
                context=context,
                reason=reason,
                command=command_payload,
                result_ref=resolved.approval_request_id,
                payload=self._approval_request_audit_payload(resolved),
            )
            return resolved
        concurrent = self.get_request(approval_request_id)
        return self._validate_terminal_request_retry(concurrent, status=status, context=context, reason=reason)

    def _replay_approved_request(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        context: ServiceRequestContext,
        reason: str,
        expires_in_seconds: int,
    ) -> SocAgentApprovalGrant:
        self._validate_terminal_request_retry(
            approval_request,
            status=SocAgentApprovalRequestStatus.APPROVED,
            context=context,
            reason=reason,
            expires_in_seconds=expires_in_seconds,
        )
        if self._grant_repository is None:
            raise SocServiceNotImplementedError("approval retry requires a SocAgentApprovalGrantRepository")
        grant = self._grant_repository.get_approval_grant_by_request_id(approval_request.approval_request_id)
        if grant is None or grant.approval_grant_id != approval_request.approval_grant_id:
            raise SocServiceConflictError(f"approved request {approval_request.approval_request_id} has no matching persisted grant")
        return grant

    def _validate_terminal_request_retry(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        status: SocAgentApprovalRequestStatus,
        context: ServiceRequestContext,
        reason: str,
        expires_in_seconds: int | None = None,
    ) -> SocAgentApprovalRequest:
        if (
            approval_request.status is status
            and approval_request.resolution_idempotency_key == context.idempotency_key
            and approval_request.resolved_by is not None
            and approval_request.resolved_by.actor_id == context.actor.actor_id
            and approval_request.resolution_reason == reason.strip()
            and approval_request.resolution_expires_in_seconds == expires_in_seconds
        ):
            return approval_request
        raise SocServiceConflictError(f"approval request {approval_request.approval_request_id} is already {approval_request.status.value}")

    def _validate_request_submission_retry(
        self,
        existing: SocAgentApprovalRequest,
        submitted: SocAgentApprovalRequest,
    ) -> SocAgentApprovalRequest:
        existing_intent = existing.model_dump(mode="json", exclude={"created_at"})
        submitted_intent = submitted.model_dump(mode="json", exclude={"created_at"})
        if existing_intent == submitted_intent:
            return existing
        raise SocServiceConflictError(f"approval request {submitted.approval_request_id} already exists with different content")

    def _require_resolution_idempotency_key(self, context: ServiceRequestContext) -> str:
        if context.idempotency_key is None or not context.idempotency_key.strip():
            raise SocServiceError("approval resolution requires an idempotency_key")
        return context.idempotency_key.strip()

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

    This service is the deterministic DeerFlow-compatible shell and context
    loader. Real model-driven conversation is owned by ``SocLeadAgentChatService``;
    both paths share the same core-service and approval boundaries.
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
                    self._approval_service.submit_request(approval_request, context=request_context)
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
            "endpoint.software_path.lookup",
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
                message=(
                    "SOC investigation chat is ready with deterministic review context loading. "
                    "Use the SOC Lead Agent entry for skills, MCP tools, and bounded LLM reasoning; "
                    "all state changes remain behind core-service and approval boundaries."
                ),
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
        request_id=context.request_id,
        trace_id=context.trace_id,
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
        correlation_result=context.correlation_result,
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
        investigation_addenda=context.investigation_addenda,
        evidence_timeline=timeline,
        counts={
            "similar_alerts": len(context.similar_alerts),
            "correlation_matches": len(context.correlation_result.matches) if context.correlation_result is not None else 0,
            "reusable_evidence": context.correlation_result.reusable_evidence_count if context.correlation_result is not None else 0,
            "domain_findings": sum(len(result.findings) for result in context.domain_triage_results),
            "action_evidence": len(context.action_evidence),
            "investigation_addenda": len(context.investigation_addenda),
            "authorization_enrichments": len(context.authorization_enrichments),
            "exact_authorization_matches": sum(item.match_result.status.value == "exact" for item in context.authorization_enrichments),
            "disposition_proposals": len(context.disposition_proposals),
            "disposition_outcomes": len(context.disposition_outcomes),
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
                    "confidence_source": correction.confidence_source.value,
                    "confidence_was_explicit": correction.confidence_was_explicit,
                    "confidence_policy_version": correction.confidence_policy_version,
                    "confidence_explanation": correction.confidence_explanation,
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
    for addendum in context.investigation_addenda:
        items.append(
            InvestigationTimelineItem(
                kind="investigation_addendum",
                title="Read-only investigation addendum",
                summary=addendum.summary,
                status=addendum.execution_status.value,
                source_id=addendum.addendum_id,
                source_refs={
                    "addendum_id": addendum.addendum_id,
                    "execution_id": addendum.execution_id,
                    "run_id": addendum.run_id,
                },
                occurred_at=addendum.source_updated_at,
                payload={
                    "source_report_id": addendum.source_report_id,
                    "evidence_refs": addendum.evidence_refs,
                    "evidence_coverage_ratio": addendum.evidence_coverage_ratio,
                    "analyst_attention_required": addendum.analyst_attention_required,
                    "measurement_gaps": addendum.measurement_gaps,
                    "shadow_only": addendum.shadow_only,
                    "decision_impact": addendum.decision_impact,
                    "new_conclusion_produced": addendum.new_conclusion_produced,
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
    for outcome in context.disposition_outcomes:
        items.append(_disposition_outcome_timeline_item(outcome))
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


def _disposition_outcome_timeline_item(
    outcome: SocDispositionOutcomeRecord,
) -> InvestigationTimelineItem:
    return InvestigationTimelineItem(
        kind="disposition_outcome",
        title="Shadow disposition outcome",
        summary=outcome.reason,
        status=f"{outcome.outcome_status.value}/{outcome.review_kind.value}",
        source_id=outcome.outcome_id,
        source_refs={
            "outcome_id": outcome.outcome_id,
            "proposal_id": outcome.proposal_id,
            "run_id": outcome.run_id,
        },
        occurred_at=outcome.observed_at,
        payload={
            "proposed_disposition": outcome.proposed_disposition.value,
            "observed_disposition": outcome.observed_disposition.value,
            "source": outcome.source.value,
            "sample_id": outcome.sample_id,
            "supersedes_outcome_id": outcome.supersedes_outcome_id,
            "decision_impact": outcome.decision_impact,
            "review_queue_impact": outcome.review_queue_impact,
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
        confidence_source=decision.confidence_source if decision is not None else None,
        confidence_is_calibrated=decision.confidence_is_calibrated if decision is not None else False,
        confidence_policy_version=decision.policy_version if decision is not None else None,
        confidence_explanation=decision.confidence_explanation if decision is not None else None,
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
        *(f"asset:{value}" for value in run.entities.assets),
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


def _supports_memory_evolution_repository(repository: object | None) -> bool:
    required_methods = (
        "save_memory_use",
        "find_memory_use_by_idempotency_key",
        "list_memory_uses",
        "save_memory_feedback",
        "find_memory_feedback_by_idempotency_key",
        "get_memory_health",
        "compare_and_set_memory_health",
        "save_memory_revision_proposal",
    )
    return repository is not None and all(callable(getattr(repository, name, None)) for name in required_methods)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _request_journal_from_analysis_request(
    run: AnalysisRun,
    request: LLMAnalysisRequest,
    *,
    context: ServiceRequestContext,
    action: AuditAction,
    invocation: AnalysisProviderInvocation,
) -> AnalysisRequestJournal:
    return AnalysisRequestJournal(
        action=action,
        request_id=context.request_id,
        trace_id=context.trace_id,
        actor=context.actor,
        idempotency_key_hash=(_stable_sha256(context.idempotency_key) if context.idempotency_key else None),
        replay_of_run_id=run.replay_of_run_id,
        request_schema_version=request.schema_version,
        request_hash=_stable_sha256(request.model_dump(mode="json")),
        source_type=request.source.source_type,
        source_system=request.source.source_system,
        detection_key=request.detection.detection_key,
        model_name=invocation.model_name,
        prompt_version=invocation.prompt_version,
        provider_step_name=invocation.step_name,
        provider_purpose=invocation.purpose,
        parser_version=invocation.parser_version,
        optional_provider=invocation.optional,
        primary_evidence_present=request.primary_evidence is not None,
        supplementary_evidence_count=len(request.supplementary_evidence),
        selected_skills=[item.skill_name for item in request.skill_context.selected_skills],
    )


def _finalize_request_journal(run: AnalysisRun) -> None:
    journal = run.request_journal
    if journal is None:
        return
    matching_step = next(
        (step for step in reversed(run.steps) if step.step_name == journal.provider_step_name),
        None,
    )
    failure_kind = None
    failure_retryable = None
    if run.status is AnalysisRunStatus.INTERRUPTED:
        status = AnalysisRequestJournalStatus.INTERRUPTED
    elif matching_step is not None and matching_step.status is PipelineStepStatus.FAILED:
        status = AnalysisRequestJournalStatus.FAILED
        raw_failure_kind = matching_step.metadata.get("failure_kind")
        if isinstance(raw_failure_kind, str):
            try:
                failure_kind = RuntimeFailureKind(raw_failure_kind)
            except ValueError:
                failure_kind = RuntimeFailureKind.INTERNAL_ERROR
        elif run.failure is not None:
            failure_kind = run.failure.kind
        else:
            failure_kind = RuntimeFailureKind.INTERNAL_ERROR
        raw_retryable = matching_step.metadata.get(
            "failure_retryable",
            matching_step.metadata.get("retryable"),
        )
        failure_retryable = raw_retryable if isinstance(raw_retryable, bool) else False
    elif run.status is AnalysisRunStatus.FAILED and run.failure is not None and run.failure.step_name == journal.provider_step_name:
        status = AnalysisRequestJournalStatus.FAILED
        failure_kind = run.failure.kind
        failure_retryable = run.failure.retryable
    else:
        status = AnalysisRequestJournalStatus.COMPLETED
    _set_active_request_journal(
        run,
        journal.model_copy(
            update={
                "status": status,
                "finalized_at": run.ended_at or _utc_now(),
                "failure_kind": failure_kind,
                "failure_retryable": failure_retryable,
            }
        ),
    )


def _complete_active_request_journal(run: AnalysisRun) -> None:
    journal = run.request_journal
    if journal is None or journal.status is not AnalysisRequestJournalStatus.RUNNING:
        return
    _set_active_request_journal(
        run,
        journal.model_copy(
            update={
                "status": AnalysisRequestJournalStatus.COMPLETED,
                "finalized_at": _utc_now(),
            }
        ),
    )


def _set_active_request_journal(
    run: AnalysisRun,
    journal: AnalysisRequestJournal,
) -> None:
    """Update the recovery pointer and its ordered, bounded audit history."""

    journals = list(run.provider_request_journals)
    identity = (
        journal.provider_step_name,
        journal.provider_started_at,
        journal.request_hash,
    )
    for index in range(len(journals) - 1, -1, -1):
        candidate = journals[index]
        if (
            candidate.provider_step_name,
            candidate.provider_started_at,
            candidate.request_hash,
        ) == identity:
            journals[index] = journal
            break
    else:
        journals.append(journal)
    run.provider_request_journals = journals[-8:]
    run.request_journal = journal


def _correction_confidence_explanation(
    source: DecisionConfidenceSource,
    *,
    explicit: bool,
) -> str:
    if source is DecisionConfidenceSource.EXTERNAL_DISPOSITION:
        return "Trusted external disposition confirmation strength; not a calibrated probability."
    if explicit:
        return "Analyst-supplied confirmation strength; not a calibrated probability."
    return "Policy default for categorical analyst confirmation; not a calibrated probability."


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
            "analysis_output_quality_status": (run.analysis_output_quality.status.value if run.analysis_output_quality is not None else None),
            "analysis_materiality": (
                {
                    "core_usable": run.analysis_materiality.core_usable,
                    "decision_usable": run.analysis_materiality.decision_usable,
                    "review_required": run.analysis_materiality.review_required,
                    "review_reasons": [item.value for item in run.analysis_materiality.review_reasons],
                    "blocked_capabilities": [item.capability.value for item in run.analysis_materiality.capability_guards if not item.allowed],
                }
                if run.analysis_materiality is not None
                else None
            ),
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
            "confidence_source": record.confidence_source.value,
            "confidence_is_calibrated": False,
            "confidence_was_explicit": record.confidence_was_explicit,
            "confidence_policy_version": record.confidence_policy_version,
            "confidence_explanation": record.confidence_explanation,
            "automation_allowed": False,
        },
    )
