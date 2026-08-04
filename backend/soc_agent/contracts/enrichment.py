"""Contracts for deterministic, read-only SOC investigation enrichment."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_network
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SocEnrichmentPlanStatus(StrEnum):
    """Lifecycle state of one immutable enrichment plan."""

    PLANNED = "planned"
    NO_ACTIONS = "no_actions"
    BLOCKED = "blocked"


class SocEnrichmentResultMode(StrEnum):
    """Result provenance required by one application composition."""

    MOCK = "mock"
    REAL = "real"


class SocEnrichmentAdapterProvenanceContract(StrEnum):
    """How an adapter declares whether its provider result is mocked."""

    MOCK_ONLY = "mock_only"
    RUNTIME_DECLARED = "runtime_declared"
    REAL_ONLY = "real_only"


class SocEnrichmentExecutionTrigger(StrEnum):
    """Entry surface that requested one persisted investigation execution."""

    KAFKA = "kafka"
    INTERNAL_BATCH = "internal_batch"
    MANUAL = "manual"
    REPLAY = "replay"


class SocEnrichmentExecutionStatus(StrEnum):
    """Durable lifecycle of one read-only investigation execution."""

    RUNNING = "running"
    COMPLETED = "completed"
    NO_ACTIONS = "no_actions"
    BLOCKED = "blocked"
    RETRYABLE_FAILED = "retryable_failed"
    FAILED = "failed"


class SocEnrichmentAttemptStatus(StrEnum):
    """Durable outcome of one planned action attempt."""

    RUNNING = "running"
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    PROVIDER_FAILED = "provider_failed"
    CONTRACT_FAILED = "contract_failed"
    DENIED = "denied"
    INTERRUPTED = "interrupted"


class SocEnrichmentSkipReason(StrEnum):
    """Stable reason why a route or entity did not become an action."""

    RUN_NOT_ANALYZABLE = "run_not_analyzable"
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_ENTITY = "invalid_entity"
    NO_ELIGIBLE_ENTITY = "no_eligible_entity"
    NETWORK_SCOPE_UNCONFIGURED = "network_scope_unconfigured"
    INTERNAL_OR_NON_GLOBAL_IP = "internal_or_non_global_ip"
    ACTION_BUDGET_EXHAUSTED = "action_budget_exhausted"


class SocEnrichmentPolicy(BaseModel):
    """Tenant-owned allowlist and budget for automatic read-only investigation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_policy.v1"] = "soc.enrichment_policy.v1"
    policy_version: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    enabled_routes: list[str] = Field(default_factory=list, max_length=20)
    asset_route: Literal["asset.lookup", "asset.locate"] | None = None
    internal_networks: list[str] = Field(default_factory=list, max_length=200)
    threat_intel_requires_internal_networks: bool = True
    max_actions_total: int = Field(default=8, ge=1, le=50)
    max_actions_per_route: int = Field(default=3, ge=1, le=20)
    asset_entity_kinds: list[Literal["ip", "domain", "host", "user"]] = Field(default_factory=lambda: ["ip", "domain", "host", "user"])
    asset_roles: list[str] = Field(
        default_factory=lambda: [
            "impacted_asset",
            "victim",
            "destination",
            "destination_ip",
            "host_ip",
            "host_name",
            "process_observation_host",
            "um_account",
            "user_id",
            "username",
        ],
        max_length=50,
    )
    threat_intel_roles: list[str] = Field(
        default_factory=lambda: [
            "attacker",
            "source",
            "source_ip",
            "threat_ioc",
            "process_command_line_ip",
            "parent_process_command_line_ip",
        ],
        max_length=50,
    )
    security_tag_entity_kinds: list[Literal["ip", "domain", "host", "user"]] = Field(default_factory=lambda: ["ip", "domain", "host", "user"])
    security_tag_roles: list[str] = Field(
        default_factory=lambda: [
            "attacker",
            "victim",
            "source",
            "destination",
            "source_ip",
            "destination_ip",
            "threat_ioc",
            "host_ip",
            "host_name",
            "process_observation_host",
            "um_account",
            "user_id",
            "username",
        ],
        max_length=50,
    )

    @field_validator(
        "enabled_routes",
        "asset_entity_kinds",
        "asset_roles",
        "threat_intel_roles",
        "security_tag_entity_kinds",
        "security_tag_roles",
    )
    @classmethod
    def unique_non_empty_values(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("enrichment policy lists cannot contain empty values")
            if item not in seen:
                normalized.append(item)
                seen.add(item)
        return normalized

    @field_validator("internal_networks")
    @classmethod
    def valid_internal_networks(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            network = ip_network(value.strip(), strict=False)
            canonical = str(network)
            if canonical not in seen:
                normalized.append(canonical)
                seen.add(canonical)
        return normalized

    @model_validator(mode="after")
    def asset_route_must_be_allowlisted(self) -> SocEnrichmentPolicy:
        supported_routes = {
            "asset.lookup",
            "asset.locate",
            "security_tag.lookup",
            "threat_intel.ip_reputation.lookup",
        }
        unsupported = sorted(set(self.enabled_routes).difference(supported_routes))
        if unsupported:
            raise ValueError(f"unsupported automatic enrichment routes: {unsupported}")
        enabled_asset_routes = set(self.enabled_routes).intersection({"asset.lookup", "asset.locate"})
        if len(enabled_asset_routes) > 1:
            raise ValueError("automatic enrichment cannot enable both asset.lookup and asset.locate")
        if self.asset_route is not None and self.asset_route not in self.enabled_routes:
            raise ValueError("asset_route must also be present in enabled_routes")
        if enabled_asset_routes and self.asset_route not in enabled_asset_routes:
            raise ValueError("an enabled asset route must be selected explicitly through asset_route")
        return self


class SocEnrichmentRetryPolicy(BaseModel):
    """Bounded retry and stale-claim policy for persistent investigation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_retry_policy.v1"] = "soc.enrichment_retry_policy.v1"
    max_attempts_per_action: int = Field(default=3, ge=1, le=10)
    stale_after_seconds: int = Field(default=300, ge=1, le=86400)


class SocEnrichmentAdapterBinding(BaseModel):
    """Exact route-to-adapter identity selected by the composition root."""

    model_config = ConfigDict(extra="forbid")

    route: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    adapter_id: str = Field(min_length=1, max_length=256)
    adapter_kind: Literal["service", "mcp", "http", "script"]

    @model_validator(mode="after")
    def route_matches_action(self) -> SocEnrichmentAdapterBinding:
        supported_routes = {
            "asset.lookup",
            "asset.locate",
            "security_tag.lookup",
            "threat_intel.ip_reputation.lookup",
        }
        if self.route not in supported_routes:
            raise ValueError(f"unsupported enrichment adapter route: {self.route}")
        if self.action != self.route:
            raise ValueError("enrichment adapter binding action must exactly match its route")
        return self


class SocEnrichmentCompositionConfig(BaseModel):
    """Default-off application wiring for deterministic enrichment planning."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_composition.v1"] = "soc.enrichment_composition.v1"
    enabled: bool = False
    required_result_mode: SocEnrichmentResultMode | None = None
    policy: SocEnrichmentPolicy = Field(default_factory=lambda: SocEnrichmentPolicy(policy_version="enrichment-disabled-v1"))
    retry_policy: SocEnrichmentRetryPolicy = Field(default_factory=SocEnrichmentRetryPolicy)
    bindings: list[SocEnrichmentAdapterBinding] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_composition(self) -> SocEnrichmentCompositionConfig:
        if not self.enabled:
            if self.required_result_mode is not None:
                raise ValueError("disabled enrichment composition cannot require a result mode")
            if self.policy.enabled_routes:
                raise ValueError("disabled enrichment composition cannot enable policy routes")
            if self.bindings:
                raise ValueError("disabled enrichment composition cannot declare adapter bindings")
            return self

        if self.policy.tenant_id is None:
            raise ValueError("enabled enrichment composition requires an explicit tenant_id")
        if not self.policy.enabled_routes:
            raise ValueError("enabled enrichment composition requires at least one policy route")
        if self.required_result_mode is None:
            raise ValueError("enabled enrichment composition requires required_result_mode")

        binding_routes = [binding.route for binding in self.bindings]
        if len(binding_routes) != len(set(binding_routes)):
            raise ValueError("enrichment composition cannot bind the same route more than once")
        expected_routes = set(self.policy.enabled_routes)
        actual_routes = set(binding_routes)
        if actual_routes != expected_routes:
            missing = sorted(expected_routes.difference(actual_routes))
            extra = sorted(actual_routes.difference(expected_routes))
            raise ValueError(f"enrichment adapter bindings must exactly match enabled routes; missing={missing}, extra={extra}")
        if "threat_intel.ip_reputation.lookup" in expected_routes and self.policy.threat_intel_requires_internal_networks and not self.policy.internal_networks:
            raise ValueError("enabled threat-intel enrichment requires explicit tenant internal_networks")
        return self


class SocEnrichmentPlannedAction(BaseModel):
    """One replayable read-only action proposed by the deterministic planner."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=64)
    route: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    reason_code: Literal[
        "asset_context_required",
        "ip_reputation_required",
        "security_tag_context_required",
    ]
    rationale: str = Field(min_length=1, max_length=1000)
    payload: dict[str, Any]
    entity_key: str = Field(min_length=1, max_length=2048)
    entity_kind: Literal["ip", "domain", "host", "user"]
    entity_role: str | None = Field(default=None, max_length=128)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    deduplication_key: str = Field(min_length=1, max_length=128)
    read_only: Literal[True] = True
    decision_impact: Literal["none"] = "none"

    @model_validator(mode="after")
    def route_matches_action(self) -> SocEnrichmentPlannedAction:
        supported_routes = {
            "asset.lookup",
            "asset.locate",
            "security_tag.lookup",
            "threat_intel.ip_reputation.lookup",
        }
        if self.route not in supported_routes:
            raise ValueError(f"unsupported planned enrichment route: {self.route}")
        if self.action != self.route:
            raise ValueError("planned enrichment action must exactly match its route")
        return self


class SocEnrichmentSkippedCandidate(BaseModel):
    """Auditable route or entity omitted by planner policy."""

    model_config = ConfigDict(extra="forbid")

    route: str | None = Field(default=None, max_length=128)
    entity_key: str | None = Field(default=None, max_length=2048)
    entity_kind: Literal["ip", "domain", "host", "user"] | None = None
    entity_role: str | None = Field(default=None, max_length=128)
    reason_code: SocEnrichmentSkipReason
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class SocEnrichmentPlan(BaseModel):
    """Immutable plan produced before any MCP/service provider invocation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_plan.v1"] = "soc.enrichment_plan.v1"
    plan_id: str = Field(min_length=1, max_length=64)
    policy_version: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    thread_id: str = Field(min_length=1, max_length=256)
    input_hash: str = Field(min_length=64, max_length=64)
    status: SocEnrichmentPlanStatus
    actions: list[SocEnrichmentPlannedAction] = Field(default_factory=list, max_length=50)
    skipped: list[SocEnrichmentSkippedCandidate] = Field(default_factory=list, max_length=200)
    decision_immutable: Literal[True] = True
    execution_boundary: Literal["soc_action_dispatcher"] = "soc_action_dispatcher"
    high_risk_actions_allowed: Literal[False] = False

    @model_validator(mode="after")
    def status_matches_actions(self) -> SocEnrichmentPlan:
        if self.status is SocEnrichmentPlanStatus.PLANNED and not self.actions:
            raise ValueError("planned enrichment status requires at least one action")
        if self.status is not SocEnrichmentPlanStatus.PLANNED and self.actions:
            raise ValueError("only planned enrichment status may contain actions")
        return self


class SocEnrichmentExecutionCommand(BaseModel):
    """Start or resume persisted investigation for one existing Runtime run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_execution_command.v1"] = "soc.enrichment_execution_command.v1"
    run_id: str = Field(min_length=1, max_length=64)
    thread_id: str = Field(min_length=1, max_length=256)
    trigger: SocEnrichmentExecutionTrigger


class SocEnrichmentReplayCommand(BaseModel):
    """Create a linked execution using the current reviewed composition."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_replay_command.v1"] = "soc.enrichment_replay_command.v1"
    execution_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


