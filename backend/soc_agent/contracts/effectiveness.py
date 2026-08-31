"""Versioned product-effectiveness contracts for SOC operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .operations import SocOperationsAvailability

NonNegativeInt = Annotated[int, Field(ge=0)]


class SocRuleRecommendationKind(StrEnum):
    """Deterministic recommendation classes; none grants execution authority."""

    INSUFFICIENT_LABELS = "insufficient_labels"
    UPSTREAM_RULE_TUNING = "upstream_rule_tuning"
    RULE_SPLIT = "rule_split"
    FAST_PATH_CANDIDATE = "fast_path_candidate"
    KEEP_FULL_ANALYSIS = "keep_full_analysis"
    IMPROVE_ADAPTER_OR_ENRICHMENT = "improve_adapter_or_enrichment"
    DETECTION_GAP = "detection_gap"
    MONITOR = "monitor"


class SocRuleRecommendationPriority(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SocEffectivenessScope(BaseModel):
    """Bounded cohort used by one effectiveness snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.effectiveness_scope.v1"] = "soc.effectiveness_scope.v1"
    window_start: datetime
    window_end: datetime
    tenant_id: str | None = Field(default=None, max_length=128)
    source_type: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_window(self) -> SocEffectivenessScope:
        if self.window_start.utcoffset() is None or self.window_end.utcoffset() is None:
            raise ValueError("effectiveness window must be timezone-aware")
        if self.window_end <= self.window_start:
            raise ValueError("effectiveness window_end must be after window_start")
        if self.window_end - self.window_start > timedelta(days=366):
            raise ValueError("effectiveness window cannot exceed 366 days")
        return self


class SocRateMetric(BaseModel):
    """One denominator-visible metric that never invents missing quality evidence."""

    model_config = ConfigDict(extra="forbid")

    metric_id: str = Field(min_length=1, max_length=128)
    availability: SocOperationsAvailability
    numerator: NonNegativeInt = 0
    denominator: NonNegativeInt = 0
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    formula: str = Field(min_length=1, max_length=1000)
    interpretation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_value(self) -> SocRateMetric:
        if self.denominator == 0:
            if self.value is not None:
                raise ValueError("zero-denominator metrics cannot report a value")
            if self.availability is SocOperationsAvailability.AVAILABLE:
                raise ValueError("zero-denominator metrics cannot be available")
            return self
        if self.availability is not SocOperationsAvailability.AVAILABLE:
            raise ValueError("measured metrics with a denominator must be available")
        expected = self.numerator / self.denominator
        if self.value is None or abs(self.value - expected) > 1e-9:
            raise ValueError("metric value must equal numerator / denominator")
        return self


class SocEffectivenessCoverage(BaseModel):
    """Population, workflow acceptance, and truth coverage for honest interpretation."""

    model_config = ConfigDict(extra="forbid")

    total_alert_count: NonNegativeInt = 0
    completed_alert_count: NonNegativeInt = 0
    superseded_run_count: NonNegativeInt = 0
    conclusion_maintained_alert_count: NonNegativeInt | None = None
    labeled_alert_count: NonNegativeInt = 0
    high_trust_labeled_alert_count: NonNegativeInt = 0
    conclusion_maintenance_rate: SocRateMetric | None = None
    label_coverage: SocRateMetric
    high_trust_label_coverage: SocRateMetric


class SocEffectivenessSummary(BaseModel):
    """Product-level quality, transfer and automation rates."""

    model_config = ConfigDict(extra="forbid")

    triage_accuracy: SocRateMetric
    detection_miss_rate: SocRateMetric
    operational_miss_rate: SocRateMetric
    transfer_precision: SocRateMetric
    attack_transfer_recall: SocRateMetric
    auto_ignore_rate: SocRateMetric
    wrong_auto_ignore_rate: SocRateMetric
    human_touch_rate: SocRateMetric


