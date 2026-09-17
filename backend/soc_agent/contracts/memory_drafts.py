"""Shared editable drafts are reviewer work in progress, never retrieval records."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.contracts.schemas import SocMemoryBusinessLessonDraft, Verdict

DraftText = Annotated[str, Field(max_length=48000)]
FacetValue = Annotated[str, Field(min_length=1, max_length=2000)]


class MemoryDraftContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_verdict: Verdict | None = None
    reviewer_context: str = Field(default="", max_length=4000)
    apply_to_future_matches: bool = False
    selected_behavior_components: list[FacetValue] | None = Field(default=None, min_length=1, max_length=100)
    promoted_facet_values: dict[Annotated[str, Field(max_length=128)], Annotated[list[FacetValue], Field(min_length=1, max_length=100)]] = Field(default_factory=dict, max_length=20)
    detection_scenario: DraftText = ""
    observed_event: DraftText = ""
    conclusion: DraftText = ""
    business_rationale: DraftText = ""
    generalization_boundaries: DraftText = ""
    invalidation_conditions: DraftText = ""
    handling_guidance: DraftText = ""


class MemoryDraftSaveCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    candidate_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: MemoryDraftContent


class MemoryDraftGenerateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)
    candidate_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    regenerate: bool = False


class MemoryDraftGenerateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[MemoryDraftGenerateCommand] = Field(min_length=1, max_length=50)


class MemoryWorkingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.memory_working_draft.v1"] = "soc.memory_working_draft.v1"
    candidate_id: str
    candidate_revision: str
    version: int = Field(ge=1)
    content: MemoryDraftContent
    updated_by: str
    updated_at: datetime
    last_generation: SocMemoryBusinessLessonDraft | None = None
    generation_job_id: str | None = None
    authority: Literal["draft_only"] = "draft_only"


class MemoryWorkingDraftView(BaseModel):
    candidate_id: str
    candidate_revision: str
    editable: bool
    stale: bool = False
    draft: MemoryWorkingDraft | None = None