class SocEnrichmentExecution(BaseModel):
    """Persisted immutable-plan envelope and mutable execution lifecycle."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_execution.v1"] = "soc.enrichment_execution.v1"
    execution_id: str = Field(default_factory=lambda: f"EEXEC-{uuid4().hex[:16].upper()}", max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=512)
    trigger: SocEnrichmentExecutionTrigger
    run_id: str = Field(min_length=1, max_length=64)
    alert_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=256)
    plan: SocEnrichmentPlan
    composition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_result_mode: SocEnrichmentResultMode
    status: SocEnrichmentExecutionStatus = SocEnrichmentExecutionStatus.RUNNING
    version: int = Field(default=1, ge=1)
    replay_of_execution_id: str | None = Field(default=None, max_length=64)
    replay_reason: str | None = Field(default=None, max_length=1000)
    attempt_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    not_found_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    retryable: bool = False
    last_error_type: str | None = Field(default=None, max_length=256)
    last_error: str | None = Field(default=None, max_length=1000)
    request_id: str | None = Field(default=None, max_length=256)
    trace_id: str | None = Field(default=None, max_length=256)
    actor_id: str = Field(min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    decision_impact: Literal["none"] = "none"
    high_risk_actions_executed: Literal[False] = False

    @model_validator(mode="after")
    def validate_execution(self) -> SocEnrichmentExecution:
        if self.plan.run_id != self.run_id or self.plan.alert_id != self.alert_id:
            raise ValueError("enrichment execution run/alert must match its persisted plan")
        if self.plan.thread_id != self.thread_id:
            raise ValueError("enrichment execution thread must match its persisted plan")
        if self.trigger is SocEnrichmentExecutionTrigger.REPLAY:
            if self.replay_of_execution_id is None or self.replay_reason is None:
                raise ValueError("replay execution requires source execution and reason")
        elif self.replay_of_execution_id is not None or self.replay_reason is not None:
            raise ValueError("only replay execution may reference a prior execution")
        terminal = self.status is not SocEnrichmentExecutionStatus.RUNNING
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal enrichment execution status must match completed_at")
        if self.status is SocEnrichmentExecutionStatus.NO_ACTIONS and self.plan.status is not SocEnrichmentPlanStatus.NO_ACTIONS:
            raise ValueError("no_actions execution requires a no_actions plan")
        if self.status is SocEnrichmentExecutionStatus.BLOCKED and self.plan.status is not SocEnrichmentPlanStatus.BLOCKED:
            raise ValueError("blocked execution requires a blocked plan")
        if self.evidence_count > self.success_count + self.not_found_count:
            raise ValueError("enrichment evidence count cannot exceed successful terminal attempts")
        return self


class SocEnrichmentActionAttempt(BaseModel):
    """One durable action attempt without duplicating the Provider response."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_action_attempt.v1"] = "soc.enrichment_action_attempt.v1"
    attempt_id: str = Field(default_factory=lambda: f"EATT-{uuid4().hex[:16].upper()}", max_length=64)
    execution_id: str = Field(min_length=1, max_length=64)
    plan_action_id: str = Field(min_length=1, max_length=64)
    attempt_number: int = Field(ge=1, le=10)
    action_idempotency_key: str = Field(min_length=1, max_length=512)
    route: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    adapter_id: str = Field(min_length=1, max_length=256)
    status: SocEnrichmentAttemptStatus = SocEnrichmentAttemptStatus.RUNNING
    version: int = Field(default=1, ge=1)
    provider_invoked: bool = False
    result_mode: SocEnrichmentResultMode | None = None
    retryable: bool = False
    evidence_id: str | None = Field(default=None, max_length=64)
    error_type: str | None = Field(default=None, max_length=256)
    error: str | None = Field(default=None, max_length=1000)
    result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_id: str | None = Field(default=None, max_length=256)
    trace_id: str | None = Field(default=None, max_length=256)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def validate_attempt(self) -> SocEnrichmentActionAttempt:
        terminal = self.status is not SocEnrichmentAttemptStatus.RUNNING
        if terminal != (self.ended_at is not None):
            raise ValueError("terminal enrichment attempt status must match ended_at")
        if self.status in {SocEnrichmentAttemptStatus.SUCCESS, SocEnrichmentAttemptStatus.NOT_FOUND}:
            if self.result_mode is None or self.evidence_id is None or self.result_hash is None:
                raise ValueError("successful enrichment attempt requires result mode, hash and evidence")
        elif self.evidence_id is not None:
            raise ValueError("failed or interrupted enrichment attempt cannot reference evidence")
        if self.retryable and self.status not in {
            SocEnrichmentAttemptStatus.PROVIDER_FAILED,
            SocEnrichmentAttemptStatus.INTERRUPTED,
        }:
            raise ValueError("only provider failure or interruption may be retryable")
        return self


