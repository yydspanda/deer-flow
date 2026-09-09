"""Read-only comparison used by the existing Memory review workflow."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import Verdict


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
    scope_relation: Literal["same", "overlap", "disjoint", "unknown"]
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
    decision_impact: Literal["none"] = "none"
