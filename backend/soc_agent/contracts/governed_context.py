"""Typed contracts for governed operational context facts."""

from __future__ import annotations

from datetime import UTC, datetime, time
from enum import StrEnum
from ipaddress import ip_address, ip_network
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts.common import ActorContext


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GovernedContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


GovernedContextEvidenceRef = Annotated[str, Field(min_length=1, max_length=512)]


class GovernedContextFactType(StrEnum):
    AUTHORIZED_ACTIVITY = "authorized_activity"


class GovernedContextFactStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REVOKED = "revoked"


class GovernedContextSourceType(StrEnum):
    AUTHORITATIVE_SYSTEM = "authoritative_system"
    ADAPTER_SYNC = "adapter_sync"
    TICKET = "ticket"
    ANALYST_CONFIRMATION = "analyst_confirmation"
    IMPORTED_DOCUMENT = "imported_document"


class GovernedContextSource(GovernedContextModel):
    """Traceable source snapshot for one governed fact version."""

    source_type: GovernedContextSourceType
    source_ref: str = Field(min_length=1, max_length=512)
    source_version: str | None = Field(default=None, max_length=256)
    observed_at: datetime = Field(default_factory=_utc_now)
    fresh_until: datetime | None = None
    authoritative: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> GovernedContextSource:
        _require_aware(self.observed_at, "source.observed_at")
        if self.fresh_until is not None:
            _require_aware(self.fresh_until, "source.fresh_until")
            if self.fresh_until <= self.observed_at:
                raise ValueError("source.fresh_until must be after source.observed_at")
        if self.source_type in {
            GovernedContextSourceType.AUTHORITATIVE_SYSTEM,
            GovernedContextSourceType.ADAPTER_SYNC,
        }:
            if not self.authoritative:
                raise ValueError("authoritative system and adapter sources must set authoritative=true")
            if not self.source_version:
                raise ValueError("authoritative system and adapter sources require source_version")
            if self.fresh_until is None:
                raise ValueError("authoritative system and adapter sources require fresh_until")
        return self


class AuthorizedActivityType(StrEnum):
    VULNERABILITY_SCAN = "vulnerability_scan"
    PENETRATION_TEST = "penetration_test"
    MAINTENANCE = "maintenance"
    AUTOMATION = "automation"
    SERVICE_TRAFFIC = "service_traffic"
    SECURITY_EXERCISE = "security_exercise"
    CUSTOM = "custom"


class AuthorizedActivitySubjectKind(StrEnum):
    ASSET_ID = "asset_id"
    SERVICE_ID = "service_id"
    ACCOUNT_ID = "account_id"
    AGENT_ID = "agent_id"
    IP = "ip"
    CIDR = "cidr"
    SECURITY_TAG = "security_tag"
    CERTIFICATE = "certificate"


class AuthorizedActivityTargetKind(StrEnum):
    ASSET_ID = "asset_id"
    SERVICE_ID = "service_id"
    APPLICATION = "application"
    DOMAIN = "domain"
    IP = "ip"
    CIDR = "cidr"
    SECURITY_TAG = "security_tag"


class AuthorizedActivityBehaviorKind(StrEnum):
    SCENARIO = "scenario"
    BEHAVIOR_SIGNATURE = "behavior_signature"
    PROCESS = "process"
    SERVICE = "service"
    PROTOCOL = "protocol"
    DETECTION_ALIAS = "detection_alias"
    TECHNIQUE = "technique"


class AuthorizedActivitySubjectSelector(GovernedContextModel):
    kind: AuthorizedActivitySubjectKind
    value: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_network_value(self) -> AuthorizedActivitySubjectSelector:
        if self.kind is AuthorizedActivitySubjectKind.IP:
            ip_address(self.value)
        elif self.kind is AuthorizedActivitySubjectKind.CIDR:
            ip_network(self.value, strict=False)
        return self


class AuthorizedActivityTargetSelector(GovernedContextModel):
    kind: AuthorizedActivityTargetKind
    value: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_network_value(self) -> AuthorizedActivityTargetSelector:
        if self.kind is AuthorizedActivityTargetKind.IP:
            ip_address(self.value)
        elif self.kind is AuthorizedActivityTargetKind.CIDR:
            ip_network(self.value, strict=False)
        return self


