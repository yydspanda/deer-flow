"""Contracts for deterministic authorized-activity matching."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts.governed_context import (
    AuthorizedActivityBehaviorKind,
    AuthorizedActivityBehaviorSelector,
    AuthorizedActivitySubjectKind,
    AuthorizedActivitySubjectSelector,
    AuthorizedActivityTargetKind,
    AuthorizedActivityTargetSelector,
    GovernedContextFactStatus,
)


class AuthorizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorizationMatchStatus(StrEnum):
    EXACT = "exact"
    PARTIAL = "partial"
    CONFLICT = "conflict"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class AuthorizationDimension(StrEnum):
    TENANT = "tenant"
    ENVIRONMENT = "environment"
    EVENT_TIME = "event_time"
    LIFECYCLE = "lifecycle"
    SOURCE_FRESHNESS = "source_freshness"
    RECURRING_WINDOW = "recurring_window"
    SUBJECT = "subject"
    TARGET = "target"
    BEHAVIOR = "behavior"


class AuthorizationDimensionStatus(StrEnum):
    MATCHED = "matched"
    MISSING = "missing"
    OUT_OF_SCOPE = "out_of_scope"
    UNAVAILABLE = "unavailable"


class AuthorizationSourceFreshness(StrEnum):
    FRESH = "fresh"
    NOT_REQUIRED = "not_required"
    STALE = "stale"
    FUTURE = "future"
    UNAVAILABLE = "unavailable"


class AuthorizationQuerySubject(AuthorizationModel):
    kind: AuthorizedActivitySubjectKind
    value: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)
    evidence_path: str = Field(min_length=1, max_length=512)
    role: str | None = Field(default=None, max_length=64)
    semantic_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_selector(self) -> AuthorizationQuerySubject:
        AuthorizedActivitySubjectSelector(
            kind=self.kind,
            value=self.value,
            namespace=self.namespace,
        )
        return self


class AuthorizationQueryTarget(AuthorizationModel):
    kind: AuthorizedActivityTargetKind
    value: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)
    evidence_path: str = Field(min_length=1, max_length=512)
    role: str | None = Field(default=None, max_length=64)
    semantic_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_selector(self) -> AuthorizationQueryTarget:
        AuthorizedActivityTargetSelector(
            kind=self.kind,
            value=self.value,
            namespace=self.namespace,
        )
        return self


class AuthorizationQueryBehavior(AuthorizationModel):
    kind: AuthorizedActivityBehaviorKind
    value: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)
    evidence_path: str = Field(min_length=1, max_length=512)
    semantic_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_selector(self) -> AuthorizationQueryBehavior:
        AuthorizedActivityBehaviorSelector(
            kind=self.kind,
            value=self.value,
            namespace=self.namespace,
        )
        return self


class AuthorizationQueryConflict(AuthorizationModel):
    conflict_type: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)
    evidence_paths: list[str] = Field(default_factory=list, max_length=50)
    blocks_authorization: bool = True


class AuthorizationQuery(AuthorizationModel):
    """Vendor-neutral event-time input to the authorized-activity matcher."""

    schema_version: Literal["soc.authorization_query.v1"] = "soc.authorization_query.v1"
    query_id: str = Field(default_factory=lambda: f"AAQ-{uuid4().hex[:20].upper()}")
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    environment: str | None = Field(default=None, max_length=128)
    event_time: datetime | None = None
    unresolved_event_time: str | None = Field(default=None, max_length=128)
    subjects: list[AuthorizationQuerySubject] = Field(default_factory=list, max_length=200)
    targets: list[AuthorizationQueryTarget] = Field(default_factory=list, max_length=200)
    behaviors: list[AuthorizationQueryBehavior] = Field(default_factory=list, max_length=300)
    conflicts: list[AuthorizationQueryConflict] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("authorization query event_time must be timezone-aware")
        return value


class AuthorizationFactRef(AuthorizationModel):
    fact_id: str = Field(min_length=1, max_length=64)
    fact_version_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    status: GovernedContextFactStatus
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuthorizationSelectorMatch(AuthorizationModel):
    dimension: Literal[
        AuthorizationDimension.SUBJECT,
        AuthorizationDimension.TARGET,
        AuthorizationDimension.BEHAVIOR,
    ]
    fact_kind: str = Field(min_length=1, max_length=128)
    fact_value: str = Field(min_length=1, max_length=512)
    query_kind: str = Field(min_length=1, max_length=128)
    query_value: str = Field(min_length=1, max_length=512)
    evidence_path: str = Field(min_length=1, max_length=512)


class AuthorizationDimensionEvaluation(AuthorizationModel):
    dimension: AuthorizationDimension
    status: AuthorizationDimensionStatus
    matched_selectors: list[AuthorizationSelectorMatch] = Field(default_factory=list, max_length=100)
    required_selector_groups: list[str] = Field(default_factory=list, max_length=100)
    missing_selector_groups: list[str] = Field(default_factory=list, max_length=100)
    out_of_scope_selector_groups: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)


class AuthorizationFactEvaluation(AuthorizationModel):
    fact_ref: AuthorizationFactRef
    status: AuthorizationMatchStatus
    source_freshness: AuthorizationSourceFreshness
    dimension_results: list[AuthorizationDimensionEvaluation] = Field(default_factory=list, max_length=20)
    matched_dimensions: list[AuthorizationDimension] = Field(default_factory=list, max_length=20)
    missing_dimensions: list[AuthorizationDimension] = Field(default_factory=list, max_length=20)
    out_of_scope_dimensions: list[AuthorizationDimension] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=200)
    matched_selector_count: int = Field(default=0, ge=0)
    required_selector_group_count: int = Field(default=0, ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class AuthorizationMatchResult(AuthorizationModel):
    """Explainable deterministic result; it does not mutate detection truth."""

    schema_version: Literal["soc.authorization_match_result.v1"] = "soc.authorization_match_result.v1"
    query_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    status: AuthorizationMatchStatus
    event_time: datetime | None = None
    policy_version: Literal["soc.authorization_match.v1"] = "soc.authorization_match.v1"
    matched_fact_refs: list[AuthorizationFactRef] = Field(default_factory=list, max_length=100)
    candidate_fact_refs: list[AuthorizationFactRef] = Field(default_factory=list, max_length=100)
    matched_dimensions: list[AuthorizationDimension] = Field(default_factory=list, max_length=20)
    missing_dimensions: list[AuthorizationDimension] = Field(default_factory=list, max_length=20)
    out_of_scope_dimensions: list[AuthorizationDimension] = Field(default_factory=list, max_length=20)
    source_freshness: list[AuthorizationSourceFreshness] = Field(default_factory=list, max_length=100)
    evidence_refs: list[str] = Field(default_factory=list, max_length=300)
    fact_evaluations: list[AuthorizationFactEvaluation] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    shadow_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_exact_result(self) -> AuthorizationMatchResult:
        if self.status is AuthorizationMatchStatus.EXACT and not self.matched_fact_refs:
            raise ValueError("exact authorization result requires at least one matched fact")
        if self.status is not AuthorizationMatchStatus.EXACT and self.matched_fact_refs:
            raise ValueError("only exact authorization results may expose matched_fact_refs")
        return self


__all__ = [
    "AuthorizationDimension",
    "AuthorizationDimensionEvaluation",
    "AuthorizationDimensionStatus",
    "AuthorizationFactEvaluation",
    "AuthorizationFactRef",
    "AuthorizationMatchResult",
    "AuthorizationMatchStatus",
    "AuthorizationQuery",
    "AuthorizationQueryBehavior",
    "AuthorizationQueryConflict",
    "AuthorizationQuerySubject",
    "AuthorizationQueryTarget",
    "AuthorizationSelectorMatch",
    "AuthorizationSourceFreshness",
]
