"""Versioned contracts for governed rollout simulation rehearsals."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import SocEvaluationDataClass

ROLLOUT_REHEARSAL_POLICY_VERSION = "soc.rollout_rehearsal_policy.v1"

BoundedValue = Annotated[str, Field(min_length=1, max_length=256)]


class SocRolloutStage(StrEnum):
    """Governed read-only rollout stages."""

    NOT_STARTED = "not_started"
    SHADOW = "shadow"
    LIMITED_PILOT = "limited_pilot"
    CONTROLLED_ROLLOUT = "controlled_rollout"


class SocRolloutOwnerRole(StrEnum):
    """Independent accountability roles required by the rollout plan."""

    PRODUCT_RISK = "product_risk"
    SOC_OPERATIONS = "soc_operations"
    SECURITY = "security"
    PLATFORM_SRE = "platform_sre"
    RESPONSE_SYSTEM = "response_system"


class SocRolloutGateId(StrEnum):
    """Real-evidence gates that a simulation is never allowed to close."""

    REAL_PROVIDER_EVIDENCE = "pi01.real_provider_evidence"
    PRODUCTION_INFRASTRUCTURE = "pi02.production_infrastructure"
    REAL_QUALITY_EVALUATION = "pi03.real_quality_evaluation"
    OPERATIONS_SLO = "pi04.operations_slo"
    ACCOUNTABLE_OWNERS = "pi05.accountable_owners"
    ROLLBACK_READINESS = "pi05.rollback_readiness"
    COHORT_ISOLATION = "pi05.cohort_isolation"


class SocRolloutRealGateStatus(StrEnum):
    """Status of real rollout evidence, separate from rehearsal completion."""

    OPEN = "open"
    NOT_MEASURED = "not_measured"
    FAILED = "failed"
    PASSED = "passed"


class SocRolloutRollbackAction(StrEnum):
    """Minimum rollback actions exercised without executing their side effects."""

    PAUSE_INGRESS = "pause_ingress"
    DISABLE_COHORT = "disable_cohort"
    PRESERVE_EVIDENCE = "preserve_evidence"
    ROUTE_INFLIGHT_TO_HUMAN = "route_inflight_to_human"
    NOTIFY_OWNERS = "notify_owners"
    VERIFY_NO_EXTERNAL_MUTATION = "verify_no_external_mutation"


class SocRolloutRehearsalStepKind(StrEnum):
    """Deterministic virtual steps in one rehearsal report."""

    VALIDATE_PLAN = "validate_plan"
    SIMULATE_STAGE_TRANSITION = "simulate_stage_transition"
    ASSESS_REAL_GATES = "assess_real_gates"
    INJECT_ROLLBACK_TRIGGER = "inject_rollback_trigger"
    SIMULATE_ROLLBACK_ACTION = "simulate_rollback_action"
    VERIFY_SAFETY_BOUNDARIES = "verify_safety_boundaries"


class SocRolloutRehearsalStepOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


REQUIRED_ROLLOUT_OWNER_ROLES = frozenset(SocRolloutOwnerRole)
REQUIRED_ROLLOUT_GATE_STAGES: dict[SocRolloutGateId, frozenset[SocRolloutStage]] = {
    SocRolloutGateId.REAL_PROVIDER_EVIDENCE: frozenset(
        {
            SocRolloutStage.SHADOW,
            SocRolloutStage.LIMITED_PILOT,
            SocRolloutStage.CONTROLLED_ROLLOUT,
        }
    ),
    SocRolloutGateId.PRODUCTION_INFRASTRUCTURE: frozenset(
        {
            SocRolloutStage.SHADOW,
            SocRolloutStage.LIMITED_PILOT,
            SocRolloutStage.CONTROLLED_ROLLOUT,
        }
    ),
    SocRolloutGateId.REAL_QUALITY_EVALUATION: frozenset({SocRolloutStage.LIMITED_PILOT, SocRolloutStage.CONTROLLED_ROLLOUT}),
    SocRolloutGateId.OPERATIONS_SLO: frozenset({SocRolloutStage.LIMITED_PILOT, SocRolloutStage.CONTROLLED_ROLLOUT}),
    SocRolloutGateId.ACCOUNTABLE_OWNERS: frozenset(
        {
            SocRolloutStage.SHADOW,
            SocRolloutStage.LIMITED_PILOT,
            SocRolloutStage.CONTROLLED_ROLLOUT,
        }
    ),
    SocRolloutGateId.ROLLBACK_READINESS: frozenset(
        {
            SocRolloutStage.SHADOW,
            SocRolloutStage.LIMITED_PILOT,
            SocRolloutStage.CONTROLLED_ROLLOUT,
        }
    ),
    SocRolloutGateId.COHORT_ISOLATION: frozenset({SocRolloutStage.CONTROLLED_ROLLOUT}),
}
REQUIRED_ROLLBACK_ACTIONS = tuple(SocRolloutRollbackAction)


class SocRolloutScope(BaseModel):
    """Bounded cohort definition that can be disabled independently."""

    model_config = ConfigDict(extra="forbid")

    cohort_id: str = Field(min_length=1, max_length=128)
    tenant_ids: list[BoundedValue] = Field(min_length=1, max_length=50)
    source_types: list[BoundedValue] = Field(min_length=1, max_length=50)
    scenario_keys: list[BoundedValue] = Field(min_length=1, max_length=100)
    operator_ids: list[BoundedValue] = Field(min_length=1, max_length=100)
    feature_flag: str = Field(min_length=1, max_length=256)
    window_start: datetime
    window_end: datetime
    maximum_alert_count: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_scope(self) -> SocRolloutScope:
        for field_name in ("tenant_ids", "source_types", "scenario_keys", "operator_ids"):
            values = getattr(self, field_name)
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must contain unique values")
        if self.window_end <= self.window_start:
            raise ValueError("rollout scope window_end must be after window_start")
        return self


class SocRolloutOwnerAssignment(BaseModel):
    """Named owner assignment; simulation identities are never real approval."""

    model_config = ConfigDict(extra="forbid")

    role: SocRolloutOwnerRole
    owner_id: str = Field(min_length=1, max_length=128)
    responsibility: str = Field(min_length=1, max_length=1000)
    confirmed_for_real_rollout: bool = False


class SocRolloutGateObservation(BaseModel):
    """One real gate observation with explicit evidence provenance."""

    model_config = ConfigDict(extra="forbid")

    gate_id: SocRolloutGateId
    required_for_stages: list[SocRolloutStage] = Field(min_length=1)
    status: SocRolloutRealGateStatus
    data_class: SocEvaluationDataClass
    mocked: bool
    reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    observed_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_gate_evidence(self) -> SocRolloutGateObservation:
        expected_mocked = self.data_class is SocEvaluationDataClass.SIMULATION
        if self.mocked is not expected_mocked:
            raise ValueError("rollout gate mocked state must match its data class")
        if len(set(self.required_for_stages)) != len(self.required_for_stages):
            raise ValueError("required_for_stages must contain unique stages")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("rollout gate evidence_refs must be unique")
        if self.status is SocRolloutRealGateStatus.PASSED:
            if self.data_class is not SocEvaluationDataClass.DESENSITIZED_REAL:
                raise ValueError("a real rollout gate cannot pass with simulation evidence")
            if not self.evidence_refs or self.observed_at is None:
                raise ValueError("a passed real rollout gate requires evidence refs and observed_at")
        if self.expires_at is not None and self.observed_at is None:
            raise ValueError("expires_at requires observed_at")
        if self.expires_at is not None and self.expires_at <= self.observed_at:
            raise ValueError("rollout gate expires_at must be after observed_at")
        return self


class SocRolloutRollbackPlan(BaseModel):
    """Rollback procedure whose steps are simulated, never executed by PI-05A."""

    model_config = ConfigDict(extra="forbid")

    trigger_ids: list[BoundedValue] = Field(min_length=1, max_length=50)
    actions: list[SocRolloutRollbackAction] = Field(min_length=len(REQUIRED_ROLLBACK_ACTIONS))
    simulated_target_stage: Literal[SocRolloutStage.SHADOW] = SocRolloutStage.SHADOW
    evidence_retention_required: Literal[True] = True
    resume_requires_new_review: Literal[True] = True

    @model_validator(mode="after")
    def validate_rollback_plan(self) -> SocRolloutRollbackPlan:
        if len(set(self.trigger_ids)) != len(self.trigger_ids):
            raise ValueError("rollback trigger_ids must be unique")
        if tuple(self.actions) != REQUIRED_ROLLBACK_ACTIONS:
            raise ValueError("rollback actions must contain the complete ordered v1 safety procedure")
        return self


class SocRolloutPlan(BaseModel):
    """Vendor-neutral plan used by rehearsal, not a mutable rollout state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.rollout_plan.v1"] = "soc.rollout_plan.v1"
    policy_version: Literal["soc.rollout_rehearsal_policy.v1"] = ROLLOUT_REHEARSAL_POLICY_VERSION
    plan_id: str = Field(min_length=1, max_length=128)
    plan_version: str = Field(min_length=1, max_length=128)
    data_class: SocEvaluationDataClass
    mocked: bool
    entry_stage: Literal[SocRolloutStage.SHADOW] = SocRolloutStage.SHADOW
    target_stage: Literal[SocRolloutStage.CONTROLLED_ROLLOUT] = SocRolloutStage.CONTROLLED_ROLLOUT
    scope: SocRolloutScope
    owners: list[SocRolloutOwnerAssignment] = Field(min_length=len(REQUIRED_ROLLOUT_OWNER_ROLES))
    gates: list[SocRolloutGateObservation] = Field(min_length=len(REQUIRED_ROLLOUT_GATE_STAGES))
    rollback: SocRolloutRollbackPlan
    read_only_provider_actions_only: Literal[True] = True
    auto_close_allowed: Literal[False] = False
    external_state_mutation_allowed: Literal[False] = False
    high_risk_action_execution_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_plan_completeness(self) -> SocRolloutPlan:
        if self.mocked is not (self.data_class is SocEvaluationDataClass.SIMULATION):
            raise ValueError("rollout plan mocked state must match its data class")
        owners = {item.role for item in self.owners}
        if len(owners) != len(self.owners) or owners != REQUIRED_ROLLOUT_OWNER_ROLES:
            raise ValueError("rollout plan requires exactly one assignment for every v1 owner role")
        gates = {item.gate_id: item for item in self.gates}
        if len(gates) != len(self.gates) or set(gates) != set(REQUIRED_ROLLOUT_GATE_STAGES):
            raise ValueError("rollout plan requires exactly one observation for every v1 real gate")
        for gate_id, required_stages in REQUIRED_ROLLOUT_GATE_STAGES.items():
            observation = gates[gate_id]
            if set(observation.required_for_stages) != required_stages:
                raise ValueError(f"rollout gate {gate_id.value} cannot weaken its required stages")
            if observation.data_class is not self.data_class or observation.mocked is not self.mocked:
                raise ValueError("rollout gate provenance must match the plan provenance")
        return self