class AuthorizedActivityBehaviorSelector(GovernedContextModel):
    kind: AuthorizedActivityBehaviorKind
    value: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)


class AuthorizedActivityRecurringWindow(GovernedContextModel):
    """A local recurring window interpreted later by the AA-01 matcher."""

    timezone: str = Field(min_length=1, max_length=128)
    days_of_week: list[int] = Field(min_length=1, max_length=7)
    start_time: time
    end_time: time

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @field_validator("days_of_week")
    @classmethod
    def validate_days_of_week(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("days_of_week values must be in range 0..6")
        if len(set(value)) != len(value):
            raise ValueError("days_of_week must not contain duplicates")
        return sorted(value)

    @model_validator(mode="after")
    def validate_window(self) -> AuthorizedActivityRecurringWindow:
        if self.start_time.tzinfo is not None or self.end_time.tzinfo is not None:
            raise ValueError("recurring window times must be local naive times; use timezone for IANA zone")
        if self.start_time == self.end_time:
            raise ValueError("recurring window start_time and end_time must differ")
        return self


class AuthorizedActivityPayload(GovernedContextModel):
    """Typed scope for an authorized activity; matching is implemented in AA-01."""

    payload_type: Literal["authorized_activity"] = "authorized_activity"
    activity_type: AuthorizedActivityType
    custom_activity_type: str | None = Field(default=None, min_length=1, max_length=128)
    subject_scope: list[AuthorizedActivitySubjectSelector] = Field(min_length=1)
    target_scope: list[AuthorizedActivityTargetSelector] = Field(min_length=1)
    behavior_scope: list[AuthorizedActivityBehaviorSelector] = Field(min_length=1)
    recurring_windows: list[AuthorizedActivityRecurringWindow] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_activity_type(self) -> AuthorizedActivityPayload:
        if self.activity_type is AuthorizedActivityType.CUSTOM and not self.custom_activity_type:
            raise ValueError("custom_activity_type is required when activity_type=custom")
        if self.activity_type is not AuthorizedActivityType.CUSTOM and self.custom_activity_type is not None:
            raise ValueError("custom_activity_type is only allowed when activity_type=custom")
        for name in ("subject_scope", "target_scope", "behavior_scope"):
            selectors = getattr(self, name)
            keys = {(item.kind.value, item.value, item.namespace) for item in selectors}
            if len(keys) != len(selectors):
                raise ValueError(f"{name} must not contain duplicate selectors")
        return self


GovernedContextPayload = AuthorizedActivityPayload


class GovernedContextFactCreateCommand(GovernedContextModel):
    schema_version: Literal["soc.governed_context_fact_create.v1"] = "soc.governed_context_fact_create.v1"
    fact_type: GovernedContextFactType = GovernedContextFactType.AUTHORIZED_ACTIVITY
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    valid_from: datetime
    valid_until: datetime
    source: GovernedContextSource
    owner_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[GovernedContextEvidenceRef] = Field(default_factory=list, max_length=100)
    payload: GovernedContextPayload

    @model_validator(mode="after")
    def validate_command(self) -> GovernedContextFactCreateCommand:
        _validate_fact_type(self.fact_type, self.payload)
        _validate_validity(self.valid_from, self.valid_until)
        return self


class GovernedContextFactTransitionCommand(GovernedContextModel):
    schema_version: Literal["soc.governed_context_fact_transition.v1"] = "soc.governed_context_fact_transition.v1"
    fact_id: str = Field(min_length=1, max_length=64)
    expected_latest_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class GovernedContextFactRevisionCommand(GovernedContextModel):
    schema_version: Literal["soc.governed_context_fact_revision.v1"] = "soc.governed_context_fact_revision.v1"
    fact_id: str = Field(min_length=1, max_length=64)
    expected_latest_version: int = Field(ge=1)
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    valid_from: datetime
    valid_until: datetime
    source: GovernedContextSource
    owner_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[GovernedContextEvidenceRef] = Field(default_factory=list, max_length=100)
    payload: GovernedContextPayload

    @model_validator(mode="after")
    def validate_command(self) -> GovernedContextFactRevisionCommand:
        _validate_validity(self.valid_from, self.valid_until)
        return self


class GovernedContextFactQuery(GovernedContextModel):
    schema_version: Literal["soc.governed_context_fact_query.v1"] = "soc.governed_context_fact_query.v1"
    fact_id: str | None = None
    fact_type: GovernedContextFactType | None = None
    status: GovernedContextFactStatus | None = None
    tenant_id: str | None = None
    environment: str | None = None
    valid_at: datetime | None = None
    latest_only: bool = True
    limit: int = Field(default=50, ge=1, le=500)

    @field_validator("valid_at")
    @classmethod
    def validate_valid_at(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _require_aware(value, "valid_at")
        return value


class GovernedContextFact(GovernedContextModel):
    """One immutable version in a governed fact lifecycle."""

    schema_version: Literal["soc.governed_context_fact.v1"] = "soc.governed_context_fact.v1"
    fact_version_id: str = Field(default_factory=lambda: f"GCFV-{uuid4().hex[:20].upper()}")
    fact_id: str = Field(default_factory=lambda: f"GCF-{uuid4().hex[:20].upper()}")
    version: int = Field(default=1, ge=1)
    fact_type: GovernedContextFactType
    status: GovernedContextFactStatus = GovernedContextFactStatus.PROPOSED
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    valid_from: datetime
    valid_until: datetime
    source: GovernedContextSource
    owner_id: str = Field(min_length=1, max_length=128)
    created_by: ActorContext
    changed_by: ActorContext
    reviewed_by: ActorContext | None = None
    status_reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[GovernedContextEvidenceRef] = Field(default_factory=list, max_length=100)
    payload: GovernedContextPayload
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_version_id: str | None = Field(default=None, max_length=64)
    is_latest: bool = True
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    state_changed_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_fact(self) -> GovernedContextFact:
        _validate_fact_type(self.fact_type, self.payload)
        _validate_validity(self.valid_from, self.valid_until)
        for name in ("created_at", "updated_at", "state_changed_at"):
            _require_aware(getattr(self, name), name)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if not self.created_at <= self.state_changed_at <= self.updated_at:
            raise ValueError("state_changed_at must be between created_at and updated_at")
        if self.version == 1 and self.supersedes_version_id is not None:
            raise ValueError("version 1 cannot supersede another fact version")
        if self.version > 1 and self.supersedes_version_id is None:
            raise ValueError("versions after 1 require supersedes_version_id")
        return self


def _validate_fact_type(
    fact_type: GovernedContextFactType,
    payload: GovernedContextPayload,
) -> None:
    if fact_type is GovernedContextFactType.AUTHORIZED_ACTIVITY and payload.payload_type != "authorized_activity":
        raise ValueError("authorized_activity fact requires AuthorizedActivityPayload")


def _validate_validity(valid_from: datetime, valid_until: datetime) -> None:
    _require_aware(valid_from, "valid_from")
    _require_aware(valid_until, "valid_until")
    if valid_until <= valid_from:
        raise ValueError("valid_until must be after valid_from")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


__all__ = [
    "AuthorizedActivityBehaviorKind",
    "AuthorizedActivityBehaviorSelector",
    "AuthorizedActivityPayload",
    "AuthorizedActivityRecurringWindow",
    "AuthorizedActivitySubjectKind",
    "AuthorizedActivitySubjectSelector",
    "AuthorizedActivityTargetKind",
    "AuthorizedActivityTargetSelector",
    "AuthorizedActivityType",
    "GovernedContextFact",
    "GovernedContextFactCreateCommand",
    "GovernedContextFactQuery",
    "GovernedContextFactRevisionCommand",
    "GovernedContextFactStatus",
    "GovernedContextFactTransitionCommand",
    "GovernedContextFactType",
    "GovernedContextEvidenceRef",
    "GovernedContextPayload",
    "GovernedContextSource",
    "GovernedContextSourceType",
]
