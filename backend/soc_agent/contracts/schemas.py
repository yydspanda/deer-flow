"""Pydantic contracts for SOC Agent runtime boundaries."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts.authorization import AuthorizationFactRef, AuthorizationMatchResult, AuthorizationQuery
from soc_agent.contracts.common import ActorContext, EntrySurface
from soc_agent.contracts.enrichment import SocEnrichmentPlan
from soc_agent.contracts.investigation_reporting import SocInvestigationAddendum
from soc_agent.contracts.role_verification import (
    RoleAdjudicationVerificationResult,
    RoleVerificationTriggerDecision,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Verdict(StrEnum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    SUSPICIOUS = "suspicious"
    UNKNOWN = "unknown"
    NEEDS_REVIEW = "needs_review"


class TriageActivityStage(StrEnum):
    """Observed maturity of one analyzer scenario, not an attack verdict."""

    DETECTION_HIT = "detection_hit"
    ATTEMPT_OBSERVED = "attempt_observed"
    EFFECT_OBSERVED = "effect_observed"
    IMPACT_CONFIRMED = "impact_confirmed"
    INDETERMINATE = "indeterminate"


class TriageScenarioOrigin(StrEnum):
    """How the analyzer arrived at one scenario assessment."""

    UPSTREAM_HINT = "upstream_hint"
    INFERRED = "inferred"
    HYBRID = "hybrid"


class AnalysisReasoningBasis(StrEnum):
    """Explicit source class used by one model reasoning statement."""

    CURRENT_EVIDENCE = "current_evidence"
    GENERAL_SECURITY_KNOWLEDGE = "general_security_knowledge"
    SKILL = "skill"
    ADAPTER_CONTRACT = "adapter_contract"
    CONFIRMED_MEMORY = "confirmed_memory"
    GOVERNED_CONTEXT = "governed_context"
    TOOL_RESULT = "tool_result"


class AnalysisContextReferenceKind(StrEnum):
    """Governed non-alert context made visible to an analysis node."""

    SKILL = "skill"
    ADAPTER_CONTRACT = "adapter_contract"
    CONFIRMED_MEMORY = "confirmed_memory"
    GOVERNED_CONTEXT = "governed_context"
    TOOL_RESULT = "tool_result"


class AnalysisKnowledgeDestination(StrEnum):
    """Model-suggested destination for candidate knowledge under review."""

    GENERAL_SKILL = "general_skill"
    TENANT_MEMORY = "tenant_memory"
    GOVERNED_CONTEXT = "governed_context"
    PROVIDER_REQUIREMENT = "provider_requirement"
    ADAPTER_MAPPING = "adapter_mapping"
    TENANT_POLICY = "tenant_policy"
    EVALUATION_FIXTURE = "evaluation_fixture"
    REJECT_OR_VERIFY = "reject_or_verify"


class DecisionConfidenceSource(StrEnum):
    """Origin of the confidence carried by an operational decision."""

    UNKNOWN = "unknown"
    STUB_HEURISTIC = "stub_heuristic"
    LLM_SELF_REPORT = "llm_self_report"
    HUMAN_CONFIRMATION = "human_confirmation"
    EXTERNAL_DISPOSITION = "external_disposition"


class DecisionEvidenceState(StrEnum):
    """Compact evidence sufficiency state used by deterministic policy guards."""

    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    CONFLICTED = "conflicted"


class DecisionReviewReason(StrEnum):
    """Stable reason codes explaining why a decision requires human review."""

    CONFIDENCE_NOT_CALIBRATED = "confidence_not_calibrated"
    STUB_ANALYZER = "stub_analyzer"
    RAW_CONFIDENCE_BELOW_THRESHOLD = "raw_confidence_below_threshold"
    FALSE_POSITIVE_REQUIRES_CONFIRMATION = "false_positive_requires_confirmation"
    UNCERTAIN_VERDICT = "uncertain_verdict"
    DEGRADED_MESSAGE_SCHEMA = "degraded_message_schema"
    UNSUPPORTED_MESSAGE_SCHEMA = "unsupported_message_schema"
    HIGH_VALUE_EVIDENCE_GAP = "high_value_evidence_gap"
    TRUNCATED_ANALYSIS_EVIDENCE = "truncated_analysis_evidence"
    FACT_CONFLICT = "fact_conflict"
    UNGROUNDED_ANALYSIS_EVIDENCE = "ungrounded_analysis_evidence"
    UNGROUNDED_ANALYSIS_REASONING = "ungrounded_analysis_reasoning"
    UNPROVEN_OUTCOME_CLAIM = "unproven_outcome_claim"
    ROLE_VERIFICATION_CHALLENGED = "role_verification_challenged"
    ROLE_VERIFICATION_UNRESOLVED = "role_verification_unresolved"
    ROLE_VERIFIER_UNAVAILABLE = "role_verifier_unavailable"
    ANALYSIS_OUTPUT_DEGRADED = "analysis_output_degraded"
    ANALYSIS_FAILED = "analysis_failed"


class ConfidenceLabelReviewStatus(StrEnum):
    """Human review state for one offline confidence label."""

    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    EXCLUDED = "excluded"


class ConfidenceLabelReviewSource(StrEnum):
    """Who or what assigned a reviewed offline label."""

    HUMAN_REVIEW = "human_review"
    SIMULATION_FIXTURE = "simulation_fixture"


class SocEvaluationDataClass(StrEnum):
    """Whether an evaluation corpus contains simulated or approved real data."""

    SIMULATION = "simulation"
    DESENSITIZED_REAL = "desensitized_real"


class AnalysisEvidenceGroundingStatus(StrEnum):
    """Whether one analyzer evidence item can be traced to bounded input."""

    GROUNDED = "grounded"
    SOURCE_MISMATCH = "source_mismatch"
    VALUE_NOT_FOUND = "value_not_found"
    MISSING_VALUE = "missing_value"
    DESCRIPTION_CONTEXT_LEAKAGE = "description_context_leakage"
    REFERENCE_NOT_FOUND = "reference_not_found"
    UNSUPPORTED_REFERENCE = "unsupported_reference"


class RuntimeFailureKind(StrEnum):
    """Stable failure categories used by review and ingestion adapters."""

    INVALID_INPUT = "invalid_input"
    INPUT_LIMIT_EXCEEDED = "input_limit_exceeded"
    ANALYZER_CAPACITY = "analyzer_capacity"
    ANALYZER_TIMEOUT = "analyzer_timeout"
    ANALYZER_UNAVAILABLE = "analyzer_unavailable"
    ANALYZER_OUTPUT_INVALID = "analyzer_output_invalid"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    DECISION_POLICY_FAILED = "decision_policy_failed"
    INTERNAL_ERROR = "internal_error"


class AnalysisRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    SUCCESS = "success"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    ROLLED_BACK = "rolled_back"
    REPLAYED = "replayed"


class AnalysisOutputSection(StrEnum):
    """Independently recoverable sections of one model analysis response."""

    CORE = "core"
    SCENARIO_ASSESSMENTS = "scenario_assessments"
    NETWORK_DIRECTION = "network_direction"
    ROLE_ADJUDICATION = "role_adjudication"


class AnalysisOutputQualityStatus(StrEnum):
    """Runtime-owned status for the model-output acceptance boundary."""

    ACCEPTED = "accepted"
    REPAIRED = "repaired"
    DEGRADED = "degraded"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class AnalysisRequestJournalStatus(StrEnum):
    """Durable lifecycle state for the non-rollbackable analyzer call."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AnalysisProviderPurpose(StrEnum):
    """Stable purpose of one bounded model-provider invocation."""

    PRIMARY_ANALYSIS = "primary_analysis"
    PRIMARY_ANALYSIS_RETRY = "primary_analysis_retry"
    PRIMARY_ANALYSIS_SECTION_REPAIR = "primary_analysis_section_repair"
    ROLE_VERIFICATION = "role_verification"
    ROLE_VERIFICATION_RETRY = "role_verification_retry"


class PipelineStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"


class SocEventType(StrEnum):
    ANALYSIS_REQUESTED = "analysis.requested"
    ANALYSIS_COMPLETED = "analysis.completed"
    ANALYSIS_FAILED = "analysis.failed"
    EXTERNAL_DISPOSITION_RECEIVED = "external_disposition.received"
    REVIEW_CORRECTED = "review.corrected"
    REVIEW_ROLE_CONFIRMED = "review.role_confirmed"
    REVIEW_REQUESTED = "review.requested"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_PATTERN_OBSERVED = "memory_pattern.observed"
    NORMALIZATION_BASELINE_ACCEPTED = "normalization.baseline_accepted"
    NORMALIZATION_DRIFT_DETECTED = "normalization.drift_detected"
    NORMALIZATION_ISSUE_UPDATED = "normalization.issue_updated"
    GOVERNED_CONTEXT_FACT_PROPOSED = "governed_context.fact_proposed"
    GOVERNED_CONTEXT_FACT_ACTIVATED = "governed_context.fact_activated"
    GOVERNED_CONTEXT_FACT_SUSPENDED = "governed_context.fact_suspended"
    GOVERNED_CONTEXT_FACT_REVOKED = "governed_context.fact_revoked"
    GOVERNED_CONTEXT_FACT_EXPIRED = "governed_context.fact_expired"
    GOVERNED_CONTEXT_FACT_REVISED = "governed_context.fact_revised"
    AUTHORIZATION_ENRICHMENT_RECORDED = "authorization.enrichment_recorded"
    AUTHORIZATION_ENRICHMENT_REPLAYED = "authorization.enrichment_replayed"
    DISPOSITION_PROPOSAL_RECORDED = "disposition.proposal_recorded"
    TENANT_POLICY_DECISION_RECORDED = "tenant_policy.decision_recorded"
    DISPOSITION_SAMPLE_CREATED = "disposition.sample_created"
    DISPOSITION_OUTCOME_RECORDED = "disposition.outcome_recorded"
    SKILL_FEEDBACK_INGESTED = "skill_feedback.ingested"
    SKILL_IMPROVEMENT_CANDIDATE_UPDATED = "skill_improvement.candidate_updated"


class AuditAction(StrEnum):
    ANALYSIS = "analysis"
    REPLAY = "replay"
    CORRECTION = "correction"
    EXTERNAL_DISPOSITION = "external_disposition"


class ReviewQueueStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ReviewQueuePriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SocMemoryCandidateStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    CONFIRMED_CANDIDATE = "confirmed_candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"
    EXPIRED = "expired"


class SocMemoryCandidateReviewDecision(StrEnum):
    CONFIRM_CANDIDATE = "confirm_candidate"
    CONFIRM = "confirm"
    REJECT = "reject"
    DEPRECATE = "deprecate"
    EXPIRE = "expire"


class SocMemoryRecordStatus(StrEnum):
    CONFIRMED = "confirmed"
    DEPRECATED = "deprecated"
    EXPIRED = "expired"


class SocMemoryRetrievalActivationAction(StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"


SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION = "soc.memory_retrieval_activation_policy.v1"


class SocMemoryCandidateType(StrEnum):
    PROCEDURE = "procedure"
    DETECTION_LESSON = "detection_lesson"
    BENIGN_PATTERN = "benign_pattern"
    ENVIRONMENT_FACT = "environment_fact"
    IDENTITY_PATTERN = "identity_pattern"
    RESPONSE_POLICY_HINT = "response_policy_hint"
    NEGATIVE_MEMORY = "negative_memory"
    ADAPTER_MAPPING = "adapter_mapping"
    EVAL_FIXTURE = "eval_fixture"


class SocMemoryTargetArtifact(StrEnum):
    PUBLIC_SKILL = "public_skill"
    TENANT_MEMORY = "tenant_memory"
    ADAPTER_MAPPING = "adapter_mapping"
    POLICY_CONFIG = "policy_config"
    NORMALIZER = "normalizer"
    DOMAIN_HANDLER = "domain_handler"
    EVAL_FIXTURE = "eval_fixture"
    PROMPT_CONTEXT = "prompt_context"
    EXTERNAL_SYNC = "external_sync"


class SocMemoryDecisionImpact(StrEnum):
    NONE = "none"
    REVIEW_HINT = "review_hint"
    ROUTING_HINT = "routing_hint"
    SUPPRESSION_HINT = "suppression_hint"
    RESPONSE_POLICY_HINT = "response_policy_hint"
    DETECTION_DECISION = "detection_decision"


class SocMemoryDecisionEffect(StrEnum):
    """Typed effect explicitly approved for one confirmed memory record."""

    REINFORCE = "reinforce"
    OVERRIDE = "override"


class SocMemoryReviewEffect(StrEnum):
    """How an approved memory directive changes the base review requirement."""

    PRESERVE = "preserve"
    REQUIRE = "require"
    CLEAR = "clear"


class SocMemoryDecisionDirective(BaseModel):
    """Reviewed, machine-readable decision effect carried by confirmed memory.

    Free-form memory text never becomes an executable directive. A reviewer must
    explicitly attach this contract while confirming the candidate, and Runtime
    retrieval governance still decides whether the record is eligible.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_decision_directive.v1"] = "soc.memory_decision_directive.v1"
    effect: SocMemoryDecisionEffect
    target_verdict: Verdict
    review_effect: SocMemoryReviewEffect = SocMemoryReviewEffect.PRESERVE
    suggested_action: str | None = Field(default=None, min_length=1, max_length=1000)
    minimum_match_score: float = Field(default=5.0, ge=0.0, le=1000.0)
    required_facet_keys: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=1, max_length=2000)
    policy_version: Literal["soc.memory_decision_directive_policy.v1"] = "soc.memory_decision_directive_policy.v1"

    @field_validator("required_facet_keys")
    @classmethod
    def normalize_required_facet_keys(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values if str(value).strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("required_facet_keys must be unique")
        return sorted(normalized)

    @model_validator(mode="after")
    def require_scoped_override(self) -> SocMemoryDecisionDirective:
        if self.effect is SocMemoryDecisionEffect.OVERRIDE and not self.required_facet_keys:
            raise ValueError("memory decision override requires at least one required facet key")
        if self.review_effect is SocMemoryReviewEffect.CLEAR and self.target_verdict is Verdict.UNKNOWN:
            raise ValueError("unknown memory verdict cannot clear human review")
        return self


class SocMemoryCandidateSourceType(StrEnum):
    PINGAN_DOC = "pingan_doc"
    ANALYSIS_RUN = "analysis_run"
    CORRECTION = "correction"
    DOMAIN_FINDING = "domain_finding"
    EXTERNAL_DISPOSITION = "external_disposition"
    MANUAL_NOTE = "manual_note"
    REVIEW_NOTE = "review_note"
    REPEATED_PATTERN = "repeated_pattern"
    EVAL_FIXTURE = "eval_fixture"


class MemoryAdmissionStatus(StrEnum):
    ADMITTED = "admitted"
    OBSERVED_ONLY = "observed_only"


class MemoryAdmissionReasonCode(StrEnum):
    VERDICT_CHANGED = "verdict_changed"
    EXPLICIT_LEAD_AGENT_ACCEPTANCE = "explicit_lead_agent_acceptance"
    EXPLICIT_PROMOTION_REQUESTED = "explicit_promotion_requested"
    ANALYST_FEEDBACK_PRESENT = "analyst_feedback_present"
    REUSABLE_ANCHOR_PRESENT = "reusable_anchor_present"
    WEAK_OR_MISSING_REASON = "weak_or_missing_reason"
    CONFIRMATION_ONLY = "confirmation_only"
    NO_HUMAN_PROMOTION_SIGNAL = "no_human_promotion_signal"
    NO_REUSABLE_ANCHOR = "no_reusable_anchor"


class ReviewNoteOrigin(StrEnum):
    """Human-owned origin of a review note proposed as candidate memory."""

    ANALYST_NOTE = "analyst_note"
    ACCEPTED_LEAD_AGENT_CONCLUSION = "accepted_lead_agent_conclusion"


class SocOperationalDisposition(StrEnum):
    """Vendor-neutral operational outcome, separate from detection truth."""

    CLOSED_TRUE_POSITIVE = "closed_true_positive"
    CLOSED_FALSE_POSITIVE = "closed_false_positive"
    CLOSED_BENIGN_TRUE_POSITIVE = "closed_benign_true_positive"
    SUPPRESSED = "suppressed"
    ESCALATED = "escalated"
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    UNKNOWN = "unknown"


# Backward-compatible name retained for external disposition contracts.
SocExternalDispositionCanonicalStatus = SocOperationalDisposition


class SocDispositionProposalReasonCode(StrEnum):
    AUTHORIZED_ACTIVITY_EXACT_MATCH = "authorized_activity_exact_match"


class SocDispositionOutcomeReviewKind(StrEnum):
    """Independent label lanes used by shadow disposition evaluation."""

    ANALYST_RESOLUTION = "analyst_resolution"
    SAMPLED_QUALITY_REVIEW = "sampled_quality_review"


class SocDispositionOutcomeSource(StrEnum):
    ANALYST = "analyst"
    EXTERNAL_DISPOSITION = "external_disposition"
    REPLAY_LABEL = "replay_label"


class SocDispositionOutcomeStatus(StrEnum):
    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"
    INCONCLUSIVE = "inconclusive"


class SocDispositionSampleReviewReadiness(StrEnum):
    READY = "ready"
    WAITING_FOR_QUEUE_CLOSE = "waiting_for_queue_close"
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"


class SocDispositionEvaluationGateStatus(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    FAILED = "failed"
    PASSED_SHADOW_EVALUATION = "passed_shadow_evaluation"


class SocDispositionEvaluationRecommendation(StrEnum):
    HOLD_SHADOW = "hold_shadow"
    ELIGIBLE_FOR_GOVERNED_ROLLOUT_REVIEW = "eligible_for_governed_rollout_review"


class SocExternalDispositionApplyStatus(StrEnum):
    MAPPED = "mapped"
    UNMATCHED = "unmatched"
    IGNORED = "ignored"


class SocDomainName(StrEnum):
    APT = "apt"
    EDR = "edr"
    HIDS = "hids"
    WAF_F5 = "waf_f5"
    GENERIC = "generic"


class SocDomainFindingSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SocDomainFindingDisposition(StrEnum):
    SUSPICIOUS = "suspicious"
    LIKELY_TRUE_POSITIVE = "likely_true_positive"
    LIKELY_FALSE_POSITIVE = "likely_false_positive"
    BENIGN_AUTHORIZED_CANDIDATE = "benign_authorized_candidate"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"


class SocEvidenceProfile(BaseModel):
    """Evidence availability and usage profile for one domain finding."""

    schema_version: str = "soc.evidence_profile.v1"
    sources: dict[str, str] = Field(default_factory=dict)
    used_sources: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SocFindingConclusion(BaseModel):
    """Current analyst-facing conclusion even when evidence is incomplete."""

    schema_version: str = "soc.finding_conclusion.v1"
    summary: str = "Current conclusion is not available for this finding."
    risk_level: SocDomainFindingSeverity = SocDomainFindingSeverity.INFO
    certainty: Literal["low", "medium_low", "medium", "medium_high", "high"] = "low"
    recommended_action: str = "manual_review"
    recommended_queue: str | None = None
    automation_allowed: bool = False
    rationale: list[str] = Field(default_factory=list)


class AuthorizationEnrichmentCommand(BaseModel):
    """Append one deterministic authorization match to an existing investigation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.authorization_enrichment_command.v1"] = "soc.authorization_enrichment_command.v1"
    run_id: str = Field(min_length=1, max_length=64)
    queue_id: str | None = Field(default=None, min_length=1, max_length=64)
    query: AuthorizationQuery
    idempotency_key: str = Field(min_length=1, max_length=128)
    replay_of_enrichment_id: str | None = Field(default=None, min_length=1, max_length=64)


class AuthorizationEnrichmentRecord(BaseModel):
    """Immutable, replayable authorization match attached to one analysis run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.authorization_enrichment_record.v1"] = "soc.authorization_enrichment_record.v1"
    enrichment_id: str = Field(default_factory=lambda: f"AAE-{uuid4().hex[:20].upper()}")
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    queue_id: str | None = Field(default=None, min_length=1, max_length=64)
    query: AuthorizationQuery
    query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    match_result: AuthorizationMatchResult
    matcher_policy_version: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    replay_of_enrichment_id: str | None = Field(default=None, min_length=1, max_length=64)
    created_by: ActorContext = Field(default_factory=ActorContext)
    created_at: datetime = Field(default_factory=utc_now)
    shadow_only: Literal[True] = True
    decision_impact: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_lineage(self) -> AuthorizationEnrichmentRecord:
        if self.query.alert_id != self.alert_id:
            raise ValueError("authorization enrichment query alert_id must match record alert_id")
        if self.match_result.alert_id != self.alert_id:
            raise ValueError("authorization enrichment result alert_id must match record alert_id")
        if self.match_result.query_id != self.query.query_id:
            raise ValueError("authorization enrichment result query_id must match the stored query")
        if self.matcher_policy_version != self.match_result.policy_version:
            raise ValueError("authorization enrichment matcher policy must match the stored result")
        return self


class AuthorizationEnrichmentApplyResult(BaseModel):
    """Service result for an initial or replayed authorization enrichment."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.authorization_enrichment_apply_result.v1"] = "soc.authorization_enrichment_apply_result.v1"
    record: AuthorizationEnrichmentRecord
    idempotent: bool = False
    event_written: bool = False


