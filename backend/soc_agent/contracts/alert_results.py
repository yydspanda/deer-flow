"""Read contracts for alert results independent of optional human tasks."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from soc_agent.contracts.investigation_reporting import SocInvestigationAddendum
from soc_agent.contracts.schemas import (
    AlertSummary,
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
    UnifiedInvestigationView,
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


class SocAlertResult(BaseModel):
    """List-friendly alert result with optional human-task linkage."""

    schema_version: Literal["soc.alert_result.v1"] = "soc.alert_result.v1"
    summary: AlertSummary
    attention_level: SocAlertAttentionLevel = SocAlertAttentionLevel.NONE
    attention_reasons: list[DecisionReviewReason] = Field(default_factory=list)
    decision_usability: SocDecisionUsability = SocDecisionUsability.USABLE
    requires_human_intervention: bool = False
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


__all__ = [
    "SocAlertAttentionLevel",
    "SocAlertInvestigationContext",
    "SocAlertResult",
    "SocDecisionUsability",
]
