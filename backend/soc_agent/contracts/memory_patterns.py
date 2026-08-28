"""Governed contracts for repeated alert-pattern memory candidates."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import (
    DecisionEvidenceState,
    SocMemoryCandidate,
    TriageActivityStage,
    Verdict,
)

MEMORY_PATTERN_AGGREGATION_POLICY_VERSION = "soc.memory_pattern_aggregation.v3"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryPatternDataClass(StrEnum):
    """Keep local/simulated pattern learning separate from operational data."""

    SIMULATION = "simulation"
    OPERATIONAL = "operational"


class MemoryPatternSourceType(StrEnum):
    """Application lanes admitted to PI-03F3."""

    KAFKA_ALERT = "kafka_alert"
    BATCH_ALERT = "batch_alert"
    ANALYSIS_RUN = "analysis_run"


class MemoryPatternDimension(StrEnum):
    """One strongest available vendor-neutral dimension owns a cohort."""

    SCENARIO = "scenario"
    DETECTION = "detection"
    BEHAVIOR = "behavior"
    COMPOUND = "compound"
    CATEGORY = "category"


class MemoryPatternRiskClass(StrEnum):
    """Coarse reviewed-memory outcome used only for cohort consistency."""

    RISK = "risk"
    BENIGN = "benign"
    UNRESOLVED = "unresolved"


class MemoryPatternLessonObservation(BaseModel):
    """Bounded conclusion snapshot retained for pattern-level lesson synthesis."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_pattern_lesson_observation.v1"] = "soc.memory_pattern_lesson_observation.v1"
    verdict: Verdict
    risk_class: MemoryPatternRiskClass
    needs_review: bool
    evidence_state: DecisionEvidenceState | None = None
    summary: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=4000)
    recommended_action: str = Field(min_length=1, max_length=2000)
    primary_scenario_key: str | None = Field(default=None, min_length=1, max_length=256)
    primary_scenario_name: str | None = Field(default=None, min_length=1, max_length=512)
    activity_stage: TriageActivityStage | None = None
    boundary_direction: str | None = Field(default=None, min_length=1, max_length=128)
    semantic_direction: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def keep_risk_class_consistent(self) -> MemoryPatternLessonObservation:
        expected = _risk_class_for_verdict(self.verdict)
        if self.risk_class is not expected:
            raise ValueError("memory pattern risk_class must match the source verdict")
        return self


