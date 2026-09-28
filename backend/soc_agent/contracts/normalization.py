"""Source-bound semantic observations, shared by adapters and review merging."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ObjectKind = Literal["host", "user", "container", "process", "file", "network", "http"]
EventKind = Literal["file_detection", "process_execution", "network_access", "web_detection", "configuration_detection", "statistical_detection", "other_detection"]
Scalar = str | int | float | bool
BusinessClueType = Literal["url", "domain", "application", "file_path", "process"]
NetworkBehaviorKind = Literal[
    "http_response_directory_listing",
    "http_response_command_output",
    "http_response_file_content",
    "http_request_directory_traversal",
    "http_request_command_execution",
    "http_request_file_upload",
    "http_response_server_banner",
]
HttpServerProduct = Literal["simplehttp", "apache", "nginx", "iis", "tomcat", "jetty", "envoy", "gunicorn", "uvicorn"]


class NetworkBehaviorDescriptor(BaseModel):
    """Bounded observable meaning; original evidence and prose stay on the fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: NetworkBehaviorKind
    server_product: HttpServerProduct | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def constrain_product_to_banner(self):
        if (self.kind == "http_response_server_banner") != (self.server_product is not None):
            raise ValueError("server_product is required only for an observed response Server banner")
        return self


class SourceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = "L0"
    source_quote: str = Field(min_length=1, max_length=12_000)
    quote_start: int | None = Field(default=None, ge=0)


class DetectionIdentifier(BaseModel):
    """Identifier namespace within one source-bound detection, never a file hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["rule", "signature", "malware", "detector"]
    source_field: str = Field(min_length=1, max_length=256)
    value: str = Field(min_length=1, max_length=256)

    @field_validator("value", mode="before")
    @classmethod
    def stringify_numeric_identifier(cls, value):
        return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


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
    identifiers: list[DetectionIdentifier] = Field(default_factory=list, max_length=16)
    reported_result: str | None = Field(default=None, max_length=500)
    action: str | None = Field(default=None, max_length=256)


class NormalizationAdditionalFactProposal(SourceQuote):
    name: str = Field(min_length=1, max_length=128)
    value: Scalar
    meaning: str = Field(min_length=1, max_length=500)
    subject_ref: str | None = Field(default=None, max_length=40)
    clue_type: BusinessClueType | None = None
    network_behavior: NetworkBehaviorDescriptor | None = Field(default=None, exclude_if=lambda value: value is None)


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
    detector_id: str | None = Field(default=None, max_length=280)
    identifiers: list[DetectionIdentifier] = Field(default_factory=list, max_length=32)
    identity_basis: Literal["legacy", "adapter_declared", "model_typed", "ambiguous"] = "legacy"
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
    # Absent fields must not alter historical alert/request serialization or hashes.
    network_behavior: NetworkBehaviorDescriptor | None = Field(default=None, exclude_if=lambda value: value is None)
    # Populated by the merger only, never accepted from an LLM proposal.
    network_behavior_verification: Literal["source_bound_v1"] | None = Field(default=None, exclude_if=lambda value: value is None)


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
