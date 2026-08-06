"""Governed contracts for feedback-derived Skill improvement candidates."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import ActorContext
from .schemas import SocEvaluationDataClass

SKILL_IMPROVEMENT_AGGREGATION_POLICY_VERSION = "soc.skill_improvement_aggregation.v1"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SkillFeedbackSourceType(StrEnum):
    """Admitted feedback lanes; free-form model output is not a source lane."""

    ANALYST_CORRECTION = "analyst_correction"
    EXTERNAL_DISPOSITION = "external_disposition"
    SIMULATION_FIXTURE = "simulation_fixture"


class SkillImprovementFailureFacet(StrEnum):
    """Typed, explainable failure dimensions used by deterministic aggregation."""

    SKILL_ROUTE_MISSING = "skill_route_missing"
    SCENARIO_GUIDANCE_INADEQUATE = "scenario_guidance_inadequate"
    VERDICT_GUIDANCE_INADEQUATE = "verdict_guidance_inadequate"
    EVIDENCE_GUIDANCE_INADEQUATE = "evidence_guidance_inadequate"
    MANUAL_CHECK_GUIDANCE_INADEQUATE = "manual_check_guidance_inadequate"
    ACTION_GUIDANCE_INADEQUATE = "action_guidance_inadequate"
    TENANT_KNOWLEDGE_LEAKAGE = "tenant_knowledge_leakage"
    OVERGENERALIZATION = "overgeneralization"
    OTHER_REVIEWED = "other_reviewed"


class SkillImprovementCandidateStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED_FOR_CHANGE = "approved_for_change"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class SkillImprovementReviewDecision(StrEnum):
    APPROVE_FOR_CHANGE = "approve_for_change"
    REJECT = "reject"
    SUPERSEDE = "supersede"
    EXPIRE = "expire"


class SkillPackageVersionRef(BaseModel):
    """Exact Skill package identity observed when feedback was produced."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1, max_length=128)
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    guidance_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    package_version: str | None = Field(default=None, min_length=1, max_length=128)


class SkillFeedbackSourceRef(BaseModel):
    """Secret-safe pointer back to the correction, disposition, or fixture."""

    model_config = ConfigDict(extra="forbid")

    source_type: SkillFeedbackSourceType
    source_id: str = Field(min_length=1, max_length=256)
    run_id: str | None = Field(default=None, max_length=64)
    alert_id: str | None = Field(default=None, max_length=128)
    queue_id: str | None = Field(default=None, max_length=64)
    external_system: str | None = Field(default=None, max_length=128)
    observed_at: datetime = Field(default_factory=_utc_now)


class SkillFeedbackObservationCreateCommand(BaseModel):
    """Admit one already-classified feedback observation into PI-03C."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=512)
    tenant_id: str = Field(min_length=1, max_length=128)
    data_class: SocEvaluationDataClass
    source: SkillFeedbackSourceRef
    target_skill: SkillPackageVersionRef
    scenario_key: str = Field(min_length=1, max_length=256)
    failure_facet: SkillImprovementFailureFacet
    feedback_summary: str = Field(min_length=1, max_length=2000)
    suggested_change: str = Field(min_length=1, max_length=2000)
    representative_sample_ref: str = Field(min_length=1, max_length=512)
    replay_set_refs: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def keep_simulation_and_real_feedback_separate(self) -> SkillFeedbackObservationCreateCommand:
        is_simulation = self.data_class is SocEvaluationDataClass.SIMULATION
        fixture_source = self.source.source_type is SkillFeedbackSourceType.SIMULATION_FIXTURE
        if is_simulation != fixture_source:
            raise ValueError("simulation feedback must use simulation_fixture; real feedback must not")
        if len(set(self.replay_set_refs)) != len(self.replay_set_refs):
            raise ValueError("replay_set_refs must be unique")
        return self


class SkillFeedbackObservation(BaseModel):
    """Immutable typed feedback used by deterministic candidate aggregation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.skill_feedback_observation.v1"] = "soc.skill_feedback_observation.v1"
    observation_id: str = Field(default_factory=lambda: f"SFO-{uuid4().hex[:12].upper()}")
    idempotency_key: str = Field(min_length=1, max_length=512)
    aggregation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant_id: str = Field(min_length=1, max_length=128)
    data_class: SocEvaluationDataClass
    source: SkillFeedbackSourceRef
    target_skill: SkillPackageVersionRef
    scenario_key: str = Field(min_length=1, max_length=256)
    failure_facet: SkillImprovementFailureFacet
    feedback_summary: str = Field(min_length=1, max_length=2000)
    suggested_change: str = Field(min_length=1, max_length=2000)
    representative_sample_ref: str = Field(min_length=1, max_length=512)
    replay_set_refs: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, str] = Field(default_factory=dict)
    mocked: bool
    skill_mutation_allowed: Literal[False] = False
    memory_write_allowed: Literal[False] = False
    runtime_decision_allowed: Literal[False] = False
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_data_provenance(self) -> SkillFeedbackObservation:
        expected_mocked = self.data_class is SocEvaluationDataClass.SIMULATION
        if self.mocked is not expected_mocked:
            raise ValueError("mocked must exactly match the feedback data class")
        return self