class SocDetectionTruthSnapshot(BaseModel):
    """Immutable detection state observed when an operational proposal is made."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.detection_truth_snapshot.v1"] = "soc.detection_truth_snapshot.v1"
    verdict: Verdict
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: Literal["decision", "analysis"]
    decision_policy_version: str | None = Field(default=None, min_length=1, max_length=128)
    latest_correction_id: str | None = Field(default=None, min_length=1, max_length=64)


class SocDispositionProposalCommand(BaseModel):
    """Request a shadow operational disposition from one persisted enrichment."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_proposal_command.v1"] = "soc.disposition_proposal_command.v1"
    enrichment_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SocDispositionProposalRecord(BaseModel):
    """Immutable shadow proposal that never mutates detection or review state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_proposal_record.v1"] = "soc.disposition_proposal_record.v1"
    proposal_id: str = Field(default_factory=lambda: f"DPROP-{uuid4().hex[:20].upper()}")
    proposal_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    queue_id: str = Field(min_length=1, max_length=64)
    source_enrichment_id: str = Field(min_length=1, max_length=64)
    source_query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_matcher_policy_version: str = Field(min_length=1, max_length=128)
    source_fact_refs: list[AuthorizationFactRef] = Field(min_length=1, max_length=100)
    source_evidence_refs: list[str] = Field(default_factory=list, max_length=300)
    detection_truth: SocDetectionTruthSnapshot
    proposed_disposition: SocOperationalDisposition
    reason_code: SocDispositionProposalReasonCode
    rationale: list[str] = Field(min_length=1, max_length=20)
    policy_version: Literal["soc.disposition_proposal_policy.v1"] = "soc.disposition_proposal_policy.v1"
    idempotency_key: str = Field(min_length=1, max_length=128)
    created_by: ActorContext = Field(default_factory=ActorContext)
    created_at: datetime = Field(default_factory=utc_now)
    proposal_mode: Literal["shadow"] = "shadow"
    application_status: Literal["not_applied"] = "not_applied"
    requires_human_review: Literal[True] = True
    auto_close_allowed: Literal[False] = False
    detection_truth_impact: Literal["none"] = "none"
    review_queue_impact: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_shadow_boundary(self) -> SocDispositionProposalRecord:
        if self.detection_truth.verdict is not Verdict.TRUE_POSITIVE:
            raise ValueError("benign true-positive disposition requires true-positive detection truth")
        if self.proposed_disposition is not SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE:
            raise ValueError("DP-01 only permits closed_benign_true_positive shadow proposals")
        if self.reason_code is not SocDispositionProposalReasonCode.AUTHORIZED_ACTIVITY_EXACT_MATCH:
            raise ValueError("DP-01 requires an exact authorized-activity reason")
        return self


class SocDispositionProposalApplyResult(BaseModel):
    """Service result for creating or deduplicating one shadow proposal."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_proposal_apply_result.v1"] = "soc.disposition_proposal_apply_result.v1"
    proposal: SocDispositionProposalRecord
    idempotent: bool = False
    event_written: bool = False


class SocDispositionEvaluationScope(BaseModel):
    """One tenant/environment/version/time cohort evaluated as a unit."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_evaluation_scope.v1"] = "soc.disposition_evaluation_scope.v1"
    tenant_id: str | None = Field(default=None, max_length=128)
    environment: str | None = Field(default=None, max_length=128)
    window_start: datetime
    window_end: datetime
    proposal_policy_version: str = Field(min_length=1, max_length=128)
    matcher_policy_version: str = Field(min_length=1, max_length=128)

    @field_validator("window_start", "window_end")
    @classmethod
    def validate_aware_window(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("disposition evaluation windows must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> SocDispositionEvaluationScope:
        if self.window_end <= self.window_start:
            raise ValueError("disposition evaluation window_end must be after window_start")
        return self


class SocDispositionSampleCreateCommand(BaseModel):
    """Create a reproducible quality-review sample from one evaluation scope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_sample_create_command.v1"] = "soc.disposition_sample_create_command.v1"
    scope: SocDispositionEvaluationScope
    sample_size: int = Field(ge=1, le=10_000)
    selection_seed: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SocDispositionSampleManifest(BaseModel):
    """Immutable deterministic sample manifest used to prevent cherry-picking."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_sample_manifest.v1"] = "soc.disposition_sample_manifest.v1"
    sample_id: str = Field(default_factory=lambda: f"DSAMPLE-{uuid4().hex[:20].upper()}")
    sample_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: SocDispositionEvaluationScope
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    population_count: int = Field(ge=1)
    population_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_proposal_ids: list[str] = Field(min_length=1, max_length=10_000)
    sample_size: int = Field(ge=1, le=10_000)
    selection_seed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sampling_method: Literal["sha256_rank_v1"] = "sha256_rank_v1"
    idempotency_key: str = Field(min_length=1, max_length=128)
    created_by: ActorContext = Field(default_factory=ActorContext)
    created_at: datetime = Field(default_factory=utc_now)
    shadow_only: Literal[True] = True
    decision_impact: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_manifest(self) -> SocDispositionSampleManifest:
        if self.sample_size != len(self.selected_proposal_ids):
            raise ValueError("sample_size must equal selected_proposal_ids length")
        if self.sample_size > self.population_count:
            raise ValueError("sample_size cannot exceed population_count")
        if len(set(self.selected_proposal_ids)) != len(self.selected_proposal_ids):
            raise ValueError("selected_proposal_ids must be unique")
        return self


class SocDispositionSampleCreateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_sample_create_result.v1"] = "soc.disposition_sample_create_result.v1"
    manifest: SocDispositionSampleManifest
    idempotent: bool = False


class SocDispositionOutcomeCommand(BaseModel):
    """Record one explicit human or trusted-system label for a proposal."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_outcome_command.v1"] = "soc.disposition_outcome_command.v1"
    proposal_id: str = Field(min_length=1, max_length=64)
    observed_disposition: SocOperationalDisposition
    review_kind: SocDispositionOutcomeReviewKind = SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION
    source: SocDispositionOutcomeSource = SocDispositionOutcomeSource.ANALYST
    source_ref: str | None = Field(default=None, min_length=1, max_length=256)
    sample_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=300)
    observed_at: datetime | None = None
    supersedes_outcome_id: str | None = Field(default=None, min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("disposition outcome observed_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_review_kind(self) -> SocDispositionOutcomeCommand:
        if self.review_kind is SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW and self.sample_id is None:
            raise ValueError("sampled quality review requires sample_id")
        if self.review_kind is SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION and self.sample_id is not None:
            raise ValueError("analyst resolution must not reference a sample manifest")
        if self.source is not SocDispositionOutcomeSource.ANALYST and self.source_ref is None:
            raise ValueError("non-analyst outcome source requires source_ref")
        return self


class SocDispositionOutcomeRecord(BaseModel):
    """Append-only proposal label; later corrections supersede instead of overwrite."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_outcome_record.v1"] = "soc.disposition_outcome_record.v1"
    outcome_id: str = Field(default_factory=lambda: f"DOUT-{uuid4().hex[:20].upper()}")
    lineage_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str = Field(min_length=1, max_length=64)
    proposal_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    queue_id: str = Field(min_length=1, max_length=64)
    proposed_disposition: SocOperationalDisposition
    observed_disposition: SocOperationalDisposition
    outcome_status: SocDispositionOutcomeStatus
    review_kind: SocDispositionOutcomeReviewKind
    source: SocDispositionOutcomeSource
    source_ref: str | None = Field(default=None, min_length=1, max_length=256)
    sample_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=300)
    proposal_policy_version: str = Field(min_length=1, max_length=128)
    supersedes_outcome_id: str | None = Field(default=None, min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    reviewed_by: ActorContext = Field(default_factory=ActorContext)
    observed_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    shadow_only: Literal[True] = True
    decision_impact: Literal["none"] = "none"
    review_queue_impact: Literal["none"] = "none"


class SocDispositionSampleManifestListResponse(BaseModel):
    """Bounded immutable campaign list for the EV-03 review inbox."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_sample_manifest_list.v1"] = "soc.disposition_sample_manifest_list.v1"
    items: list[SocDispositionSampleManifest]
    limit: int = Field(ge=1, le=500)
    has_more: bool = False


class SocDispositionSampleReviewItem(BaseModel):
    """One manifest-selected proposal projected for an identified reviewer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_sample_review_item.v1"] = "soc.disposition_sample_review_item.v1"
    sample_id: str = Field(min_length=1, max_length=64)
    selection_rank: int = Field(ge=1)
    proposal_id: str = Field(min_length=1, max_length=64)
    proposal: SocDispositionProposalRecord | None = None
    queue_item: ReviewQueueItem | None = None
    primary_outcome: SocDispositionOutcomeRecord | None = None
    sampled_outcome: SocDispositionOutcomeRecord | None = None
    sampled_outcome_independent: bool | None = None
    reviewer_independent: bool | None = None
    readiness: SocDispositionSampleReviewReadiness
    can_record_outcome: bool = False
    blocking_reasons: list[str] = Field(default_factory=list, max_length=20)
    auto_close_allowed: Literal[False] = False
    decision_impact: Literal["none"] = "none"


class SocDispositionSampleReviewInbox(BaseModel):
    """Derived campaign progress and one bounded page of review work."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_sample_review_inbox.v1"] = "soc.disposition_sample_review_inbox.v1"
    manifest: SocDispositionSampleManifest
    reviewer_actor_id: str = Field(min_length=1, max_length=128)
    total_count: int = Field(ge=1)
    completed_count: int = Field(ge=0)
    remaining_count: int = Field(ge=0)
    reviewer_conflict_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0.0, le=1.0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    has_more: bool = False
    items: list[SocDispositionSampleReviewItem]
    auto_close_allowed: Literal[False] = False
    decision_impact: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_counts(self) -> SocDispositionSampleReviewInbox:
        if self.completed_count + self.remaining_count != self.total_count:
            raise ValueError("sample review completed and remaining counts must equal total_count")
        if self.manifest.sample_size != self.total_count:
            raise ValueError("sample review total_count must equal manifest sample_size")
        if self.offset + len(self.items) > self.total_count:
            raise ValueError("sample review page exceeds manifest bounds")
        return self


class SocDispositionOutcomeApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_outcome_apply_result.v1"] = "soc.disposition_outcome_apply_result.v1"
    outcome: SocDispositionOutcomeRecord
    idempotent: bool = False
    event_written: bool = False


class SocDispositionEvaluationGatePolicy(BaseModel):
    """Explicit deployment-owned thresholds; the report cannot enable automation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_evaluation_gate_policy.v1"] = "soc.disposition_evaluation_gate_policy.v1"
    policy_version: str = Field(min_length=1, max_length=128)
    scope: SocDispositionEvaluationScope
    accepted_primary_sources: list[SocDispositionOutcomeSource] = Field(min_length=1, max_length=10)
    accepted_sample_sources: list[SocDispositionOutcomeSource] = Field(min_length=1, max_length=10)
    minimum_proposal_count: int = Field(ge=1)
    minimum_resolved_count: int = Field(ge=1)
    minimum_resolution_rate: float = Field(ge=0.0, le=1.0)
    minimum_shadow_precision: float = Field(ge=0.0, le=1.0)
    maximum_override_rate: float = Field(ge=0.0, le=1.0)
    minimum_sampled_review_count: int = Field(ge=1)
    minimum_sampled_precision: float = Field(ge=0.0, le=1.0)
    minimum_sample_coverage_rate: float = Field(ge=0.0, le=1.0)
    minimum_sample_agreement_count: int = Field(ge=1)
    minimum_sample_agreement_rate: float = Field(ge=0.0, le=1.0)
    minimum_freshness_pass_rate: float = Field(ge=0.0, le=1.0)
    maximum_fact_version_fanout: int = Field(ge=1)
    auto_close_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_source_allowlists(self) -> SocDispositionEvaluationGatePolicy:
        if len(set(self.accepted_primary_sources)) != len(self.accepted_primary_sources):
            raise ValueError("accepted_primary_sources must be unique")
        if len(set(self.accepted_sample_sources)) != len(self.accepted_sample_sources):
            raise ValueError("accepted_sample_sources must be unique")
        return self


class SocDispositionEvaluationMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    value: float | None = None
    threshold: float
    comparator: Literal[">=", "<="]
    passed: bool
    reason: str = Field(min_length=1, max_length=1000)


class SocDispositionFactFanout(BaseModel):
    fact_id: str = Field(min_length=1, max_length=64)
    fact_version_id: str = Field(min_length=1, max_length=64)
    proposal_count: int = Field(ge=1)


class SocDispositionEvaluationReport(BaseModel):
    """Read-only EV-01 metrics and rollout-review recommendation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_evaluation_report.v1"] = "soc.disposition_evaluation_report.v1"
    policy: SocDispositionEvaluationGatePolicy
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_complete: bool
    proposal_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    confirmed_count: int = Field(ge=0)
    overridden_count: int = Field(ge=0)
    inconclusive_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    resolution_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    shadow_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    override_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    sampled_population_count: int = Field(ge=0)
    sampled_review_count: int = Field(ge=0)
    sampled_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    sample_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    sample_agreement_count: int = Field(ge=0)
    sample_agreement_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    freshness_pass_count: int = Field(ge=0)
    freshness_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    maximum_fact_version_fanout: int = Field(ge=0)
    fact_fanout: list[SocDispositionFactFanout] = Field(default_factory=list, max_length=10_000)
    metrics: list[SocDispositionEvaluationMetric] = Field(default_factory=list, max_length=50)
    gate_status: SocDispositionEvaluationGateStatus
    recommendation: SocDispositionEvaluationRecommendation
    rollout_review_eligible: bool = False
    rollback_signals: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    auto_close_allowed: Literal[False] = False
    generated_at: datetime = Field(default_factory=utc_now)


class SocEvent(BaseModel):
    schema_version: str = "soc.event.v1"
    event_id: str = Field(default_factory=lambda: f"EVT-{uuid4().hex[:12].upper()}")
    event_type: SocEventType
    request_id: str
    run_id: str | None = None
    alert_id: str | None = None
    actor: ActorContext
    occurred_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class SocAgentStreamEvent(BaseModel):
    """DeerFlow-compatible stream event emitted by SOC interactive services."""

    schema_version: str = "soc.agent_stream.v1"
    type: Literal["values", "messages-tuple", "custom", "end"]
    data: dict[str, Any] = Field(default_factory=dict)


class SocAgentChatRequest(BaseModel):
    """One operator message sent to the SOC interactive investigation surface."""

    schema_version: str = "soc.agent_chat_request.v1"
    message: str = Field(min_length=1)
    thread_id: str | None = None
    queue_id: str | None = None
    run_id: str | None = None
    allowed_routes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocAgentChatResponse(BaseModel):
    """Materialized response for headless callers over the same stream contract."""

    schema_version: str = "soc.agent_chat_response.v1"
    thread_id: str
    events: list[SocAgentStreamEvent] = Field(default_factory=list)
    final_text: str = ""


class SocSkillRecommendation(BaseModel):
    """One DeerFlow skill recommendation for a SOC alert or investigation."""

    skill_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    matched_fields: list[str] = Field(default_factory=list)


class SocSkillResolution(BaseModel):
    """Resolved SOC domain skills without loading skill content directly."""

    schema_version: str = "soc.skill_resolution.v1"
    alert_id: str | None = None
    agent_name: str = "soc-triage"
    selected_skills: list[SocSkillRecommendation] = Field(default_factory=list)
    available_agent_skills: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SocSkillContextItem(BaseModel):
    """Reviewed package guidance injected into bounded SOC prompts."""

    skill_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    matched_fields: list[str] = Field(default_factory=list)
    guidance: str = Field(min_length=1)
    guidance_source: str = Field(min_length=1)
    guidance_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    estimated_token_count: int = Field(ge=1)
    token_budget: int = Field(default=240, ge=0)


class SocSkillContext(BaseModel):
    """Bounded skill context derived from DeerFlow skill selection."""

    schema_version: str = "soc.skill_context.v2"
    source: str = "soc_skill_package_projection"
    selected_skills: list[SocSkillContextItem] = Field(default_factory=list)
    total_token_budget: int = Field(default=0, ge=0)
    total_estimated_token_count: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)


class SocLeadAgentProfile(BaseModel):
    """DeerFlow custom-agent profile payload recommended for SOC triage."""

    schema_version: str = "soc.lead_agent_profile.v2"
    name: str = "soc-triage"
    description: str
    skills: list[str] = Field(default_factory=list)
    tool_groups: list[str] | None = None
    middlewares: list[str] = Field(default_factory=list)
    soul: str


class SocLeadAgentInstallResult(BaseModel):
    """Result of installing the SOC profile into DeerFlow custom-agent storage."""

    schema_version: str = "soc.lead_agent_install_result.v1"
    agent_name: str
    user_id: str
    agent_dir: str
    config_path: str
    soul_path: str
    status: Literal["dry_run", "created", "updated", "skipped"]
    dry_run: bool = False
    overwrite: bool = False
    message: str


class SocSpecialistSubagentInstallResult(BaseModel):
    """Result of merging SOC specialists into DeerFlow root configuration."""

    schema_version: str = "soc.specialist_subagent_install_result.v1"
    config_path: str
    status: Literal["dry_run", "created", "updated", "unchanged"]
    agent_names: list[str] = Field(default_factory=list)
    existing_custom_agent_names: list[str] = Field(default_factory=list)
    changed_agent_names: list[str] = Field(default_factory=list)
    overwritten_agent_names: list[str] = Field(default_factory=list)
    dry_run: bool = False
    overwrite: bool = False
    message: str


