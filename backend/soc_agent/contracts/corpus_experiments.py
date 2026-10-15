"""DEV experiment contracts; round identity never becomes a Memory matching facet."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.contracts.processing_jobs import SocProcessingJob
from soc_agent.contracts.schemas import SocMemoryCandidate

Batch = Literal["learning", "validation"]
RoundState = Literal["prepared", "running", "paused", "blocked", "completed"]
BoundedCorpusId = Annotated[str, Field(min_length=1, max_length=128)]


class CorpusExperimentRebalance(BaseModel):
    """Explicit references to preserved learning; never copies review authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["soc.corpus_experiment_rebalance.v1"] = "soc.corpus_experiment_rebalance.v1"
    parent_experiment_id: str = Field(min_length=1, max_length=64)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    inherited_memory_ids: list[BoundedCorpusId] = Field(default_factory=list, max_length=5000)
    learning_run_imports: dict[BoundedCorpusId, BoundedCorpusId] = Field(default_factory=dict, max_length=20000)
    learning_run_hashes: dict[BoundedCorpusId, str] = Field(default_factory=dict, max_length=20000)
    facts_experiment_id: str | None = Field(default=None, min_length=1, max_length=64)
    fact_hashes: dict[BoundedCorpusId, str] = Field(default_factory=dict, max_length=20000)
    creation_request_hash: str | None = None

    @field_validator("inherited_memory_ids")
    @classmethod
    def _distinct_memories(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("inherited memory IDs must be distinct")
        return value


class CorpusExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_experiment.v1"] = "soc.corpus_experiment.v1"
    experiment_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    source_identity: dict[str, Any]
    member_count: int = Field(default=0, ge=0)
    created_by: str
    created_at: datetime
    rebalance: CorpusExperimentRebalance | None = None
    fact_source_experiment_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _one_origin(self):
        if self.rebalance is not None and self.fact_source_experiment_id is not None:
            raise ValueError("a fact preparation experiment cannot also inherit learning")
        return self


class CorpusExperimentMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_index: int = Field(ge=0)
    group_id: str = Field(min_length=1, max_length=512)
    sequence_number: int = Field(ge=0)
    position_in_group: int = Field(ge=0)
    batch: Batch
    validation_tier: Literal["main", "supplementary"] | None = None
    event_time: datetime | None = None
    rule_code: str | None = None
    detection_key: str | None = None
    reason: str

    @field_validator("event_time")
    @classmethod
    def _utc(cls, value):
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("event time must include a timezone")
        return value.astimezone(UTC)


class CorpusRoundSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch: Batch
    scope: Literal["reuse", "explore", "all"] = "reuse"
    group_ids: list[str] = Field(default_factory=list, max_length=500)
    rule_codes: list[str] = Field(default_factory=list, max_length=100)
    alert_ids: list[str] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def _valid_scope(self):
        if self.batch == "learning" and self.scope != "reuse":
            raise ValueError("learning only accepts the reuse scope")
        return self


class CorpusMemorySnapshotEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    version: int = Field(ge=1)
    content_hash: str
    facets_hash: str
    record_hash: str


class CorpusRetestProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_round_id: str = Field(min_length=1, max_length=64)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_results_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_identity: dict[str, Any]


class CorpusRetestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["soc.corpus_retest_plan.v1"] = "soc.corpus_retest_plan.v1"
    experiment_id: str = Field(min_length=1, max_length=64)
    provenance: CorpusRetestProvenance
    selection: CorpusRoundSelection
    previous_run_ids: dict[str, str | None]
    validation_tiers: dict[str, Literal["main", "supplementary"] | None]
    filters: dict[str, Any]
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CorpusRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.corpus_round.v1"] = "soc.corpus_round.v1"
    round_id: str = Field(min_length=1, max_length=64)
    experiment_id: str = Field(min_length=1, max_length=64)
    selection: CorpusRoundSelection
    purpose: Literal["memory", "full_flow", "facts"] = "memory"
    options: SocAnalysisExecutionOptions = Field(default_factory=SocAnalysisExecutionOptions)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_snapshot: dict[str, Any]
    creation_command_hash: str | None = None
    memory_snapshot: list[CorpusMemorySnapshotEntry] = Field(default_factory=list)
    governance_hash: str | None = None
    memory_mode: Literal["snapshot", "none"] = "snapshot"
    parent_round_id: str | None = None
    retest_provenance: CorpusRetestProvenance | None = None
    state: RoundState = "prepared"
    state_reason: str | None = None
    version: int = Field(default=1, ge=1)
    concurrency: int = Field(default=3, ge=1, le=16)
    execution_limit: int = Field(default=5, ge=1)
    created_by: str
    created_at: datetime
    updated_at: datetime | None = None
    state_history: list[dict[str, Any]] = Field(default_factory=list)
    dispatch_alert_ids: list[str] | None = None
    superseded_by_round_id: str | None = Field(default=None, min_length=1, max_length=64)
    learning_generation: str | None = Field(default=None, min_length=1, max_length=64, exclude_if=lambda value: value is None)
    pattern_accumulation_scope: MemoryPatternAccumulationScope | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _facts_do_not_decide(self):
        if self.purpose == "facts" and (self.memory_mode != "none" or self.memory_snapshot or self.options.normalization_review_mode != "apply" or self.options.tenant_policy_enabled):
            raise ValueError("fact preparation cannot retrieve Memory or apply enterprise policy and requires semantic apply")
        return self


class CorpusRoundCreateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=64)
    selection: CorpusRoundSelection
    purpose: Literal["memory", "full_flow", "facts"] = "memory"
    options: SocAnalysisExecutionOptions = Field(default_factory=SocAnalysisExecutionOptions)
    memory_mode: Literal["snapshot", "none"] = "snapshot"
    parent_round_id: str | None = None
    retest_provenance: CorpusRetestProvenance | None = None
    execution_limit: int = Field(default=5, ge=1)
    concurrency: int = Field(default=3, ge=1, le=16)

    @model_validator(mode="after")
    def _consistent_purpose(self):
        if self.purpose == "memory" and self.options.tenant_policy_enabled:
            raise ValueError("Memory-only experiments cannot enable enterprise policy; choose full_flow")
        if self.purpose == "facts" and (
            self.memory_mode != "none" or self.options.normalization_review_mode != "apply" or self.options.tenant_policy_enabled or self.options.tenant_policy_advisor_enabled or self.options.tenant_policy_signal_providers_enabled
        ):
            raise ValueError("fact preparation requires semantic apply, no Memory and no enterprise policy")
        return self


class CorpusExecutionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    observation_id: str | None = None
    candidate_id: str | None = None
    pattern_reason: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    snapshot_changed_during_run: bool = False


class CorpusMemberPage(BaseModel):
    total: int
    items: list[CorpusExperimentMember]


class CorpusRoundItem(BaseModel):
    round_id: str
    alert_id: str
    group_id: str
    sequence_number: int
    job_id: str
    job: SocProcessingJob


class CorpusRoundItemPage(BaseModel):
    total: int
    items: list[CorpusRoundItem]


class CorpusRoundTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elapsed_ms: int | None = Field(default=None, ge=0)
    running_ms: int | None = Field(default=None, ge=0)
    paused_ms: int | None = Field(default=None, ge=0)
    terminal_count: int = Field(default=0, ge=0)
    processed_per_minute: float | None = Field(default=None, ge=0)
    estimated_remaining_seconds: int | None = Field(default=None, ge=0)
    estimate_status: Literal["estimated", "insufficient_samples", "not_running", "finished", "unavailable_history"]
    basis: Literal["admitted_budget_running_state_wall_time"] = "admitted_budget_running_state_wall_time"


class CorpusRoundProgress(BaseModel):
    round: CorpusRound
    selected_count: int
    admitted_count: int
    counts: dict[str, int]
    active_count: int
    completed_count: int
    failed_count: int
    timing: CorpusRoundTiming | None = None


class CorpusPrepareCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)


class CorpusRoundBrief(BaseModel):
    round_id: str
    batch: Batch
    state: RoundState
    created_at: datetime


class CorpusCandidatePage(BaseModel):
    total: int
    items: list[SocMemoryCandidate]


class CorpusRoundStartCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_limit: int | None = Field(default=None, ge=1)
    concurrency: int | None = Field(default=None, ge=1, le=16)
    restore_full_scope: bool = Field(default=False, strict=True)


class CorpusRoundRetryCommand(BaseModel):
    """Recover selected failures under the round's immutable behavior snapshot."""

    model_config = ConfigDict(extra="forbid")
    expected_round_version: int = Field(ge=1, strict=True)
    kind: Literal["failed", "semantic_failed"]
    alert_ids: list[BoundedCorpusId] = Field(default_factory=list, max_length=20000)
    concurrency: int | None = Field(default=None, ge=1, le=16, strict=True)


class CorpusPatternBackfillCommand(BaseModel):
    """Explicit saved results, never a request to rerun a model."""

    model_config = ConfigDict(extra="forbid")
    expected_round_version: int = Field(ge=1, strict=True)
    source_runs: dict[BoundedCorpusId, BoundedCorpusId] = Field(min_length=1, max_length=100)


class CorpusSemanticRetry(BaseModel):
    """Server-owned attempt metadata; never a change to the frozen round options."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    generation: int = Field(ge=1, le=2)
    previous_run_id: BoundedCorpusId
    refresh_normalization: Literal[True] = True


class CorpusQuickCommand(BaseModel):
    """Analyst commands have no experiment, round, budget or browser filters."""

    model_config = ConfigDict(extra="forbid")
    batch: Batch
    scope: Literal["all", "reuse", "explore"] = "all"
    action: Literal["start", "pause", "run", "rerun", "retry", "restart_validation", "restart_learning"] = "start"
    alert_id: str | None = Field(default=None, min_length=1, max_length=128)
    job_id: BoundedCorpusId | None = None
    expected_attempt_count: int | None = Field(default=None, ge=1, strict=True)
    restart_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    options: SocAnalysisExecutionOptions = Field(default_factory=SocAnalysisExecutionOptions)

    @model_validator(mode="after")
    def _single(self):
        if (self.action in {"run", "rerun", "retry"}) != bool(self.alert_id):
            raise ValueError("single-alert commands require an alert ID")
        if self.action == "retry":
            if not self.job_id or self.expected_attempt_count is None:
                raise ValueError("retry requires the displayed job and attempt count")
        elif self.job_id is not None or self.expected_attempt_count is not None:
            raise ValueError("job identity is only accepted for retry")
        if self.action in {"restart_validation", "restart_learning"}:
            expected_batch = "learning" if self.action == "restart_learning" else "validation"
            if self.batch != expected_batch or self.scope != "all" or not self.restart_token:
                raise ValueError("restart requires the matching whole batch and its current restart token")
        elif self.restart_token is not None:
            raise ValueError("restart token is only accepted for restarting a batch")
        return self


class CorpusConcurrencyCommand(BaseModel):
    """Resource admission limit; never part of frozen alert behavior."""

    model_config = ConfigDict(extra="forbid")
    max_concurrency: int = Field(ge=1, le=16, strict=True)
