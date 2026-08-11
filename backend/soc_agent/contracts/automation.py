"""Vendor-neutral contracts for governed SOC decision and action automation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import ActorContext
from .schemas import (
    AlertSourceType,
    DecisionEvidenceState,
    SocAgentRiskLevel,
    SocOperationalDisposition,
    Verdict,
)


class SocAutomationPolicyMode(StrEnum):
    SHADOW = "shadow"
    ENFORCED = "enforced"


class SocAutomationContributorKind(StrEnum):
    CURRENT_EVIDENCE = "current_evidence"
    MODEL_REASONING = "model_reasoning"
    CONFIRMED_MEMORY = "confirmed_memory"
    SKILL = "skill"
    GOVERNED_CONTEXT = "governed_context"
    TOOL_RESULT = "tool_result"
    TENANT_POLICY = "tenant_policy"
    SYSTEM_POLICY = "system_policy"
    HUMAN_APPROVAL = "human_approval"


class SocAutomationContributorRole(StrEnum):
    SUPPORTS = "supports"
    OPPOSES = "opposes"
    OVERRIDES = "overrides"
    AUTHORIZES = "authorizes"
    OBSERVED = "observed"


class SocDecisionTransitionKind(StrEnum):
    UNCHANGED = "unchanged"
    REINFORCED = "reinforced"
    OVERRIDDEN = "overridden"
    CONFLICTED = "conflicted"


class SocDecisionStageKind(StrEnum):
    BASE = "base"
    MEMORY = "memory"
    TENANT_POLICY = "tenant_policy"
    EFFECTIVE = "effective"


class SocDecisionStageStatus(StrEnum):
    OBSERVED = "observed"
    DISABLED = "disabled"
    NO_INPUT = "no_input"
    NO_MATCH = "no_match"
    SHADOW_MATCHED = "shadow_matched"
    UNCHANGED = "unchanged"
    REINFORCED = "reinforced"
    OVERRIDDEN = "overridden"
    APPLIED = "applied"
    CONFLICTED = "conflicted"


class SocDispositionTransitionKind(StrEnum):
    NO_CHANGE = "no_change"
    PROPOSED = "proposed"
    APPLIED = "applied"


class SocActionAuthorizationMode(StrEnum):
    AUTOMATIC_POLICY = "automatic_policy"
    HUMAN_APPROVAL = "human_approval"


class SocActionAuthorizationDecision(StrEnum):
    AUTHORIZED = "authorized"
    DENIED = "denied"
    REQUIRES_HUMAN = "requires_human"
    SHADOW_ONLY = "shadow_only"


class SocActionExecutionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED = "skipped"


class SocAutomationTargetSelector(StrEnum):
    SOURCE_IP = "source_ip"
    DESTINATION_IP = "destination_ip"
    ATTACKER_IP = "attacker_ip"
    VICTIM_IP = "victim_ip"
    IMPACTED_HOST = "impacted_host"
    USER = "user"


class SocAutomationContributorRef(BaseModel):
    """Bounded provenance for one input to a transition or authorization."""

    model_config = ConfigDict(extra="forbid")

    kind: SocAutomationContributorKind
    role: SocAutomationContributorRole = SocAutomationContributorRole.OBSERVED
    ref_id: str = Field(min_length=1, max_length=512)
    version: str | None = Field(default=None, max_length=128)
    content_hash: str | None = Field(default=None, max_length=128)
    score: float | None = Field(default=None, ge=0.0)
    detail: str | None = Field(default=None, max_length=1000)


class SocDecisionSnapshot(BaseModel):
    """Comparable detection state used for before/after reporting."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_state: DecisionEvidenceState
    suggested_action: str = Field(min_length=1, max_length=1000)
    needs_review: bool
    policy_version: str = Field(min_length=1, max_length=128)


class SocDecisionStageEvaluation(BaseModel):
    """One replayable stage in Base -> Memory -> Tenant -> Effective."""

    model_config = ConfigDict(extra="forbid")

    stage: SocDecisionStageKind
    status: SocDecisionStageStatus
    before: SocDecisionSnapshot | None = None
    after: SocDecisionSnapshot
    disposition_before: SocOperationalDisposition | None = None
    disposition_after: SocOperationalDisposition | None = None
    source_id: str | None = Field(default=None, max_length=512)
    source_version: str | None = Field(default=None, max_length=128)
    source_hash: str | None = Field(default=None, max_length=128)
    source_decision_id: str | None = Field(default=None, max_length=64)
    selected_rule_id: str | None = Field(default=None, max_length=128)
    contributors: list[SocAutomationContributorRef] = Field(default_factory=list, max_length=300)
    summary: str = Field(min_length=1, max_length=2000)


class SocAutomationRuleMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[Verdict] = Field(default_factory=list, max_length=10)
    source_types: list[AlertSourceType] = Field(default_factory=list, max_length=20)
    evidence_states: list[DecisionEvidenceState] = Field(default_factory=list, max_length=10)
    scenario_keys: list[str] = Field(default_factory=list, max_length=50)
    rule_codes: list[str] = Field(default_factory=list, max_length=100)
    detection_keys: list[str] = Field(default_factory=list, max_length=100)
    tenant_policy_rule_ids: list[str] = Field(default_factory=list, max_length=100)
    model_names: list[str] = Field(default_factory=list, max_length=20)
    prompt_versions: list[str] = Field(default_factory=list, max_length=20)
    decision_policy_versions: list[str] = Field(default_factory=list, max_length=20)
    minimum_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool | None = None

    @field_validator(
        "scenario_keys",
        "rule_codes",
        "detection_keys",
        "tenant_policy_rule_ids",
        "model_names",
        "prompt_versions",
        "decision_policy_versions",
    )
    @classmethod
    def normalize_match_strings(cls, values: list[str]) -> list[str]:
        return sorted({str(value).strip() for value in values if str(value).strip()})


class SocAutomationActionSpec(BaseModel):
    """Pinned action adapter plus deterministic target projection."""

    model_config = ConfigDict(extra="forbid")

    route: str = Field(min_length=1, max_length=256)
    action: str = Field(min_length=1, max_length=256)
    adapter_id: str = Field(min_length=1, max_length=256)
    target_selector: SocAutomationTargetSelector
    target_payload_field: str = Field(min_length=1, max_length=128)
    static_payload: dict[str, Any] = Field(default_factory=dict)


class SocAutomationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    priority: int = Field(default=100, ge=0, le=1_000_000)
    enabled: bool = True
    match: SocAutomationRuleMatch = Field(default_factory=SocAutomationRuleMatch)
    disposition: SocOperationalDisposition | None = None
    action: SocAutomationActionSpec | None = None
    authorization_mode: SocActionAuthorizationMode = SocActionAuthorizationMode.HUMAN_APPROVAL
    review_required_override_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_effect(self) -> SocAutomationRule:
        if self.disposition is None and self.action is None:
            raise ValueError("automation rule requires a disposition or action")
        if self.action is not None and self.authorization_mode is SocActionAuthorizationMode.AUTOMATIC_POLICY:
            if not self.match.verdicts:
                raise ValueError("automatic action rule requires explicit verdict matches")
            if not self.match.evidence_states:
                raise ValueError("automatic action rule requires explicit evidence-state matches")
            if not self.match.model_names:
                raise ValueError("automatic action rule requires explicit model-name matches")
            if not self.match.prompt_versions:
                raise ValueError("automatic action rule requires explicit prompt-version matches")
            if not self.match.decision_policy_versions:
                raise ValueError("automatic action rule requires explicit decision-policy-version matches")
            if self.match.minimum_confidence is None:
                raise ValueError("automatic action rule requires an explicit minimum confidence")
            if self.match.needs_review is None:
                raise ValueError("automatic action rule requires an explicit needs_review match")
            if self.match.needs_review is True and not self.review_required_override_reason:
                raise ValueError("automatic action over a review-required decision requires review_required_override_reason")
            if self.match.needs_review is False and self.review_required_override_reason is not None:
                raise ValueError("review_required_override_reason is valid only when needs_review=true")
        return self