class SocLeadAgentReviewContextArtifact(BaseModel):
    """Bounded ReviewQueue context handed to the DeerFlow SOC lead agent."""

    schema_version: str = "soc.lead_agent_review_context_artifact.v1"
    artifact_id: str = Field(default_factory=lambda: f"LCTX-{uuid4().hex[:12].upper()}")
    queue_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    alert_id: str = Field(min_length=1)
    context_hash: str = Field(min_length=1)
    skill_context_hash: str | None = None
    actor: ActorContext | None = None
    review: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    fact_context: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] | None = None
    similar_alerts: list[dict[str, Any]] = Field(default_factory=list)
    action_evidence: list[dict[str, Any]] = Field(default_factory=list)
    investigation_addenda: list[dict[str, Any]] = Field(default_factory=list)
    external_dispositions: list[dict[str, Any]] = Field(default_factory=list)
    authorization_enrichments: list[dict[str, Any]] = Field(default_factory=list)
    disposition_proposals: list[dict[str, Any]] = Field(default_factory=list)
    disposition_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)
    relevant_memories: dict[str, Any] | None = None
    investigation_view: dict[str, Any] | None = None
    skill_context: SocSkillContext | None = None
    instructions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SocSpecialistDelegationContext(BaseModel):
    """Server-built bounded case context passed to one SOC specialist."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.specialist_delegation_context.v1"] = "soc.specialist_delegation_context.v1"
    delegation_id: str = Field(min_length=1, max_length=256)
    specialist_name: str = Field(min_length=1, max_length=128)
    chat_thread_id: str = Field(min_length=1, max_length=128)
    chat_run_id: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=256)
    queue_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    artifact_id: str = Field(min_length=1, max_length=64)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_context_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    task_description: str = Field(min_length=1, max_length=160)
    lead_agent_task: str = Field(min_length=1, max_length=4000)
    evidence_context: dict[str, Any] = Field(default_factory=dict)
    projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_source: Literal["soc_review_service"] = "soc_review_service"
    result_authority: Literal["advisory_only"] = "advisory_only"
    decision_impact: Literal["none"] = "none"
    external_fact_authority: bool = False
    action_allowed: bool = False
    memory_write_allowed: bool = False


class SocSpecialistDelegationProvenance(BaseModel):
    """Trusted metadata stamped on a native DeerFlow specialist task result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.specialist_delegation_provenance.v1"] = "soc.specialist_delegation_provenance.v1"
    delegation_id: str = Field(min_length=1, max_length=256)
    specialist_name: str = Field(min_length=1, max_length=128)
    chat_thread_id: str = Field(min_length=1, max_length=128)
    chat_run_id: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=256)
    queue_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    artifact_id: str = Field(min_length=1, max_length=64)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    bounded_context_char_count: int = Field(ge=1)
    result_status: Literal[
        "accepted_advisory",
        "execution_failed",
        "rejected_action_marker",
    ] = "accepted_advisory"
    result_authority: Literal["advisory_only"] = "advisory_only"
    decision_impact: Literal["none"] = "none"
    external_fact_authority: bool = False
    action_allowed: bool = False
    memory_write_allowed: bool = False


class SocLeadAgentReviewThreadBinding(BaseModel):
    """Server-owned immutable binding between one DeerFlow thread and review item."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.lead_agent_review_thread_binding.v1"] = "soc.lead_agent_review_thread_binding.v1"
    queue_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    bound_by_actor_id: str = Field(min_length=1, max_length=256)
    bound_at: datetime = Field(default_factory=utc_now)


class SocLeadAgentReviewContextProvenance(BaseModel):
    """Exact bounded review context consumed by one Lead Agent model output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.lead_agent_review_context_provenance.v1"] = "soc.lead_agent_review_context_provenance.v1"
    artifact_schema_version: Literal["soc.lead_agent_review_context_artifact.v1"] = "soc.lead_agent_review_context_artifact.v1"
    artifact_id: str = Field(min_length=1, max_length=64)
    queue_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_context_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    chat_thread_id: str = Field(min_length=1, max_length=256)
    chat_run_id: str = Field(min_length=1, max_length=256)
    rendered_char_count: int = Field(ge=1)
    source: Literal["gateway_soc_review_service"] = "gateway_soc_review_service"
    injection_mode: Literal["transient_model_context"] = "transient_model_context"
    context_created_at: datetime


class SocAgentRouteDecision(BaseModel):
    """Whitelisted capability route selected for one SOC chat request."""

    schema_version: str = "soc.agent_route_decision.v1"
    route: str = Field(min_length=1)
    allowed: bool
    reason: str = Field(min_length=1)
    requires_human_approval: bool = False
    input_text: str | None = None


class SocAgentRiskLevel(StrEnum):
    READ_ONLY = "read_only"
    ANALYST_WRITE = "analyst_write"
    HIGH_RISK = "high_risk"
    UNKNOWN = "unknown"


class SocAgentApprovalRequestStatus(StrEnum):
    """Lifecycle state for one human approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SocAgentPermissionDecision(BaseModel):
    """Permission decision for one routed SOC Agent action."""

    schema_version: str = "soc.agent_permission_decision.v1"
    decision_id: str = Field(default_factory=lambda: f"PERM-{uuid4().hex[:12].upper()}")
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    allowed: bool
    risk_level: SocAgentRiskLevel = SocAgentRiskLevel.UNKNOWN
    reason: str = Field(min_length=1)
    requires_human_approval: bool = False
    approval_request_id: str | None = None
    policy_version: str = "soc.agent_action_policy.v1"
    actor: ActorContext | None = None


class SocAgentActionProposal(BaseModel):
    """Structured action candidate proposed by a lead agent or skill."""

    schema_version: str = "soc.agent_action_proposal.v1"
    proposal_id: str = Field(default_factory=lambda: f"SAP-{uuid4().hex[:12].upper()}")
    source: Literal["lead_agent", "skill", "deterministic", "mcp"] = "lead_agent"
    thread_id: str | None = None
    queue_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    context_hash: str | None = None
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    proposed_by: ActorContext | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SocAgentApprovalRequest(BaseModel):
    """Human approval request for a blocked high-risk SOC Agent action."""

    schema_version: str = "soc.agent_approval_request.v1"
    approval_request_id: str = Field(default_factory=lambda: f"APR-{uuid4().hex[:12].upper()}")
    permission_decision_id: str
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    risk_level: SocAgentRiskLevel
    reason: str = Field(min_length=1)
    requested_by: ActorContext
    submitted_by: ActorContext | None = None
    source_proposal_id: str | None = None
    action_payload: dict[str, Any] = Field(default_factory=dict)
    context_refs: dict[str, Any] = Field(default_factory=dict)
    status: SocAgentApprovalRequestStatus = SocAgentApprovalRequestStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    resolved_by: ActorContext | None = None
    resolution_reason: str | None = Field(default=None, min_length=1)
    resolution_idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    resolution_expires_in_seconds: int | None = Field(default=None, gt=0, le=86_400)
    approval_grant_id: str | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> SocAgentApprovalRequest:
        resolution_fields = (
            self.resolved_at,
            self.resolved_by,
            self.resolution_reason,
            self.resolution_idempotency_key,
            self.resolution_expires_in_seconds,
            self.approval_grant_id,
        )
        if self.status is SocAgentApprovalRequestStatus.PENDING:
            if any(value is not None for value in resolution_fields):
                raise ValueError("pending approval request cannot contain resolution fields")
            return self

        if self.resolved_at is None or self.resolved_by is None:
            raise ValueError("resolved approval request requires resolved_at and resolved_by")
        if self.resolution_reason is None or self.resolution_idempotency_key is None:
            raise ValueError("resolved approval request requires reason and idempotency key")
        if self.status is SocAgentApprovalRequestStatus.APPROVED:
            if self.approval_grant_id is None or self.resolution_expires_in_seconds is None:
                raise ValueError("approved request requires grant id and grant expiry")
        elif self.approval_grant_id is not None or self.resolution_expires_in_seconds is not None:
            raise ValueError("rejected or expired request cannot reference an approval grant")
        return self


class SocAgentApprovalGrant(BaseModel):
    """One-time execution grant produced after human approval."""

    schema_version: str = "soc.agent_approval_grant.v1"
    approval_grant_id: str = Field(default_factory=lambda: f"APG-{uuid4().hex[:12].upper()}")
    execution_token_id: str = Field(default_factory=lambda: f"SAT-{uuid4().hex[:16].upper()}")
    approval_request_id: str
    permission_decision_id: str
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    risk_level: SocAgentRiskLevel
    requested_by: ActorContext
    approved_by: ActorContext
    approval_reason: str = Field(min_length=1)
    idempotency_key: str | None = None
    status: Literal["approved", "consumed"] = "approved"
    single_use: Literal[True] = True
    approved_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    consumed_at: datetime | None = None
    consumed_by: ActorContext | None = None
    consume_idempotency_key: str | None = None
    execution_result_id: str | None = None
    execution_result_payload: dict[str, Any] | None = None
    policy_version: str = "soc.agent_action_policy.v1"


class SocAgentActionCommand(BaseModel):
    """Command to execute a registered SOC action adapter."""

    schema_version: str = "soc.agent_action_command.v1"
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    dry_run: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class SocAgentApprovedActionCommand(SocAgentActionCommand):
    """Command to validate an approved high-risk action before execution."""

    schema_version: str = "soc.agent_approved_action_command.v1"
    execution_token_id: str = Field(min_length=1)


class SocAgentActionResult(BaseModel):
    """Result of dispatching an allowed SOC Agent route to a service action."""

    schema_version: str = "soc.agent_action_result.v1"
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: Literal["success", "denied", "failed"]
    message: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_human_approval: bool = False


class InvestigationEvidence(BaseModel):
    """Investigation evidence produced by bounded SOC service actions."""

    schema_version: str = "soc.investigation_evidence.v1"
    evidence_id: str = Field(default_factory=lambda: f"EVI-{uuid4().hex[:12].upper()}")
    source_type: Literal["read_only_action_result"] = "read_only_action_result"
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: Literal["success", "denied", "failed"]
    message: str = Field(min_length=1)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    mocked: bool = False
    queue_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    thread_id: str | None = None
    source_proposal_id: str | None = None
    context_hash: str | None = None
    request_id: str | None = Field(default=None, max_length=256)
    trace_id: str | None = Field(default=None, max_length=256)
    actor: ActorContext | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SocMemoryCandidateSource(BaseModel):
    """Auditable origin metadata for one proposed SOC memory candidate."""

    source_type: SocMemoryCandidateSourceType
    source_surface: EntrySurface | None = None
    source_id: str | None = None
    source_doc: str | None = None
    source_section: str | None = None
    capability_card_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    queue_id: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    correction_id: str | None = None
    eval_sample_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_traceable_reference(self) -> SocMemoryCandidateSource:
        references = (
            self.source_id,
            self.source_doc,
            self.capability_card_id,
            self.run_id,
            self.alert_id,
            self.queue_id,
            self.thread_id,
            self.message_id,
            self.correction_id,
            self.eval_sample_id,
        )
        if not any(references):
            raise ValueError("memory candidate source must include at least one traceable reference")
        return self


class SocMemoryCandidateValidity(BaseModel):
    """Scope and freshness window for candidate knowledge under review."""

    valid_from: datetime = Field(default_factory=utc_now)
    valid_until: datetime | None = None
    review_after_days: int | None = Field(default=None, ge=1)
    notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_forward_window(self) -> SocMemoryCandidateValidity:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class SocMemoryCandidateCreateCommand(BaseModel):
    """Command to propose candidate SOC knowledge without confirming it."""

    candidate_type: SocMemoryCandidateType
    target_artifact: SocMemoryTargetArtifact
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tenant_scope: str = Field(default="global", min_length=1)
    tenant_id: str | None = None
    source: SocMemoryCandidateSource
    evidence_refs: list[str] = Field(min_length=1)
    validity: SocMemoryCandidateValidity
    idempotency_key: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    decision_impact: SocMemoryDecisionImpact = SocMemoryDecisionImpact.NONE
    review_owner: str | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryAdmissionDecision(BaseModel):
    """Deterministic quality gate applied before a review candidate is created."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_admission_decision.v1"] = "soc.memory_admission_decision.v1"
    policy_version: Literal["soc.memory_admission_policy.v1"] = "soc.memory_admission_policy.v1"
    status: MemoryAdmissionStatus
    source_type: SocMemoryCandidateSourceType
    candidate_type: SocMemoryCandidateType
    quality_score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[MemoryAdmissionReasonCode] = Field(min_length=1)
    reusable_facets: dict[str, list[str]] = Field(default_factory=dict)
    command_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str | None = None


class SocMemoryCandidateReviewCommand(BaseModel):
    """Command to review one SOC memory candidate without bypassing service audit."""

    candidate_id: str = Field(min_length=1)
    decision: SocMemoryCandidateReviewDecision
    reason: str = Field(min_length=1)
    record_summary: str | None = None
    record_content: str | None = None
    decision_directive: SocMemoryDecisionDirective | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def restrict_decision_directive_to_confirmation(
        self,
    ) -> SocMemoryCandidateReviewCommand:
        if self.decision_directive is not None and self.decision is not SocMemoryCandidateReviewDecision.CONFIRM:
            raise ValueError("decision_directive is allowed only when confirming a memory candidate")
        return self


class SocMemoryCandidate(BaseModel):
    """Reviewable candidate knowledge item that cannot affect runtime decisions."""

    schema_version: str = "soc.memory_candidate.v1"
    candidate_id: str = Field(default_factory=lambda: f"MC-{uuid4().hex[:12].upper()}")
    candidate_type: SocMemoryCandidateType
    target_artifact: SocMemoryTargetArtifact
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tenant_scope: str = Field(default="global", min_length=1)
    tenant_id: str | None = None
    status: SocMemoryCandidateStatus = SocMemoryCandidateStatus.PENDING_REVIEW
    source: SocMemoryCandidateSource
    evidence_refs: list[str] = Field(min_length=1)
    validity: SocMemoryCandidateValidity
    idempotency_key: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    decision_impact: SocMemoryDecisionImpact = SocMemoryDecisionImpact.NONE
    runtime_decision_allowed: Literal[False] = False
    review_required: Literal[True] = True
    review_owner: str | None = None
    reviewed_by: ActorContext | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    proposed_by: ActorContext | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SocMemoryRecord(BaseModel):
    """Confirmed SOC memory record with an explicitly governed retrieval gate."""

    schema_version: str = "soc.memory_record.v1"
    memory_id: str = Field(default_factory=lambda: f"MEM-{uuid4().hex[:12].upper()}")
    version: int = Field(default=1, ge=1)
    memory_type: SocMemoryCandidateType
    target_artifact: SocMemoryTargetArtifact
    status: SocMemoryRecordStatus = SocMemoryRecordStatus.CONFIRMED
    tenant_scope: str = Field(default="global", min_length=1)
    tenant_id: str | None = None
    source_candidate_id: str
    source: SocMemoryCandidateSource
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(min_length=1)
    validity: SocMemoryCandidateValidity
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_impact: SocMemoryDecisionImpact = SocMemoryDecisionImpact.NONE
    decision_directive: SocMemoryDecisionDirective | None = None
    content_hash: str = Field(min_length=1)
    facets_hash: str = Field(min_length=1)
    retrieval_enabled: bool = False
    retrieval_policy_version: str | None = None
    retrieval_valid_until: datetime | None = None
    retrieval_review_due_at: datetime | None = None
    retrieval_updated_by: ActorContext | None = None
    retrieval_updated_at: datetime | None = None
    retrieval_reason: str | None = None
    created_by: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deprecated_by: ActorContext | None = None
    deprecated_at: datetime | None = None
    deprecation_reason: str | None = None
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocMemoryRetrievalActivationCommand(BaseModel):
    """Version-controlled command for enabling or disabling memory retrieval."""

    schema_version: Literal["soc.memory_retrieval_activation_command.v1"] = "soc.memory_retrieval_activation_command.v1"
    memory_id: str = Field(min_length=1, max_length=64)
    action: SocMemoryRetrievalActivationAction
    expected_record_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    activation_valid_until: datetime | None = None
    review_after_days: int | None = Field(default=None, ge=1, le=365)
    policy_version: Literal["soc.memory_retrieval_activation_policy.v1"] = SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_enable_governance_window(self) -> SocMemoryRetrievalActivationCommand:
        if self.action is SocMemoryRetrievalActivationAction.ENABLE:
            if self.activation_valid_until is None:
                raise ValueError("activation_valid_until is required when enabling retrieval")
            if self.activation_valid_until.utcoffset() is None:
                raise ValueError("activation_valid_until must include a timezone")
            if self.review_after_days is None:
                raise ValueError("review_after_days is required when enabling retrieval")
        elif self.activation_valid_until is not None or self.review_after_days is not None:
            raise ValueError("disable does not accept activation validity or review scheduling fields")
        return self


class SocMemoryRetrievalActivationResult(BaseModel):
    """Auditable result of one retrieval activation transition."""

    schema_version: str = "soc.memory_retrieval_activation_result.v1"
    record: SocMemoryRecord
    action: SocMemoryRetrievalActivationAction
    previous_record_version: int = Field(ge=1)
    previous_retrieval_enabled: bool
    audit_id: str | None = None
    policy_version: str = SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION
    changed_at: datetime = Field(default_factory=utc_now)


