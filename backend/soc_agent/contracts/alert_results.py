"""Read contracts for alert results independent of optional human tasks."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from soc_agent.contracts.investigation_reporting import SocInvestigationAddendum
from soc_agent.contracts.schemas import (
    AlertSummary,
    AnalysisCapability,
    AnalysisConclusionSupport,
    AnalysisRun,
    AuthorizationEnrichmentRecord,
    CorrelationResult,
    DecisionAuditRecord,
    DecisionReviewReason,
    InvestigationEvidence,
    ReviewQueueItem,
    SimilarAlertMatch,
    SocDispositionOutcomeRecord,
    SocDispositionProposalRecord,
    SocDomainTriageResult,
    SocExternalDispositionRecord,
    SocMemoryCandidate,
    SocMemoryRetrievalResult,
    SocOperationalDisposition,
    UnifiedInvestigationView,
    Verdict,
)


class SocAlertAttentionLevel(StrEnum):
    """Operator attention derived separately from model uncertainty."""

    NONE = "none"
    ADVISORY = "advisory"
    REQUIRED = "required"


class SocDecisionUsability(StrEnum):
    """Whether the current decision can be presented and acted on safely."""

    USABLE = "usable"
    DEGRADED = "degraded"
    FAILED = "failed"


class SocCaseClosureStatus(StrEnum):
    """Operator-facing completion state for one analysis and handling chain."""

    CLOSED = "closed"
    CLOSED_WITH_LIMITATIONS = "closed_with_limitations"
    HANDLING_PENDING = "handling_pending"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    FAILED = "failed"


class SocCaseEvidenceGapImpact(StrEnum):
    """Material effect of missing evidence on the operator-facing outcome."""

    NONE = "none"
    ADVISORY = "advisory"
    CAPABILITY_LIMITED = "capability_limited"
    DECISION_BLOCKING = "decision_blocking"


class SocCaseDecisionChange(StrEnum):
    """How governed post-processing changed the technical verdict."""

    UNCHANGED = "unchanged"
    MEMORY_REINFORCED = "memory_reinforced"
    MEMORY_OVERRIDDEN = "memory_overridden"
    CONFLICTED = "conflicted"


class SocCaseOutcomeBasisKind(StrEnum):
    CURRENT_ANALYSIS = "current_analysis"
    CONFIRMED_MEMORY = "confirmed_memory"
    TENANT_POLICY = "tenant_policy"
    EXTERNAL_FEEDBACK = "external_feedback"


class SocCaseOutcomeBasis(BaseModel):
    """One bounded reason behind the final security or handling outcome."""

    kind: SocCaseOutcomeBasisKind
    summary: str = Field(min_length=1, max_length=3000)
    source_id: str | None = Field(default=None, max_length=512)


class SocCaseContributionKind(StrEnum):
    EVIDENCE_TRACE = "evidence_trace"
    REVIEWED_MEMORY_REUSED = "reviewed_memory_reused"
    TENANT_POLICY_APPLIED = "tenant_policy_applied"
    RECURRING_PATTERN = "recurring_pattern"


class SocCaseContribution(BaseModel):
    """Measured work contributed by the system for this alert."""

    kind: SocCaseContributionKind
    summary: str = Field(min_length=1, max_length=1000)
    count: int | None = Field(default=None, ge=0)


class SocCaseOutcomeView(BaseModel):
    """Single operator-facing answer derived from existing governed lineage."""

    schema_version: Literal["soc.case_outcome_view.v1"] = "soc.case_outcome_view.v1"
    event_summary: str = Field(min_length=1, max_length=4000)
    security_verdict: Verdict | None = None
    base_verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    decision_usable: bool = False
    decision_reason: str | None = Field(default=None, max_length=8000)
    decision_change: SocCaseDecisionChange = SocCaseDecisionChange.UNCHANGED
    change_summary: str | None = Field(default=None, max_length=2000)
    operational_disposition: SocOperationalDisposition | None = None
    recommended_handling: Literal["ignore", "transfer", "undetermined"] = "undetermined"
    recommended_handling_basis: str | None = Field(default=None, max_length=128)
    handling_reason: str | None = Field(default=None, max_length=3000)
    handling_recommendation: str | None = Field(default=None, max_length=1000)
    closure_status: SocCaseClosureStatus
    closure_reason_codes: list[str] = Field(default_factory=list, max_length=20)
    progress_label: str | None = Field(default=None, max_length=128)
    progress_detail: str | None = Field(default=None, max_length=4000)
    evidence_gap_impact: SocCaseEvidenceGapImpact = SocCaseEvidenceGapImpact.NONE
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)
    blocked_capabilities: list[AnalysisCapability] = Field(default_factory=list)
    action_limits_relevant: bool = False
    conclusion_support: AnalysisConclusionSupport | None = None
    prior_analysis_gaps: list[str] = Field(default_factory=list, max_length=20)
    next_steps: list[str] = Field(default_factory=list, max_length=20)
    basis: list[SocCaseOutcomeBasis] = Field(default_factory=list, max_length=10)
    contributions: list[SocCaseContribution] = Field(default_factory=list, max_length=10)
    memory_context_count: int = Field(default=0, ge=0)
    memory_directive_applied: bool = False
    tenant_policy_applied: bool = False


class SocAlertResult(BaseModel):
    """List-friendly alert result with optional human-task linkage."""

    schema_version: Literal["soc.alert_result.v1"] = "soc.alert_result.v1"
    summary: AlertSummary
    attention_level: SocAlertAttentionLevel = SocAlertAttentionLevel.NONE
    attention_reasons: list[DecisionReviewReason] = Field(default_factory=list)
    decision_usability: SocDecisionUsability = SocDecisionUsability.USABLE
    requires_human_intervention: bool = False
    recommended_handling: Literal["ignore", "transfer", "undetermined"] = "undetermined"
    queue_item: ReviewQueueItem | None = None


class SocAlertInvestigationContext(BaseModel):
    """Complete alert investigation keyed by run, whether or not a task exists."""

    schema_version: Literal["soc.alert_investigation_context.v1"] = "soc.alert_investigation_context.v1"
    result: SocAlertResult
    run: AnalysisRun
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
    operator_outcome: SocCaseOutcomeView | None = None


__all__ = [
    "SocAlertAttentionLevel",
    "SocAlertInvestigationContext",
    "SocAlertResult",
    "SocCaseClosureStatus",
    "SocCaseContribution",
    "SocCaseContributionKind",
    "SocCaseDecisionChange",
    "SocCaseEvidenceGapImpact",
    "SocCaseOutcomeBasis",
    "SocCaseOutcomeBasisKind",
    "SocCaseOutcomeView",
    "SocDecisionUsability",
]