class MemoryPatternCohortQuality(BaseModel):
    """Deterministic quality gate evaluated before expert review is requested."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_pattern_cohort_quality.v1"] = "soc.memory_pattern_cohort_quality.v1"
    support_count: int = Field(ge=1)
    distinct_source_count: int = Field(ge=1)
    conclusive_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    risk_class_counts: dict[str, int] = Field(default_factory=dict)
    dominant_risk_class: MemoryPatternRiskClass | None = None
    consistency_ratio: float = Field(ge=0.0, le=1.0)
    applicability_facets: dict[str, list[str]] = Field(default_factory=dict)
    strong_anchor_facets: dict[str, list[str]] = Field(default_factory=dict)
    quality_gate_passed: bool
    reason_codes: list[str] = Field(default_factory=list)
    representative_observation_ids: list[str] = Field(default_factory=list)


def _risk_class_for_verdict(verdict: Verdict) -> MemoryPatternRiskClass:
    if verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return MemoryPatternRiskClass.RISK
    if verdict is Verdict.FALSE_POSITIVE:
        return MemoryPatternRiskClass.BENIGN
    return MemoryPatternRiskClass.UNRESOLVED


class MemoryPatternSignature(BaseModel):
    """Stable recurrence identity without a mandatory rule-code tuple."""

    model_config = ConfigDict(extra="forbid")

    dimension: MemoryPatternDimension
    value: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=512)
    origin: str = Field(min_length=1, max_length=128)
    facets: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("value", "label", "origin")
    @classmethod
    def strip_signature_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("memory pattern signature text must not be blank")
        return normalized

    @field_validator("facets")
    @classmethod
    def keep_facets_bounded(cls, facets: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(facets) > 20:
            raise ValueError("memory pattern signature supports at most 20 facets")
        normalized: dict[str, list[str]] = {}
        for key, values in facets.items():
            facet_key = key.strip()
            if not facet_key or len(facet_key) > 128:
                raise ValueError("memory pattern facet keys must be 1-128 characters")
            cleaned = [" ".join(value.split()) for value in values]
            if any(not value or len(value) > 512 for value in cleaned):
                raise ValueError("memory pattern facet values must be 1-512 characters")
            normalized[facet_key] = list(dict.fromkeys(cleaned))[:20]
        return normalized


class MemoryPatternSourceRef(BaseModel):
    """Exact transport lineage plus a replay-stable distinct alert identity."""

    model_config = ConfigDict(extra="forbid")

    source_type: MemoryPatternSourceType
    source_id: str = Field(min_length=1, max_length=256)
    transport_ref: str = Field(min_length=1, max_length=512)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def require_aware_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("memory pattern observed_at must be timezone-aware")
        return value.astimezone(UTC)


class MemoryPatternAggregationPolicy(BaseModel):
    """Versioned fixed-window aggregation and immutable projection limits."""

    model_config = ConfigDict(extra="forbid")

    policy_version: Literal[
        "soc.memory_pattern_aggregation.v1",
        "soc.memory_pattern_aggregation.v2",
        "soc.memory_pattern_aggregation.v3",
    ] = MEMORY_PATTERN_AGGREGATION_POLICY_VERSION
    window_seconds: int = Field(default=86_400, ge=300, le=2_592_000)
    minimum_support: int = Field(default=5, ge=2, le=100)
    minimum_distinct_sources: int = Field(default=5, ge=2, le=100)
    minimum_conclusive_support: int = Field(default=5, ge=2, le=100)
    minimum_consistency_ratio: float = Field(default=0.8, ge=0.5, le=1.0)
    maximum_representative_sources: int = Field(default=10, ge=1, le=50)
    maximum_representative_conclusions: int = Field(default=3, ge=1, le=10)
    maximum_evidence_refs: int = Field(default=50, ge=1, le=200)
    window_basis: Literal["source_observed_at"] = "source_observed_at"
    supersession_mode: Literal["manual_only"] = "manual_only"

    @model_validator(mode="after")
    def keep_thresholds_coherent(self) -> MemoryPatternAggregationPolicy:
        if self.minimum_distinct_sources > self.minimum_support:
            raise ValueError("minimum_distinct_sources cannot exceed minimum_support")
        if self.minimum_conclusive_support > self.minimum_support:
            raise ValueError("minimum_conclusive_support cannot exceed minimum_support")
        return self


class MemoryPatternObservationCreateCommand(BaseModel):
    """Admit one completed Runtime result as a source observation, never memory."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=512)
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    data_class: MemoryPatternDataClass
    profile_id: str = Field(default="soc.generic", min_length=1, max_length=128)
    profile_version: str = Field(default="1", min_length=1, max_length=128)
    feature_schema_version: str = Field(
        default="soc.memory_features.generic.v1",
        min_length=1,
        max_length=128,
    )
    occurrence_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: MemoryPatternSourceRef
    signature: MemoryPatternSignature
    lesson: MemoryPatternLessonObservation
    evidence_refs: list[str] = Field(min_length=1, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "tenant_id",
        "environment",
        "profile_id",
        "profile_version",
        "feature_schema_version",
    )
    @classmethod
    def strip_scope_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory pattern scope must not be blank")
        return normalized

    @field_validator("evidence_refs")
    @classmethod
    def keep_evidence_refs_unique(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 512 for value in normalized):
            raise ValueError("memory pattern evidence refs must be 1-512 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("memory pattern evidence refs must be unique")
        return normalized


class MemoryPatternObservation(BaseModel):
    """Immutable alert recurrence observation used only by deterministic aggregation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "soc.memory_pattern_observation.v1",
        "soc.memory_pattern_observation.v2",
        "soc.memory_pattern_observation.v3",
    ] = "soc.memory_pattern_observation.v3"
    observation_id: str = Field(default_factory=lambda: f"MPO-{uuid4().hex[:12].upper()}")
    idempotency_key: str = Field(min_length=1, max_length=512)
    aggregation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineage_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    data_class: MemoryPatternDataClass
    profile_id: str = Field(default="soc.generic", min_length=1, max_length=128)
    profile_version: str = Field(default="1", min_length=1, max_length=128)
    feature_schema_version: str = Field(
        default="soc.memory_features.generic.v1",
        min_length=1,
        max_length=128,
    )
    occurrence_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: MemoryPatternSourceRef
    signature: MemoryPatternSignature
    lesson: MemoryPatternLessonObservation | None = None
    window_start: datetime
    window_end: datetime
    aggregation_policy: MemoryPatternAggregationPolicy
    evidence_refs: list[str] = Field(min_length=1, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict)
    mocked: bool
    direct_memory_candidate_allowed: Literal[False] = False
    runtime_decision_allowed: Literal[False] = False
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_observation_boundaries(self) -> MemoryPatternObservation:
        if self.window_start.tzinfo is None or self.window_start.utcoffset() is None:
            raise ValueError("memory pattern window_start must be timezone-aware")
        if self.window_end.tzinfo is None or self.window_end.utcoffset() is None:
            raise ValueError("memory pattern window_end must be timezone-aware")
        if self.window_end <= self.window_start:
            raise ValueError("memory pattern window_end must be after window_start")
        if not self.window_start <= self.source.observed_at < self.window_end:
            raise ValueError("memory pattern observed_at must fall inside its fixed window")
        expected_mocked = self.data_class is MemoryPatternDataClass.SIMULATION
        if self.mocked is not expected_mocked:
            raise ValueError("mocked must exactly match the memory pattern data class")
        requires_lesson = self.schema_version in {
            "soc.memory_pattern_observation.v2",
            "soc.memory_pattern_observation.v3",
        } or self.aggregation_policy.policy_version in {
            "soc.memory_pattern_aggregation.v2",
            MEMORY_PATTERN_AGGREGATION_POLICY_VERSION,
        }
        if requires_lesson and self.lesson is None:
            raise ValueError("v2 memory pattern observations require a lesson snapshot")
        return self


class MemoryPatternAggregationResult(BaseModel):
    """Outcome of one observation admission and candidate threshold check."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_pattern_aggregation_result.v2"] = "soc.memory_pattern_aggregation_result.v2"
    observation: MemoryPatternObservation
    support_count: int = Field(ge=1)
    distinct_source_count: int = Field(ge=1)
    minimum_support: int = Field(ge=2)
    minimum_distinct_sources: int = Field(ge=2)
    threshold_met: bool
    cohort_quality: MemoryPatternCohortQuality
    candidate: SocMemoryCandidate | None = None
    candidate_coverage: Literal[
        "none",
        "current_cohort",
        "equivalent_lesson",
        "lineage_governance",
    ] = "none"
    candidate_created: bool = False
    candidate_frozen: bool = False
    idempotent: bool = False
    duplicate_source: bool = False
    duplicate_occurrence: bool = False
    note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_candidate_gate(self) -> MemoryPatternAggregationResult:
        expected_threshold = self.support_count >= self.minimum_support and self.distinct_source_count >= self.minimum_distinct_sources
        if self.threshold_met is not expected_threshold:
            raise ValueError("memory pattern threshold flag does not match support counts")
        if self.candidate_created and self.candidate is None:
            raise ValueError("candidate_created requires a candidate")
        if self.candidate_created and not self.threshold_met:
            raise ValueError("candidate creation requires the aggregation threshold")
        if self.candidate_created and not self.cohort_quality.quality_gate_passed:
            raise ValueError("candidate creation requires the lesson quality gate")
        if self.candidate_created and self.candidate_coverage != "current_cohort":
            raise ValueError("candidate creation must cover the current cohort")
        if self.candidate is None and self.candidate_coverage != "none":
            raise ValueError("candidate coverage requires a candidate")
        return self