class SocMemoryQuery(BaseModel):
    """Retrieval query for confirmed SOC memory records.

    Facets are optional by design. Version 1 retains broad scored retrieval.
    Version 2 fails closed per record unless a type-appropriate exact anchor
    matches; an alert without such an anchor simply receives no memory context.
    """

    schema_version: Literal["soc.memory_query.v2"] = "soc.memory_query.v2"
    policy_version: Literal[
        "soc.memory_retrieval_policy.v1",
        "soc.memory_retrieval_policy.v2",
    ] = "soc.memory_retrieval_policy.v1"
    memory_types: list[SocMemoryCandidateType] = Field(default_factory=list)
    statuses: list[SocMemoryRecordStatus] = Field(default_factory=lambda: [SocMemoryRecordStatus.CONFIRMED])
    tenant_scope: str | None = None
    tenant_id: str | None = None
    facets: dict[str, list[str]] = Field(default_factory=dict)
    text_terms: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=50)
    candidate_limit: int = Field(default=200, ge=1, le=1000)
    min_score: float = Field(default=1.0, ge=0.0)
    max_tokens: int = Field(default=1200, ge=100, le=10000)
    require_retrieval_enabled: Literal[True] = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("facets", mode="before")
    @classmethod
    def normalize_facets(cls, value: Any) -> dict[str, list[str]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("facets must be an object")
        normalized: dict[str, list[str]] = {}
        for raw_key, raw_values in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            normalized_values = sorted({str(item).strip() for item in values if str(item).strip()})
            if normalized_values:
                normalized[key] = normalized_values
        return normalized

    @field_validator("text_terms", "evidence_refs", mode="before")
    @classmethod
    def normalize_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        return sorted({str(item).strip() for item in values if str(item).strip()})


class SocMemoryMatch(BaseModel):
    """One retrieval-safe memory match with replayable scoring metadata."""

    memory_id: str
    version: int = Field(ge=1)
    record: SocMemoryRecord
    score: float = Field(ge=0.0)
    match_reasons: list[str] = Field(default_factory=list)
    matched_facets: dict[str, list[str]] = Field(default_factory=dict)
    anchor_match_reasons: list[str] = Field(default_factory=list)
    matched_anchor_facets: dict[str, list[str]] = Field(default_factory=dict)
    token_estimate: int = Field(ge=1)
    content_hash: str
    facets_hash: str
    retrieval_enabled: Literal[True] = True


class SocMemoryRetrievalDiff(BaseModel):
    """Timestamp-independent replay diff for two deterministic retrieval results."""

    schema_version: str = "soc.memory_retrieval_diff.v1"
    baseline_policy_version: str
    current_policy_version: str
    added_memory_ids: list[str] = Field(default_factory=list)
    removed_memory_ids: list[str] = Field(default_factory=list)
    changed_memory_ids: list[str] = Field(default_factory=list)
    unchanged_memory_ids: list[str] = Field(default_factory=list)
    changed: bool = False


class SocMemoryRetrievalResult(BaseModel):
    """Retrieval result that is safe to inspect before prompt injection is enabled."""

    schema_version: Literal["soc.memory_retrieval_result.v2"] = "soc.memory_retrieval_result.v2"
    policy_version: str = "soc.memory_retrieval_policy.v1"
    query: SocMemoryQuery
    matches: list[SocMemoryMatch] = Field(default_factory=list)
    total_candidate_count: int = Field(default=0, ge=0)
    skipped_retrieval_disabled: int = Field(default=0, ge=0)
    skipped_ungoverned_activation: int = Field(default=0, ge=0)
    skipped_activation_expired: int = Field(default=0, ge=0)
    skipped_review_overdue: int = Field(default=0, ge=0)
    skipped_status: int = Field(default=0, ge=0)
    skipped_expired: int = Field(default=0, ge=0)
    skipped_missing_strong_anchor: int = Field(default=0, ge=0)
    skipped_below_min_score: int = Field(default=0, ge=0)
    returned_count: int = Field(default=0, ge=0)
    total_token_estimate: int = Field(default=0, ge=0)
    max_tokens: int = Field(default=1200, ge=100)
    replay_diff: SocMemoryRetrievalDiff | None = None
    created_at: datetime = Field(default_factory=utc_now)


class SocMemoryCandidateReviewResult(BaseModel):
    """Result of a candidate review transition."""

    schema_version: str = "soc.memory_candidate_review_result.v1"
    candidate: SocMemoryCandidate
    memory_record: SocMemoryRecord | None = None
    previous_status: SocMemoryCandidateStatus
    decision: SocMemoryCandidateReviewDecision
    reviewed_at: datetime = Field(default_factory=utc_now)


class SocExternalDispositionEvent(BaseModel):
    """Vendor-neutral external ticket/case disposition event."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.external_disposition.v1"] = "soc.external_disposition.v1"
    tenant_id: str | None = None
    external_system: str = Field(min_length=1)
    external_case_id: str = Field(min_length=1)
    source_event_id: str | None = None
    source_version: str | None = None
    external_alert_ref: str | None = None
    soc_alert_id: str | None = None
    soc_run_id: str | None = None
    soc_queue_id: str | None = None
    external_status: str = Field(min_length=1)
    external_reason: str | None = None
    external_tags: list[str] = Field(default_factory=list)
    operator: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime
    raw_payload_hash: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocExternalDispositionStatusMapping(BaseModel):
    """One external status to canonical disposition mapping rule."""

    external_status: str = Field(min_length=1)
    canonical_status: SocExternalDispositionCanonicalStatus
    external_system: str | None = None
    trust_level: Literal["low", "medium", "high"] = "medium"
    apply_to_review: bool = True
    notes: str | None = None


class SocExternalDispositionMappingConfig(BaseModel):
    """Configurable status mapping used by external disposition adapters/services."""

    schema_version: str = "soc.external_disposition_mapping.v1"
    tenant_id: str | None = None
    status_mappings: list[SocExternalDispositionStatusMapping] = Field(default_factory=list)
    default_canonical_status: SocExternalDispositionCanonicalStatus = SocExternalDispositionCanonicalStatus.UNKNOWN


class SocExternalDispositionAdapterConfig(BaseModel):
    """Generic field-path mapping from an external payload to the canonical event."""

    schema_version: str = "soc.external_disposition_adapter.v1"
    external_system: str = Field(min_length=1)
    tenant_id: str | None = None
    field_paths: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocExternalDispositionRecord(BaseModel):
    """Persisted external disposition event and local mapping outcome."""

    schema_version: str = "soc.external_disposition_record.v1"
    disposition_id: str = Field(default_factory=lambda: f"XDISP-{uuid4().hex[:12].upper()}")
    event: SocExternalDispositionEvent
    canonical_status: SocExternalDispositionCanonicalStatus
    apply_status: SocExternalDispositionApplyStatus
    idempotency_key: str = Field(min_length=1)
    target_run_id: str | None = None
    target_alert_id: str | None = None
    target_queue_id: str | None = None
    matched_by: str | None = None
    apply_reason: str = Field(min_length=1)
    audit_id: str | None = None
    correction_id: str | None = None
    memory_candidate_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocExternalDispositionApplyResult(BaseModel):
    """Service result for applying one external disposition event."""

    schema_version: str = "soc.external_disposition_apply_result.v1"
    record: SocExternalDispositionRecord
    idempotent: bool = False
    audit_written: bool = False
    correction_applied: bool = False
    memory_candidate_created: bool = False
    disposition_outcome_recorded: bool = False
    disposition_outcome_id: str | None = None
    disposition_outcome_idempotent: bool = False
    disposition_outcome_skip_reason: str | None = None


class SocAgentActionAdapterDescriptor(BaseModel):
    """Registered adapter capability for an approved SOC action."""

    schema_version: str = "soc.agent_action_adapter_descriptor.v1"
    adapter_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    risk_level: SocAgentRiskLevel = SocAgentRiskLevel.UNKNOWN
    adapter_kind: Literal["noop", "service", "mcp", "http", "script"] = "noop"
    external_side_effect: Literal["none", "read", "write", "destructive"] = "none"
    dry_run_supported: bool = True
    execute_supported: bool = False
    idempotency_required: bool = True
    required_payload_fields: list[str] = Field(default_factory=list)
    required_context_refs: list[str] = Field(default_factory=list)
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocAssetLookupRecord(BaseModel):
    """Read-only asset inventory record returned by the SOC asset lookup adapter."""

    schema_version: str = "soc.asset_lookup_record.v1"
    asset_key: str = Field(min_length=1)
    asset_id: str | None = None
    hostname: str | None = None
    primary_ip: str | None = None
    owner: str | None = None
    business_unit: str | None = None
    environment: str | None = None
    criticality: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    source: str = "static"
    attributes: dict[str, Any] = Field(default_factory=dict)


class SocThreatIntelReputationRecord(BaseModel):
    """Read-only threat-intelligence reputation for a network entity."""

    schema_version: str = "soc.threat_intel_reputation_record.v1"
    ip: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    score: int | None = Field(default=None, ge=0, le=100)
    last_seen: datetime | None = None
    geo: str | None = None
    source: str = "static"
    expires_at: datetime | None = None
    stale: bool = False
    mocked: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)


class SocSecurityTagRecord(BaseModel):
    """Read-only authorization, maintenance, or security-testing tag evidence."""

    schema_version: str = "soc.security_tag_record.v1"
    entity_key: str = Field(min_length=1)
    entity_type: str | None = None
    labels: list[str] = Field(default_factory=list)
    tag_types: list[str] = Field(default_factory=list)
    is_valid: bool = False
    valid_until: datetime | None = None
    source: str = "static"
    mocked: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)


class SocDaemonMessage(BaseModel):
    """Versioned message envelope consumed by the SOC daemon boundary.

    Real Kafka consumers should decode transport metadata into this contract
    before calling core services. The payload stays source-specific at the
    boundary and is validated by the downstream service selected by ``kind``.
    """

    schema_version: str = "soc.daemon_message.v1"
    message_id: str = Field(default_factory=lambda: f"SDM-{uuid4().hex[:12].upper()}")
    kind: Literal["alert", "approval_request"]
    payload: dict[str, Any] = Field(default_factory=dict)
    topic: str | None = None
    partition: int | None = None
    offset: int | None = None
    key: str | None = None
    received_at: datetime = Field(default_factory=utc_now)


class SocDaemonProcessResult(BaseModel):
    """Result of processing one daemon message through core services."""

    schema_version: str = "soc.daemon_process_result.v1"
    message_id: str
    kind: Literal["alert", "approval_request"]
    status: Literal["processed", "failed"]
    run_id: str | None = None
    alert_id: str | None = None
    analysis_status: str | None = None
    normalization_issue_count: int = Field(default=0, ge=0)
    normalization_issue_ids: list[str] = Field(default_factory=list)
    normalization_warnings: list[str] = Field(default_factory=list)
    approval_request_id: str | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AlertSourceType(StrEnum):
    UNKNOWN = "unknown"
    SIEM = "siem"
    EDR = "edr"
    XDR = "xdr"
    HIDS = "hids"
    NIDS = "nids"
    NDR = "ndr"
    WAF = "waf"
    F5 = "f5"
    IAM = "iam"
    CLOUD = "cloud"
    THREAT_INTEL = "threat_intel"
    OTHER = "other"


class EvidenceLayer(StrEnum):
    RAW_MESSAGE = "raw_message"
    RAW_STRUCTURED = "raw_structured"
    PROCESSED_FIELD = "processed_field"
    AGENT_INFERENCE = "agent_inference"
    HUMAN_CONFIRMED = "human_confirmed"


class EvidenceTrustLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class FieldReasoningStatus(StrEnum):
    """Whether one evidence path is eligible as an independent fact source."""

    SELECTED_EVIDENCE = "selected_evidence"
    SUPPLEMENTARY_EVIDENCE = "supplementary_evidence"
    INCLUDED_CANONICAL_PROJECTION = "included_canonical_projection"
    EXCLUDED_UNSELECTED_FALLBACK = "excluded_unselected_fallback"
    EXCLUDED_DUPLICATE_PROJECTION = "excluded_duplicate_projection"


class SensitiveEvidenceMode(StrEnum):
    """How bounded model evidence handles sensitive field values."""

    REDACT = "redact"
    FULL = "full"


class EvidenceInputPolicyName(StrEnum):
    RAW_MESSAGE_FIRST = "raw_message_first"
    STRUCTURED_FALLBACK = "structured_fallback"
    CANONICAL_FIELDS_FIRST = "canonical_fields_first"
    HYBRID_WITH_CONFLICT_CHECK = "hybrid_with_conflict_check"


class EntityKind(StrEnum):
    IP = "ip"
    DOMAIN = "domain"
    URL = "url"
    EMAIL = "email"
    PROCESS = "process"
    USER = "user"
    HOST = "host"
    FILE_HASH = "file_hash"
    RULE_CODE = "rule_code"
    RULE_NAME = "rule_name"
    RULE = "rule"
    MITRE = "mitre"
    ASSET = "asset"
    BEHAVIOR = "behavior"


class EntityExtractionSource(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    NORMALIZER = "normalizer"
    ANALYST = "analyst"


class AlertSourceRef(BaseModel):
    """Where the alert came from.

    This keeps vendor/product names out of the core detection logic while still
    letting adapters preserve enough source context for memory and audit.
    """

    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    vendor: str | None = None
    product: str | None = None
    integration_name: str | None = None


class DetectionRuleRef(BaseModel):
    """Normalized detection identity.

    ``rule_code`` is a strong optional identifier. ``detection_key`` is the
    runtime-generated fallback key used by memory and lessons when a source does
    not provide stable rule IDs.
    """

    rule_code: str | None = None
    rule_name: str | None = None
    rule_version: str | None = None
    rule_category: str | None = None
    detection_key: str | None = None


class AlertEventRef(BaseModel):
    event_id: str | None = None
    event_time: datetime | None = None
    received_at: datetime = Field(default_factory=utc_now)


class AlertClassification(BaseModel):
    severity: str | None = None
    category: str | None = None
    tactic: list[str] = Field(default_factory=list)
    technique: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class NetworkObservationRef(BaseModel):
    """One network/session observation without collapsing adjacent raw events."""

    observation_id: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1)
    event_time: str | None = None
    source_ip: str | None = None
    destination_ip: str | None = None
    src_port: int | None = None
    dst_port: int | None = None
    protocol: str | None = None
    application_protocol: str | None = None
    direction: str | None = None
    community_id: str | None = None
    flow_id: str | int | None = None
    sensor_source_ip: str | None = None
    sensor_source_port: int | None = None
    sensor_target_ip: str | None = None
    sensor_target_port: int | None = None
    sensor_source_zone: str | None = None
    sensor_target_zone: str | None = None
    bytes_to_server: int | None = Field(default=None, ge=0)
    bytes_to_client: int | None = Field(default=None, ge=0)
    packets_to_server: int | None = Field(default=None, ge=0)
    packets_to_client: int | None = Field(default=None, ge=0)
    forwarded_chain: list[str] = Field(default_factory=list)


class NetworkEntityRef(BaseModel):
    source_ip: str | None = None
    destination_ip: str | None = None
    src_port: int | None = None
    dst_port: int | None = None
    protocol: str | None = None
    application_protocol: str | None = None
    direction: str | None = None
    domain: str | None = None
    url: str | None = None
    observations: list[NetworkObservationRef] = Field(default_factory=list)


class ProcessNodeRef(BaseModel):
    """One process node observed in a source process tree."""

    process_name: str = Field(min_length=1)
    process_id: int | None = Field(default=None, ge=0)
    process_path: str | None = None
    command_line: str | None = None
    username: str | None = None
    md5: str | None = None
    sha256: str | None = None


class ProcessObservationRef(BaseModel):
    """One process observation with its source and full available ancestry."""

    observation_id: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1)
    event_time: str | None = None
    host_name: str | None = None
    parent_process_id: int | None = Field(default=None, ge=0)
    nodes: list[ProcessNodeRef] = Field(default_factory=list)


class ProcessEntityRef(BaseModel):
    process_name: str | None = None
    process_id: int | None = Field(default=None, ge=0)
    process_path: str | None = None
    command_line: str | None = None
    parent_process_name: str | None = None
    parent_process_id: int | None = Field(default=None, ge=0)
    parent_command_line: str | None = None
    md5: str | None = None
    sha256: str | None = None
    observations: list[ProcessObservationRef] = Field(default_factory=list)


class UserEntityRef(BaseModel):
    username: str | None = None
    user_id: str | None = None
    um_account: str | None = None
    src_user: str | None = None
    dst_user: str | None = None


class HostEntityRef(BaseModel):
    host_name: str | None = None
    host_id: str | None = None
    asset_id: str | None = None
    asset_group: str | None = None
    ip_addresses: list[str] = Field(default_factory=list)


class FileObservationRelation(StrEnum):
    ENDPOINT_ACTION_TARGET = "endpoint_action_target"
    OBSERVED_ARTIFACT = "observed_artifact"


class FileObservationRef(BaseModel):
    """One file artifact with exact evidence provenance and relation."""

    observation_id: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1)
    relation: FileObservationRelation
    event_time: str | None = None
    process_id: int | None = Field(default=None, ge=0)
    file_name: str | None = None
    file_path: str | None = None
    sha256: str | None = None
    sha1: str | None = None
    md5: str | None = None
    exists: bool | None = None

    @model_validator(mode="after")
    def require_artifact_identity(self) -> FileObservationRef:
        if not any(
            (
                self.file_name,
                self.file_path,
                self.sha256,
                self.sha1,
                self.md5,
            )
        ):
            raise ValueError("file observation requires a name, path, or hash")
        return self


class FileEntityRef(BaseModel):
    file_name: str | None = None
    file_path: str | None = None
    sha256: str | None = None
    sha1: str | None = None
    md5: str | None = None
    observations: list[FileObservationRef] = Field(default_factory=list)


class HttpObservationRef(BaseModel):
    """One bounded HTTP transaction view linked to its raw message."""

    observation_id: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1)
    event_time: str | None = None
    method: str | None = None
    host: str | None = None
    path: str | None = None
    url: str | None = None
    protocol: str | None = None
    port: int | None = Field(default=None, ge=0, le=65535)
    status_code: int | None = None
    user_agent: str | None = None
    referer: str | None = None
    x_forwarded_for: str | None = None


class HttpEntityRef(BaseModel):
    method: str | None = None
    host: str | None = None
    path: str | None = None
    url: str | None = None
    protocol: str | None = None
    port: int | None = Field(default=None, ge=0, le=65535)
    status_code: int | None = None
    user_agent: str | None = None
    referer: str | None = None
    x_forwarded_for: str | None = None
    observations: list[HttpObservationRef] = Field(default_factory=list)


class EmailObservationRef(BaseModel):
    """One bounded email-message view with exact source provenance."""

    observation_id: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1)
    event_time: str | None = None
    message_id: str | None = None
    sender_addresses: list[str] = Field(default_factory=list, max_length=100)
    recipient_addresses: list[str] = Field(default_factory=list, max_length=500)
    cc_addresses: list[str] = Field(default_factory=list, max_length=500)
    subject: str | None = Field(default=None, max_length=2000)
    links: list[str] = Field(default_factory=list, max_length=200)
    attachment_names: list[str] = Field(default_factory=list, max_length=200)


class EmailEntityRef(BaseModel):
    """Canonical email summary; message body remains bounded evidence."""

    message_id: str | None = None
    sender_addresses: list[str] = Field(default_factory=list, max_length=100)
    recipient_addresses: list[str] = Field(default_factory=list, max_length=500)
    cc_addresses: list[str] = Field(default_factory=list, max_length=500)
    subject: str | None = Field(default=None, max_length=2000)
    links: list[str] = Field(default_factory=list, max_length=200)
    attachment_names: list[str] = Field(default_factory=list, max_length=200)
    observations: list[EmailObservationRef] = Field(default_factory=list, max_length=100)


class ThreatEntityRef(BaseModel):
    iocs: list[str] = Field(default_factory=list)
    campaign: str | None = None
    threat_actor: str | None = None
    malware_family: str | None = None


class AlertEntitySet(BaseModel):
    network: NetworkEntityRef = Field(default_factory=NetworkEntityRef)
    process: ProcessEntityRef = Field(default_factory=ProcessEntityRef)
    user: UserEntityRef = Field(default_factory=UserEntityRef)
    host: HostEntityRef = Field(default_factory=HostEntityRef)
    file: FileEntityRef = Field(default_factory=FileEntityRef)
    http: HttpEntityRef = Field(default_factory=HttpEntityRef)
    email: EmailEntityRef | None = None
    threat: ThreatEntityRef = Field(default_factory=ThreatEntityRef)


class EvidenceItem(BaseModel):
    evidence_ref: str | None = Field(
        default=None,
        pattern=r"^E-[A-F0-9]{12}$",
    )
    source: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=2000)
    value: str | int | float | bool | None = None

    @field_validator("value")
    @classmethod
    def bound_string_value(cls, value: str | int | float | bool | None) -> str | int | float | bool | None:
        if isinstance(value, str) and len(value) > 4000:
            raise ValueError("evidence string value exceeds 4000 characters")
        return value


class AnalysisEvidenceCatalogItem(BaseModel):
    """One exact current-alert scalar visible to the bounded analyzer."""

    schema_version: Literal["soc.analysis_evidence_catalog_item.v1"] = "soc.analysis_evidence_catalog_item.v1"
    evidence_ref: str = Field(pattern=r"^E-[A-F0-9]{12}$")
    source_path: str = Field(min_length=1, max_length=512)
    value: str | int | float | bool | None
    value_type: Literal["string", "integer", "number", "boolean", "null"]
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.UNKNOWN
    source_kind: Literal["current_alert"] = "current_alert"


class AnalysisContextCatalogItem(BaseModel):
    """One governed non-alert reference available to model reasoning."""

    schema_version: Literal["soc.analysis_context_catalog_item.v1"] = "soc.analysis_context_catalog_item.v1"
    context_ref: str = Field(pattern=r"^(?:S|A|M|C|T)-[A-F0-9]{12}$")
    kind: AnalysisContextReferenceKind
    label: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=512)
    summary: str = Field(min_length=1, max_length=4000)
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reference_namespace(self) -> AnalysisContextCatalogItem:
        expected_prefix = {
            AnalysisContextReferenceKind.SKILL: "S-",
            AnalysisContextReferenceKind.ADAPTER_CONTRACT: "A-",
            AnalysisContextReferenceKind.CONFIRMED_MEMORY: "M-",
            AnalysisContextReferenceKind.GOVERNED_CONTEXT: "C-",
            AnalysisContextReferenceKind.TOOL_RESULT: "T-",
        }[self.kind]
        if not self.context_ref.startswith(expected_prefix):
            raise ValueError(f"context_ref for {self.kind.value} must start with {expected_prefix}")
        return self


class AnalysisReasoningItem(BaseModel):
    """One explicit inference whose support class is auditable."""

    schema_version: Literal["soc.analysis_reasoning_item.v1"] = "soc.analysis_reasoning_item.v1"
    reasoning_id: str = Field(pattern=r"^R-[0-9]{2}$")
    statement: str = Field(min_length=1, max_length=3000)
    basis: list[AnalysisReasoningBasis] = Field(min_length=1, max_length=7)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    context_refs: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_support_namespaces(self) -> AnalysisReasoningItem:
        if len(set(self.basis)) != len(self.basis):
            raise ValueError("reasoning basis values must be unique")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("reasoning evidence_refs must be unique")
        if len(set(self.context_refs)) != len(self.context_refs):
            raise ValueError("reasoning context_refs must be unique")
        if any(not re.fullmatch(r"E-[A-F0-9]{12}", ref) for ref in self.evidence_refs):
            raise ValueError("reasoning evidence_refs must use E-* references")
        if any(not re.fullmatch(r"(?:S|A|M|C|T)-[A-F0-9]{12}", ref) for ref in self.context_refs):
            raise ValueError("reasoning context_refs must use S/A/M/C/T references")

        required_prefixes = {
            AnalysisReasoningBasis.SKILL: "S-",
            AnalysisReasoningBasis.ADAPTER_CONTRACT: "A-",
            AnalysisReasoningBasis.CONFIRMED_MEMORY: "M-",
            AnalysisReasoningBasis.GOVERNED_CONTEXT: "C-",
            AnalysisReasoningBasis.TOOL_RESULT: "T-",
        }
        for basis, prefix in required_prefixes.items():
            if basis in self.basis and not any(ref.startswith(prefix) for ref in self.context_refs):
                raise ValueError(f"reasoning basis {basis.value} requires a {prefix} context reference")
        return self


class AnalysisKnowledgeCandidate(BaseModel):
    """Model-proposed knowledge that remains inert until human review."""

    schema_version: Literal["soc.analysis_knowledge_candidate.v1"] = "soc.analysis_knowledge_candidate.v1"
    candidate_id: str = Field(pattern=r"^K-[0-9]{2}$")
    statement: str = Field(min_length=1, max_length=2000)
    destination_hint: AnalysisKnowledgeDestination
    scope_hint: Literal[
        "global",
        "tenant",
        "provider",
        "source",
        "detection",
        "scenario",
        "event",
    ]
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    reasoning_refs: list[str] = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_candidate_references(self) -> AnalysisKnowledgeCandidate:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("knowledge candidate evidence_refs must be unique")
        if len(set(self.reasoning_refs)) != len(self.reasoning_refs):
            raise ValueError("knowledge candidate reasoning_refs must be unique")
        if any(not re.fullmatch(r"E-[A-F0-9]{12}", ref) for ref in self.evidence_refs):
            raise ValueError("knowledge candidate evidence_refs must use E-* references")
        if any(not re.fullmatch(r"R-[0-9]{2}", ref) for ref in self.reasoning_refs):
            raise ValueError("knowledge candidate reasoning_refs must use R-* references")
        return self


class TriageScenarioAssessment(BaseModel):
    """One open-vocabulary scenario assessment grounded in analyzer evidence."""

    schema_version: Literal["soc.triage_scenario_assessment.v2"] = "soc.triage_scenario_assessment.v2"
    scenario_name: str = Field(min_length=1, max_length=256)
    scenario_key: str | None = Field(default=None, min_length=1, max_length=256)
    is_primary: bool = False
    origin: TriageScenarioOrigin
    confidence: float = Field(ge=0.0, le=1.0)
    activity_stage: TriageActivityStage
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    reasoning_refs: list[str] = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1, max_length=2000)
    competing_explanations: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"E-[A-F0-9]{12}", value) for value in values):
            raise ValueError("scenario evidence_refs must use E-* references")
        if len(set(values)) != len(values):
            raise ValueError("scenario evidence_refs must be unique")
        return values

    @field_validator("reasoning_refs")
    @classmethod
    def validate_reasoning_refs(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"R-[0-9]{2}", value) for value in values):
            raise ValueError("scenario reasoning_refs must use R-* references")
        if len(set(values)) != len(values):
            raise ValueError("scenario reasoning_refs must be unique")
        return values

    @field_validator("competing_explanations")
    @classmethod
    def validate_competing_explanations(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("competing explanations must be non-empty strings")
        if any(len(value) > 1000 for value in values):
            raise ValueError("competing explanation exceeds 1000 characters")
        return values


class AnalysisEvidenceGroundingItem(BaseModel):
    """Deterministic grounding result for one analyzer evidence item."""

    evidence_index: int = Field(ge=0)
    evidence_ref: str = Field(pattern=r"^E-[A-F0-9]{12}$")
    source: str = Field(min_length=1, max_length=256)
    status: AnalysisEvidenceGroundingStatus
    matched_context_paths: list[str] = Field(default_factory=list, max_length=10)
    foreign_description_context_paths: list[str] = Field(
        default_factory=list,
        max_length=10,
    )
    reason: str = Field(min_length=1, max_length=1000)


class AnalysisReasoningGroundingItem(BaseModel):
    """Reference-integrity result for one explicit model inference."""

    reasoning_index: int = Field(ge=0)
    reasoning_id: str = Field(pattern=r"^R-[0-9]{2}$")
    status: AnalysisEvidenceGroundingStatus
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    context_refs: list[str] = Field(default_factory=list, max_length=20)
    missing_refs: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1000)


class AnalysisEvidenceGroundingReport(BaseModel):
    """Audit report proving which analyzer claims came from bounded context."""

    schema_version: str = "soc.analysis_evidence_grounding.v3"
    total_count: int = Field(default=0, ge=0)
    grounded_count: int = Field(default=0, ge=0)
    ungrounded_count: int = Field(default=0, ge=0)
    description_leakage_count: int = Field(default=0, ge=0)
    items: list[AnalysisEvidenceGroundingItem] = Field(default_factory=list, max_length=40)
    reasoning_total_count: int = Field(default=0, ge=0)
    reasoning_grounded_count: int = Field(default=0, ge=0)
    reasoning_ungrounded_count: int = Field(default=0, ge=0)
    reasoning_items: list[AnalysisReasoningGroundingItem] = Field(
        default_factory=list,
        max_length=20,
    )
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_counts(self) -> AnalysisEvidenceGroundingReport:
        if self.total_count != len(self.items):
            raise ValueError("grounding total_count must equal item count")
        if self.grounded_count + self.ungrounded_count != self.total_count:
            raise ValueError("grounding counts must sum to total_count")
        actual_grounded = sum(item.status is AnalysisEvidenceGroundingStatus.GROUNDED for item in self.items)
        if actual_grounded != self.grounded_count:
            raise ValueError("grounding grounded_count does not match item statuses")
        actual_description_leakage = sum(item.status is AnalysisEvidenceGroundingStatus.DESCRIPTION_CONTEXT_LEAKAGE for item in self.items)
        if actual_description_leakage != self.description_leakage_count:
            raise ValueError("grounding description_leakage_count does not match item statuses")
        if self.reasoning_total_count != len(self.reasoning_items):
            raise ValueError("reasoning grounding total_count must equal item count")
        if self.reasoning_grounded_count + self.reasoning_ungrounded_count != self.reasoning_total_count:
            raise ValueError("reasoning grounding counts must sum to total_count")
        actual_reasoning_grounded = sum(item.status is AnalysisEvidenceGroundingStatus.GROUNDED for item in self.reasoning_items)
        if actual_reasoning_grounded != self.reasoning_grounded_count:
            raise ValueError("reasoning grounding grounded_count does not match item statuses")
        return self


class EvidenceInputPolicy(BaseModel):
    """Which input should later reasoning nodes treat as the primary evidence.

    This policy is source-adapter output. The runtime can inspect it before
    fact reconstruction, while vendors with clean schemas can omit it.
    """

    name: EvidenceInputPolicyName
    primary_input_path: str | None = None
    fallback_input_path: str | None = None
    selected_input_path: str | None = None
    supplementary_input_paths: list[str] = Field(default_factory=list)
    selected_layer: EvidenceLayer = EvidenceLayer.RAW_STRUCTURED
    fallback_reason: str | None = None
    ignore_processed_fields_for_reasoning: bool = False
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.MEDIUM


class NestedJsonRepairStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


class NestedJsonRepairObservation(BaseModel):
    """Auditable outcome of a repair attempt after strict nested JSON parsing failed."""

    field_path: str = Field(min_length=1)
    status: NestedJsonRepairStatus
    strategy: str = "json_repair"
    repair_log_count: int = Field(default=0, ge=0)
    reason: str = Field(min_length=1)


class ParsedRawMessageEvidence(BaseModel):
    """Deterministic parser output derived from one preserved raw message."""

    schema_version: str = "soc.parsed_raw_message.v2"
    source_path: str = Field(min_length=1)
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    message_hash: str = Field(min_length=1)
    original_length: int = Field(ge=0)
    fields: dict[str, Any] = Field(default_factory=dict)
    decoded_fields: dict[str, Any] = Field(default_factory=dict)
    repaired_fields: dict[str, Any] = Field(default_factory=dict)
    repair_observations: list[NestedJsonRepairObservation] = Field(default_factory=list)
    header: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class MessageSchemaStatus(StrEnum):
    RECOGNIZED = "recognized"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class MessageSchemaObservation(BaseModel):
    """Structural parser observation used for schema drift detection."""

    source_path: str = Field(min_length=1)
    parser_name: str | None = None
    parser_version: str | None = None
    schema_fingerprint: str | None = None
    status: MessageSchemaStatus
    field_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class SourceFieldSemantic(BaseModel):
    """Adapter-owned meaning for a source field that must not be guessed by core."""

    field_path: str = Field(min_length=1)
    semantic_type: str = Field(min_length=1)
    meaning: str = Field(min_length=1)
    participates_in_entities: bool = False
    participates_in_reasoning: bool = False


class EncodedSpanOmission(BaseModel):
    """Auditable encoded span removed only from model-bound evidence."""

    field_path: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    original_chars: int = Field(ge=1)
    sha256: str = Field(min_length=64, max_length=64)


class BoundedAnalysisEvidence(BaseModel):
    """Size-bounded evidence content allowed to enter an analysis node."""

    schema_version: str = "soc.bounded_analysis_evidence.v4"
    source_path: str = Field(min_length=1)
    layer: EvidenceLayer
    trust_level: EvidenceTrustLevel
    sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.REDACT
    content: str
    parser_name: str | None = None
    original_length: int = Field(default=0, ge=0)
    truncated: bool = False
    projected_field_paths: list[str] = Field(default_factory=list)
    sanitized_field_paths: list[str] = Field(default_factory=list)
    omitted_field_paths: list[str] = Field(default_factory=list)
    omission_reasons: dict[str, str] = Field(default_factory=dict)
    encoded_span_omissions: list[EncodedSpanOmission] = Field(default_factory=list)


class BoundedEvidenceHighlight(BaseModel):
    """Compact model-visible value retained from evidence outside full-message budgets."""

    schema_version: str = "soc.bounded_evidence_highlight.v2"
    semantic_type: str = Field(min_length=1)
    meaning: str = Field(min_length=1)
    value: str = Field(min_length=1, max_length=1000)
    evidence_paths: list[str] = Field(min_length=1, max_length=5)
    occurrence_count: int = Field(default=1, ge=1)
    evidence_paths_truncated: bool = False
    truncated: bool = False
    sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.REDACT


class CompactedObservationFact(BaseModel):
    """One bounded canonical fact retained in an observation summary."""

    field_path: str = Field(min_length=1, max_length=256)
    value: str | int | float | bool
    truncated: bool = False


class CompactedObservationValueCount(BaseModel):
    """Frequency of one value inside a compacted observation group."""

    value: str | int | float | bool
    occurrence_count: int = Field(ge=1)
    truncated: bool = False


class CompactedObservationVariation(BaseModel):
    """Bounded value distribution for a field that varied inside a group."""

    field_path: str = Field(min_length=1, max_length=256)
    distinct_value_count: int = Field(ge=2)
    values: list[CompactedObservationValueCount] = Field(
        default_factory=list,
        max_length=12,
    )
    values_truncated: bool = False


class CompactedObservationProfile(BaseModel):
    """A correlated combination of varying facts, not independent marginals."""

    profile_id: str = Field(pattern=r"^OP-[A-F0-9]{12}$")
    occurrence_count: int = Field(ge=1)
    representative_source_path: str = Field(min_length=1)
    varying_facts: list[CompactedObservationFact] = Field(
        default_factory=list,
        max_length=40,
    )


class CompactedObservationGroup(BaseModel):
    """Repeated source messages sharing one canonical observation shape."""

    group_id: str = Field(pattern=r"^OG-[A-F0-9]{12}$")
    parser_names: list[str] = Field(default_factory=list, max_length=10)
    observation_kinds: list[str] = Field(default_factory=list, max_length=10)
    occurrence_count: int = Field(ge=1)
    source_paths: list[str] = Field(default_factory=list, max_length=100)
    source_path_count: int = Field(ge=1)
    source_paths_truncated: bool = False
    representative_source_path: str = Field(min_length=1)
    first_seen: str | None = None
    last_seen: str | None = None
    stable_facts: list[CompactedObservationFact] = Field(
        default_factory=list,
        max_length=80,
    )
    varying_facts: list[CompactedObservationVariation] = Field(
        default_factory=list,
        max_length=40,
    )
    profiles: list[CompactedObservationProfile] = Field(
        default_factory=list,
        max_length=20,
    )
    profile_count: int = Field(ge=1)
    profiles_truncated: bool = False
    non_dominant_profile_count: int = Field(default=0, ge=0)


class EvidenceCompactionReport(BaseModel):
    """Audit report for loss-bounded grouping before model analysis."""

    schema_version: Literal["soc.evidence_compaction_report.v1"] = "soc.evidence_compaction_report.v1"
    strategy_version: Literal["soc.observation_compaction.v1"] = "soc.observation_compaction.v1"
    raw_payload_retained: bool = True
    source_message_count: int = Field(default=0, ge=0)
    typed_observation_count: int = Field(default=0, ge=0)
    behavior_group_count: int = Field(default=0, ge=0)
    profile_count: int = Field(default=0, ge=0)
    repeated_shape_message_count: int = Field(default=0, ge=0)
    collapsed_repetition_count: int = Field(default=0, ge=0)
    non_dominant_profile_count: int = Field(default=0, ge=0)
    selected_evidence_paths: list[str] = Field(default_factory=list, max_length=5)
    represented_field_paths: list[str] = Field(
        default_factory=list,
        max_length=5000,
    )
    represented_field_count: int = Field(default=0, ge=0)
    represented_source_count: int = Field(default=0, ge=0)
    unrepresented_source_count: int = Field(default=0, ge=0)
    high_value_omission_count: int = Field(default=0, ge=0)
    groups: list[CompactedObservationGroup] = Field(
        default_factory=list,
        max_length=100,
    )
    warnings: list[str] = Field(default_factory=list)


class FieldTrust(BaseModel):
    """Source trust and reasoning eligibility for one fact input path."""

    field_path: str
    layer: EvidenceLayer
    source_trust: EvidenceTrustLevel = EvidenceTrustLevel.UNKNOWN
    reasoning_status: FieldReasoningStatus
    participates: bool
    reason: str | None = None

    @model_validator(mode="after")
    def validate_reasoning_status(self) -> FieldTrust:
        participating_statuses = {
            FieldReasoningStatus.SELECTED_EVIDENCE,
            FieldReasoningStatus.SUPPLEMENTARY_EVIDENCE,
            FieldReasoningStatus.INCLUDED_CANONICAL_PROJECTION,
        }
        expected = self.reasoning_status in participating_statuses
        if self.participates is not expected:
            raise ValueError("participates must agree with reasoning_status eligibility")
        return self


class RoleClaimType(StrEnum):
    OBSERVATION = "observation"
    VENDOR_ASSERTION = "vendor_assertion"
    DERIVED_HYPOTHESIS = "derived_hypothesis"
    EXTERNAL_EVIDENCE = "external_evidence"
    HUMAN_CONFIRMATION = "human_confirmation"


class RoleResolutionStatus(StrEnum):
    OBSERVED = "observed"
    TENTATIVE = "tentative"
    CONFLICTED = "conflicted"
    CONFIRMED = "confirmed"
    UNRESOLVED = "unresolved"


class NetworkDirectionAssessmentStatus(StrEnum):
    """How far the analyzer could reconstruct network direction."""

    NOT_ASSESSED = "not_assessed"
    OBSERVED = "observed"
    INFERRED = "inferred"
    CONFLICTED = "conflicted"
    INDETERMINATE = "indeterminate"


class NetworkBoundaryDirection(StrEnum):
    """Organization-boundary direction, separate from wire and attacker roles."""

    EXTERNAL_TO_INTERNAL = "external_to_internal"
    INTERNAL_TO_EXTERNAL = "internal_to_external"
    INTERNAL_TO_INTERNAL = "internal_to_internal"
    EXTERNAL_TO_EXTERNAL = "external_to_external"
    PROXY_MEDIATED = "proxy_mediated"
    INDETERMINATE = "indeterminate"
    NOT_APPLICABLE = "not_applicable"


class AdjudicatedRoleType(StrEnum):
    """Open-system security roles that may differ from observed tuple roles."""

    INITIATOR = "initiator"
    RESPONDER = "responder"
    ATTACKER = "attacker"
    VICTIM = "victim"
    IMPACTED_ASSET = "impacted_asset"
    PROXY = "proxy"
    RELAY = "relay"
    SCANNER = "scanner"
    C2 = "c2"


class AdjudicatedRoleStatus(StrEnum):
    """Analyzer role state. Human confirmation is deliberately a separate mutation."""

    TENTATIVE = "tentative"
    RESOLVED_FROM_EVIDENCE = "resolved_from_evidence"
    CONFLICTED = "conflicted"
    UNRESOLVED = "unresolved"


class RoleAdjudicationStatus(StrEnum):
    NOT_ASSESSED = "not_assessed"
    TENTATIVE = "tentative"
    RESOLVED_FROM_EVIDENCE = "resolved_from_evidence"
    CONFLICTED = "conflicted"


class RoleClaim(BaseModel):
    """One observable or asserted role claim with separate evidence and semantic confidence."""

    claim_id: str = Field(min_length=1)
    role: Literal["source", "destination", "attacker", "victim", "impacted_asset"]
    value: str = Field(min_length=1)
    claim_type: RoleClaimType
    evidence_path: str = Field(min_length=1)
    observation_scope: str | None = None
    source_layer: EvidenceLayer
    evidence_trust: EvidenceTrustLevel = EvidenceTrustLevel.UNKNOWN
    semantic_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class ScenarioHypothesis(BaseModel):
    """A bounded scenario hypothesis used to interpret network and security roles."""

    scenario_type: str = Field(min_length=1)
    status: Literal["tentative", "confirmed"] = "tentative"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_paths: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class ScenarioSignal(BaseModel):
    """Source-adapter evidence that may support a vendor-neutral scenario hypothesis."""

    text: str = Field(min_length=1)
    evidence_path: str = Field(min_length=1)
    source_layer: EvidenceLayer
    evidence_trust: EvidenceTrustLevel = EvidenceTrustLevel.UNKNOWN


class RoleResolution(BaseModel):
    """Auditable resolution for one role; conflicted roles may retain a provisional value."""

    role: Literal["source", "destination", "attacker", "victim", "impacted_asset"]
    status: RoleResolutionStatus
    selected_value: str | None = None
    semantic_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_claim_ids: list[str] = Field(default_factory=list)
    contradicting_claim_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    evidence_gaps: list[str] = Field(default_factory=list)
    manual_checks: list[str] = Field(default_factory=list)
    automation_allowed: bool = False


class NetworkDirectionAssessment(BaseModel):
    """Analyzer assessment of wire, boundary, and semantic direction."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.network_direction_assessment.v1"] = "soc.network_direction_assessment.v1"
    status: NetworkDirectionAssessmentStatus = NetworkDirectionAssessmentStatus.NOT_ASSESSED
    observed_flow: Literal["source_to_destination", "multiple_flows", "not_available"] = "not_available"
    boundary_direction: NetworkBoundaryDirection = NetworkBoundaryDirection.NOT_APPLICABLE
    semantic_direction: str | None = Field(default=None, min_length=1, max_length=256)
    connection_initiator: str | None = Field(default=None, min_length=1, max_length=1000)
    intermediaries: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    reasoning_refs: list[str] = Field(default_factory=list, max_length=20)
    context_refs: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="Not assessed.", min_length=1, max_length=3000)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_assessment_references(self) -> NetworkDirectionAssessment:
        _validate_analysis_reference_lists(
            evidence_refs=self.evidence_refs,
            reasoning_refs=self.reasoning_refs,
            context_refs=self.context_refs,
            owner="network direction assessment",
        )
        if self.status is not NetworkDirectionAssessmentStatus.NOT_ASSESSED and (not self.evidence_refs or not self.reasoning_refs):
            raise ValueError("assessed network direction requires evidence_refs and reasoning_refs")
        return self


