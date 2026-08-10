"""Vendor-neutral contracts for tenant operational policy evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from ipaddress import ip_network
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts.authorization import AuthorizationMatchStatus
from soc_agent.contracts.common import ActorContext
from soc_agent.contracts.schemas import (
    AlertSourceType,
    SocDetectionTruthSnapshot,
    SocOperationalDisposition,
    Verdict,
    utc_now,
)


class TenantNetworkScope(StrEnum):
    """How one canonical endpoint relates to tenant-managed network ranges."""

    PRESENT = "present"
    INTERNAL = "internal"
    EXTERNAL = "external"


class TenantPolicyResponsePosture(StrEnum):
    """Advisory response posture; never an action authorization."""

    STANDARD_TRIAGE = "standard_triage"
    NO_AUTOMATED_RESPONSE = "no_automated_response"
    MANUAL_VALIDATION_REQUIRED = "manual_validation_required"


class TenantPolicyEvaluationStatus(StrEnum):
    MATCHED = "matched"
    NO_MATCH = "no_match"


class TenantPolicyTimeSource(StrEnum):
    ALERT_EVENT_TIME = "alert_event_time"
    ALERT_EVENT_TIME_TIMEZONE_ASSUMED = "alert_event_time_timezone_assumed"
    EVALUATION_TIME_FALLBACK = "evaluation_time_fallback"


class TenantPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantPolicyRuleMatch(TenantPolicyModel):
    """AND-composed generic conditions; values inside each list are OR-composed."""

    source_types: list[AlertSourceType] = Field(default_factory=list, max_length=20)
    detection_verdicts: list[Verdict] = Field(default_factory=list, max_length=10)
    detection_categories: list[str] = Field(default_factory=list, max_length=50)
    scenario_keys: list[str] = Field(default_factory=list, max_length=50)
    source_ip_scope: TenantNetworkScope | None = None
    destination_ip_scope: TenantNetworkScope | None = None
    http_host_globs: list[str] = Field(default_factory=list, max_length=50)
    authorization_statuses: list[AuthorizationMatchStatus] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def require_condition(self) -> TenantPolicyRuleMatch:
        if not any(
            (
                self.source_types,
                self.detection_verdicts,
                self.detection_categories,
                self.scenario_keys,
                self.source_ip_scope,
                self.destination_ip_scope,
                self.http_host_globs,
                self.authorization_statuses,
            )
        ):
            raise ValueError("tenant policy rule requires at least one match condition")
        return self

    @field_validator("detection_categories", "scenario_keys", "http_host_globs")
    @classmethod
    def require_non_empty_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("tenant policy match values must be non-empty")
        return values


class TenantPolicyRecommendation(TenantPolicyModel):
    """A bounded analyst recommendation, separate from detection truth."""

    response_posture: TenantPolicyResponsePosture
    recommended_disposition: SocOperationalDisposition | None = None
    summary: str = Field(min_length=1, max_length=2000)
    rationale: list[str] = Field(min_length=1, max_length=20)
    manual_checks: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("rationale", "manual_checks")
    @classmethod
    def bound_guidance(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("tenant policy guidance must be non-empty")
        if any(len(value) > 1000 for value in values):
            raise ValueError("tenant policy guidance exceeds 1000 characters")
        return values


class TenantDispositionRule(TenantPolicyModel):
    rule_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=256)
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    match: TenantPolicyRuleMatch
    recommendation: TenantPolicyRecommendation

    @model_validator(mode="after")
    def preserve_authorization_proposal_boundary(self) -> TenantDispositionRule:
        if self.match.authorization_statuses and self.recommendation.recommended_disposition is not None:
            raise ValueError("authorization-conditioned disposition must use the governed enrichment/proposal path")
        return self


class TenantDispositionPolicy(TenantPolicyModel):
    """Versioned operator-owned policy loaded outside the generic Runtime."""

    schema_version: Literal["soc.tenant_disposition_policy.v1"] = "soc.tenant_disposition_policy.v1"
    policy_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    policy_version: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    applicable_environments: list[str] = Field(min_length=1, max_length=20)
    owner: str = Field(min_length=1, max_length=256)
    source_ref: str = Field(min_length=1, max_length=1000)
    change_reason: str = Field(min_length=1, max_length=2000)
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=256)
    reviewed_at: datetime | None = None
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    internal_networks: list[str] = Field(default_factory=list, max_length=200)
    rules: list[TenantDispositionRule] = Field(min_length=1, max_length=200)
    policy_mode: Literal["shadow"] = "shadow"

    @model_validator(mode="after")
    def validate_policy(self) -> TenantDispositionPolicy:
        if self.effective_from and self.effective_until and self.effective_until <= self.effective_from:
            raise ValueError("tenant policy effective_until must be after effective_from")
        for field_name, value in (
            ("effective_from", self.effective_from),
            ("effective_until", self.effective_until),
            ("reviewed_at", self.reviewed_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"tenant policy {field_name} must be timezone-aware")
        if (self.reviewed_by is None) != (self.reviewed_at is None):
            raise ValueError("tenant policy reviewed_by and reviewed_at must be set together")
        for value in self.internal_networks:
            try:
                ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid tenant policy network: {value}") from exc
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("tenant policy rule_id values must be unique")
        normalized_environments = [value.strip().casefold() for value in self.applicable_environments]
        if any(not value for value in normalized_environments):
            raise ValueError("tenant policy environments must be non-empty")
        if len(set(normalized_environments)) != len(normalized_environments):
            raise ValueError("tenant policy environments must be unique")
        return self


class TenantPolicyConditionEvaluation(TenantPolicyModel):
    condition: str = Field(min_length=1, max_length=128)
    matched: bool
    evidence_paths: list[str] = Field(default_factory=list, max_length=50)
    detail: str = Field(min_length=1, max_length=1000)


class TenantPolicyRuleEvaluation(TenantPolicyModel):
    rule_id: str = Field(min_length=1, max_length=128)
    rule_name: str = Field(min_length=1, max_length=256)
    priority: int = Field(ge=0, le=10_000)
    matched: bool
    conditions: list[TenantPolicyConditionEvaluation] = Field(min_length=1, max_length=20)


class TenantPolicyDecision(TenantPolicyModel):
    """Immutable shadow decision; it cannot mutate Runtime or operational state."""

    schema_version: Literal["soc.tenant_policy_decision.v1"] = "soc.tenant_policy_decision.v1"
    decision_id: str = Field(default_factory=lambda: f"TPD-{uuid4().hex[:20].upper()}")
    decision_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_owner: str = Field(min_length=1, max_length=256)
    policy_source_ref: str = Field(min_length=1, max_length=1000)
    policy_change_reason: str = Field(min_length=1, max_length=2000)
    policy_reviewed_by: str | None = Field(default=None, min_length=1, max_length=256)
    policy_reviewed_at: datetime | None = None
    policy_time: datetime
    policy_time_source: TenantPolicyTimeSource
    evaluation_status: TenantPolicyEvaluationStatus
    selected_rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    rule_evaluations: list[TenantPolicyRuleEvaluation] = Field(default_factory=list, max_length=200)
    detection_truth: SocDetectionTruthSnapshot
    runtime_suggested_action: str | None = Field(default=None, max_length=1000)
    authorization_status: AuthorizationMatchStatus | None = None
    authorization_query_id: str | None = Field(default=None, min_length=1, max_length=64)
    response_posture: TenantPolicyResponsePosture = TenantPolicyResponsePosture.STANDARD_TRIAGE
    recommended_disposition: SocOperationalDisposition | None = None
    summary: str = Field(min_length=1, max_length=2000)
    rationale: list[str] = Field(min_length=1, max_length=20)
    manual_checks: list[str] = Field(default_factory=list, max_length=20)
    evaluated_by: ActorContext
    triggered_by: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    shadow_only: Literal[True] = True
    requires_human_review: Literal[True] = True
    auto_apply_allowed: Literal[False] = False
    detection_truth_impact: Literal["none"] = "none"
    review_queue_impact: Literal["none"] = "none"
    action_impact: Literal["none"] = "none"
    memory_impact: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_match_lineage(self) -> TenantPolicyDecision:
        if self.policy_time.tzinfo is None or self.policy_time.utcoffset() is None:
            raise ValueError("tenant policy decision policy_time must be timezone-aware")
        if (self.policy_reviewed_by is None) != (self.policy_reviewed_at is None):
            raise ValueError("tenant policy decision reviewed_by and reviewed_at must be set together")
        matched_ids = {item.rule_id for item in self.rule_evaluations if item.matched}
        if self.evaluation_status is TenantPolicyEvaluationStatus.MATCHED:
            if self.selected_rule_id is None or self.selected_rule_id not in matched_ids:
                raise ValueError("matched tenant policy decision requires a selected matched rule")
        elif self.selected_rule_id is not None or matched_ids:
            raise ValueError("no-match tenant policy decision cannot carry matched rule lineage")
        return self


__all__ = [
    "TenantDispositionPolicy",
    "TenantDispositionRule",
    "TenantNetworkScope",
    "TenantPolicyConditionEvaluation",
    "TenantPolicyDecision",
    "TenantPolicyEvaluationStatus",
    "TenantPolicyRecommendation",
    "TenantPolicyResponsePosture",
    "TenantPolicyRuleEvaluation",
    "TenantPolicyRuleMatch",
    "TenantPolicyTimeSource",
]
