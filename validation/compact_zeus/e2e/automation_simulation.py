"""Isolated governed-action simulation for the ten-alert E2E package."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from soc_agent.actions.adapters import SocActionAdapterRegistry
from soc_agent.automation import load_soc_automation_policy
from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    ActorType,
    AnalysisRun,
    EntrySurface,
    ServiceRequestContext,
    SocAgentActionAdapterDescriptor,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentRiskLevel,
    SocAutomationEvaluationResult,
)
from soc_agent.core import SocAutomationService
from soc_agent.protocols import MemoryRecordRepository, SocAutomationRepository

DEFAULT_SIMULATION_POLICY = Path(__file__).with_name(
    "automation-policy.simulation.json"
)


class E2ESimulatedNetworkBlockAdapter:
    """Model a high-risk response without performing an external operation."""

    descriptor = SocAgentActionAdapterDescriptor(
        adapter_id="e2e-simulated-network-block",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        adapter_kind="service",
        external_side_effect="write",
        dry_run_supported=True,
        execute_supported=True,
        idempotency_required=True,
        required_payload_fields=["ip", "duration_seconds"],
        required_context_refs=["run_id", "alert_id"],
        description=(
            "E2E-only response simulation. It records the governed execution "
            "contract but never calls a firewall, EDR, SOAR, or vendor API."
        ),
        metadata={
            "validation_only": True,
            "mocked": True,
            "provider_mode": "e2e_simulation",
        },
    )

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="E2E simulated network-block preflight passed.",
            payload={
                "mocked": True,
                "provider_mode": "e2e_simulation",
                "external_side_effect": "not_executed",
            },
        )

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        idempotency_key = context.idempotency_key
        if not idempotency_key:
            raise ValueError("E2E simulated response requires an idempotency key")
        target = str(command.payload.get("ip") or "").strip()
        request_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="E2E simulated network block recorded; no external system was called.",
            payload={
                "mocked": True,
                "provider_mode": "e2e_simulation",
                "external_side_effect": "simulated_only",
                "external_request_id": f"SIM-{request_id.upper()}",
                "external_state_before": {
                    "target": target,
                    "blocked": False,
                    "source": "simulation",
                },
                "external_state_after": {
                    "target": target,
                    "blocked": True,
                    "source": "simulation",
                },
            },
        )


def run_governed_automation_simulation(
    runs: Sequence[AnalysisRun],
    *,
    automation_repository: SocAutomationRepository,
    memory_repository: MemoryRecordRepository,
    policy_path: Path = DEFAULT_SIMULATION_POLICY,
) -> list[SocAutomationEvaluationResult]:
    """Evaluate the real automation service against a fake external adapter."""

    registry = SocActionAdapterRegistry([E2ESimulatedNetworkBlockAdapter()])
    policy = load_soc_automation_policy(policy_path)
    service = SocAutomationService(
        repository=automation_repository,
        policy=policy,
        environment=policy.environment,
        memory_repository=memory_repository,
        action_adapter_registry=registry,
        execute_authorized_actions=True,
    )
    actor = ActorContext(
        actor_id="soc-e2e-governed-automation",
        actor_type=ActorType.SYSTEM,
        surface=EntrySurface.DAEMON,
        roles=["soc_automation", "validation_fixture"],
        auth_source=ActorAuthSource.SYSTEM,
    )
    results: list[SocAutomationEvaluationResult] = []
    for run in runs:
        results.append(
            service.evaluate(
                run,
                context=ServiceRequestContext(
                    actor=actor,
                    trace_id=run.run_id,
                    idempotency_key=f"soc-e2e-automation:{run.run_id}",
                ),
            )
        )
    return results


__all__ = [
    "DEFAULT_SIMULATION_POLICY",
    "E2ESimulatedNetworkBlockAdapter",
    "run_governed_automation_simulation",
]