class AdjudicatedRole(BaseModel):
    """One model-adjudicated semantic role with exact support references."""

    model_config = ConfigDict(extra="forbid")

    role: AdjudicatedRoleType
    entity_type: str = Field(min_length=1, max_length=100)
    value: str | None = Field(default=None, min_length=1, max_length=1000)
    status: AdjudicatedRoleStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    reasoning_refs: list[str] = Field(min_length=1, max_length=20)
    context_refs: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_role_references(self) -> AdjudicatedRole:
        _validate_analysis_reference_lists(
            evidence_refs=self.evidence_refs,
            reasoning_refs=self.reasoning_refs,
            context_refs=self.context_refs,
            owner="adjudicated role",
        )
        if self.status is AdjudicatedRoleStatus.UNRESOLVED:
            if self.value is not None:
                raise ValueError("an unresolved role must not claim a concrete entity value")
        elif self.value is None:
            raise ValueError("a tentative, resolved, or conflicted role requires a concrete entity value")
        return self


class ResponseTargetProposal(BaseModel):
    """Action-specific target suggestion; policy and authorization remain external."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(pattern=r"^RT-[0-9]{2}$")
    action_kind: str = Field(min_length=1, max_length=200)
    target_type: str = Field(min_length=1, max_length=100)
    target_value: str = Field(min_length=1, max_length=1000)
    target_role: AdjudicatedRoleType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    reasoning_refs: list[str] = Field(min_length=1, max_length=20)
    context_refs: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=1, max_length=3000)
    policy_review_required: Literal[True] = True
    automation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_target_references(self) -> ResponseTargetProposal:
        _validate_analysis_reference_lists(
            evidence_refs=self.evidence_refs,
            reasoning_refs=self.reasoning_refs,
            context_refs=self.context_refs,
            owner="response target proposal",
        )
        return self


class RoleAdjudicationResult(BaseModel):
    """Semantic role and response-target output from the bounded analyzer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.role_adjudication_result.v1"] = "soc.role_adjudication_result.v1"
    status: RoleAdjudicationStatus = RoleAdjudicationStatus.NOT_ASSESSED
    roles: list[AdjudicatedRole] = Field(default_factory=list, max_length=30)
    response_target_proposals: list[ResponseTargetProposal] = Field(default_factory=list, max_length=20)
    conflicts: list[str] = Field(default_factory=list, max_length=20)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="Not assessed.", min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_adjudication(self) -> RoleAdjudicationResult:
        if self.status is RoleAdjudicationStatus.NOT_ASSESSED:
            if self.roles or self.response_target_proposals:
                raise ValueError("not_assessed role adjudication cannot contain roles or targets")
            return self
        if not self.roles:
            raise ValueError("assessed role adjudication requires at least one role")
        role_keys = [
            (
                item.role,
                item.entity_type.casefold(),
                item.value.casefold() if item.value is not None else None,
            )
            for item in self.roles
        ]
        if len(role_keys) != len(set(role_keys)):
            raise ValueError("adjudicated roles must be unique")
        proposal_ids = [item.proposal_id for item in self.response_target_proposals]
        if len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("response target proposal IDs must be unique")
        entity_keys = {(item.entity_type.casefold(), item.value.casefold()) for item in self.roles if item.value is not None}
        for proposal in self.response_target_proposals:
            target_key = (proposal.target_type.casefold(), proposal.target_value.casefold())
            if target_key not in entity_keys:
                raise ValueError("each response target proposal must reference an adjudicated entity")
        return self


