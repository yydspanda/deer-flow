"""Immutable, label-blind evidence and manifests for a successor corpus experiment."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from soc_agent.contracts.corpus_experiments import CorpusExperimentMember, CorpusMemorySnapshotEntry


class CorpusRebalanceFact(BaseModel):
    """Saved semantic facts only; Runtime verdicts and operational labels are excluded.

    The caller resolves ``pattern_key`` from the trusted Memory feature projection,
    including detector identity and core behavior. The planner additionally binds it
    to the feature profile identity, never to the old navigation group.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    alert_id: str = Field(min_length=1, max_length=128)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str | None = None
    profile_id: str | None = None
    profile_version: str | None = None
    feature_schema_version: str | None = None
    pattern_key: str | None = None
    occurrence_key: str | None = None
    event_time: datetime | None = None
    semantic_status: Literal["applied", "partial", "unchanged", "failed", "skipped", "unknown"] = "unknown"

    @field_validator("event_time")
    @classmethod
    def _utc(cls, value):
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("event time must include a timezone")
        return value.astimezone(UTC)


class CorpusRebalancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    holdout_per_pattern: Literal[1, 2] = 2
    minimum_learning_occurrences: Literal[5] = 5
    missing_facts: Literal["block", "supplementary"] = "block"


class CorpusRebalanceGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str
    pattern_key: str
    profile_id: str
    profile_version: str
    feature_schema_version: str
    member_count: int
    independent_occurrences: int
    learning_occurrences: int
    validation_occurrences: int
    learning_occurrence_keys: list[str]
    validation_occurrence_keys: list[str]
    learning_count: int
    validation_main_count: int
    validation_supplementary_count: int
    warnings: list[str] = Field(default_factory=list)


class CorpusRebalancePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["soc.corpus_rebalance_plan.v1"] = "soc.corpus_rebalance_plan.v1"
    parent_experiment_id: str
    parent_plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_members_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant_id: str
    environment: str
    source_identity: dict[str, Any]
    policy: CorpusRebalancePolicy
    facts: list[CorpusRebalanceFact]
    facts_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_run_ids: list[str]
    inherited_memory_refs: list[CorpusMemorySnapshotEntry] = Field(default_factory=list)
    members: list[CorpusExperimentMember]
    counts: dict[str, int]
    moved_alert_ids: list[str]
    groups: list[CorpusRebalanceGroup]
    missing_facts: dict[str, list[str]]
    blockers: list[str]
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
