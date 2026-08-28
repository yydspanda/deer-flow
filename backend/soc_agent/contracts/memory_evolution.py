"""Append-only contracts for operational Memory use and feedback evolution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import SocMemoryApplicabilityReport, SocMemoryRecord, Verdict


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SocMemoryUseEffect(StrEnum):
    CONTEXT_ONLY = "context_only"
    REINFORCED = "reinforced"
    OVERRIDDEN = "overridden"
    CONFLICTED = "conflicted"


class SocMemoryFeedbackSource(StrEnum):
    ANALYST_CORRECTION = "analyst_correction"
    EXTERNAL_DISPOSITION = "external_disposition"
    REVIEW_RESOLUTION = "review_resolution"


class SocMemoryFeedbackTrust(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SocMemoryFeedbackAlignment(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class SocMemoryHealthStatus(StrEnum):
    HEALTHY = "healthy"
    WATCH = "watch"
    SUSPENDED = "suspended"


class SocMemoryRevisionProposalStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SocMemoryRevisionReviewDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class SocMemoryUseRecord(BaseModel):
    """One exact confirmed-Memory projection into one analysis run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_use.v1"] = "soc.memory_use.v1"
    use_id: str = Field(default_factory=lambda: f"MU-{uuid4().hex[:12].upper()}")
    idempotency_key: str = Field(min_length=1, max_length=512)
    memory_id: str = Field(min_length=1, max_length=64)
    memory_version: int = Field(ge=1)
    memory_content_hash: str = Field(min_length=1, max_length=128)
    memory_facets_hash: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    context_ref: str = Field(min_length=1, max_length=64)
    retrieval_policy_version: str = Field(min_length=1, max_length=128)
    retrieval_score: float = Field(ge=0.0)
    matched_facets: dict[str, list[str]] = Field(default_factory=dict)
    applicability_report: SocMemoryApplicabilityReport
    base_verdict: Verdict
    effective_verdict: Verdict
    effect: SocMemoryUseEffect
    directive_applied: bool
    decision_transition_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=_utc_now)


class SocMemoryFeedbackEvent(BaseModel):
    """Immutable final-outcome feedback associated with one prior Memory use."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_feedback.v1"] = "soc.memory_feedback.v1"
    feedback_id: str = Field(default_factory=lambda: f"MF-{uuid4().hex[:12].upper()}")
    idempotency_key: str = Field(min_length=1, max_length=512)
    use_id: str = Field(min_length=1, max_length=64)
    memory_id: str = Field(min_length=1, max_length=64)
    memory_version: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    source: SocMemoryFeedbackSource
    trust: SocMemoryFeedbackTrust
    final_verdict: Verdict
    memory_reviewed_verdict: Verdict | None = None
    memory_target_verdict: Verdict | None = None
    directive_was_active: bool = False
    applicability_status: str | None = Field(default=None, max_length=64)
    alignment: SocMemoryFeedbackAlignment
    reason: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=256)
    actor_id: str = Field(min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=_utc_now)


class SocMemoryHealthRecord(BaseModel):
    """Current derived health for one immutable Memory version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_health.v1"] = "soc.memory_health.v1"
    memory_id: str = Field(min_length=1, max_length=64)
    memory_version: int = Field(ge=1)
    version: int = Field(default=1, ge=1)
    status: SocMemoryHealthStatus = SocMemoryHealthStatus.HEALTHY
    use_count: int = Field(default=0, ge=0)
    support_count: int = Field(default=0, ge=0)
    contradiction_count: int = Field(default=0, ge=0)
    not_applicable_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    last_use_at: datetime | None = None
    last_feedback_at: datetime | None = None
    last_feedback_id: str | None = Field(default=None, max_length=64)
    suspension_reason: str | None = Field(default=None, max_length=2000)
    updated_at: datetime = Field(default_factory=_utc_now)