def _validate_analysis_reference_lists(
    *,
    evidence_refs: list[str],
    reasoning_refs: list[str],
    context_refs: list[str],
    owner: str,
) -> None:
    groups = (
        (evidence_refs, r"E-[A-F0-9]{12}", "E-* evidence"),
        (reasoning_refs, r"R-[0-9]{2}", "R-* reasoning"),
        (context_refs, r"(?:S|A|M|C|T)-[A-F0-9]{12}", "S/A/M/C/T context"),
    )
    for values, pattern, label in groups:
        if len(values) != len(set(values)):
            raise ValueError(f"{owner} {label} references must be unique")
        if any(not re.fullmatch(pattern, value) for value in values):
            raise ValueError(f"{owner} must use valid {label} references")


class CanonicalFieldProvenance(BaseModel):
    """Explains which source field supplied one canonical value and what alternatives existed."""

    canonical_path: str = Field(min_length=1)
    selected_value: str = Field(min_length=1)
    selected_from: str = Field(min_length=1)
    source_layer: EvidenceLayer
    trust_level: EvidenceTrustLevel
    selection_reason: str = Field(min_length=1)
    alternative_values: list[str] = Field(default_factory=list)


class ConflictReport(BaseModel):
    """Structured conflict found before LLM analysis or human review."""

    conflict_type: str = Field(min_length=1)
    severity: Literal["info", "warning", "critical"] = "warning"
    description: str = Field(min_length=1)
    involved_fields: list[str] = Field(default_factory=list)
    candidate_values: dict[str, list[str]] = Field(default_factory=dict)
    resolution_status: RoleResolutionStatus = RoleResolutionStatus.UNRESOLVED
    provisional_value: str | None = None
    resolution_reason: str | None = None
    blocks_automation: bool = True