class SocRolloutRehearsalRequest(BaseModel):
    """Explicitly confirmed PI-05A simulation input."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.rollout_rehearsal_request.v1"] = "soc.rollout_rehearsal_request.v1"
    plan: SocRolloutPlan
    injected_rollback_trigger_id: str = Field(min_length=1, max_length=256)
    requested_by: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=2000)
    confirm_simulation_only: Literal[True]

    @model_validator(mode="after")
    def keep_rehearsal_simulated(self) -> SocRolloutRehearsalRequest:
        if self.plan.data_class is not SocEvaluationDataClass.SIMULATION or not self.plan.mocked:
            raise ValueError("PI-05A rehearsal accepts only explicit simulation plans")
        if self.injected_rollback_trigger_id not in self.plan.rollback.trigger_ids:
            raise ValueError("injected rollback trigger must be declared by the plan")
        if any(owner.confirmed_for_real_rollout for owner in self.plan.owners):
            raise ValueError("simulation owner assignments cannot claim real rollout confirmation")
        return self


class SocRolloutGateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: SocRolloutGateId
    required_for_stages: list[SocRolloutStage]
    observed_status: SocRolloutRealGateStatus
    observed_reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)
    real_gate_satisfied: Literal[False] = False
    rehearsal_control_exercised: Literal[True] = True
    blocking_reason: str = Field(min_length=1)


class SocRolloutStageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: SocRolloutStage
    required_gate_ids: list[SocRolloutGateId] = Field(min_length=1)
    blocked_gate_ids: list[SocRolloutGateId] = Field(min_length=1)
    real_promotion_eligible: Literal[False] = False


class SocRolloutRehearsalStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    kind: SocRolloutRehearsalStepKind
    outcome: SocRolloutRehearsalStepOutcome
    detail: str = Field(min_length=1, max_length=2000)
    from_stage: SocRolloutStage | None = None
    to_stage: SocRolloutStage | None = None
    rollback_action: SocRolloutRollbackAction | None = None
    simulated: Literal[True] = True
    external_effect_executed: Literal[False] = False


class SocRolloutRollbackRehearsal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_id: str = Field(min_length=1, max_length=256)
    actions_exercised: list[SocRolloutRollbackAction]
    simulated_target_stage: Literal[SocRolloutStage.SHADOW] = SocRolloutStage.SHADOW
    passed: bool
    external_effect_count: Literal[0] = 0


class SocRolloutRehearsalDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_rehearsal_id: str = Field(min_length=1)
    baseline_semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed: bool
    changed_components: list[str] = Field(default_factory=list)


class SocRolloutRehearsalReport(BaseModel):
    """Replayable proof of the control flow, never proof of real rollout readiness."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.rollout_rehearsal_report.v1"] = "soc.rollout_rehearsal_report.v1"
    rehearsal_id: str = Field(pattern=r"^SRR-[0-9A-F]{12}$")
    generated_at: datetime
    policy_version: Literal["soc.rollout_rehearsal_policy.v1"] = ROLLOUT_REHEARSAL_POLICY_VERSION
    plan_id: str
    plan_version: str
    requested_by: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=2000)
    scope: SocRolloutScope
    owners: list[SocRolloutOwnerAssignment]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_hashes: dict[str, str]
    data_class: Literal[SocEvaluationDataClass.SIMULATION] = SocEvaluationDataClass.SIMULATION
    mocked: Literal[True] = True
    current_real_stage: Literal[SocRolloutStage.NOT_STARTED] = SocRolloutStage.NOT_STARTED
    simulated_entry_stage: Literal[SocRolloutStage.SHADOW] = SocRolloutStage.SHADOW
    simulated_final_stage: Literal[SocRolloutStage.SHADOW] = SocRolloutStage.SHADOW
    engineering_rehearsal_passed: bool
    gate_assessments: list[SocRolloutGateAssessment]
    stage_assessments: list[SocRolloutStageAssessment]
    steps: list[SocRolloutRehearsalStep] = Field(min_length=1)
    rollback: SocRolloutRollbackRehearsal
    simulated_stage_transition_count: int = Field(ge=1)
    real_stage_transition_count: Literal[0] = 0
    external_effect_count: Literal[0] = 0
    stage_transition_allowed: Literal[False] = False
    production_approval_granted: Literal[False] = False
    real_rollout_claim_allowed: Literal[False] = False
    auto_close_allowed: Literal[False] = False
    external_state_mutation_allowed: Literal[False] = False
    high_risk_action_execution_allowed: Literal[False] = False
    limitations: list[str] = Field(min_length=1)
    diff: SocRolloutRehearsalDiff | None = None

    @model_validator(mode="after")
    def validate_rehearsal_boundaries(self) -> SocRolloutRehearsalReport:
        if set(self.component_hashes) != {
            "gate_assessments",
            "plan",
            "rollback",
            "stage_assessments",
            "steps",
        }:
            raise ValueError("rollout rehearsal component hashes are incomplete")
        if {item.gate_id for item in self.gate_assessments} != set(REQUIRED_ROLLOUT_GATE_STAGES):
            raise ValueError("rollout rehearsal gate assessments are incomplete")
        if {item.role for item in self.owners} != REQUIRED_ROLLOUT_OWNER_ROLES:
            raise ValueError("rollout rehearsal owner projection is incomplete")
        if any(item.confirmed_for_real_rollout for item in self.owners):
            raise ValueError("rollout rehearsal cannot project a real owner confirmation")
        expected_stages = {
            SocRolloutStage.SHADOW,
            SocRolloutStage.LIMITED_PILOT,
            SocRolloutStage.CONTROLLED_ROLLOUT,
        }
        if {item.stage for item in self.stage_assessments} != expected_stages:
            raise ValueError("rollout rehearsal stage assessments are incomplete")
        if [item.sequence for item in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("rollout rehearsal steps must have contiguous sequence numbers")
        transition_count = sum(item.kind is SocRolloutRehearsalStepKind.SIMULATE_STAGE_TRANSITION for item in self.steps)
        if self.simulated_stage_transition_count != transition_count:
            raise ValueError("simulated transition count must match rehearsal steps")
        expected_pass = self.rollback.passed and all(item.outcome is SocRolloutRehearsalStepOutcome.PASSED for item in self.steps)
        if self.engineering_rehearsal_passed is not expected_pass:
            raise ValueError("engineering rehearsal pass must match step and rollback outcomes")
        if any(item.real_gate_satisfied for item in self.gate_assessments):
            raise ValueError("a simulation report cannot close a real rollout gate")
        return self


__all__ = [
    "REQUIRED_ROLLBACK_ACTIONS",
    "REQUIRED_ROLLOUT_GATE_STAGES",
    "REQUIRED_ROLLOUT_OWNER_ROLES",
    "ROLLOUT_REHEARSAL_POLICY_VERSION",
    "SocRolloutGateAssessment",
    "SocRolloutGateId",
    "SocRolloutGateObservation",
    "SocRolloutOwnerAssignment",
    "SocRolloutOwnerRole",
    "SocRolloutPlan",
    "SocRolloutRealGateStatus",
    "SocRolloutRehearsalDiff",
    "SocRolloutRehearsalReport",
    "SocRolloutRehearsalRequest",
    "SocRolloutRehearsalStep",
    "SocRolloutRehearsalStepKind",
    "SocRolloutRehearsalStepOutcome",
    "SocRolloutRollbackAction",
    "SocRolloutRollbackPlan",
    "SocRolloutRollbackRehearsal",
    "SocRolloutScope",
    "SocRolloutStage",
    "SocRolloutStageAssessment",
]
