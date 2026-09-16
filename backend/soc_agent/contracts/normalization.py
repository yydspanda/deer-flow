"""Source-bound semantic observations, shared by adapters and review merging."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ObjectKind = Literal["host", "user", "container", "process", "file", "network", "http"]
EventKind = Literal["file_detection", "process_execution", "network_access", "web_detection", "configuration_detection", "statistical_detection", "other_detection"]
Scalar = str | int | float | bool
BusinessClueType = Literal["url", "domain", "application", "file_path", "process"]


class SourceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = "L0"
    source_quote: str = Field(min_length=1, max_length=12_000)
    quote_start: int | None = Field(default=None, ge=0)


class NormalizationObjectProposal(SourceQuote):
    id: str = Field(min_length=1, max_length=40)
    kind: ObjectKind
    existing_ref: str | None = Field(default=None, max_length=40)
    attributes: dict[str, Scalar] = Field(min_length=1, max_length=24)


class NormalizationEventProposal(SourceQuote):
    kind: EventKind
    subject_refs: list[str] = Field(default_factory=list, max_length=8)
    name: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=256)
    detector_id: str | None = Field(default=None, max_length=256)
    reported_result: str | None = Field(default=None, max_length=500)
    action: str | None = Field(default=None, max_length=256)


class NormalizationAdditionalFactProposal(SourceQuote):
    name: str = Field(min_length=1, max_length=128)
    value: Scalar
    meaning: str = Field(min_length=1, max_length=500)
    subject_ref: str | None = Field(default=None, max_length=40)
    clue_type: BusinessClueType | None = None


class NormalizationReviewOutput(BaseModel):
    """Published output schema; the reader validates items independently for recovery."""

    model_config = ConfigDict(extra="forbid")

    objects: list[NormalizationObjectProposal] = Field(default_factory=list, max_length=40)
    events: list[NormalizationEventProposal] = Field(default_factory=list, max_length=40)
    additional_facts: list[NormalizationAdditionalFactProposal] = Field(default_factory=list, max_length=40)
    unresolved: list[str] = Field(default_factory=list, max_length=20)


class CanonicalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1, max_length=128)
    evidence_path: str = Field(min_length=1, max_length=512)
    event_scope_id: str = Field(min_length=1, max_length=512)


class DetectionObservationRef(CanonicalObservation):
    kind: EventKind
    subject_refs: list[str] = Field(default_factory=list, max_length=8)
    name: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=256)
    detector_id: str | None = Field(default=None, max_length=256)
    reported_result: str | None = Field(default=None, max_length=500)
    action: str | None = Field(default=None, max_length=256)


class ContextObservationRef(CanonicalObservation):
    kind: Literal["host", "user", "container"]
    attributes: dict[str, Scalar] = Field(min_length=1, max_length=24)


class SupplementaryFactRef(CanonicalObservation):
    name: str = Field(min_length=1, max_length=128)
    value: Scalar
    meaning: str = Field(min_length=1, max_length=500)
    subject_ref: str | None = Field(default=None, max_length=512)
    clue_type: BusinessClueType | None = None


class NormalizationSource(BaseModel):
    source_id: str
    source_path: str
    text: str = Field(max_length=48_000)
    truncated: bool = False


class NormalizationObservationChange(BaseModel):
    target: str
    before: dict | None = None
    after: dict
    source_id: str
    source_path: str
    source_quote: str
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=0)
    reference_validation_status: Literal["verified", "not_checked"] = "verified"
    reason: str
    canonical_status: Literal["applied", "shadow"]
    model_input_status: Literal["present", "not_verified", "not_assessed"] = "not_assessed"