class FactReconstructionResult(BaseModel):
    """Pre-analysis fact layer built from evidence policy and normalized fields."""

    schema_version: str = "soc.fact_reconstruction.v2"
    evidence_policy: EvidenceInputPolicy | None = None
    selected_input_path: str | None = None
    selected_input_available: bool = False
    field_trusts: list[FieldTrust] = Field(default_factory=list)
    canonical_field_provenance: list[CanonicalFieldProvenance] = Field(default_factory=list)
    role_claims: list[RoleClaim] = Field(default_factory=list)
    scenario_hypotheses: list[ScenarioHypothesis] = Field(default_factory=list)
    role_resolutions: list[RoleResolution] = Field(default_factory=list)
    conflict_reports: list[ConflictReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvidenceCoverageOmission(BaseModel):
    """One parsed field that was not passed through unchanged."""

    field_path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class EvidenceCoverageGap(BaseModel):
    """High-value evidence absent from or not represented in analysis context."""

    field_path: str = Field(min_length=1)
    expected_target: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    rule_id: str | None = None
    importance: Literal["medium", "high", "critical"] = "high"


class EvidenceCoverageReport(BaseModel):
    """Trace which parsed evidence is used, projected, sanitized, or omitted."""

    schema_version: str = "soc.evidence_coverage.v4"
    message_schemas: list[MessageSchemaObservation] = Field(default_factory=list)
    structured_field_paths: list[str] = Field(default_factory=list)
    parsed_field_paths: list[str] = Field(default_factory=list)
    decoded_field_paths: list[str] = Field(default_factory=list)
    repaired_field_paths: list[str] = Field(default_factory=list)
    canonical_source_paths: list[str] = Field(default_factory=list)
    fact_source_paths: list[str] = Field(default_factory=list)
    scenario_source_paths: list[str] = Field(default_factory=list)
    llm_projected_paths: list[str] = Field(default_factory=list)
    llm_sanitized_paths: list[str] = Field(default_factory=list)
    llm_compacted_encoded_paths: list[str] = Field(default_factory=list)
    llm_truncated_evidence_paths: list[str] = Field(default_factory=list)
    omissions: list[EvidenceCoverageOmission] = Field(default_factory=list)
    high_value_gaps: list[EvidenceCoverageGap] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AlertInput(BaseModel):
    """Canonical alert input accepted by the SOC runtime.

    Source-specific payloads must be converted into this shape by a normalizer
    before they enter pipeline, DB, memory, API response, or Kafka contracts.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.alert.v1"
    tenant_id: str | None = None
    alert_id: str = Field(default_factory=lambda: f"ALT-{uuid4().hex[:12].upper()}")
    source: AlertSourceRef = Field(default_factory=AlertSourceRef)
    detection: DetectionRuleRef = Field(default_factory=DetectionRuleRef)
    event: AlertEventRef = Field(default_factory=AlertEventRef)
    classification: AlertClassification = Field(default_factory=AlertClassification)
    entities: AlertEntitySet = Field(default_factory=AlertEntitySet)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class EntityMention(BaseModel):
    """Normalized entity mention produced by deterministic or LLM extraction."""

    kind: EntityKind
    value: str = Field(min_length=1)
    key: str = Field(min_length=1)
    role: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: EntityExtractionSource = EntityExtractionSource.DETERMINISTIC
    evidence_path: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExtractedEntities(BaseModel):
    mentions: list[EntityMention] = Field(default_factory=list)
    ips: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    rule_codes: list[str] = Field(default_factory=list)
    rule_names: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LLMAnalysisRequest(BaseModel):
    """Bounded input contract for deterministic or configured LLM nodes."""

    schema_version: str = "soc.llm_analysis_request.v5"
    alert_id: str
    tenant_id: str | None = None
    environment: str | None = Field(default=None, max_length=128)
    source: AlertSourceRef = Field(default_factory=AlertSourceRef)
    detection: DetectionRuleRef = Field(default_factory=DetectionRuleRef)
    classification: AlertClassification = Field(default_factory=AlertClassification)
    canonical_entities: AlertEntitySet = Field(default_factory=AlertEntitySet)
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    fact_reconstruction: FactReconstructionResult = Field(default_factory=FactReconstructionResult)
    primary_evidence_path: str | None = None
    primary_evidence: BoundedAnalysisEvidence | None = None
    supplementary_evidence: list[BoundedAnalysisEvidence] = Field(default_factory=list)
    evidence_highlights: list[BoundedEvidenceHighlight] = Field(default_factory=list)
    evidence_compaction: EvidenceCompactionReport = Field(
        default_factory=EvidenceCompactionReport,
    )
    evidence_coverage: EvidenceCoverageReport = Field(default_factory=EvidenceCoverageReport)
    source_field_semantics: list[SourceFieldSemantic] = Field(default_factory=list)
    conflict_count: int = Field(default=0, ge=0)
    conflict_types: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skill_context: SocSkillContext = Field(default_factory=SocSkillContext)
    evidence_catalog: list[AnalysisEvidenceCatalogItem] = Field(
        default_factory=list,
        max_length=150,
    )
    context_catalog: list[AnalysisContextCatalogItem] = Field(
        default_factory=list,
        max_length=100,
    )


class NormalizationReport(BaseModel):
    """Cheap quality report for deterministic alert normalization."""

    schema_version: str = "soc.normalization_report.v1"
    adapter: str
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    normalized_fields: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    unmapped_field_count: int = Field(default=0, ge=0)
    message_schemas: list[MessageSchemaObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractionReport(BaseModel):
    """Cheap quality report for deterministic entity extraction."""

    schema_version: str = "soc.extraction_report.v1"
    mention_count: int = Field(default=0, ge=0)
    entity_counts: dict[str, int] = Field(default_factory=dict)
    missing_entity_kinds: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NormalizationInspectionResult(BaseModel):
    """Output for inspect-only normalization and entity extraction."""

    schema_version: str = "soc.normalization_inspection.v1"
    alert: AlertInput
    entities: ExtractedEntities
    normalization_report: NormalizationReport
    extraction_report: ExtractionReport


class NormalizationDriftSample(BaseModel):
    """One sample's normalize/extract quality summary for drift triage."""

    path: str
    status: Literal["success", "failed"]
    run_id: str | None = None
    alert_id: str | None = None
    adapter: str | None = None
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    entity_counts: dict[str, int] = Field(default_factory=dict)
    missing_entity_kinds: list[str] = Field(default_factory=list)
    schema_fingerprints: list[str] = Field(default_factory=list)
    novel_schema_fingerprints: list[str] = Field(default_factory=list)
    schema_statuses: list[MessageSchemaStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class NormalizationDriftReport(BaseModel):
    """Batch report for spotting normalization and extraction drift."""

    schema_version: str = "soc.normalization_drift_report.v1"
    sample_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    adapter_counts: dict[str, int] = Field(default_factory=dict)
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    missing_field_counts: dict[str, int] = Field(default_factory=dict)
    unmapped_field_counts: dict[str, int] = Field(default_factory=dict)
    entity_kind_counts: dict[str, int] = Field(default_factory=dict)
    missing_entity_kind_counts: dict[str, int] = Field(default_factory=dict)
    schema_fingerprint_counts: dict[str, int] = Field(default_factory=dict)
    schema_baseline_applied: bool = False
    known_schema_fingerprint_count: int = Field(default=0, ge=0)
    novel_schema_fingerprint_counts: dict[str, int] = Field(default_factory=dict)
    schema_status_counts: dict[str, int] = Field(default_factory=dict)
    warning_counts: dict[str, int] = Field(default_factory=dict)
    suspicious_samples: list[NormalizationDriftSample] = Field(default_factory=list)
    samples: list[NormalizationDriftSample] = Field(default_factory=list)


class NormalizationBaselineStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class NormalizationSchemaBaseline(BaseModel):
    """Human-approved schema fingerprints for one source/parser scope."""

    schema_version: str = "soc.normalization_schema_baseline.v1"
    baseline_id: str = Field(default_factory=lambda: f"NSB-{uuid4().hex[:12].upper()}")
    version: int = Field(default=1, ge=1)
    status: NormalizationBaselineStatus = NormalizationBaselineStatus.ACTIVE
    tenant_id: str | None = None
    source_system: str | None = None
    adapter: str = Field(min_length=1)
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    accepted_fingerprints: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    approved_by: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    superseded_at: datetime | None = None


class NormalizationBaselineAcceptCommand(BaseModel):
    tenant_id: str | None = None
    source_system: str | None = None
    adapter: str = Field(min_length=1)
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    accepted_fingerprints: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)


class NormalizationMaintenanceIssueType(StrEnum):
    BASELINE_MISSING = "baseline_missing"
    NOVEL_SCHEMA = "novel_schema"
    DEGRADED_SCHEMA = "degraded_schema"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    HIGH_VALUE_GAP = "high_value_gap"
    EVIDENCE_TRUNCATED = "evidence_truncated"


class NormalizationMaintenanceIssueStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class NormalizationMaintenanceSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NormalizationMaintenanceIssue(BaseModel):
    """Deduplicated parser/mapping maintenance work, separate from alert review."""

    schema_version: str = "soc.normalization_maintenance_issue.v1"
    issue_id: str = Field(default_factory=lambda: f"NMI-{uuid4().hex[:12].upper()}")
    dedupe_key: str = Field(min_length=1)
    issue_type: NormalizationMaintenanceIssueType
    severity: NormalizationMaintenanceSeverity
    status: NormalizationMaintenanceIssueStatus = NormalizationMaintenanceIssueStatus.OPEN
    tenant_id: str | None = None
    source_system: str | None = None
    adapter: str = Field(min_length=1)
    parser_name: str | None = None
    parser_version: str | None = None
    schema_fingerprint: str | None = None
    source_path: str | None = None
    expected_target: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    occurrence_count: int = Field(default=1, ge=1)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    acknowledged_by: ActorContext | None = None
    acknowledged_at: datetime | None = None
    resolved_by: ActorContext | None = None
    resolved_at: datetime | None = None
    resolution_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class NormalizationMaintenanceIssueUpdateCommand(BaseModel):
    issue_id: str = Field(min_length=1)
    status: Literal["acknowledged", "resolved", "ignored"]
    reason: str = Field(min_length=1)


class NormalizationMonitoringResult(BaseModel):
    schema_version: str = "soc.normalization_monitoring_result.v1"
    run_id: str
    alert_id: str
    baseline_ids: list[str] = Field(default_factory=list)
    created_issue_ids: list[str] = Field(default_factory=list)
    updated_issue_ids: list[str] = Field(default_factory=list)
    issues: list[NormalizationMaintenanceIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvidenceFieldImportance(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceFieldImportanceRule(BaseModel):
    """Configurable mapping expectation for parsed/decoded/repaired evidence."""

    rule_id: str = Field(min_length=1)
    source_patterns: list[str] = Field(min_length=1)
    expected_target: str = Field(min_length=1)
    importance: EvidenceFieldImportance = EvidenceFieldImportance.HIGH
    source_types: list[AlertSourceType] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class NormalizationSuggestionStatus(StrEnum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"


class NormalizationMappingSuggestion(BaseModel):
    suggestion_id: str = Field(default_factory=lambda: f"NMS-{uuid4().hex[:12].upper()}")
    status: NormalizationSuggestionStatus = NormalizationSuggestionStatus.CANDIDATE
    target_path: str = Field(min_length=1)
    source_paths: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class NormalizationSuggestionReport(BaseModel):
    schema_version: str = "soc.normalization_suggestion_report.v1"
    generated_by: Literal["deterministic", "llm_replay", "llm"]
    model_name: str | None = None
    prompt_version: str = "soc-normalization-suggest-v1"
    source_report_hash: str
    suggestions: list[NormalizationMappingSuggestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int | None = Field(default=None, ge=0)
    usage: dict[str, Any] = Field(default_factory=dict)
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    auto_apply_allowed: bool = False


class NormalizationSuggestionPrompt(BaseModel):
    """Sanitized offline prompt bundle; it never invokes or mutates runtime."""

    schema_version: str = "soc.normalization_suggestion_prompt.v1"
    prompt_version: str = "soc-normalization-suggest-v1"
    source_report_hash: str
    system_prompt: str
    user_prompt: str
    observed_source_paths: list[str] = Field(default_factory=list)
    allowed_target_paths: list[str] = Field(default_factory=list)


class ConfidenceCalibrationSample(BaseModel):
    """One traceable human label used for offline confidence calibration."""

    schema_version: str = "soc.confidence_calibration_sample.v2"
    sample_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    alert_id: str = Field(min_length=1)
    input_hash: str = Field(min_length=1)
    source_path: str | None = None
    predicted_verdict: Verdict
    actual_verdict: Verdict | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    model_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=4000)
    recommended_action: str = Field(min_length=1, max_length=1000)
    evidence_grounded_count: int = Field(default=0, ge=0)
    evidence_ungrounded_count: int = Field(default=0, ge=0)
    review_reasons: list[DecisionReviewReason] = Field(default_factory=list)
    review_status: ConfidenceLabelReviewStatus = ConfidenceLabelReviewStatus.PENDING_REVIEW
    review_source: ConfidenceLabelReviewSource | None = None
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None

    @model_validator(mode="after")
    def validate_review_state(self) -> ConfidenceCalibrationSample:
        review_fields = (
            self.review_source,
            self.reviewer_id,
            self.reviewed_at,
            self.review_reason,
        )
        if self.review_status is ConfidenceLabelReviewStatus.PENDING_REVIEW:
            if self.actual_verdict is not None or any(value is not None for value in review_fields):
                raise ValueError("pending confidence label cannot carry analyst review fields")
            return self

        if self.review_source is None or not self.reviewer_id or self.reviewed_at is None or not self.review_reason:
            raise ValueError("reviewed confidence label requires review_source, reviewer_id, reviewed_at, and review_reason")
        if self.review_status is ConfidenceLabelReviewStatus.ACCEPTED:
            if self.actual_verdict is None:
                raise ValueError("accepted confidence label requires actual_verdict")
            if self.actual_verdict in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW}:
                raise ValueError("unresolved actual verdict must be excluded rather than accepted")
        return self


class ConfidenceCalibrationLabelSet(BaseModel):
    """Versioned review bundle generated from bounded AnalysisRun outputs."""

    schema_version: str = "soc.confidence_calibration_label_set.v1"
    label_set_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    samples: list[ConfidenceCalibrationSample] = Field(min_length=1, max_length=10000)


class ConfidenceLabelCorpusManifest(BaseModel):
    """Immutable provenance seal around one reviewed confidence label set."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.confidence_label_corpus_manifest.v1"
    manifest_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    corpus_version: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=100)
    data_class: SocEvaluationDataClass
    created_by: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4000)
    source_refs: list[str] = Field(min_length=1, max_length=100)
    label_set_id: str = Field(min_length=1)
    label_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_count: int = Field(ge=1, le=10000)
    sample_ids: list[str] = Field(min_length=1, max_length=10000)
    sample_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    reviewer_ids: list[str] = Field(default_factory=list, max_length=1000)
    review_source_counts: dict[str, int] = Field(default_factory=dict)
    calibration_input_eligible: bool = False
    mocked: bool
    real_quality_claim_allowed: bool = False
    supersedes_manifest_id: str | None = None
    supersedes_manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator(
        "corpus_version",
        "tenant_id",
        "environment",
        "created_by",
        "rationale",
    )
    @classmethod
    def strip_manifest_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("corpus manifest text fields must not be blank")
        return normalized

    @field_validator("source_refs", "sample_ids", "reviewer_ids")
    @classmethod
    def require_unique_manifest_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("corpus manifest list values must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("corpus manifest list values must be unique")
        return normalized

    @field_validator("review_source_counts")
    @classmethod
    def validate_review_source_counts(cls, values: dict[str, int]) -> dict[str, int]:
        allowed = {item.value for item in ConfidenceLabelReviewSource}
        if any(key not in allowed for key in values):
            raise ValueError("corpus manifest contains an unknown review source")
        if any(count < 0 for count in values.values()):
            raise ValueError("corpus manifest review-source counts cannot be negative")
        return dict(sorted(values.items()))

    @model_validator(mode="after")
    def validate_integrity_boundary(self) -> ConfidenceLabelCorpusManifest:
        if self.sample_count != len(self.sample_ids):
            raise ValueError("sample_count must match sample_ids")
        if self.accepted_count + self.pending_count + self.excluded_count != self.sample_count:
            raise ValueError("review status counts must match sample_count")
        if sum(self.review_source_counts.values()) != self.accepted_count + self.excluded_count:
            raise ValueError("review-source counts must match reviewed sample count")
        if self.data_class is SocEvaluationDataClass.DESENSITIZED_REAL and self.review_source_counts.get(ConfidenceLabelReviewSource.SIMULATION_FIXTURE.value, 0):
            raise ValueError("a real corpus cannot contain simulation-fixture labels")
        if self.mocked is not (self.data_class is SocEvaluationDataClass.SIMULATION):
            raise ValueError("mocked must match the evaluation data class")
        if self.real_quality_claim_allowed:
            raise ValueError("a corpus manifest cannot authorize real quality claims")
        supersession = (
            self.supersedes_manifest_id,
            self.supersedes_manifest_sha256,
        )
        if any(value is not None for value in supersession) and not all(value is not None for value in supersession):
            raise ValueError("supersession requires both manifest id and manifest hash")
        if self.supersedes_manifest_id == self.manifest_id:
            raise ValueError("a corpus manifest cannot supersede itself")
        return self


class ConfidenceLabelCorpusVerificationReport(BaseModel):
    """Integrity result for one manifest and its exact label-set payload."""

    schema_version: str = "soc.confidence_label_corpus_verification.v1"
    manifest_id: str
    label_set_id: str
    data_class: SocEvaluationDataClass
    mocked: bool
    integrity_passed: bool
    label_set_hash_matches: bool
    sample_identity_matches: bool
    review_summary_matches: bool
    review_source_summary_matches: bool
    calibration_input_eligible: bool
    real_quality_claim_allowed: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConfidenceLabelSetValidationReport(BaseModel):
    """Readiness report produced before calibration metrics are computed."""

    schema_version: str = "soc.confidence_label_set_validation_report.v1"
    label_set_id: str
    sample_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    calibratable: bool = False
    model_names: list[str] = Field(default_factory=list)
    prompt_versions: list[str] = Field(default_factory=list)
    pipeline_versions: list[str] = Field(default_factory=list)
    actual_verdict_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConfidenceCalibrationBin(BaseModel):
    lower_bound: float = Field(ge=0.0, le=1.0)
    upper_bound: float = Field(ge=0.0, le=1.0)
    sample_count: int = Field(ge=0)
    average_confidence: float = Field(ge=0.0, le=1.0)
    empirical_accuracy: float = Field(ge=0.0, le=1.0)


class ConfidenceThresholdProfile(BaseModel):
    profile_version: str = Field(min_length=1)
    label_set_id: str | None = None
    dataset_hash: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    pipeline_version: str | None = None
    review_below: float = Field(ge=0.0, le=1.0)
    auto_action_allowed: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ConfidenceCalibrationReport(BaseModel):
    schema_version: str = "soc.confidence_calibration_report.v2"
    label_set_id: str | None = None
    dataset_hash: str
    model_name: str
    prompt_version: str
    pipeline_version: str
    sample_count: int = Field(ge=0)
    actual_verdict_counts: dict[str, int] = Field(default_factory=dict)
    accuracy: float = Field(ge=0.0, le=1.0)
    brier_score: float = Field(ge=0.0, le=1.0)
    expected_calibration_error: float = Field(ge=0.0, le=1.0)
    bins: list[ConfidenceCalibrationBin] = Field(default_factory=list)
    threshold_profile: ConfidenceThresholdProfile
    warnings: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    schema_version: Literal["soc.analysis_result.v4"] = "soc.analysis_result.v4"
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=4000)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=40)
    reasoning: list[AnalysisReasoningItem] = Field(min_length=1, max_length=20)
    scenario_assessments: list[TriageScenarioAssessment] = Field(
        default_factory=list,
        max_length=10,
    )
    network_direction: NetworkDirectionAssessment = Field(
        default_factory=NetworkDirectionAssessment,
    )
    role_adjudication: RoleAdjudicationResult = Field(
        default_factory=RoleAdjudicationResult,
    )
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)
    manual_checks: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=8000)
    recommended_action: str = Field(min_length=1, max_length=1000)
    knowledge_candidates: list[AnalysisKnowledgeCandidate] = Field(
        default_factory=list,
        max_length=20,
    )

    @field_validator("evidence")
    @classmethod
    def require_evidence(cls, evidence: list[EvidenceItem]) -> list[EvidenceItem]:
        if not evidence:
            raise ValueError("analysis result must include at least one evidence item")
        refs = [item.evidence_ref for item in evidence]
        if any(ref is None for ref in refs):
            raise ValueError("analysis evidence must include an E-* evidence_ref")
        if len(set(refs)) != len(refs):
            raise ValueError("analysis evidence_refs must be unique")
        return evidence

    @field_validator("reasoning")
    @classmethod
    def require_unique_reasoning_ids(
        cls,
        values: list[AnalysisReasoningItem],
    ) -> list[AnalysisReasoningItem]:
        ids = [item.reasoning_id for item in values]
        if len(set(ids)) != len(ids):
            raise ValueError("analysis reasoning IDs must be unique")
        return values

    @field_validator("knowledge_candidates")
    @classmethod
    def require_unique_knowledge_candidate_ids(
        cls,
        values: list[AnalysisKnowledgeCandidate],
    ) -> list[AnalysisKnowledgeCandidate]:
        ids = [item.candidate_id for item in values]
        if len(set(ids)) != len(ids):
            raise ValueError("analysis knowledge candidate IDs must be unique")
        return values

    @field_validator("evidence_gaps", "manual_checks")
    @classmethod
    def bound_triage_guidance(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("triage guidance entries must be non-empty strings")
        if any(len(value) > 1000 for value in values):
            raise ValueError("triage guidance entry exceeds 1000 characters")
        return values

    @model_validator(mode="after")
    def validate_scenario_assessments(self) -> AnalysisResult:
        if self.scenario_assessments:
            primary_count = sum(item.is_primary for item in self.scenario_assessments)
            if primary_count != 1:
                raise ValueError("analysis result with scenario assessments requires exactly one primary scenario")

            normalized_names = [item.scenario_name.strip().casefold() for item in self.scenario_assessments]
            if len(set(normalized_names)) != len(normalized_names):
                raise ValueError("analysis scenario names must be unique")

        evidence_refs = {item.evidence_ref for item in self.evidence if item.evidence_ref is not None}
        reasoning_ids = {item.reasoning_id for item in self.reasoning}
        invalid_reasoning_evidence_refs = sorted({ref for item in self.reasoning for ref in item.evidence_refs if ref not in evidence_refs})
        if invalid_reasoning_evidence_refs:
            raise ValueError(f"reasoning evidence_refs must reference analysis evidence; invalid refs: {invalid_reasoning_evidence_refs}")
        invalid_evidence_refs = sorted({ref for item in self.scenario_assessments for ref in item.evidence_refs if ref not in evidence_refs})
        if invalid_evidence_refs:
            raise ValueError(f"scenario evidence_refs must reference analysis evidence; invalid refs: {invalid_evidence_refs}")
        invalid_reasoning_refs = sorted({ref for item in self.scenario_assessments for ref in item.reasoning_refs if ref not in reasoning_ids})
        if invalid_reasoning_refs:
            raise ValueError(f"scenario reasoning_refs must reference analysis reasoning; invalid refs: {invalid_reasoning_refs}")

        for candidate in self.knowledge_candidates:
            missing_evidence = sorted(set(candidate.evidence_refs) - evidence_refs)
            missing_reasoning = sorted(set(candidate.reasoning_refs) - reasoning_ids)
            if missing_evidence or missing_reasoning:
                raise ValueError(f"knowledge candidate references must resolve inside AnalysisResult; candidate={candidate.candidate_id}, missing_evidence={missing_evidence}, missing_reasoning={missing_reasoning}")

        directional_items: list[Any] = [self.network_direction]
        directional_items.extend(self.role_adjudication.roles)
        directional_items.extend(self.role_adjudication.response_target_proposals)
        for item in directional_items:
            missing_evidence = sorted(set(item.evidence_refs) - evidence_refs)
            missing_reasoning = sorted(set(item.reasoning_refs) - reasoning_ids)
            if missing_evidence or missing_reasoning:
                raise ValueError(f"direction/role evidence and reasoning references must resolve inside AnalysisResult; missing_evidence={missing_evidence}, missing_reasoning={missing_reasoning}")
        return self


class Decision(BaseModel):
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_source: DecisionConfidenceSource = DecisionConfidenceSource.UNKNOWN
    confidence_is_calibrated: bool = False
    calibrated_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    calibration_profile_version: str | None = None
    evidence_state: DecisionEvidenceState = DecisionEvidenceState.PARTIAL
    suggested_action: str
    needs_review: bool
    review_reasons: list[DecisionReviewReason] = Field(default_factory=list)
    reason: str
    policy_version: str = "soc.decision_policy.v5"
    confidence_explanation: str | None = None
    automation_allowed: Literal[False] = False


class CorrectionCommand(BaseModel):
    run_id: str
    corrected_verdict: Verdict
    reason: str = Field(min_length=1)
    corrected_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class CorrectionRecord(BaseModel):
    correction_id: str = Field(default_factory=lambda: f"COR-{uuid4().hex[:12].upper()}")
    run_id: str
    previous_verdict: Verdict | None = None
    corrected_verdict: Verdict
    reason: str
    corrected_confidence: float | None = None
    confidence_source: DecisionConfidenceSource = DecisionConfidenceSource.UNKNOWN
    confidence_was_explicit: bool = False
    confidence_policy_version: str | None = None
    confidence_explanation: str | None = None
    actor: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    candidate_knowledge_status: Literal["not_created", "observed_only", "pending_review"] = "not_created"
    memory_candidate_id: str | None = None
    memory_admission: MemoryAdmissionDecision | None = None


class HumanConfirmedRole(BaseModel):
    """Analyst-confirmed semantic role; separate from model adjudication."""

    model_config = ConfigDict(extra="forbid")

    role: AdjudicatedRoleType
    entity_type: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=1, max_length=3000)

    @field_validator("evidence_refs")
    @classmethod
    def validate_current_evidence_refs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("human role evidence_refs must be unique")
        if any(not re.fullmatch(r"E-[A-F0-9]{12}", value) for value in values):
            raise ValueError("human role evidence_refs must use E-* references")
        return values


class HumanConfirmedResponseTarget(BaseModel):
    """Analyst-selected action target; still not action authorization."""

    model_config = ConfigDict(extra="forbid")

    action_kind: str = Field(min_length=1, max_length=200)
    target_type: str = Field(min_length=1, max_length=100)
    target_value: str = Field(min_length=1, max_length=1000)
    target_role: AdjudicatedRoleType
    source_proposal_id: str | None = Field(default=None, pattern=r"^RT-[0-9]{2}$")
    rationale: str = Field(min_length=1, max_length=3000)
    automation_allowed: Literal[False] = False


class RoleAdjudicationConfirmationCommand(BaseModel):
    """Optimistic-lock command for one human role confirmation revision."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(default=0, ge=0)
    roles: list[HumanConfirmedRole] = Field(min_length=1, max_length=30)
    response_targets: list[HumanConfirmedResponseTarget] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=3000)

    @model_validator(mode="after")
    def validate_confirmation(self) -> RoleAdjudicationConfirmationCommand:
        role_keys = [(item.role, item.entity_type.casefold(), item.value.casefold()) for item in self.roles]
        if len(role_keys) != len(set(role_keys)):
            raise ValueError("human-confirmed roles must be unique")
        target_keys = [(item.action_kind.casefold(), item.target_type.casefold(), item.target_value.casefold()) for item in self.response_targets]
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("human-confirmed response targets must be unique")
        known_entities = {(item.entity_type.casefold(), item.value.casefold()) for item in self.roles}
        for target in self.response_targets:
            key = (target.target_type.casefold(), target.target_value.casefold())
            if key not in known_entities:
                raise ValueError("each confirmed response target must reference a confirmed entity in the same command")
        return self


class RoleAdjudicationRevisionRecord(BaseModel):
    """Append-only before/after lineage for human role confirmation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.role_adjudication_revision.v1"] = "soc.role_adjudication_revision.v1"
    revision_id: str = Field(default_factory=lambda: f"RAR-{uuid4().hex[:12].upper()}")
    run_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    previous_revision_id: str | None = None
    base_model_adjudication_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_effective_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    roles: list[HumanConfirmedRole] = Field(min_length=1, max_length=30)
    response_targets: list[HumanConfirmedResponseTarget] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=3000)
    actor: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    automation_allowed: Literal[False] = False


class DecisionAuditRecord(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"AUD-{uuid4().hex[:12].upper()}")
    action: AuditAction
    run_id: str
    alert_id: str
    actor: ActorContext
    occurred_at: datetime = Field(default_factory=utc_now)
    input_hash: str | None = None
    previous_verdict: Verdict | None = None
    final_verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    replay_of_run_id: str | None = None
    correction_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AlertSummary(BaseModel):
    """Queryable read model for alert queues, dedup, and review surfaces.

    ``AnalysisRun`` remains the full source of truth. This model intentionally
    keeps only indexed/list-friendly fields that UI, TUI, daemon, and future
    correlation steps need to scan cheaply.
    """

    schema_version: str = "soc.alert_summary.v1"
    run_id: str
    alert_id: str
    tenant_id: str | None = None
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    detection_key: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    severity: str | None = None
    category: str | None = None
    entity_keys: list[str] = Field(default_factory=list)
    status: AnalysisRunStatus
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_source: DecisionConfidenceSource | None = None
    confidence_is_calibrated: bool = False
    confidence_policy_version: str | None = None
    confidence_explanation: str | None = None
    needs_review: bool = False
    review_reasons: list[DecisionReviewReason] = Field(default_factory=list)
    summary: str | None = None
    recommended_action: str | None = None
    input_hash: str | None = None
    replay_of_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SimilarAlertQuery(BaseModel):
    """Candidate retrieval query derived from one alert summary."""

    run_id: str
    detection_key: str | None = None
    rule_code: str | None = None
    source_type: AlertSourceType | None = None
    category: str | None = None
    entity_keys: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)
    candidate_limit: int = Field(default=200, ge=1, le=1000)


class SimilarAlertMatch(BaseModel):
    """Scored historical alert summary match."""

    summary: AlertSummary
    score: float = Field(ge=0.0)
    matched_reasons: list[str] = Field(default_factory=list)


CORRELATION_SCORING_POLICY_VERSION = "soc.correlation.scoring.v1"


class CorrelationQuery(BaseModel):
    """Request to correlate one alert summary with recent historical alerts."""

    run_id: str
    limit: int = Field(default=10, ge=1, le=100)
    candidate_limit: int = Field(default=200, ge=1, le=1000)
    evidence_limit_per_match: int = Field(default=5, ge=0, le=50)


class CorrelationEvidenceRef(BaseModel):
    """Reusable investigation evidence attached to a correlated historical alert."""

    evidence_id: str
    route: str
    action: str
    status: Literal["success", "denied", "failed"]
    message: str
    result_payload: dict[str, Any] = Field(default_factory=dict)
    queue_id: str | None = None
    run_id: str | None = None
    alert_id: str | None = None
    source_proposal_id: str | None = None
    created_at: datetime


class CorrelationMatch(BaseModel):
    """One correlated historical alert plus evidence that can be reused in review."""

    summary: AlertSummary
    score: float = Field(ge=0.0)
    matched_reasons: list[str] = Field(default_factory=list)
    reusable_evidence: list[CorrelationEvidenceRef] = Field(default_factory=list)


class CorrelationResult(BaseModel):
    """Deterministic correlation result for CLI, TUI, Web, and review context."""

    schema_version: str = "soc.correlation_result.v1"
    scoring_policy_version: str = CORRELATION_SCORING_POLICY_VERSION
    query: CorrelationQuery
    subject_summary: AlertSummary
    matches: list[CorrelationMatch] = Field(default_factory=list)
    reusable_evidence_count: int = 0
    generated_at: datetime = Field(default_factory=utc_now)


class ReviewQueueItem(BaseModel):
    """Human review queue item derived from an alert summary."""

    schema_version: str = "soc.review_queue.v1"
    queue_id: str = Field(default_factory=lambda: f"REV-{uuid4().hex[:12].upper()}")
    run_id: str
    alert_id: str
    tenant_id: str | None = None
    status: ReviewQueueStatus = ReviewQueueStatus.OPEN
    priority: ReviewQueuePriority = ReviewQueuePriority.MEDIUM
    reason: str
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    severity: str | None = None
    category: str | None = None
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    review_reasons: list[DecisionReviewReason] = Field(default_factory=list)
    entity_keys: list[str] = Field(default_factory=list)
    summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None
    closed_by: ActorContext | None = None
    close_reason: str | None = None


class ReviewQueueCloseCommand(BaseModel):
    queue_id: str
    reason: str = Field(min_length=1)


class ReviewNoteCommand(BaseModel):
    """Analyst note captured from a review queue item as candidate memory."""

    queue_id: str = Field(min_length=1)
    note: str = Field(min_length=1, max_length=12_000)
    origin: ReviewNoteOrigin = ReviewNoteOrigin.ANALYST_NOTE
    source_thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=256)
    acceptance_reason: str | None = Field(default=None, min_length=1, max_length=2_000)
    scenario_key: str | None = None
    domain: SocDomainName | None = None
    finding_id: str | None = None
    confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    promote_to_memory: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_lead_agent_lineage_only_for_explicit_acceptance(self) -> ReviewNoteCommand:
        lineage = (
            self.source_thread_id,
            self.source_message_id,
            self.acceptance_reason,
        )
        if self.origin is ReviewNoteOrigin.ACCEPTED_LEAD_AGENT_CONCLUSION:
            if any(value is None or not value.strip() for value in lineage):
                raise ValueError("accepted Lead Agent conclusion requires source_thread_id, source_message_id, and acceptance_reason")
        elif any(value is not None for value in lineage):
            raise ValueError("Lead Agent lineage is only valid for accepted_lead_agent_conclusion notes")
        return self


class ReviewNoteResult(BaseModel):
    """Result of recording a review note through the memory-candidate boundary."""

    queue_item: ReviewQueueItem
    memory_candidate: SocMemoryCandidate | None = None
    memory_admission: MemoryAdmissionDecision | None = None


class PipelineStepTrace(BaseModel):
    step_name: str
    status: PipelineStepStatus
    input_hash: str | None = None
    output_hash: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisOutputIssue(BaseModel):
    """Sanitized, runtime-owned record of one rejected output section."""

    model_config = ConfigDict(extra="forbid")

    section: AnalysisOutputSection
    stage: str = Field(min_length=1, max_length=128)
    error_type: str = Field(min_length=1, max_length=256)
    attempt: int = Field(ge=1, le=3)
    field_paths: list[str] = Field(default_factory=list, max_length=20)
    issue_codes: list[str] = Field(default_factory=list, max_length=20)


class AnalysisOutputQuality(BaseModel):
    """Acceptance lineage for a model result; the model cannot set this object."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.analysis_output_quality.v1"] = "soc.analysis_output_quality.v1"
    status: AnalysisOutputQualityStatus = AnalysisOutputQualityStatus.ACCEPTED
    accepted_sections: list[AnalysisOutputSection] = Field(
        default_factory=lambda: list(AnalysisOutputSection),
        max_length=len(AnalysisOutputSection),
    )
    degraded_sections: list[AnalysisOutputSection] = Field(
        default_factory=list,
        max_length=len(AnalysisOutputSection),
    )
    repair_attempted: bool = False
    deterministic_fallback_used: bool = False
    issues: list[AnalysisOutputIssue] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_section_lineage(self) -> AnalysisOutputQuality:
        if len(self.accepted_sections) != len(set(self.accepted_sections)):
            raise ValueError("accepted analysis output sections must be unique")
        if len(self.degraded_sections) != len(set(self.degraded_sections)):
            raise ValueError("degraded analysis output sections must be unique")
        if set(self.accepted_sections) & set(self.degraded_sections):
            raise ValueError("analysis output sections cannot be both accepted and degraded")
        if self.status is AnalysisOutputQualityStatus.ACCEPTED:
            if self.degraded_sections or self.repair_attempted or self.deterministic_fallback_used or self.issues:
                raise ValueError("accepted analysis output cannot carry repair or degradation lineage")
        elif self.status is AnalysisOutputQualityStatus.REPAIRED:
            if not self.repair_attempted or self.degraded_sections or self.deterministic_fallback_used:
                raise ValueError("repaired analysis output requires a successful repair without degraded sections")
        elif self.status is AnalysisOutputQualityStatus.DEGRADED:
            if not self.degraded_sections or self.deterministic_fallback_used:
                raise ValueError("degraded analysis output requires at least one degraded section")
            if AnalysisOutputSection.CORE in self.degraded_sections:
                raise ValueError("a degraded model result cannot retain an invalid core section")
        elif self.status is AnalysisOutputQualityStatus.DETERMINISTIC_FALLBACK:
            if not self.deterministic_fallback_used:
                raise ValueError("deterministic fallback status requires fallback lineage")
            if self.accepted_sections or set(self.degraded_sections) != set(AnalysisOutputSection):
                raise ValueError("deterministic fallback must reject every model-output section")
        return self


class AnalysisNodeOutput(BaseModel):
    """Auditable output returned by a bounded SOC analysis node."""

    analysis: AnalysisResult
    model_name: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    parser_version: str | None = None
    output_quality: AnalysisOutputQuality = Field(default_factory=AnalysisOutputQuality)
    effective_analyzer_step_name: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeFailure(BaseModel):
    """Sanitized failure state retained on a failed analysis run."""

    schema_version: str = "soc.runtime_failure.v1"
    step_name: str = Field(min_length=1, max_length=128)
    kind: RuntimeFailureKind
    retryable: bool = False
    error_type: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=1000)