class SkillImprovementAggregationPolicy(BaseModel):
    """Versioned deterministic threshold and projection limits."""

    model_config = ConfigDict(extra="forbid")

    policy_version: Literal["soc.skill_improvement_aggregation.v1"] = SKILL_IMPROVEMENT_AGGREGATION_POLICY_VERSION
    minimum_distinct_sources: int = Field(default=3, ge=2, le=100)
    maximum_representative_samples: int = Field(default=5, ge=1, le=20)
    maximum_replay_set_refs: int = Field(default=50, ge=1, le=200)


class SkillImprovementRepresentativeSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1, max_length=64)
    source: SkillFeedbackSourceRef
    sample_ref: str = Field(min_length=1, max_length=512)
    feedback_summary: str = Field(min_length=1, max_length=2000)


class SkillImprovementCandidate(BaseModel):
    """Review backlog item; approval never edits or activates a Skill."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.skill_improvement_candidate.v1"] = "soc.skill_improvement_candidate.v1"
    candidate_id: str = Field(default_factory=lambda: f"SIC-{uuid4().hex[:12].upper()}")
    aggregation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    aggregation_policy_version: str = Field(min_length=1, max_length=128)
    candidate_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(default=1, ge=1)
    status: SkillImprovementCandidateStatus = SkillImprovementCandidateStatus.PENDING_REVIEW
    tenant_id: str = Field(min_length=1, max_length=128)
    data_class: SocEvaluationDataClass
    target_skill: SkillPackageVersionRef
    scenario_key: str = Field(min_length=1, max_length=256)
    failure_facet: SkillImprovementFailureFacet
    threshold: int = Field(ge=2)
    occurrence_count: int = Field(ge=2)
    observation_ids: list[str] = Field(min_length=2)
    source_refs: list[SkillFeedbackSourceRef] = Field(min_length=2)
    representative_samples: list[SkillImprovementRepresentativeSample] = Field(min_length=1)
    suggested_changes: list[str] = Field(min_length=1)
    replay_set_refs: list[str] = Field(default_factory=list)
    mocked: bool
    human_review_required: Literal[True] = True
    skill_mutation_allowed: Literal[False] = False
    skill_activation_allowed: Literal[False] = False
    memory_write_allowed: Literal[False] = False
    runtime_decision_allowed: Literal[False] = False
    real_quality_claim_allowed: Literal[False] = False
    reviewed_by: ActorContext | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = Field(default=None, max_length=2000)
    superseded_by_candidate_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_candidate(self) -> SkillImprovementCandidate:
        if len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError("observation_ids must be unique")
        source_ids = [item.source_id for item in self.source_refs]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("candidate source refs must be distinct")
        if self.occurrence_count != len(self.observation_ids) or self.occurrence_count != len(source_ids):
            raise ValueError("occurrence_count must equal distinct observations and sources")
        if self.occurrence_count < self.threshold:
            raise ValueError("candidate occurrence_count must meet its threshold")
        if self.mocked is not (self.data_class is SocEvaluationDataClass.SIMULATION):
            raise ValueError("candidate mocked must exactly match its data class")
        sample_source_ids = {item.source.source_id for item in self.representative_samples}
        if not sample_source_ids.issubset(set(source_ids)):
            raise ValueError("representative samples must reference candidate sources")
        reviewed = self.status is not SkillImprovementCandidateStatus.PENDING_REVIEW
        if reviewed and (self.reviewed_by is None or self.reviewed_at is None or not self.review_reason):
            raise ValueError("terminal or approved candidates require complete review provenance")
        if not reviewed and any((self.reviewed_by, self.reviewed_at, self.review_reason, self.superseded_by_candidate_id)):
            raise ValueError("pending candidates cannot carry review provenance")
        if self.status is SkillImprovementCandidateStatus.SUPERSEDED and not self.superseded_by_candidate_id:
            raise ValueError("superseded candidates require superseded_by_candidate_id")
        if self.status is not SkillImprovementCandidateStatus.SUPERSEDED and self.superseded_by_candidate_id:
            raise ValueError("superseded_by_candidate_id is valid only for superseded candidates")
        return self


class SkillImprovementAggregationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.skill_improvement_aggregation_result.v1"] = "soc.skill_improvement_aggregation_result.v1"
    observation: SkillFeedbackObservation
    distinct_source_count: int = Field(ge=1)
    threshold: int = Field(ge=2)
    candidate: SkillImprovementCandidate | None = None
    candidate_created: bool = False
    candidate_updated: bool = False
    candidate_frozen: bool = False
    idempotent: bool = False
    threshold_met: bool = False
    note: str = Field(min_length=1)


class SkillImprovementIngestReport(BaseModel):
    """Typed PI-03C ingest summary consumed by offline completion checks."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.skill_improvement_ingest_report.v1"] = "soc.skill_improvement_ingest_report.v1"
    input_count: int = Field(ge=1)
    simulation_count: int = Field(ge=0)
    real_feedback_count: int = Field(ge=0)
    candidate_ids: list[str] = Field(default_factory=list)
    candidate_count: int = Field(ge=0)
    mocked: bool
    skill_mutation_allowed: Literal[False] = False
    skill_activation_allowed: Literal[False] = False
    real_quality_claim_allowed: Literal[False] = False
    results: list[SkillImprovementAggregationResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ingest_summary(self) -> SkillImprovementIngestReport:
        if self.input_count != len(self.results):
            raise ValueError("input_count must match the number of aggregation results")
        if self.input_count != self.simulation_count + self.real_feedback_count:
            raise ValueError("feedback data-class counts must add up to input_count")
        if self.mocked is not (self.simulation_count == self.input_count):
            raise ValueError("ingest mocked state must match its feedback data classes")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate_ids must be unique")
        if self.candidate_count != len(self.candidate_ids):
            raise ValueError("candidate_count must match candidate_ids")
        result_candidate_ids = {result.candidate.candidate_id for result in self.results if result.candidate is not None}
        if result_candidate_ids != set(self.candidate_ids):
            raise ValueError("candidate_ids must match candidates projected by aggregation results")
        return self


class SkillImprovementReviewCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=64)
    decision: SkillImprovementReviewDecision
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)
    superseded_by_candidate_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_supersession_target(self) -> SkillImprovementReviewCommand:
        has_target = self.superseded_by_candidate_id is not None
        if has_target != (self.decision is SkillImprovementReviewDecision.SUPERSEDE):
            raise ValueError("supersede requires exactly one superseded_by_candidate_id")
        if self.superseded_by_candidate_id == self.candidate_id:
            raise ValueError("a candidate cannot supersede itself")
        return self


class SkillImprovementReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.skill_improvement_review_result.v1"] = "soc.skill_improvement_review_result.v1"
    candidate: SkillImprovementCandidate
    previous_status: SkillImprovementCandidateStatus
    decision: SkillImprovementReviewDecision
    idempotent: bool = False


class SkillImprovementReplayDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added_observation_ids: list[str] = Field(default_factory=list)
    removed_observation_ids: list[str] = Field(default_factory=list)
    added_replay_set_refs: list[str] = Field(default_factory=list)
    removed_replay_set_refs: list[str] = Field(default_factory=list)
    candidate_content_changed: bool = False


class SkillImprovementReplayReport(BaseModel):
    """Recompute candidate aggregation; it does not execute Skill behavior."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.skill_improvement_replay_report.v1"] = "soc.skill_improvement_replay_report.v1"
    candidate_id: str = Field(min_length=1, max_length=64)
    aggregation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_candidate_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recomputed_candidate_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed: bool
    diff: SkillImprovementReplayDiff
    observation_count: int = Field(ge=0)
    source_integrity_passed: bool
    skill_behavior_replay_executed: Literal[False] = False
    skill_mutation_allowed: Literal[False] = False
    skill_activation_allowed: Literal[False] = False
    real_quality_claim_allowed: Literal[False] = False
    created_at: datetime = Field(default_factory=_utc_now)


__all__ = [
    "SKILL_IMPROVEMENT_AGGREGATION_POLICY_VERSION",
    "SkillFeedbackObservation",
    "SkillFeedbackObservationCreateCommand",
    "SkillFeedbackSourceRef",
    "SkillFeedbackSourceType",
    "SkillImprovementAggregationPolicy",
    "SkillImprovementAggregationResult",
    "SkillImprovementCandidate",
    "SkillImprovementCandidateStatus",
    "SkillImprovementFailureFacet",
    "SkillImprovementIngestReport",
    "SkillImprovementReplayDiff",
    "SkillImprovementReplayReport",
    "SkillImprovementRepresentativeSample",
    "SkillImprovementReviewCommand",
    "SkillImprovementReviewDecision",
    "SkillImprovementReviewResult",
    "SkillPackageVersionRef",
]