class SocComputeEffectiveness(BaseModel):
    """Measured model cost and output-quality telemetry for the same cohort."""

    model_config = ConfigDict(extra="forbid")

    run_count: NonNegativeInt = 0
    provider_run_count: NonNegativeInt = 0
    provider_call_count: NonNegativeInt = 0
    token_measured_run_count: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    average_tokens_per_measured_run: float | None = Field(default=None, ge=0.0)
    duration_measured_run_count: NonNegativeInt = 0
    average_total_duration_ms: float | None = Field(default=None, ge=0.0)
    repair_run_count: NonNegativeInt = 0
    fallback_run_count: NonNegativeInt = 0
    degraded_run_count: NonNegativeInt = 0
    token_measurement_coverage: SocRateMetric
    repair_rate: SocRateMetric
    fallback_rate: SocRateMetric
    degraded_rate: SocRateMetric


class SocRuleImprovementRecommendation(BaseModel):
    """Advisory rule improvement candidate derived from measured aggregates."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.rule_improvement_recommendation.v1"] = "soc.rule_improvement_recommendation.v1"
    kind: SocRuleRecommendationKind
    priority: SocRuleRecommendationPriority
    title: str = Field(min_length=1, max_length=256)
    rationale: list[str] = Field(min_length=1, max_length=12)
    suggested_next_step: str = Field(min_length=1, max_length=2000)
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    policy_version: str = Field(min_length=1, max_length=128)
    authority: Literal["advisory"] = "advisory"
    status: Literal["candidate"] = "candidate"


class SocRuleEffectivenessAggregate(BaseModel):
    """Repository-owned exact counts before product policy interpretation."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = None
    source_type: str = Field(min_length=1, max_length=32)
    source_system: str | None = Field(default=None, max_length=128)
    detection_key: str | None = Field(default=None, max_length=256)
    rule_code: str | None = Field(default=None, max_length=128)
    rule_name: str | None = Field(default=None, max_length=256)
    alert_count: NonNegativeInt = 0
    completed_count: NonNegativeInt = 0
    superseded_run_count: NonNegativeInt = 0
    conclusion_maintained_count: NonNegativeInt = 0
    labeled_count: NonNegativeInt = 0
    high_trust_labeled_count: NonNegativeInt = 0
    correct_count: NonNegativeInt = 0
    final_risk_count: NonNegativeInt = 0
    final_false_positive_count: NonNegativeInt = 0
    detection_miss_count: NonNegativeInt = 0
    transfer_count: NonNegativeInt = 0
    labeled_transfer_count: NonNegativeInt = 0
    transferred_risk_count: NonNegativeInt = 0
    auto_ignore_count: NonNegativeInt = 0
    labeled_auto_ignore_count: NonNegativeInt = 0
    wrong_auto_ignore_count: NonNegativeInt = 0
    human_touch_count: NonNegativeInt = 0
    provider_run_count: NonNegativeInt = 0
    provider_call_count: NonNegativeInt = 0
    token_measured_run_count: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    duration_measured_run_count: NonNegativeInt = 0
    total_duration_ms: NonNegativeInt = 0
    repair_run_count: NonNegativeInt = 0
    fallback_run_count: NonNegativeInt = 0
    degraded_run_count: NonNegativeInt = 0
    memory_context_use_count: NonNegativeInt = 0
    memory_directive_use_count: NonNegativeInt = 0
    memory_contradiction_count: NonNegativeInt = 0


class SocRuleEffectiveness(BaseModel):
    """Analyst-facing rule/detection-family quality and cost projection."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.rule_effectiveness.v1"] = "soc.rule_effectiveness.v1"
    group_key: str = Field(pattern=r"^[0-9a-f]{16}$")
    tenant_id: str | None = None
    source_type: str
    source_system: str | None = None
    detection_identity: str = Field(min_length=1, max_length=256)
    detection_key: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None
    alert_count: NonNegativeInt = 0
    completed_count: NonNegativeInt = 0
    labeled_count: NonNegativeInt = 0
    high_trust_labeled_count: NonNegativeInt = 0
    label_coverage: float = Field(ge=0.0, le=1.0)
    final_risk_count: NonNegativeInt = 0
    final_false_positive_count: NonNegativeInt = 0
    confirmed_risk_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    false_positive_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    triage_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    miss_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    transfer_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    auto_ignore_rate: float = Field(ge=0.0, le=1.0)
    wrong_auto_ignore_count: NonNegativeInt = 0
    provider_call_count: NonNegativeInt = 0
    provider_run_count: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    average_total_duration_ms: float | None = Field(default=None, ge=0.0)
    repair_run_count: NonNegativeInt = 0
    fallback_run_count: NonNegativeInt = 0
    degraded_run_count: NonNegativeInt = 0
    memory_context_use_count: NonNegativeInt = 0
    memory_directive_use_count: NonNegativeInt = 0
    memory_contradiction_count: NonNegativeInt = 0
    recommendation: SocRuleImprovementRecommendation


class SocRuleEffectivenessSelector(BaseModel):
    """Exact persisted identity selected from one rule-effectiveness row."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = None
    source_type: str
    source_system: str | None = None
    detection_key: str | None = None
    rule_code: str | None = None
    rule_name: str | None = None


