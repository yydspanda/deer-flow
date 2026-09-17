"""Read-only comparison used by the existing Memory review workflow."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import SocMemoryApplicabilitySpec, Verdict


class MemoryScopeDifference(BaseModel):
    facet: str
    candidate_values: list[str] = Field(default_factory=list)
    memory_values: list[str] = Field(default_factory=list)


class RelatedGovernedMemory(BaseModel):
    memory_id: str
    version: int
    summary: str
    conclusion: str
    reviewed_verdict: Verdict | None = None
    scope_relation: Literal["same", "strict_subset", "strict_superset", "overlap", "disjoint", "unknown"]
    conclusion_relation: Literal["agrees", "differs", "undetermined"]
    retrieved_in_source_run: bool = False
    retrieval_enabled: bool
    directive_enabled: bool
    differences: list[MemoryScopeDifference] = Field(default_factory=list)


class MemoryGovernancePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_governance_preview.v1"] = "soc.memory_governance_preview.v1"
    candidate_id: str
    reviewer_verdict: Verdict | None = None
    recommendation: Literal["new", "reinforce", "revise", "distinguish", "inspect"]
    explanation: str
    related_memories: list[RelatedGovernedMemory] = Field(default_factory=list)
    related_count: int = 0
    source_reason: str | None = None
    source_memory_refs: list[str] = Field(default_factory=list)
    sample_coverage: dict[str, int] = Field(default_factory=dict)
    reviewed_applicability: SocMemoryApplicabilitySpec | None = None
    decision_impact: Literal["none"] = "none"


class MemoryScopeRefinementCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=64)
    expected_updated_at: datetime
    promoted_facet_values: dict[str, list[str]] = Field(default_factory=dict, max_length=20)
    selected_behavior_components: list[str] | None = Field(default=None, min_length=1, max_length=100)


class MemoryScopeBoundaryReleaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)
    exception_memory_id: str = Field(min_length=1, max_length=64)
    expected_exception_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class MemoryScopeException(BaseModel):
    memory_id: str
    version: int
    summary: str
    active: bool
    released: bool


class MemoryScopeBoundaries(BaseModel):
    memory_id: str
    version: int
    active: bool
    exceptions: list[MemoryScopeException] = Field(default_factory=list)