class SocEnrichmentWorkflowResult(BaseModel):
    """Accurate persistence and invocation report returned by the D3 workflow."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.enrichment_workflow_result.v1"] = "soc.enrichment_workflow_result.v1"
    execution: SocEnrichmentExecution
    attempts: list[SocEnrichmentActionAttempt] = Field(default_factory=list, max_length=500)
    idempotent_replay: bool = False
    provider_invocation_count: int = Field(default=0, ge=0)
    execution_persisted: Literal[True] = True
    attempts_persisted: Literal[True] = True
    evidence_persisted_count: int = Field(default=0, ge=0)
    base_run_mutated: Literal[False] = False


__all__ = [
    "SocEnrichmentActionAttempt",
    "SocEnrichmentAdapterBinding",
    "SocEnrichmentAdapterProvenanceContract",
    "SocEnrichmentCompositionConfig",
    "SocEnrichmentAttemptStatus",
    "SocEnrichmentExecution",
    "SocEnrichmentExecutionCommand",
    "SocEnrichmentExecutionStatus",
    "SocEnrichmentExecutionTrigger",
    "SocEnrichmentPlan",
    "SocEnrichmentPlannedAction",
    "SocEnrichmentPlanStatus",
    "SocEnrichmentPolicy",
    "SocEnrichmentReplayCommand",
    "SocEnrichmentResultMode",
    "SocEnrichmentRetryPolicy",
    "SocEnrichmentSkippedCandidate",
    "SocEnrichmentSkipReason",
    "SocEnrichmentWorkflowResult",
]
