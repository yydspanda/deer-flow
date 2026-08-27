"""Read-model contracts for the operational SOC Memory Center."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .memory_patterns import MemoryPatternDataClass, MemoryPatternObservation
from .schemas import (
    SocMemoryCandidate,
    SocMemoryCandidateStatus,
    SocMemoryRecord,
    SocMemoryRecordStatus,
)


class SocMemoryProfileState(StrEnum):
    CURRENT = "current"
    LEGACY = "legacy"
    UNREGISTERED = "unregistered"


class SocMemoryPatternLifecycleState(StrEnum):
    COLLECTING = "collecting"
    CANDIDATE_PENDING = "candidate_pending"
    CANDIDATE_INTERMEDIATE = "candidate_intermediate"
    MEMORY_INACTIVE = "memory_inactive"
    MEMORY_ACTIVE = "memory_active"
    TERMINAL_HISTORY = "terminal_history"


class SocMemoryPatternStageFilter(StrEnum):
    COLLECTING = "collecting"
    AWAITING_REVIEW = "awaiting_review"
    MATERIALIZING = "materializing"
    PERSISTED = "persisted"
    TERMINAL = "terminal"


class SocMemoryFutureUseState(StrEnum):
    NOT_READY = "not_ready"
    PAUSED = "paused"
    REFERENCE_ONLY = "reference_only"
    EXACT_MATCH_DECISION = "exact_match_decision"
    BLOCKED = "blocked"


class MemoryPatternLineageStats(BaseModel):
    """Cross-window aggregate for one stable repeated-behavior lineage."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_pattern_lineage_stats.v1"] = "soc.memory_pattern_lineage_stats.v1"
    lineage_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant_id: str
    environment: str
    data_class: MemoryPatternDataClass
    profile_id: str
    profile_version: str
    feature_schema_version: str
    pattern_dimension: str
    pattern_value: str
    pattern_label: str
    support_count: int = Field(ge=1)
    distinct_source_count: int = Field(ge=1)
    aggregation_window_count: int = Field(ge=1)
    first_observed_at: datetime
    last_observed_at: datetime
    first_window_start: datetime
    last_window_end: datetime


class MemoryPatternLineageStatsPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryPatternLineageStats]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class MemoryCenterProfileInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    profile_version: str
    feature_schema_version: str
    pattern_count: int = Field(ge=0)
    aggregation_window_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)


class MemoryCenterInventory(BaseModel):
    """Exact persisted counts; current-vs-legacy is added by the Core Service."""

    model_config = ConfigDict(extra="forbid")

    pattern_count: int = Field(ge=0)
    aggregation_window_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    candidate_status_counts: dict[str, int] = Field(default_factory=dict)
    record_status_counts: dict[str, int] = Field(default_factory=dict)
    retrieval_enabled_record_count: int = Field(ge=0)
    profile_inventory: list[MemoryCenterProfileInventory] = Field(default_factory=list)


class SocMemoryCenterCandidateRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    status: SocMemoryCandidateStatus
    summary: str
    support_count_at_creation: int = Field(ge=0)
    distinct_source_count_at_creation: int = Field(ge=0)
    superseded_by_candidate_id: str | None = None


class SocMemoryCenterRecordRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    version: int = Field(ge=1)
    status: SocMemoryRecordStatus
    summary: str
    retrieval_enabled: bool
    decision_directive_ready: bool = False
    retrieval_valid_until: datetime | None = None
    retrieval_review_due_at: datetime | None = None


class SocMemoryCenterPatternSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_center_pattern.v1"] = "soc.memory_center_pattern.v1"
    lineage_key: str
    tenant_id: str
    environment: str
    data_class: MemoryPatternDataClass
    pattern_dimension: str
    pattern_value: str
    pattern_label: str
    profile_id: str
    profile_version: str
    feature_schema_version: str
    current_profile_version: str | None = None
    current_feature_schema_version: str | None = None
    profile_state: SocMemoryProfileState
    lifecycle_state: SocMemoryPatternLifecycleState
    future_use_state: SocMemoryFutureUseState
    attention_reasons: list[str] = Field(default_factory=list)
    support_count: int = Field(ge=1)
    distinct_source_count: int = Field(ge=1)
    aggregation_window_count: int = Field(ge=1)
    candidate_snapshot_count: int = Field(ge=0)
    reinforcement_count: int = Field(ge=0)
    first_observed_at: datetime
    last_observed_at: datetime
    first_window_start: datetime
    last_window_end: datetime
    candidate: SocMemoryCenterCandidateRef | None = None
    memory_record: SocMemoryCenterRecordRef | None = None


class SocMemoryCenterMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_count: int = Field(ge=0)
    aggregation_window_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    pending_candidate_count: int = Field(ge=0)
    confirmed_memory_count: int = Field(ge=0)
    retrieval_enabled_memory_count: int = Field(ge=0)
    superseded_candidate_count: int = Field(ge=0)
    legacy_profile_pattern_count: int = Field(ge=0)
    unregistered_profile_pattern_count: int = Field(ge=0)


class SocMemoryCenterOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_center_overview.v1"] = "soc.memory_center_overview.v1"
    metrics: SocMemoryCenterMetrics
    items: list[SocMemoryCenterPatternSummary]
    terminal_history_count: int = Field(ge=0)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SocMemoryCenterPatternDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_center_pattern_detail.v1"] = "soc.memory_center_pattern_detail.v1"
    pattern: SocMemoryCenterPatternSummary
    candidates: list[SocMemoryCandidate] = Field(default_factory=list)
    memory_records: list[SocMemoryRecord] = Field(default_factory=list)
    observations: list[MemoryPatternObservation]
    observation_total: int = Field(ge=0)
    observation_limit: int = Field(ge=1)
    observation_offset: int = Field(ge=0)
    suggested_successor_candidate_id: str | None = None


__all__ = [
    "MemoryCenterInventory",
    "MemoryCenterProfileInventory",
    "MemoryPatternLineageStats",
    "MemoryPatternLineageStatsPage",
    "SocMemoryCenterCandidateRef",
    "SocMemoryCenterPatternDetail",
    "SocMemoryCenterPatternSummary",
    "SocMemoryCenterMetrics",
    "SocMemoryCenterOverview",
    "SocMemoryCenterRecordRef",
    "SocMemoryFutureUseState",
    "SocMemoryPatternLifecycleState",
    "SocMemoryPatternStageFilter",
    "SocMemoryProfileState",
]