class AnalysisRequestJournal(BaseModel):
    """Bounded metadata persisted immediately before one provider invocation.

    The journal intentionally stores no rendered prompt, evidence values,
    provider response, headers, credentials, or tokens. ``AnalysisRun`` keeps
    the existing source input snapshot separately for governed replay/audit.
    ``request_journal`` is the active/latest recovery pointer; the bounded
    ordered history lives in ``provider_request_journals``.
    """

    schema_version: str = "soc.analysis_request_journal.v2"
    status: AnalysisRequestJournalStatus = AnalysisRequestJournalStatus.RUNNING
    action: Literal[AuditAction.ANALYSIS, AuditAction.REPLAY]
    request_id: str = Field(min_length=1, max_length=256)
    trace_id: str | None = Field(default=None, max_length=256)
    actor: ActorContext
    idempotency_key_hash: str | None = Field(default=None, min_length=1, max_length=128)
    replay_of_run_id: str | None = None
    request_schema_version: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(min_length=1, max_length=128)
    source_type: AlertSourceType = AlertSourceType.UNKNOWN
    source_system: str | None = Field(default=None, max_length=256)
    detection_key: str | None = Field(default=None, max_length=512)
    model_name: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=256)
    provider_step_name: str = Field(min_length=1, max_length=128)
    provider_purpose: AnalysisProviderPurpose = AnalysisProviderPurpose.PRIMARY_ANALYSIS
    parser_version: str | None = Field(default=None, max_length=256)
    optional_provider: bool = False
    primary_evidence_present: bool = False
    supplementary_evidence_count: int = Field(default=0, ge=0)
    selected_skills: list[str] = Field(default_factory=list, max_length=50)
    provider_started_at: datetime = Field(default_factory=utc_now)
    finalized_at: datetime | None = None
    failure_kind: RuntimeFailureKind | None = None
    failure_retryable: bool | None = None
    recovered_at: datetime | None = None
    recovered_by: ActorContext | None = None
    recovery_reason: str | None = Field(default=None, max_length=1000)
    recovery_run_id: str | None = None


class AnalysisProviderInvocation(BaseModel):
    """Secret-free descriptor supplied to the pre-provider journal hook."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.analysis_provider_invocation.v1"] = "soc.analysis_provider_invocation.v1"
    step_name: str = Field(min_length=1, max_length=128)
    purpose: AnalysisProviderPurpose
    model_name: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=256)
    parser_version: str | None = Field(default=None, max_length=256)
    optional: bool = False


class AnalysisRunRecoveryCommand(BaseModel):
    """Claim and replay one stale pre-provider journal."""

    run_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)
    stale_after_seconds: int = Field(default=300, ge=0, le=86400)


class AnalysisRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"RUN-{uuid4().hex[:12].upper()}")
    alert_id: str
    status: AnalysisRunStatus
    pipeline_version: str = "soc-runtime-v1"
    model_name: str = "stub"
    prompt_version: str = "stub"
    input_payload: dict[str, Any] | None = None
    input_hash: str | None = None
    replay_of_run_id: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    total_duration_ms: int | None = Field(default=None, ge=0)
    steps: list[PipelineStepTrace] = Field(default_factory=list)
    entities: ExtractedEntities | None = None
    normalization_report: NormalizationReport | None = None
    normalization_monitoring_result: NormalizationMonitoringResult | None = None
    extraction_report: ExtractionReport | None = None
    fact_reconstruction: FactReconstructionResult | None = None
    llm_analysis_request: LLMAnalysisRequest | None = None
    analysis: AnalysisResult | None = None
    analysis_output_quality: AnalysisOutputQuality | None = None
    analysis_evidence_grounding: AnalysisEvidenceGroundingReport | None = None
    decision: Decision | None = None
    failure: RuntimeFailure | None = None
    request_journal: AnalysisRequestJournal | None = None
    provider_request_journals: list[AnalysisRequestJournal] = Field(
        default_factory=list,
        max_length=8,
    )
    corrections: list[CorrectionRecord] = Field(default_factory=list)
    role_adjudication_revisions: list[RoleAdjudicationRevisionRecord] = Field(
        default_factory=list,
    )
    role_verification_trigger: RoleVerificationTriggerDecision | None = None
    role_adjudication_verification: RoleAdjudicationVerificationResult | None = None

    @model_validator(mode="after")
    def validate_failure_state(self) -> AnalysisRun:
        if self.status is AnalysisRunStatus.FAILED and self.failure is None:
            raise ValueError("failed analysis run requires RuntimeFailure")
        if self.status is not AnalysisRunStatus.FAILED and self.failure is not None:
            raise ValueError("only failed analysis run may carry RuntimeFailure")
        return self


class SocDomainTriageRequest(BaseModel):
    """Input contract for one bounded SOC domain handler."""

    schema_version: str = "soc.domain_triage_request.v1"
    request_id: str = Field(default_factory=lambda: f"DTR-{uuid4().hex[:12].upper()}")
    run: AnalysisRun
    domain: SocDomainName | None = None
    skill_context: SocSkillContext = Field(default_factory=SocSkillContext)
    investigation_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    correlation_result: CorrelationResult | None = None
    capability_card_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocDomainFinding(BaseModel):
    """One bounded domain finding; it is not an operational verdict."""

    schema_version: str = "soc.domain_finding.v1"
    finding_id: str = Field(default_factory=lambda: f"DFN-{uuid4().hex[:12].upper()}")
    domain: SocDomainName
    scenario_key: str | None = None
    scenario_name: str | None = None
    vendor_scenarios: list[str] = Field(default_factory=list)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    severity: SocDomainFindingSeverity = SocDomainFindingSeverity.MEDIUM
    disposition: SocDomainFindingDisposition = SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_profile: SocEvidenceProfile = Field(default_factory=SocEvidenceProfile)
    current_conclusion: SocFindingConclusion = Field(default_factory=SocFindingConclusion)
    evidence_refs: list[str] = Field(default_factory=list)
    capability_card_refs: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    human_checklist: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocDomainTriageResult(BaseModel):
    """Output from one SOC domain handler."""

    schema_version: str = "soc.domain_triage_result.v1"
    request_id: str
    run_id: str
    alert_id: str
    domain: SocDomainName
    handler_id: str = Field(min_length=1)
    findings: list[SocDomainFinding] = Field(default_factory=list)
    evidence_ref_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SocOrchestratorActionSpec(BaseModel):
    """One explicit read-only action requested by the main orchestrator."""

    route: str = Field(min_length=1)
    action: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    origin: Literal["explicit", "planned"] = "explicit"
    plan_action_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def planned_origin_requires_action_id(self) -> SocOrchestratorActionSpec:
        if self.action is not None and self.action != self.route:
            raise ValueError("orchestrator action must exactly match its route")
        if self.origin == "planned" and self.plan_action_id is None:
            raise ValueError("planned orchestrator action requires plan_action_id")
        if self.origin == "explicit" and self.plan_action_id is not None:
            raise ValueError("explicit orchestrator action cannot carry plan_action_id")
        return self


class SocOrchestratorRouteStep(BaseModel):
    """One route/action/evidence step inside a main orchestrator report."""

    route: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: Literal["success", "denied", "failed"]
    message: str = Field(min_length=1)
    evidence_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    origin: Literal["explicit", "planned"] = "explicit"
    plan_action_id: str | None = None

    @model_validator(mode="after")
    def origin_matches_plan_action(self) -> SocOrchestratorRouteStep:
        if self.origin == "planned" and self.plan_action_id is None:
            raise ValueError("planned route step requires plan_action_id")
        if self.origin == "explicit" and self.plan_action_id is not None:
            raise ValueError("explicit route step cannot carry plan_action_id")
        return self


class SocOrchestratorReviewContextSummary(BaseModel):
    """Bounded review context summary for analyst-facing reports."""

    run_id: str
    alert_id: str
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = True
    reason: str | None = None
    analysis_summary: str | None = None
    action_evidence_count: int = Field(default=0, ge=0)
    correlation_match_count: int = Field(default=0, ge=0)
    reusable_evidence_count: int = Field(default=0, ge=0)
    domain_finding_count: int = Field(default=0, ge=0)


class SocMainOrchestratorRequest(BaseModel):
    """Input for one bounded main-orchestrator demo run."""

    schema_version: str = "soc.main_orchestrator_request.v1"
    payload: dict[str, Any]
    sample_id: str | None = None
    thread_id: str | None = None
    action_specs: list[SocOrchestratorActionSpec] = Field(default_factory=list)
    capability_card_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnifiedInvestigationReport(BaseModel):
    """Unified report assembled by the SOC main orchestrator."""

    schema_version: str = "soc.unified_investigation_report.v1"
    report_id: str = Field(default_factory=lambda: f"UIR-{uuid4().hex[:12].upper()}")
    sample_id: str | None = None
    run: AnalysisRun
    skill_context: SocSkillContext = Field(default_factory=SocSkillContext)
    enrichment_plan: SocEnrichmentPlan | None = None
    route_steps: list[SocOrchestratorRouteStep] = Field(default_factory=list)
    investigation_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    correlation_result: CorrelationResult | None = None
    domain_triage_results: list[SocDomainTriageResult] = Field(default_factory=list)
    review_context: SocOrchestratorReviewContextSummary
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvestigationTimelineItem(BaseModel):
    """One analyst-facing event in the unified investigation timeline."""

    schema_version: str = "soc.investigation_timeline_item.v1"
    item_id: str = Field(default_factory=lambda: f"TIM-{uuid4().hex[:12].upper()}")
    kind: Literal[
        "analysis",
        "decision",
        "correlation",
        "domain_finding",
        "read_only_evidence",
        "investigation_addendum",
        "authorization_enrichment",
        "disposition_proposal",
        "disposition_outcome",
        "external_disposition",
        "memory_candidate",
        "relevant_memory",
        "audit",
        "correction",
    ]
    title: str = Field(min_length=1)
    summary: str | None = None
    status: str | None = None
    severity: str | None = None
    source_id: str | None = None
    source_refs: dict[str, str] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class UnifiedInvestigationView(BaseModel):
    """Read-only analyst view assembled from existing SOC investigation products."""

    schema_version: str = "soc.unified_investigation_view.v1"
    view_id: str = Field(default_factory=lambda: f"UIV-{uuid4().hex[:12].upper()}")
    queue_id: str
    run_id: str
    alert_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    runtime_verdict: Verdict | None = None
    runtime_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = True
    automation_allowed: bool = False
    primary_summary: str | None = None
    primary_reason: str | None = None
    correlation_result: CorrelationResult | None = None
    domain_triage_results: list[SocDomainTriageResult] = Field(default_factory=list)
    investigation_addenda: list[SocInvestigationAddendum] = Field(default_factory=list)
    evidence_timeline: list[InvestigationTimelineItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    boundary_notes: list[str] = Field(
        default_factory=lambda: [
            "This view is read-only analyst context.",
            "Investigation addenda summarize durable read-only lookups and never replace the Runtime verdict.",
            "Domain findings and relevant memories do not change the operational verdict.",
            "Authorization enrichments are shadow matches and do not change detection truth or disposition.",
            "Disposition proposals are shadow-only and require human review; they do not close review items.",
            "Disposition outcomes are evaluation labels only; they do not mutate detection truth or review state.",
            "External feedback and memory updates must still pass service boundaries.",
        ]
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvestigationContext(BaseModel):
    """Read model used by analyst surfaces to open one review item."""

    schema_version: str = "soc.investigation_context.v1"
    queue_item: ReviewQueueItem
    run: AnalysisRun
    summary: AlertSummary | None = None
    audit_records: list[DecisionAuditRecord] = Field(default_factory=list)
    similar_alerts: list[SimilarAlertMatch] = Field(default_factory=list)
    action_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    investigation_addenda: list[SocInvestigationAddendum] = Field(default_factory=list)
    authorization_enrichments: list[AuthorizationEnrichmentRecord] = Field(default_factory=list)
    disposition_proposals: list[SocDispositionProposalRecord] = Field(default_factory=list)
    disposition_outcomes: list[SocDispositionOutcomeRecord] = Field(default_factory=list)
    external_dispositions: list[SocExternalDispositionRecord] = Field(default_factory=list)
    memory_candidates: list[SocMemoryCandidate] = Field(default_factory=list)
    relevant_memories: SocMemoryRetrievalResult | None = None
    correlation_result: CorrelationResult | None = None
    domain_triage_results: list[SocDomainTriageResult] = Field(default_factory=list)
    investigation_view: UnifiedInvestigationView | None = None
