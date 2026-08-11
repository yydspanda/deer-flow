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
    """Operational response posture; never an action authorization."""

    STANDARD_TRIAGE = "standard_triage"
    NO_AUTOMATED_RESPONSE = "no_automated_response"
    MANUAL_VALIDATION_REQUIRED = "manual_validation_required"


class TenantPolicyMode(StrEnum):
    """Whether a reviewed tenant policy may affect the effective decision."""

    SHADOW = "shadow"
    ENFORCED = "enforced"


class TenantPolicyReviewEffect(StrEnum):
    """How a matched tenant rule treats the current review requirement."""

    PRESERVE = "preserve"
    REQUIRE = "require"
    CLEAR = "clear"


class TenantPolicyEvaluationStatus(StrEnum):
    MATCHED = "matched"
    NO_MATCH = "no_match"


class TenantPolicyDecisionSource(StrEnum):
    """Which governed mechanism produced the tenant decision."""

    DETERMINISTIC_RULE = "deterministic_rule"
    LLM_POLICY_SKILL = "llm_policy_skill"
    NO_MATCH = "no_match"


class TenantPolicyAdvisorStatus(StrEnum):
    """Execution status of an optional bounded tenant policy advisor."""

    COMPLETED = "completed"
    FAILED_CLOSED = "failed_closed"


class TenantPolicyTimeSource(StrEnum):
    ALERT_EVENT_TIME = "alert_event_time"
    ALERT_EVENT_TIME_TIMEZONE_ASSUMED = "alert_event_time_timezone_assumed"
    EVALUATION_TIME_FALLBACK = "evaluation_time_fallback"


class TenantPolicySignalProviderStatus(StrEnum):
    """Outcome of one optional, read-only tenant policy signal provider."""

    COMPLETED = "completed"
    NOT_APPLICABLE = "not_applicable"
    FAILED_CLOSED = "failed_closed"


class TenantPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantPolicySignal(TenantPolicyModel):
    """One governed provider signal consumed by generic tenant policy rules."""

    schema_version: Literal["soc.tenant_policy_signal.v1"] = "soc.tenant_policy_signal.v1"
    signal_id: str = Field(min_length=1, max_length=128)
    signal_key: str = Field(min_length=1, max_length=256)
    signal_value: str = Field(min_length=1, max_length=256)
    provider_id: str = Field(min_length=1, max_length=256)
    provider_version: str = Field(min_length=1, max_length=128)
    source_ref: str = Field(min_length=1, max_length=1000)
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    subject: str | None = Field(default=None, min_length=1, max_length=4096)
    evidence_paths: list[str] = Field(default_factory=list, max_length=50)
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=50)

    @field_validator("signal_key", "signal_value", "provider_id", "provider_version", "source_ref")
    @classmethod
    def strip_required_signal_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tenant policy signal text must be non-empty")
        return normalized

    @field_validator("evidence_paths")
    @classmethod
    def normalize_signal_evidence_paths(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("tenant policy signal evidence paths must be non-empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("tenant policy signal evidence paths must be unique")
        return normalized


class TenantPolicySignalResolution(TenantPolicyModel):
    """Auditable output of one signal provider, including fail-closed outcomes."""

    schema_version: Literal["soc.tenant_policy_signal_resolution.v1"] = "soc.tenant_policy_signal_resolution.v1"
    provider_id: str = Field(min_length=1, max_length=256)
    provider_version: str = Field(min_length=1, max_length=128)
    status: TenantPolicySignalProviderStatus
    source_ref: str | None = Field(default=None, min_length=1, max_length=1000)
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    signals: list[TenantPolicySignal] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_provider_outcome(self) -> TenantPolicySignalResolution:
        if self.status is TenantPolicySignalProviderStatus.COMPLETED:
            if self.error_code is not None:
                raise ValueError("completed tenant policy signal resolution cannot carry an error")
        elif self.status is TenantPolicySignalProviderStatus.NOT_APPLICABLE:
            if self.signals or self.error_code is not None:
                raise ValueError("not-applicable tenant policy signal resolution cannot carry signals or an error")
        else:
            if self.signals or self.error_code is None:
                raise ValueError("failed-closed tenant policy signal resolution requires an error and no signals")
        if any(signal.provider_id != self.provider_id or signal.provider_version != self.provider_version for signal in self.signals):
            raise ValueError("tenant policy signal provider lineage must match its resolution")
        return self


class TenantPolicyRuleMatch(TenantPolicyModel):
    """AND-composed generic conditions; values inside each list are OR-composed."""

    source_types: list[AlertSourceType] = Field(default_factory=list, max_length=20)
    detection_verdicts: list[Verdict] = Field(default_factory=list, max_length=10)
    detection_categories: list[str] = Field(default_factory=list, max_length=50)
    rule_codes: list[str] = Field(default_factory=list, max_length=100)
    detection_keys: list[str] = Field(default_factory=list, max_length=100)
    scenario_keys: list[str] = Field(default_factory=list, max_length=50)
    classification_labels: dict[str, list[str]] = Field(
        default_factory=dict,
        max_length=50,
    )
    classification_labels_excluded: dict[str, list[str]] = Field(
        default_factory=dict,
        max_length=50,
    )
    source_ip_scope: TenantNetworkScope | None = None
    destination_ip_scope: TenantNetworkScope | None = None
    http_host_globs: list[str] = Field(default_factory=list, max_length=50)
    http_status_codes: list[int] = Field(default_factory=list, max_length=50)
    http_status_excluded_codes: list[int] = Field(default_factory=list, max_length=50)
    authorization_statuses: list[AuthorizationMatchStatus] = Field(default_factory=list, max_length=10)
    policy_signals: dict[str, list[str]] = Field(default_factory=dict, max_length=50)

    @model_validator(mode="after")
    def require_condition(self) -> TenantPolicyRuleMatch:
        if not any(
            (
                self.source_types,
                self.detection_verdicts,
                self.detection_categories,
                self.rule_codes,
                self.detection_keys,
                self.scenario_keys,
                self.classification_labels,
                self.classification_labels_excluded,
                self.source_ip_scope,
                self.destination_ip_scope,
                self.http_host_globs,
                self.http_status_codes,
                self.http_status_excluded_codes,
                self.authorization_statuses,
                self.policy_signals,
            )
        ):
            raise ValueError("tenant policy rule requires at least one match condition")
        if set(self.http_status_codes) & set(self.http_status_excluded_codes):
            raise ValueError("tenant policy HTTP status include/exclude conditions cannot overlap")
        return self

    @field_validator(
        "detection_categories",
        "rule_codes",
        "detection_keys",
        "scenario_keys",
        "http_host_globs",
    )
    @classmethod
    def require_non_empty_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("tenant policy match values must be non-empty")
        return values

    @field_validator("http_status_codes", "http_status_excluded_codes")
    @classmethod
    def validate_http_status_codes(cls, values: list[int]) -> list[int]:
        if any(value < 100 or value > 599 for value in values):
            raise ValueError("tenant policy HTTP status codes must be in range 100..599")
        return sorted(set(values))

    @field_validator("classification_labels", "classification_labels_excluded", "policy_signals")
    @classmethod
    def validate_classification_labels(
        cls,
        values: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        normalized_keys: set[str] = set()
        for key, expected_values in values.items():
            normalized_key = key.strip().casefold()
            if not normalized_key:
                raise ValueError("tenant policy classification label keys must be non-empty")
            if normalized_key in normalized_keys:
                raise ValueError("tenant policy classification label keys must be unique")
            normalized_keys.add(normalized_key)
            if not expected_values or any(not value.strip() for value in expected_values):
                raise ValueError("tenant policy classification label values must be non-empty")
            normalized_values = [value.strip().casefold() for value in expected_values]
            if len(set(normalized_values)) != len(normalized_values):
                raise ValueError("tenant policy classification label values must be unique")
        return values


class TenantPolicyRecommendation(TenantPolicyModel):
    """A bounded operational decision, separate from detection truth."""

    response_posture: TenantPolicyResponsePosture
    recommended_disposition: SocOperationalDisposition | None = None
    review_effect: TenantPolicyReviewEffect = TenantPolicyReviewEffect.PRESERVE
    suggested_action: str | None = Field(default=None, min_length=1, max_length=1000)
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


class TenantPolicyAdvice(TenantPolicyModel):
    """Typed output from a tenant-owned policy Skill.

    Advice is operational handling context. It cannot rewrite Runtime detection
    truth or authorize an external action.
    """

    schema_version: Literal["soc.tenant_policy_advice.v1"] = "soc.tenant_policy_advice.v1"
    evaluation_status: TenantPolicyEvaluationStatus
    response_posture: TenantPolicyResponsePosture = TenantPolicyResponsePosture.STANDARD_TRIAGE
    recommended_disposition: SocOperationalDisposition | None = None
    review_effect: TenantPolicyReviewEffect = TenantPolicyReviewEffect.PRESERVE
    suggested_action: str | None = Field(default=None, min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=2000)
    rationale: list[str] = Field(min_length=1, max_length=20)
    manual_checks: list[str] = Field(default_factory=list, max_length=20)
    policy_signal_keys: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=40)
    reasoning_refs: list[str] = Field(default_factory=list, max_length=20)
    context_refs: list[str] = Field(default_factory=list, max_length=20)

    @field_validator(
        "rationale",
        "manual_checks",
        "policy_signal_keys",
        "evidence_refs",
        "reasoning_refs",
        "context_refs",
    )
    @classmethod
    def require_bounded_non_empty_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("tenant policy advice list values must be non-empty")
        if len(set(values)) != len(values):
            raise ValueError("tenant policy advice list values must be unique")
        if any(len(value) > 1000 for value in values):
            raise ValueError("tenant policy advice value exceeds 1000 characters")
        return values

    @model_validator(mode="after")
    def validate_advice_effect(self) -> TenantPolicyAdvice:
        if self.evaluation_status is TenantPolicyEvaluationStatus.MATCHED:
            if not self.evidence_refs:
                raise ValueError("matched tenant policy advice requires evidence_refs")
            if not self.policy_signal_keys:
                raise ValueError("matched tenant policy advice requires policy_signal_keys")
            return self
        if self.recommended_disposition is not None:
            raise ValueError("no-match tenant policy advice cannot set disposition")
        if self.review_effect is not TenantPolicyReviewEffect.PRESERVE:
            raise ValueError("no-match tenant policy advice must preserve review")
        if self.suggested_action is not None:
            raise ValueError("no-match tenant policy advice cannot set an action")
        return self


class TenantPolicyAdvisorProvenance(TenantPolicyModel):
    """Secret-free lineage for one bounded policy Skill invocation."""

    schema_version: Literal["soc.tenant_policy_advisor_provenance.v1"] = "soc.tenant_policy_advisor_provenance.v1"
    advisor_id: str = Field(min_length=1, max_length=128)
    status: TenantPolicyAdvisorStatus
    model_name: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=128)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_name: str = Field(min_length=1, max_length=128)
    skill_version: str = Field(min_length=1, max_length=128)
    skill_source_ref: str = Field(min_length=1, max_length=1000)
    skill_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    repair_applied: bool = False
    usage: dict[str, int | float | str] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_status(self) -> TenantPolicyAdvisorProvenance:
        if self.status is TenantPolicyAdvisorStatus.COMPLETED:
            if self.response_hash is None or self.error_code is not None:
                raise ValueError("completed tenant policy advisor requires response hash and no error")
        elif self.error_code is None:
            raise ValueError("failed tenant policy advisor requires error_code")
        return self


class TenantPolicyAdvisorResult(TenantPolicyModel):
    """Advisor output plus the exact provenance used to resolve it."""

    advice: TenantPolicyAdvice
    provenance: TenantPolicyAdvisorProvenance


class TenantDispositionRule(TenantPolicyModel):
    rule_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=256)
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    match: TenantPolicyRuleMatch
    recommendation: TenantPolicyRecommendation

    @model_validator(mode="after")
    def require_exact_authorization_for_disposition(self) -> TenantDispositionRule:
        if self.match.authorization_statuses and self.recommendation.recommended_disposition is not None and any(status is not AuthorizationMatchStatus.EXACT for status in self.match.authorization_statuses):
            raise ValueError("authorization-conditioned disposition requires exact authorization")
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
    policy_mode: TenantPolicyMode = TenantPolicyMode.SHADOW

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
        if self.policy_mode is TenantPolicyMode.ENFORCED and self.reviewed_by is None:
            raise ValueError("enforced tenant policy requires reviewed_by and reviewed_at")
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
    """Immutable tenant decision evaluated after the base Runtime decision."""

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
    decision_source: TenantPolicyDecisionSource = TenantPolicyDecisionSource.DETERMINISTIC_RULE
    selected_rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    rule_evaluations: list[TenantPolicyRuleEvaluation] = Field(default_factory=list, max_length=200)
    policy_signal_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_signal_resolutions: list[TenantPolicySignalResolution] = Field(default_factory=list, max_length=20)
    detection_truth: SocDetectionTruthSnapshot
    runtime_suggested_action: str | None = Field(default=None, max_length=1000)
    authorization_status: AuthorizationMatchStatus | None = None
    authorization_query_id: str | None = Field(default=None, min_length=1, max_length=64)
    policy_mode: TenantPolicyMode = TenantPolicyMode.SHADOW
    response_posture: TenantPolicyResponsePosture = TenantPolicyResponsePosture.STANDARD_TRIAGE
    recommended_disposition: SocOperationalDisposition | None = None
    review_effect: TenantPolicyReviewEffect = TenantPolicyReviewEffect.PRESERVE
    suggested_action: str | None = Field(default=None, min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=2000)
    rationale: list[str] = Field(min_length=1, max_length=20)
    manual_checks: list[str] = Field(default_factory=list, max_length=20)
    advisor_advice: TenantPolicyAdvice | None = None
    advisor_provenance: TenantPolicyAdvisorProvenance | None = None
    evaluated_by: ActorContext
    triggered_by: ActorContext
    created_at: datetime = Field(default_factory=utc_now)
    shadow_only: bool = True
    requires_human_review: bool = True
    auto_apply_allowed: bool = False
    detection_truth_impact: Literal["none"] = "none"
    review_queue_impact: Literal["none", "preserve", "require", "clear"] = "none"
    disposition_impact: Literal["none", "proposed", "eligible"] = "none"
    action_impact: Literal["none"] = "none"
    memory_impact: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_match_lineage(self) -> TenantPolicyDecision:
        if self.policy_time.tzinfo is None or self.policy_time.utcoffset() is None:
            raise ValueError("tenant policy decision policy_time must be timezone-aware")
        if (self.policy_reviewed_by is None) != (self.policy_reviewed_at is None):
            raise ValueError("tenant policy decision reviewed_by and reviewed_at must be set together")
        if (self.decision_source is TenantPolicyDecisionSource.LLM_POLICY_SKILL) != (self.advisor_provenance is not None and self.advisor_advice is not None):
            raise ValueError("LLM policy Skill decisions require exclusive advice and provenance")
        if self.decision_source is not TenantPolicyDecisionSource.LLM_POLICY_SKILL and (self.advisor_provenance is not None or self.advisor_advice is not None):
            raise ValueError("non-advisor tenant decisions cannot carry advisor payloads")
        matched_ids = {item.rule_id for item in self.rule_evaluations if item.matched}
        if self.evaluation_status is TenantPolicyEvaluationStatus.MATCHED:
            if self.selected_rule_id is None or self.selected_rule_id not in matched_ids:
                raise ValueError("matched tenant policy decision requires a selected matched rule")
        elif self.selected_rule_id is not None or matched_ids:
            raise ValueError("no-match tenant policy decision cannot carry matched rule lineage")
        expected_shadow = self.policy_mode is TenantPolicyMode.SHADOW
        if self.shadow_only is not expected_shadow:
            raise ValueError("tenant policy decision shadow_only must match policy_mode")
        if self.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH:
            if self.auto_apply_allowed or self.review_queue_impact != "none" or self.disposition_impact != "none":
                raise ValueError("no-match tenant policy decision cannot carry an application effect")
        elif self.policy_mode is TenantPolicyMode.SHADOW:
            if self.auto_apply_allowed or self.review_queue_impact != "none":
                raise ValueError("shadow tenant policy decision cannot be applied")
            expected_disposition_impact = "proposed" if self.recommended_disposition is not None else "none"
            if self.disposition_impact != expected_disposition_impact:
                raise ValueError("shadow tenant policy disposition impact must remain proposed")
        else:
            if not self.auto_apply_allowed:
                raise ValueError("matched enforced tenant policy decision must be application-eligible")
            if self.review_queue_impact != self.review_effect.value:
                raise ValueError("enforced tenant policy review impact must match review_effect")
            expected_disposition_impact = "eligible" if self.recommended_disposition is not None else "none"
            if self.disposition_impact != expected_disposition_impact:
                raise ValueError("enforced tenant policy disposition impact is inconsistent")
        return self


__all__ = [
    "TenantDispositionPolicy",
    "TenantDispositionRule",
    "TenantNetworkScope",
    "TenantPolicyAdvice",
    "TenantPolicyAdvisorProvenance",
    "TenantPolicyAdvisorResult",
    "TenantPolicyAdvisorStatus",
    "TenantPolicyConditionEvaluation",
    "TenantPolicyDecision",
    "TenantPolicyDecisionSource",
    "TenantPolicyEvaluationStatus",
    "TenantPolicyMode",
    "TenantPolicyRecommendation",
    "TenantPolicyResponsePosture",
    "TenantPolicyReviewEffect",
    "TenantPolicyRuleEvaluation",
    "TenantPolicyRuleMatch",
    "TenantPolicySignal",
    "TenantPolicySignalProviderStatus",
    "TenantPolicySignalResolution",
    "TenantPolicyTimeSource",
]