class SocBehaviorGroupEffectivenessAggregate(BaseModel):
    """Repository facts for one same-behavior lineage under a selected rule."""

    model_config = ConfigDict(extra="forbid")

    lineage_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    behavior_label: str = Field(min_length=1, max_length=512)
    environment: str
    data_class: str
    profile_id: str
    profile_version: str
    sample_count: NonNegativeInt = 0
    distinct_alert_count: NonNegativeInt = 0
    window_count: NonNegativeInt = 0
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    first_observed_at: datetime
    last_observed_at: datetime
    candidate_id: str | None = None
    candidate_status: str | None = None
    memory_id: str | None = None
    memory_version: int | None = Field(default=None, ge=1)
    memory_status: str | None = None
    retrieval_enabled: bool = False


class SocMemoryEffectivenessAggregate(BaseModel):
    """Repository facts for one immutable Memory version."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    memory_version: int = Field(ge=1)
    summary: str | None = None
    record_status: str | None = None
    retrieval_enabled: bool = False
    use_alert_count: NonNegativeInt = 0
    context_only_count: NonNegativeInt = 0
    directive_count: NonNegativeInt = 0
    reinforced_count: NonNegativeInt = 0
    overridden_count: NonNegativeInt = 0
    conflicted_count: NonNegativeInt = 0
    feedback_count: NonNegativeInt = 0
    high_trust_feedback_count: NonNegativeInt = 0
    directive_high_trust_feedback_count: NonNegativeInt = 0
    directive_correct_count: NonNegativeInt = 0
    support_count: NonNegativeInt = 0
    contradiction_count: NonNegativeInt = 0
    not_applicable_count: NonNegativeInt = 0
    unknown_count: NonNegativeInt = 0
    helpful_correction_count: NonNegativeInt = 0
    harmful_override_count: NonNegativeInt = 0
    wrong_auto_ignore_count: NonNegativeInt = 0
    source_rule_codes: list[str] = Field(default_factory=list)
    actual_rule_codes: list[str] = Field(default_factory=list)
    last_use_at: datetime | None = None
    last_feedback_at: datetime | None = None


class SocMemoryEffectiveness(BaseModel):
    """Analyst-facing outcome evidence for one Memory version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_effectiveness.v1"] = "soc.memory_effectiveness.v1"
    memory_id: str
    memory_version: int = Field(ge=1)
    summary: str | None = None
    record_status: str | None = None
    retrieval_enabled: bool = False
    use_alert_count: NonNegativeInt = 0
    context_only_count: NonNegativeInt = 0
    directive_count: NonNegativeInt = 0
    high_trust_feedback_count: NonNegativeInt = 0
    support_count: NonNegativeInt = 0
    contradiction_count: NonNegativeInt = 0
    not_applicable_count: NonNegativeInt = 0
    helpful_correction_count: NonNegativeInt = 0
    harmful_override_count: NonNegativeInt = 0
    wrong_auto_ignore_count: NonNegativeInt = 0
    final_outcome_coverage: SocRateMetric
    directive_accuracy: SocRateMetric
    source_rule_codes: list[str] = Field(default_factory=list)
    actual_rule_codes: list[str] = Field(default_factory=list)
    last_use_at: datetime | None = None
    last_feedback_at: datetime | None = None
    causal_note: Literal["directive_effects_attributable_context_effects_non_causal"] = "directive_effects_attributable_context_effects_non_causal"