class SocMemoryRevisionProposal(BaseModel):
    """Review task produced by material feedback; never an in-place rewrite."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_revision_proposal.v1"] = "soc.memory_revision_proposal.v1"
    proposal_id: str = Field(default_factory=lambda: f"MRP-{uuid4().hex[:12].upper()}")
    idempotency_key: str = Field(min_length=1, max_length=512)
    memory_id: str = Field(min_length=1, max_length=64)
    memory_version: int = Field(ge=1)
    source_feedback_id: str = Field(min_length=1, max_length=64)
    status: SocMemoryRevisionProposalStatus = SocMemoryRevisionProposalStatus.PENDING_REVIEW
    reason: str = Field(min_length=1, max_length=4000)
    proposed_excluded_facets: dict[str, list[str]] = Field(default_factory=dict)
    proposed_target_verdict: Verdict | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    reviewed_at: datetime | None = None
    reviewed_by: str | None = Field(default=None, max_length=128)
    review_reason: str | None = Field(default=None, max_length=4000)

    @field_validator("proposed_excluded_facets")
    @classmethod
    def keep_exclusions_bounded(
        cls,
        value: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        return {str(key).strip().casefold(): sorted({str(item).strip() for item in values if str(item).strip()})[:20] for key, values in value.items() if str(key).strip() and any(str(item).strip() for item in values)}


class SocMemoryRevisionReviewCommand(BaseModel):
    """Resolve one pending feedback proposal without rewriting Memory."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_revision_review_command.v1"] = "soc.memory_revision_review_command.v1"
    proposal_id: str = Field(min_length=1, max_length=64)
    decision: SocMemoryRevisionReviewDecision
    reason: str = Field(min_length=1, max_length=4000)


class SocMemoryRevisionReviewResult(BaseModel):
    """Auditable proposal transition; the Memory record remains unchanged."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_revision_review_result.v1"] = "soc.memory_revision_review_result.v1"
    proposal: SocMemoryRevisionProposal
    previous_status: SocMemoryRevisionProposalStatus
    decision: SocMemoryRevisionReviewDecision
    memory_record_changed: Literal[False] = False
    retrieval_reenabled: Literal[False] = False
    audit_id: str | None = None
    reviewed_at: datetime = Field(default_factory=_utc_now)


class SocMemoryFeedbackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_feedback_result.v1"] = "soc.memory_feedback_result.v1"
    feedback_events: list[SocMemoryFeedbackEvent] = Field(default_factory=list)
    health_records: list[SocMemoryHealthRecord] = Field(default_factory=list)
    revision_proposals: list[SocMemoryRevisionProposal] = Field(default_factory=list)
    suspended_memory_ids: list[str] = Field(default_factory=list)


class SocMemoryLineageReport(BaseModel):
    """Read model for one confirmed Memory and its operational outcomes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_lineage_report.v1"] = "soc.memory_lineage_report.v1"
    record: SocMemoryRecord
    uses: list[SocMemoryUseRecord] = Field(default_factory=list)
    feedback: list[SocMemoryFeedbackEvent] = Field(default_factory=list)
    health: list[SocMemoryHealthRecord] = Field(default_factory=list)
    revision_proposals: list[SocMemoryRevisionProposal] = Field(default_factory=list)


__all__ = [
    "SocMemoryFeedbackAlignment",
    "SocMemoryFeedbackEvent",
    "SocMemoryFeedbackResult",
    "SocMemoryFeedbackSource",
    "SocMemoryFeedbackTrust",
    "SocMemoryHealthRecord",
    "SocMemoryHealthStatus",
    "SocMemoryLineageReport",
    "SocMemoryRevisionProposal",
    "SocMemoryRevisionProposalStatus",
    "SocMemoryRevisionReviewCommand",
    "SocMemoryRevisionReviewDecision",
    "SocMemoryRevisionReviewResult",
    "SocMemoryUseEffect",
    "SocMemoryUseRecord",
]