class MemoryPatternReplayReport(BaseModel):
    """Read-only deterministic replay over one persisted aggregation cohort."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_pattern_replay_report.v2"] = "soc.memory_pattern_replay_report.v2"
    aggregation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineage_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1, max_length=128)
    support_count: int = Field(ge=1)
    distinct_source_count: int = Field(ge=1)
    threshold_met: bool
    cohort_quality: MemoryPatternCohortQuality
    candidate_id: str | None = None
    candidate_status: str | None = None
    candidate_coverage: Literal[
        "none",
        "current_cohort",
        "equivalent_lesson",
        "lineage_governance",
    ] = "none"
    candidate_origin_aggregation_key: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    candidate_snapshot_observation_ids: list[str] = Field(default_factory=list)
    current_observation_ids: list[str] = Field(min_length=1)
    added_observation_ids: list[str] = Field(default_factory=list)
    missing_observation_ids: list[str] = Field(default_factory=list)
    baseline_evidence_set_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    recomputed_snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_integrity_passed: bool
    source_integrity_checked: bool
    changed: bool
    supersession_mode: Literal["manual_only"] = "manual_only"
    candidate_mutation_performed: Literal[False] = False
    runtime_decision_allowed: Literal[False] = False
    replayed_at: datetime = Field(default_factory=_utc_now)


__all__ = [
    "MEMORY_PATTERN_AGGREGATION_POLICY_VERSION",
    "MemoryPatternAggregationPolicy",
    "MemoryPatternAggregationResult",
    "MemoryPatternCohortQuality",
    "MemoryPatternDataClass",
    "MemoryPatternDimension",
    "MemoryPatternLessonObservation",
    "MemoryPatternObservation",
    "MemoryPatternObservationCreateCommand",
    "MemoryPatternReplayReport",
    "MemoryPatternRiskClass",
    "MemoryPatternSignature",
    "MemoryPatternSourceRef",
    "MemoryPatternSourceType",
]