class SocBehaviorGroupEffectiveness(BaseModel):
    """One user-facing same-behavior group beneath a Rule Code."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.behavior_group_effectiveness.v1"] = "soc.behavior_group_effectiveness.v1"
    lineage_key: str
    behavior_label: str
    environment: str
    data_class: str
    sample_count: NonNegativeInt = 0
    distinct_alert_count: NonNegativeInt = 0
    window_count: NonNegativeInt = 0
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    first_observed_at: datetime
    last_observed_at: datetime
    candidate_id: str | None = None
    candidate_status: str | None = None
    memory_id: str | None = None
    memory_version: int | None = None
    memory_status: str | None = None
    retrieval_enabled: bool = False


class SocRuleEffectivenessDetail(BaseModel):
    """Rule Code -> same behavior -> Memory read model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.rule_effectiveness_detail.v1"] = "soc.rule_effectiveness_detail.v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    scope: SocEffectivenessScope
    rule: SocRuleEffectiveness
    behavior_groups: list[SocBehaviorGroupEffectiveness] = Field(default_factory=list)
    memories: list[SocMemoryEffectiveness] = Field(default_factory=list)
    relationship_note: Literal["memory_rule_relationship_derived_from_actual_runs"] = "memory_rule_relationship_derived_from_actual_runs"


class SocRuleOptimizationPolicy(BaseModel):
    """Versioned tenant-overridable thresholds for advisory recommendations."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = "soc.rule_optimization_policy.v1"
    minimum_labeled_alerts: int = Field(default=20, ge=1)
    minimum_label_coverage: float = Field(default=0.2, ge=0.0, le=1.0)
    high_false_positive_rate: float = Field(default=0.7, ge=0.0, le=1.0)
    high_miss_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    mixed_outcome_floor: float = Field(default=0.2, ge=0.0, le=0.5)
    high_degraded_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    high_volume_alert_count: int = Field(default=100, ge=1)
    stable_outcome_rate: float = Field(default=0.9, ge=0.5, le=1.0)


class SocEffectivenessSnapshot(BaseModel):
    """Read-only product snapshot; recommendations cannot mutate rules or policy."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.effectiveness_snapshot.v1"] = "soc.effectiveness_snapshot.v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    availability: SocOperationsAvailability
    scope: SocEffectivenessScope
    coverage: SocEffectivenessCoverage | None = None
    summary: SocEffectivenessSummary | None = None
    compute: SocComputeEffectiveness | None = None
    rules: list[SocRuleEffectiveness] = Field(default_factory=list, max_length=5000)
    recommendation_policy_version: str
    aggregation_mode: Literal["latest_run_per_alert_sql_v1"] = "latest_run_per_alert_sql_v1"
    error_code: str | None = None
    measurement_notes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_availability(self) -> SocEffectivenessSnapshot:
        measured = self.coverage is not None and self.summary is not None and self.compute is not None
        if self.availability is SocOperationsAvailability.AVAILABLE and not measured:
            raise ValueError("available effectiveness snapshot requires measured sections")
        if self.availability is not SocOperationsAvailability.AVAILABLE and measured:
            raise ValueError("unavailable effectiveness snapshot cannot include partial measured sections")
        return self


__all__ = [
    "SocComputeEffectiveness",
    "SocBehaviorGroupEffectiveness",
    "SocBehaviorGroupEffectivenessAggregate",
    "SocEffectivenessCoverage",
    "SocEffectivenessScope",
    "SocEffectivenessSnapshot",
    "SocEffectivenessSummary",
    "SocRateMetric",
    "SocMemoryEffectiveness",
    "SocMemoryEffectivenessAggregate",
    "SocRuleEffectiveness",
    "SocRuleEffectivenessAggregate",
    "SocRuleEffectivenessDetail",
    "SocRuleEffectivenessSelector",
    "SocRuleImprovementRecommendation",
    "SocRuleOptimizationPolicy",
    "SocRuleRecommendationKind",
    "SocRuleRecommendationPriority",
]
