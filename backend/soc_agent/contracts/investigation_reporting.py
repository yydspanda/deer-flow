"""Read-only reporting contracts for persisted SOC investigation workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soc_agent.contracts.enrichment import (
    SocEnrichmentExecutionStatus,
    SocEnrichmentExecutionTrigger,
    SocEnrichmentPlanStatus,
    SocEnrichmentResultMode,
)


class SocInvestigationMeasurementStatus(StrEnum):
    """Whether a telemetry dimension has real source measurements."""

    MEASURED = "measured"
    PARTIAL = "partial"
    NOT_MEASURED = "not_measured"


class SocInvestigationRouteTelemetry(BaseModel):
    """Secret-free execution metrics for one exact route/adapter binding."""

    model_config = ConfigDict(extra="forbid")

    route: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    adapter_id: str = Field(min_length=1, max_length=256)
    planned_action_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    provider_invocation_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    not_found_count: int = Field(ge=0)
    final_failure_count: int = Field(ge=0)
    provider_failure_attempt_count: int = Field(ge=0)
    contract_failure_attempt_count: int = Field(ge=0)
    denied_attempt_count: int = Field(ge=0)
    interrupted_attempt_count: int = Field(ge=0)
    evidence_reference_count: int = Field(ge=0)
    persisted_evidence_count: int = Field(ge=0)
    missing_evidence_count: int = Field(ge=0)
    real_result_count: int = Field(ge=0)
    mock_result_count: int = Field(ge=0)
    attempt_latency_sample_count: int = Field(ge=0)
    attempt_latency_ms_p50: float | None = Field(default=None, ge=0.0)
    attempt_latency_ms_p95: float | None = Field(default=None, ge=0.0)
    attempt_latency_ms_max: float | None = Field(default=None, ge=0.0)
    evidence_coverage_ratio: float = Field(ge=0.0, le=1.0)


class SocInvestigationShadowReport(BaseModel):
    """Recomputable telemetry projection over one durable D3 execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.investigation_shadow_report.v1"] = "soc.investigation_shadow_report.v1"
    report_id: str = Field(min_length=1, max_length=64)
    projection_version: Literal["soc-investigation-shadow-report-v1"] = "soc-investigation-shadow-report-v1"
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=256)
    trigger: SocEnrichmentExecutionTrigger
    execution_status: SocEnrichmentExecutionStatus
    plan_id: str = Field(min_length=1, max_length=64)
    plan_status: SocEnrichmentPlanStatus
    policy_version: str = Field(min_length=1, max_length=128)
    required_result_mode: SocEnrichmentResultMode
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_updated_at: datetime
    execution_duration_ms: float | None = Field(default=None, ge=0.0)
    planned_action_count: int = Field(ge=0)
    skipped_candidate_count: int = Field(ge=0)
    skip_reason_counts: dict[str, int] = Field(default_factory=dict)
    attempt_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    provider_invocation_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    not_found_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    evidence_reference_count: int = Field(ge=0)
    persisted_evidence_count: int = Field(ge=0)
    missing_evidence_count: int = Field(ge=0)
    evidence_coverage_ratio: float = Field(ge=0.0, le=1.0)
    attempt_latency_sample_count: int = Field(ge=0)
    attempt_latency_ms_p50: float | None = Field(default=None, ge=0.0)
    attempt_latency_ms_p95: float | None = Field(default=None, ge=0.0)
    attempt_latency_ms_max: float | None = Field(default=None, ge=0.0)
    routes: list[SocInvestigationRouteTelemetry] = Field(default_factory=list, max_length=50)
    cost_measurement_status: Literal[SocInvestigationMeasurementStatus.NOT_MEASURED] = SocInvestigationMeasurementStatus.NOT_MEASURED
    cost_amount: None = None
    measurement_gaps: list[str] = Field(default_factory=list, max_length=50)
    shadow_only: Literal[True] = True
    decision_impact: Literal["none"] = "none"
    base_run_mutated: Literal[False] = False
    auto_close_allowed: Literal[False] = False
    confirmed_memory_write_allowed: Literal[False] = False
    high_risk_actions_allowed: Literal[False] = False

    @model_validator(mode="after")
    def counts_are_consistent(self) -> SocInvestigationShadowReport:
        if self.missing_evidence_count > self.evidence_reference_count:
            raise ValueError("missing evidence cannot exceed referenced evidence")
        if self.persisted_evidence_count + self.missing_evidence_count != self.evidence_reference_count:
            raise ValueError("persisted and missing evidence must partition evidence references")
        return self


class SocInvestigationAddendumItem(BaseModel):
    """Bounded analyst projection for one planned read-only lookup."""

    model_config = ConfigDict(extra="forbid")

    plan_action_id: str = Field(min_length=1, max_length=64)
    route: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    adapter_id: str | None = Field(default=None, max_length=256)
    status: Literal[
        "not_run",
        "running",
        "success",
        "not_found",
        "provider_failed",
        "contract_failed",
        "denied",
        "interrupted",
    ]
    attempt_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    provider_invoked: bool
    result_mode: SocEnrichmentResultMode | None = None
    evidence_id: str | None = Field(default=None, max_length=64)
    evidence_available: bool = False
    evidence_summary: str | None = Field(default=None, max_length=1000)
    latest_attempt_latency_ms: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def evidence_fields_are_consistent(self) -> SocInvestigationAddendumItem:
        if self.evidence_available and self.evidence_id is None:
            raise ValueError("available addendum evidence requires an evidence id")
        if not self.evidence_available and self.evidence_summary is not None:
            raise ValueError("missing addendum evidence cannot carry an evidence summary")
        return self


class SocInvestigationAddendum(BaseModel):
    """Analyst-visible summary that never replaces the base Runtime conclusion."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.investigation_addendum.v1"] = "soc.investigation_addendum.v1"
    addendum_id: str = Field(min_length=1, max_length=64)
    projection_version: Literal["soc-investigation-addendum-v1"] = "soc-investigation-addendum-v1"
    source_report_id: str = Field(min_length=1, max_length=64)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    trigger: SocEnrichmentExecutionTrigger
    execution_status: SocEnrichmentExecutionStatus
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_updated_at: datetime
    base_runtime_status: str = Field(min_length=1, max_length=64)
    base_runtime_verdict: str | None = Field(default=None, max_length=64)
    summary: str = Field(min_length=1, max_length=2000)
    items: list[SocInvestigationAddendumItem] = Field(default_factory=list, max_length=50)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    evidence_coverage_ratio: float = Field(ge=0.0, le=1.0)
    analyst_attention_required: bool
    measurement_gaps: list[str] = Field(default_factory=list, max_length=50)
    addendum_kind: Literal["read_only_execution_summary"] = "read_only_execution_summary"
    reasoning_status: Literal["not_requested"] = "not_requested"
    new_conclusion_produced: Literal[False] = False
    grounding_status: Literal["deterministic_evidence_reference_check"] = "deterministic_evidence_reference_check"
    projection_persisted: Literal[False] = False
    durable_sources_persisted: Literal[True] = True
    shadow_only: Literal[True] = True
    decision_impact: Literal["none"] = "none"
    base_run_mutated: Literal[False] = False
    automation_allowed: Literal[False] = False
    auto_close_allowed: Literal[False] = False
    confirmed_memory_write_allowed: Literal[False] = False
    high_risk_actions_allowed: Literal[False] = False


__all__ = [
    "SocInvestigationAddendum",
    "SocInvestigationAddendumItem",
    "SocInvestigationMeasurementStatus",
    "SocInvestigationRouteTelemetry",
    "SocInvestigationShadowReport",
]
