"""Public protocols for replaceable SOC Agent dependencies."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol

from soc_agent.contracts import (
    AlertInput,
    AlertSummary,
    AnalysisEvidenceGroundingReport,
    AnalysisMaterialityReport,
    AnalysisNodeOutput,
    AnalysisOutputQuality,
    AnalysisProviderInvocation,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationEnrichmentRecord,
    CorrectionRecord,
    Decision,
    DecisionAuditRecord,
    GovernedContextFact,
    GovernedContextFactQuery,
    InvestigationEvidence,
    LLMAnalysisRequest,
    MemoryCenterInventory,
    MemoryPatternAggregationResult,
    MemoryPatternDataClass,
    MemoryPatternLineageStatsPage,
    MemoryPatternObservation,
    MemoryPatternSourceType,
    NormalizationBaselineStatus,
    NormalizationMaintenanceIssue,
    NormalizationMaintenanceIssueStatus,
    NormalizationMonitoringResult,
    NormalizationSchemaBaseline,
    ProcessingJobStatus,
    ReviewQueueItem,
    ReviewQueueStatus,
    RoleAdjudicationVerificationResult,
    RoleVerificationNodeOutput,
    RoleVerificationTriggerDecision,
    ServiceRequestContext,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SkillFeedbackObservation,
    SkillFeedbackSourceType,
    SkillImprovementCandidate,
    SkillImprovementCandidateStatus,
    SocActionAuthorizationRecord,
    SocActionExecutionRecord,
    SocAgentActionAdapterDescriptor,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentApprovalRequestStatus,
    SocBehaviorGroupEffectivenessAggregate,
    SocCallbackAttemptRecord,
    SocCallbackOutboxRecord,
    SocCallbackOutboxSubmission,
    SocDecisionTransitionRecord,
    SocDispositionOutcomeRecord,
    SocDispositionOutcomeReviewKind,
    SocDispositionProposalRecord,
    SocDispositionSampleManifest,
    SocDispositionTransitionRecord,
    SocEffectivenessScope,
    SocEnrichmentActionAttempt,
    SocEnrichmentExecution,
    SocEnrichmentExecutionCommand,
    SocEnrichmentPlan,
    SocEnrichmentWorkflowResult,
    SocEvaluationDataClass,
    SocEvent,
    SocExternalDispositionRecord,
    SocMemoryBusinessLessonDraft,
    SocMemoryCandidate,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryEffectivenessAggregate,
    SocMemoryFeedbackEvent,
    SocMemoryFeedbackResult,
    SocMemoryHealthRecord,
    SocMemoryQuery,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMemoryRevisionProposal,
    SocMemoryRevisionProposalStatus,
    SocMemoryUseRecord,
    SocMutationAuditRecord,
    SocMutationOperation,
    SocOperationsKafkaSnapshot,
    SocPersistedOperationsMetrics,
    SocProcessingJob,
    SocProcessingJobEvent,
    SocProcessingJobSubmission,
    SocRuleEffectivenessAggregate,
    SocRuleEffectivenessSelector,
    TenantDispositionPolicy,
    TenantPolicyAdvisorResult,
    TenantPolicyDecision,
    TenantPolicySignalResolution,
    Verdict,
)


class AlertNormalizer(Protocol):
    """Convert a loose source payload into canonical alert input."""

    def __call__(self, payload: Mapping[str, Any]) -> AlertInput: ...


class AnalysisRuntime(Protocol):
    """Run the deterministic analysis pipeline."""

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun: ...


class ProcessingJobRepository(Protocol):
    """Stable persistence boundary for durable jobs and callback delivery."""

    def submit(
        self,
        submission: SocProcessingJobSubmission,
        *,
        now: datetime | None = None,
    ) -> tuple[SocProcessingJob, bool]: ...

    def get(self, job_id: str) -> SocProcessingJob | None: ...

    def claim_next(
        self,
        *,
        queue_name: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> SocProcessingJob | None: ...

    def recover_expired_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> list[str]: ...

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        expected_status: ProcessingJobStatus,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> SocProcessingJob: ...

    def transition(
        self,
        job_id: str,
        *,
        worker_id: str,
        expected_status: ProcessingJobStatus,
        target_status: ProcessingJobStatus,
        event_type: str,
        now: datetime | None = None,
        run_id: str | None = None,
        result_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        available_at: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> SocProcessingJob: ...

    def complete_with_callback(
        self,
        job_id: str,
        *,
        worker_id: str,
        expected_status: ProcessingJobStatus,
        target_status: ProcessingJobStatus,
        event_type: str,
        result_payload: dict[str, Any],
        callback: SocCallbackOutboxSubmission,
        now: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> tuple[SocProcessingJob, SocCallbackOutboxRecord]: ...

    def get_callback(self, outbox_id: str) -> SocCallbackOutboxRecord | None: ...

    def list_callbacks(self, job_id: str) -> list[SocCallbackOutboxRecord]: ...

    def claim_next_callback(
        self,
        *,
        destination: str,
        dispatcher_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> SocCallbackOutboxRecord | None: ...

    def mark_callback_retry(
        self,
        outbox_id: str,
        *,
        dispatcher_id: str,
        error_code: str,
        error_message: str,
        response_metadata: dict[str, Any] | None = None,
        available_at: datetime,
        now: datetime | None = None,
        dead_letter: bool = False,
    ) -> SocCallbackOutboxRecord: ...

    def mark_callback_delivered(
        self,
        outbox_id: str,
        *,
        dispatcher_id: str,
        response_metadata: dict[str, Any],
        now: datetime | None = None,
    ) -> SocCallbackOutboxRecord: ...

    def list_events(self, job_id: str) -> list[SocProcessingJobEvent]: ...

    def list_callback_attempts(
        self,
        outbox_id: str,
    ) -> list[SocCallbackAttemptRecord]: ...


AnalysisBeforeProviderHook = Callable[
    [AnalysisRun, LLMAnalysisRequest, AnalysisProviderInvocation],
    None,
]
AnalysisRequestEnricher = Callable[[LLMAnalysisRequest], LLMAnalysisRequest]


class JournaledAnalysisRuntime(Protocol):
    """Runtime extension that exposes the exact pre-provider persistence point."""

    def analyze_journaled(
        self,
        payload: Mapping[str, Any],
        *,
        before_provider: AnalysisBeforeProviderHook,
    ) -> AnalysisRun: ...


class LLMAnalyzer(Protocol):
    """Bounded LLM analysis node used behind a fixed runtime step."""

    step_name: str
    model_name: str
    prompt_version: str

    def analyze(self, request: LLMAnalysisRequest) -> AnalysisNodeOutput: ...


class RoleAdjudicationVerifier(Protocol):
    """Optional conditional second-pass verifier behind deterministic routing."""

    step_name: str
    model_name: str
    prompt_version: str
    parser_version: str
    minimum_confidence: float

    def evaluate_trigger(
        self,
        analysis: AnalysisResult,
        *,
        request: LLMAnalysisRequest,
        grounding: AnalysisEvidenceGroundingReport,
    ) -> RoleVerificationTriggerDecision: ...

    def verify(
        self,
        request: LLMAnalysisRequest,
        analysis: AnalysisResult,
        trigger: RoleVerificationTriggerDecision,
        *,
        primary_model_name: str,
    ) -> RoleVerificationNodeOutput: ...


class DecisionPolicy(Protocol):
    """Convert bounded analyzer output into an operational decision."""

    def decide(
        self,
        analysis: AnalysisResult,
        *,
        request: LLMAnalysisRequest,
        grounding: AnalysisEvidenceGroundingReport,
        analyzer_step_name: str,
        output_quality: AnalysisOutputQuality | None = None,
        role_verification: RoleAdjudicationVerificationResult | None = None,
        materiality: AnalysisMaterialityReport | None = None,
    ) -> Decision: ...


class NormalizationMaintenanceMonitor(Protocol):
    """Optional post-analysis monitor for parser/mapping maintenance signals."""

    def monitor_run(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> NormalizationMonitoringResult: ...


class PostAnalysisObserver(Protocol):
    """Optional best-effort observer invoked only after analysis persistence."""

    def observe(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> None: ...


class TenantDispositionPolicyResolver(Protocol):
    """Resolve an operator-owned policy without importing tenant code."""

    def resolve(
        self,
        *,
        tenant_id: str | None,
        environment: str,
        evaluated_at: datetime | None = None,
    ) -> TenantDispositionPolicy | None: ...


class TenantPolicyAdvisor(Protocol):
    """Optional bounded policy-Skill reasoning behind a tenant adapter."""

    def advise(
        self,
        policy: TenantDispositionPolicy,
        run: AnalysisRun,
    ) -> TenantPolicyAdvisorResult: ...


class TenantPolicySignalProvider(Protocol):
    """Resolve optional governed context without importing tenant logic into the evaluator."""

    provider_id: str
    provider_version: str

    def resolve(
        self,
        policy: TenantDispositionPolicy,
        run: AnalysisRun,
        *,
        environment: str,
    ) -> TenantPolicySignalResolution: ...


class AlertRepository(Protocol):
    """Persistence boundary for analysis runs and alert summaries."""

    def save_run(self, run: AnalysisRun) -> None: ...

    def get_run(self, run_id: str) -> AnalysisRun | None: ...

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]: ...

    def list_runs_by_alert_id(
        self,
        alert_id: str,
        *,
        limit: int = 20,
    ) -> list[AnalysisRun]: ...

    def claim_run_recovery(
        self,
        run: AnalysisRun,
        *,
        expected_status: AnalysisRunStatus = AnalysisRunStatus.RUNNING,
    ) -> bool: ...


class DecisionAuditRepository(Protocol):
    """Persistence boundary for decision audit records."""

    def save_audit_record(self, record: DecisionAuditRecord) -> None: ...

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]: ...

    def find_audit_record_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        action: str | None = None,
    ) -> DecisionAuditRecord | None: ...


class AlertSummaryRepository(Protocol):
    """Persistence boundary for queryable alert summaries."""

    def save_alert_summary(self, summary: AlertSummary) -> None: ...

    def get_alert_summary(self, run_id: str) -> AlertSummary | None: ...

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]: ...

    def find_similar_alert_summaries(self, query: SimilarAlertQuery) -> list[SimilarAlertMatch]: ...


class ReviewQueueRepository(Protocol):
    """Persistence boundary for human review queue items."""

    def save_review_item(self, item: ReviewQueueItem) -> None: ...

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None: ...

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None: ...

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = None,
        limit: int = 50,
    ) -> list[ReviewQueueItem]: ...


class AnalysisPersistence(Protocol):
    """Atomic persistence boundary for one completed Runtime analysis."""

    def save_analysis_bundle(
        self,
        *,
        run: AnalysisRun,
        summary: AlertSummary,
        review_item: ReviewQueueItem | None,
        audit_record: DecisionAuditRecord,
    ) -> None: ...


class SocOperationsRepositoryError(RuntimeError):
    """Sanitized failure raised by a read-only operations repository."""


class SocOperationsRepository(Protocol):
    """Exact aggregate read boundary for SOC operational persistence."""

    def read_persisted_metrics(self) -> SocPersistedOperationsMetrics: ...


class SocEffectivenessRepositoryError(RuntimeError):
    """Sanitized failure raised by the product-effectiveness read model."""


class SocEffectivenessRepository(Protocol):
    """Exact aggregate boundary over persisted SOC lineage."""

    def read_rule_aggregates(
        self,
        scope: SocEffectivenessScope,
    ) -> list[SocRuleEffectivenessAggregate]: ...

    def read_behavior_group_aggregates(
        self,
        scope: SocEffectivenessScope,
        selector: SocRuleEffectivenessSelector,
    ) -> list[SocBehaviorGroupEffectivenessAggregate]: ...

    def read_memory_aggregates(
        self,
        scope: SocEffectivenessScope,
        selector: SocRuleEffectivenessSelector,
    ) -> list[SocMemoryEffectivenessAggregate]: ...


class SocOperationsKafkaProbe(Protocol):
    """Secret-free Kafka readiness projection used by operations surfaces."""

    def snapshot(self, *, check_connectivity: bool = False) -> SocOperationsKafkaSnapshot: ...


class InvestigationEvidenceRepository(Protocol):
    """Persistence boundary for investigation evidence produced by safe actions."""

    def save_evidence(self, evidence: InvestigationEvidence) -> None: ...

    def get_evidence(self, evidence_id: str) -> InvestigationEvidence | None: ...

    def list_evidence(
        self,
        *,
        queue_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[InvestigationEvidence]: ...


class SocEnrichmentExecutionRepository(Protocol):
    """Persistent, optimistic-concurrency boundary for PI-01D3 execution state."""

    def create_enrichment_execution(self, execution: SocEnrichmentExecution) -> bool: ...

    def get_enrichment_execution(self, execution_id: str) -> SocEnrichmentExecution | None: ...

    def find_enrichment_execution_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocEnrichmentExecution | None: ...

    def list_enrichment_executions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 20,
    ) -> list[SocEnrichmentExecution]: ...

    def compare_and_set_enrichment_execution(
        self,
        execution: SocEnrichmentExecution,
        *,
        expected_version: int,
    ) -> bool: ...

    def create_enrichment_action_attempt(self, attempt: SocEnrichmentActionAttempt) -> bool: ...

    def get_enrichment_action_attempt(
        self,
        attempt_id: str,
    ) -> SocEnrichmentActionAttempt | None: ...

    def compare_and_set_enrichment_action_attempt(
        self,
        attempt: SocEnrichmentActionAttempt,
        *,
        expected_version: int,
    ) -> bool: ...

    def list_enrichment_action_attempts(
        self,
        execution_id: str,
    ) -> list[SocEnrichmentActionAttempt]: ...


class SocInvestigationWorkflowPort(Protocol):
    """Existing-run investigation bridge used by daemon and batch entry points."""

    def execute(
        self,
        command: SocEnrichmentExecutionCommand | Mapping[str, Any],
        *,
        context: ServiceRequestContext,
    ) -> SocEnrichmentWorkflowResult: ...


class AuthorizationEnrichmentRepository(Protocol):
    """Append-only persistence boundary for authorization match enrichments."""

    def save_authorization_enrichment(self, record: AuthorizationEnrichmentRecord) -> None: ...

    def get_authorization_enrichment(self, enrichment_id: str) -> AuthorizationEnrichmentRecord | None: ...

    def find_authorization_enrichment_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AuthorizationEnrichmentRecord | None: ...

    def list_authorization_enrichments(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[AuthorizationEnrichmentRecord]: ...


class SocDispositionProposalRepository(Protocol):
    """Append-only persistence boundary for shadow disposition proposals."""

    def save_disposition_proposal(self, proposal: SocDispositionProposalRecord) -> None: ...

    def get_disposition_proposal(self, proposal_id: str) -> SocDispositionProposalRecord | None: ...

    def find_disposition_proposal_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionProposalRecord | None: ...

    def find_disposition_proposal_by_key(
        self,
        proposal_key: str,
    ) -> SocDispositionProposalRecord | None: ...

    def list_disposition_proposals(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        enrichment_id: str | None = None,
        limit: int = 50,
    ) -> list[SocDispositionProposalRecord]: ...


class TenantPolicyDecisionRepository(Protocol):
    """Append-only persistence boundary for tenant policy decisions."""

    def save_tenant_policy_decision(self, decision: TenantPolicyDecision) -> None: ...

    def get_tenant_policy_decision(self, decision_id: str) -> TenantPolicyDecision | None: ...

    def find_tenant_policy_decision_by_key(self, decision_key: str) -> TenantPolicyDecision | None: ...

    def find_tenant_policy_decision_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> TenantPolicyDecision | None: ...

    def list_tenant_policy_decisions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        tenant_id: str | None = None,
        policy_id: str | None = None,
        limit: int = 100,
    ) -> list[TenantPolicyDecision]: ...


class SocDispositionEvaluationRepository(Protocol):
    """Append-only persistence for sample manifests and reviewed outcomes."""

    def save_disposition_sample_manifest(self, manifest: SocDispositionSampleManifest) -> None: ...

    def get_disposition_sample_manifest(self, sample_id: str) -> SocDispositionSampleManifest | None: ...

    def find_disposition_sample_manifest_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionSampleManifest | None: ...

    def find_disposition_sample_manifest_by_key(
        self,
        sample_key: str,
    ) -> SocDispositionSampleManifest | None: ...

    def list_disposition_sample_manifests(
        self,
        *,
        scope_hash: str | None = None,
        limit: int = 100,
    ) -> list[SocDispositionSampleManifest]: ...

    def save_disposition_outcome(self, outcome: SocDispositionOutcomeRecord) -> None: ...

    def get_disposition_outcome(self, outcome_id: str) -> SocDispositionOutcomeRecord | None: ...

    def find_disposition_outcome_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocDispositionOutcomeRecord | None: ...

    def list_disposition_outcomes(
        self,
        *,
        proposal_id: str | None = None,
        queue_id: str | None = None,
        review_kind: SocDispositionOutcomeReviewKind | None = None,
        sample_id: str | None = None,
        limit: int = 500,
    ) -> list[SocDispositionOutcomeRecord]: ...

    def list_latest_disposition_outcomes_for_proposals(
        self,
        *,
        proposal_ids: Sequence[str],
        review_kind: SocDispositionOutcomeReviewKind,
        sample_id: str | None = None,
    ) -> list[SocDispositionOutcomeRecord]: ...


class MemoryCandidateRepository(Protocol):
    """Persistence boundary for SOC memory candidates."""

    def save_memory_candidate(self, candidate: SocMemoryCandidate) -> None: ...

    def get_memory_candidate(self, candidate_id: str) -> SocMemoryCandidate | None: ...

    def find_memory_candidate_by_idempotency_key(self, idempotency_key: str) -> SocMemoryCandidate | None: ...

    def find_memory_candidate_by_source_id(
        self,
        source_id: str,
    ) -> SocMemoryCandidate | None: ...

    def list_memory_candidates(
        self,
        *,
        status: SocMemoryCandidateStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryCandidate]: ...

    def find_memory_candidates_by_source_ids(
        self,
        source_ids: Sequence[str],
    ) -> list[SocMemoryCandidate]: ...

    def find_memory_candidates_by_lineage_keys(
        self,
        lineage_keys: Sequence[str],
    ) -> list[SocMemoryCandidate]: ...


class MemoryBusinessLessonDrafter(Protocol):
    """Advisory generator for one non-persisted candidate lesson draft."""

    model_name: str
    prompt_version: str

    def draft(
        self,
        candidate: SocMemoryCandidate,
        *,
        reviewer_verdict: Verdict,
        reviewer_context: str | None = None,
    ) -> SocMemoryBusinessLessonDraft: ...


class MemoryPatternObservationRepository(Protocol):
    """Immutable persistence boundary for repeated-pattern source observations."""

    def save_memory_pattern_observation(
        self,
        observation: MemoryPatternObservation,
    ) -> None: ...

    def get_memory_pattern_observation(
        self,
        observation_id: str,
    ) -> MemoryPatternObservation | None: ...

    def find_memory_pattern_observation_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> MemoryPatternObservation | None: ...

    def list_memory_pattern_observations(
        self,
        *,
        aggregation_key: str | None = None,
        lineage_key: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        data_class: MemoryPatternDataClass | None = None,
        source_type: MemoryPatternSourceType | None = None,
        alert_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[MemoryPatternObservation]: ...


class MemoryCenterRepository(Protocol):
    """Query-optimized persistence boundary for the Memory Center read model."""

    def get_memory_center_inventory(self) -> MemoryCenterInventory: ...

    def list_memory_pattern_lineage_stats(
        self,
        *,
        tenant_id: str | None = None,
        environment: str | None = None,
        data_class: MemoryPatternDataClass | None = None,
        profile_id: str | None = None,
        search: str | None = None,
        include_terminal_history: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> MemoryPatternLineageStatsPage: ...

    def find_memory_candidates_by_lineage_keys(
        self,
        lineage_keys: Sequence[str],
    ) -> list[SocMemoryCandidate]: ...


class MemoryPatternObserver(Protocol):
    """Optional application bridge used by Kafka and batch entry surfaces."""

    def observe_run(
        self,
        run: AnalysisRun,
        *,
        source_type: MemoryPatternSourceType,
        transport_ref: str,
        environment: str,
        data_class: MemoryPatternDataClass,
        context: ServiceRequestContext,
    ) -> MemoryPatternAggregationResult: ...


class MemoryEvolutionRepository(Protocol):
    """Append-only use/feedback plus versioned health persistence."""

    def save_memory_use(self, record: SocMemoryUseRecord) -> None: ...

    def find_memory_use_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocMemoryUseRecord | None: ...

    def list_memory_uses(
        self,
        *,
        memory_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 500,
    ) -> list[SocMemoryUseRecord]: ...

    def save_memory_feedback(self, event: SocMemoryFeedbackEvent) -> None: ...

    def find_memory_feedback_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocMemoryFeedbackEvent | None: ...

    def list_memory_feedback(
        self,
        *,
        memory_id: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
    ) -> list[SocMemoryFeedbackEvent]: ...

    def get_memory_health(
        self,
        memory_id: str,
        memory_version: int,
    ) -> SocMemoryHealthRecord | None: ...

    def compare_and_set_memory_health(
        self,
        record: SocMemoryHealthRecord,
        *,
        expected_version: int | None,
    ) -> bool: ...

    def save_memory_revision_proposal(
        self,
        proposal: SocMemoryRevisionProposal,
    ) -> None: ...

    def get_memory_revision_proposal(
        self,
        proposal_id: str,
    ) -> SocMemoryRevisionProposal | None: ...

    def find_memory_revision_proposal_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SocMemoryRevisionProposal | None: ...

    def list_memory_revision_proposals(
        self,
        *,
        memory_id: str | None = None,
        status: SocMemoryRevisionProposalStatus | None = None,
        limit: int = 500,
    ) -> list[SocMemoryRevisionProposal]: ...

    def compare_and_set_memory_revision_proposal(
        self,
        proposal: SocMemoryRevisionProposal,
        *,
        expected_status: SocMemoryRevisionProposalStatus,
    ) -> bool: ...


class MemoryFeedbackObserver(Protocol):
    """Correction bridge implemented by the Memory evolution service."""

    def capture_run_usage(self, run: AnalysisRun) -> list[SocMemoryUseRecord]: ...

    def record_correction_feedback(
        self,
        run: AnalysisRun,
        correction: CorrectionRecord,
        *,
        context: ServiceRequestContext,
    ) -> SocMemoryFeedbackResult: ...


class MemoryRecordRepository(Protocol):
    """Persistence boundary for confirmed SOC memory records."""

    def save_memory_record(self, record: SocMemoryRecord) -> None: ...

    def compare_and_set_memory_record(
        self,
        record: SocMemoryRecord,
        *,
        expected_version: int,
    ) -> bool: ...

    def get_memory_record(self, memory_id: str) -> SocMemoryRecord | None: ...

    def get_memory_record_by_candidate_id(self, candidate_id: str) -> SocMemoryRecord | None: ...

    def list_memory_records(
        self,
        *,
        status: SocMemoryRecordStatus | None = None,
        memory_type: SocMemoryCandidateType | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        source_candidate_id: str | None = None,
        source_run_id: str | None = None,
        source_alert_id: str | None = None,
        retrieval_enabled: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocMemoryRecord]: ...

    def find_memory_records_by_candidate_ids(
        self,
        candidate_ids: Sequence[str],
    ) -> list[SocMemoryRecord]: ...

    def find_memory_candidate_records(
        self,
        query: SocMemoryQuery,
    ) -> list[SocMemoryRecord]: ...


class SocAutomationRepository(Protocol):
    """Append-only persistence boundary for governed automation lineage."""

    def save_decision_transition(self, record: SocDecisionTransitionRecord) -> None: ...

    def find_decision_transition_by_key(
        self,
        transition_key: str,
    ) -> SocDecisionTransitionRecord | None: ...

    def save_disposition_transition(self, record: SocDispositionTransitionRecord) -> None: ...

    def find_disposition_transition_by_key(
        self,
        transition_key: str,
    ) -> SocDispositionTransitionRecord | None: ...

    def save_action_authorization(self, record: SocActionAuthorizationRecord) -> None: ...

    def find_action_authorization_by_key(
        self,
        authorization_key: str,
    ) -> SocActionAuthorizationRecord | None: ...

    def save_action_execution(self, record: SocActionExecutionRecord) -> None: ...

    def find_action_execution_by_key(
        self,
        execution_key: str,
    ) -> SocActionExecutionRecord | None: ...

    def list_decision_transitions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 100,
    ) -> list[SocDecisionTransitionRecord]: ...

    def list_disposition_transitions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 100,
    ) -> list[SocDispositionTransitionRecord]: ...

    def list_action_authorizations(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 100,
    ) -> list[SocActionAuthorizationRecord]: ...

    def list_action_executions(
        self,
        *,
        run_id: str | None = None,
        authorization_id: str | None = None,
        limit: int = 100,
    ) -> list[SocActionExecutionRecord]: ...


class SocExternalDispositionRepository(Protocol):
    """Persistence boundary for external disposition feedback events."""

    def save_external_disposition(self, record: SocExternalDispositionRecord) -> None: ...

    def find_external_disposition_by_idempotency_key(self, idempotency_key: str) -> SocExternalDispositionRecord | None: ...

    def list_external_dispositions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        external_system: str | None = None,
        external_case_id: str | None = None,
        limit: int = 50,
    ) -> list[SocExternalDispositionRecord]: ...


class SocAgentApprovalGrantRepository(Protocol):
    """Persistence boundary for approved high-risk action grants."""

    def save_approval_grant(self, grant: SocAgentApprovalGrant) -> None: ...

    def get_approval_grant(self, approval_grant_id: str) -> SocAgentApprovalGrant | None: ...

    def get_approval_grant_by_token(self, execution_token_id: str) -> SocAgentApprovalGrant | None: ...

    def get_approval_grant_by_request_id(self, approval_request_id: str) -> SocAgentApprovalGrant | None: ...


class SocAgentApprovalRequestRepository(Protocol):
    """Persistence boundary for high-risk action approval request lifecycle."""

    def create_approval_request(self, approval_request: SocAgentApprovalRequest) -> bool:
        """Insert one immutable request, returning false when the id already exists."""
        ...

    def get_approval_request(self, approval_request_id: str) -> SocAgentApprovalRequest | None: ...

    def list_approval_requests(
        self,
        *,
        status: str | None = "pending",
        limit: int = 50,
    ) -> list[SocAgentApprovalRequest]: ...

    def resolve_approval_request(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        expected_status: SocAgentApprovalRequestStatus,
        grant: SocAgentApprovalGrant | None = None,
    ) -> bool:
        """Atomically compare-and-set request state and optionally insert its grant."""
        ...


class NormalizationSchemaBaselineRepository(Protocol):
    """Persistence boundary for approved parser schema fingerprints."""

    def save_normalization_baseline(self, baseline: NormalizationSchemaBaseline) -> None: ...

    def get_normalization_baseline(self, baseline_id: str) -> NormalizationSchemaBaseline | None: ...

    def list_normalization_baselines(
        self,
        *,
        status: NormalizationBaselineStatus | None = None,
        tenant_id: str | None = None,
        source_system: str | None = None,
        adapter: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
        limit: int = 50,
    ) -> list[NormalizationSchemaBaseline]: ...


class NormalizationMaintenanceIssueRepository(Protocol):
    """Persistence boundary for parser/mapping maintenance issues."""

    def save_normalization_issue(self, issue: NormalizationMaintenanceIssue) -> None: ...

    def get_normalization_issue(self, issue_id: str) -> NormalizationMaintenanceIssue | None: ...

    def find_normalization_issue_by_dedupe_key(self, dedupe_key: str) -> NormalizationMaintenanceIssue | None: ...

    def list_normalization_issues(
        self,
        *,
        status: NormalizationMaintenanceIssueStatus | None = None,
        tenant_id: str | None = None,
        source_system: str | None = None,
        limit: int = 50,
    ) -> list[NormalizationMaintenanceIssue]: ...


class GovernedContextFactRepository(Protocol):
    """Append-only persistence boundary for governed context fact versions."""

    def append_governed_context_fact(
        self,
        fact: GovernedContextFact,
        *,
        expected_latest_version: int | None,
    ) -> None: ...

    def get_governed_context_fact(
        self,
        fact_id: str,
        *,
        version: int | None = None,
    ) -> GovernedContextFact | None: ...

    def list_governed_context_facts(
        self,
        query: GovernedContextFactQuery,
    ) -> list[GovernedContextFact]: ...

    def list_governed_context_fact_versions(
        self,
        fact_id: str,
        *,
        limit: int = 100,
    ) -> list[GovernedContextFact]: ...


class SkillImprovementRepository(Protocol):
    """Persistence boundary for PI-03C feedback and candidate backlog."""

    def save_skill_feedback_observation(self, observation: SkillFeedbackObservation) -> None: ...

    def get_skill_feedback_observation(self, observation_id: str) -> SkillFeedbackObservation | None: ...

    def find_skill_feedback_observation_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> SkillFeedbackObservation | None: ...

    def list_skill_feedback_observations(
        self,
        *,
        aggregation_key: str | None = None,
        tenant_id: str | None = None,
        data_class: SocEvaluationDataClass | None = None,
        source_type: SkillFeedbackSourceType | None = None,
        limit: int = 500,
    ) -> list[SkillFeedbackObservation]: ...

    def save_skill_improvement_candidate(self, candidate: SkillImprovementCandidate) -> None: ...

    def compare_and_set_skill_improvement_candidate(
        self,
        candidate: SkillImprovementCandidate,
        *,
        expected_version: int,
    ) -> bool: ...

    def get_skill_improvement_candidate(self, candidate_id: str) -> SkillImprovementCandidate | None: ...

    def find_skill_improvement_candidate_by_aggregation_key(
        self,
        aggregation_key: str,
    ) -> SkillImprovementCandidate | None: ...

    def list_skill_improvement_candidates(
        self,
        *,
        status: SkillImprovementCandidateStatus | None = None,
        tenant_id: str | None = None,
        data_class: SocEvaluationDataClass | None = None,
        skill_name: str | None = None,
        limit: int = 100,
    ) -> list[SkillImprovementCandidate]: ...


class SocMutationAuditRepository(Protocol):
    """Append-only persistence for service-level state mutation audits."""

    def append_mutation_audit(self, record: SocMutationAuditRecord) -> None: ...

    def find_mutation_audit_by_idempotency_key(
        self,
        operation: SocMutationOperation,
        idempotency_key: str,
    ) -> SocMutationAuditRecord | None: ...

    def list_mutation_audits(
        self,
        *,
        operation: SocMutationOperation | None = None,
        run_id: str | None = None,
        queue_id: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[SocMutationAuditRecord]: ...


class SocMutationRepository(
    AlertRepository,
    AlertSummaryRepository,
    DecisionAuditRepository,
    ReviewQueueRepository,
    MemoryCandidateRepository,
    MemoryRecordRepository,
    SocExternalDispositionRepository,
    SocDispositionProposalRepository,
    SocDispositionEvaluationRepository,
    SocAgentApprovalGrantRepository,
    SocAgentApprovalRequestRepository,
    SocMutationAuditRepository,
    MemoryPatternObservationRepository,
    MemoryEvolutionRepository,
    SkillImprovementRepository,
    Protocol,
):
    """Composite repository exposed only inside one mutation transaction."""


class SocMutationUnitOfWork(Protocol):
    """One transaction spanning all repositories touched by an SOC command."""

    def mutation_transaction(self) -> AbstractContextManager[SocMutationRepository]: ...


class SocActionAdapter(Protocol):
    """Replaceable adapter for approved SOC response actions."""

    descriptor: SocAgentActionAdapterDescriptor

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...


class SocActionAdapterRegistryPort(Protocol):
    """Allowlisted registry boundary for approved SOC response action adapters."""

    def list_descriptors(self) -> list[SocAgentActionAdapterDescriptor]: ...

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...

    def preflight_execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...


class SocEnrichmentPlannerPort(Protocol):
    """Build an immutable read-only action plan from a completed analysis run."""

    def plan(self, run: AnalysisRun, *, thread_id: str) -> SocEnrichmentPlan: ...


class SocEventSink(Protocol):
    """Event boundary for TUI/CLI progress, API SSE, channels, daemon logs, and audit."""

    def emit(self, event: SocEvent) -> None: ...