class SocAutomationPolicy(BaseModel):
    """Server-owned policy that may authorize actions without model authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.automation_policy.v1"] = "soc.automation_policy.v1"
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=128)
    mode: SocAutomationPolicyMode = SocAutomationPolicyMode.SHADOW
    valid_from: datetime
    valid_until: datetime
    reviewed_by: str | None = Field(default=None, max_length=128)
    reviewed_at: datetime | None = None
    rules: list[SocAutomationRule] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_governance(self) -> SocAutomationPolicy:
        if self.valid_from.utcoffset() is None or self.valid_until.utcoffset() is None:
            raise ValueError("automation policy validity must be timezone-aware")
        if self.valid_until <= self.valid_from:
            raise ValueError("automation policy valid_until must be after valid_from")
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("automation policy rule_id values must be unique")
        if self.mode is SocAutomationPolicyMode.ENFORCED and (not self.reviewed_by or self.reviewed_at is None):
            raise ValueError("enforced automation policy requires reviewed_by and reviewed_at")
        if self.reviewed_at is not None and self.reviewed_at.utcoffset() is None:
            raise ValueError("automation policy reviewed_at must be timezone-aware")
        return self


class SocDecisionTransitionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[
        "soc.decision_transition.v1",
        "soc.decision_transition.v2",
    ] = "soc.decision_transition.v2"
    transition_id: str = Field(default_factory=lambda: f"DTR-{uuid4().hex[:16].upper()}")
    transition_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    before: SocDecisionSnapshot
    after: SocDecisionSnapshot
    effective_disposition: SocOperationalDisposition | None = None
    transition_kind: SocDecisionTransitionKind
    stages: list[SocDecisionStageEvaluation] = Field(default_factory=list, max_length=4)
    contributors: list[SocAutomationContributorRef] = Field(default_factory=list, max_length=300)
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    counterfactual_status: Literal["not_measured", "paired_replay"] = "not_measured"
    created_by: ActorContext
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_stage_lineage(self) -> SocDecisionTransitionRecord:
        if not self.stages:
            return self
        expected = [
            SocDecisionStageKind.BASE,
            SocDecisionStageKind.MEMORY,
            SocDecisionStageKind.TENANT_POLICY,
            SocDecisionStageKind.EFFECTIVE,
        ]
        if [stage.stage for stage in self.stages] != expected:
            raise ValueError("decision transition stages must be Base -> Memory -> Tenant Policy -> Effective")
        if self.stages[0].after != self.before:
            raise ValueError("base decision stage must equal transition before")
        for previous, current in zip(self.stages, self.stages[1:]):
            if current.before != previous.after:
                raise ValueError("each decision stage before snapshot must equal the previous stage after snapshot")
        if self.stages[-1].after != self.after:
            raise ValueError("effective decision stage must equal transition after")
        if self.stages[-1].disposition_after != self.effective_disposition:
            raise ValueError("effective stage disposition must equal transition disposition")
        return self


class SocDispositionTransitionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.disposition_transition.v1"] = "soc.disposition_transition.v1"
    transition_id: str = Field(default_factory=lambda: f"DSPTR-{uuid4().hex[:16].upper()}")
    transition_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    decision_transition_id: str = Field(min_length=1, max_length=64)
    before: SocOperationalDisposition | None = None
    after: SocOperationalDisposition | None = None
    transition_kind: SocDispositionTransitionKind
    contributors: list[SocAutomationContributorRef] = Field(default_factory=list, max_length=300)
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    selected_rule_id: str | None = Field(default=None, max_length=128)
    created_by: ActorContext
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SocActionAuthorizationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.action_authorization.v1"] = "soc.action_authorization.v1"
    authorization_id: str = Field(default_factory=lambda: f"AAUTH-{uuid4().hex[:16].upper()}")
    authorization_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    decision_transition_id: str = Field(min_length=1, max_length=64)
    disposition_transition_id: str | None = Field(default=None, max_length=64)
    mode: SocActionAuthorizationMode
    decision: SocActionAuthorizationDecision
    route: str = Field(min_length=1, max_length=256)
    action: str = Field(min_length=1, max_length=256)
    adapter_id: str = Field(min_length=1, max_length=256)
    risk_level: SocAgentRiskLevel
    target_type: SocAutomationTargetSelector
    target_value: str = Field(min_length=1, max_length=2000)
    command_payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=2000)
    contributors: list[SocAutomationContributorRef] = Field(default_factory=list, max_length=300)
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    selected_rule_id: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = None
    authorized_by: ActorContext
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SocActionExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.action_execution.v1"] = "soc.action_execution.v1"
    execution_id: str = Field(default_factory=lambda: f"AEX-{uuid4().hex[:16].upper()}")
    execution_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    route: str = Field(min_length=1, max_length=256)
    action: str = Field(min_length=1, max_length=256)
    adapter_id: str = Field(min_length=1, max_length=256)
    status: SocActionExecutionStatus
    attempt: int = Field(default=1, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=512)
    external_request_id: str | None = Field(default=None, max_length=512)
    external_state_before: dict[str, Any] | None = None
    external_state_after: dict[str, Any] | None = None
    result_payload: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = Field(default=None, max_length=256)
    error_message: str | None = Field(default=None, max_length=2000)
    executed_by: ActorContext
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None


class SocAutomationEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.automation_evaluation_result.v1"] = "soc.automation_evaluation_result.v1"
    decision_transition: SocDecisionTransitionRecord
    disposition_transition: SocDispositionTransitionRecord | None = None
    authorization: SocActionAuthorizationRecord | None = None
    execution: SocActionExecutionRecord | None = None
    selected_rule_id: str | None = None
    tenant_policy_decision_id: str | None = None
    effective_disposition: SocOperationalDisposition | None = None
    idempotent: bool = False


__all__ = [
    "SocActionAuthorizationDecision",
    "SocActionAuthorizationMode",
    "SocActionAuthorizationRecord",
    "SocActionExecutionRecord",
    "SocActionExecutionStatus",
    "SocAutomationActionSpec",
    "SocAutomationContributorKind",
    "SocAutomationContributorRef",
    "SocAutomationContributorRole",
    "SocAutomationEvaluationResult",
    "SocAutomationPolicy",
    "SocAutomationPolicyMode",
    "SocAutomationRule",
    "SocAutomationRuleMatch",
    "SocAutomationTargetSelector",
    "SocDecisionSnapshot",
    "SocDecisionStageEvaluation",
    "SocDecisionStageKind",
    "SocDecisionStageStatus",
    "SocDecisionTransitionKind",
    "SocDecisionTransitionRecord",
    "SocDispositionTransitionKind",
    "SocDispositionTransitionRecord",
]
