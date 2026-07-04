from __future__ import annotations

import pytest

from soc_agent.action_adapters import (
    DryRunOnlySocActionAdapter,
    SocActionAdapterNotFoundError,
    SocActionAdapterRegistry,
    SocActionAdapterRegistryError,
)
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocAgentActionAdapterDescriptor,
    SocAgentApprovedActionCommand,
    SocAgentRiskLevel,
)


def test_registry_rejects_duplicate_route_action_adapter() -> None:
    registry = SocActionAdapterRegistry()
    adapter = DryRunOnlySocActionAdapter(_block_ip_descriptor())

    registry.register(adapter)

    with pytest.raises(SocActionAdapterRegistryError, match="already registered"):
        registry.register(DryRunOnlySocActionAdapter(_block_ip_descriptor(adapter_id="another-adapter")))


def test_registry_requires_explicit_adapter_for_action() -> None:
    registry = SocActionAdapterRegistry()

    with pytest.raises(SocActionAdapterNotFoundError, match="no action adapter registered"):
        registry.get(route="response.block_ip", action="response.block_ip")


def test_dry_run_adapter_validates_payload_and_context_refs() -> None:
    registry = SocActionAdapterRegistry([DryRunOnlySocActionAdapter(_block_ip_descriptor())])
    result = registry.dry_run(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route="response.block_ip",
            action="response.block_ip",
            dry_run=True,
            payload={
                "ip": "198.51.100.10",
                "duration_seconds": 900,
                "context_refs": {"queue_id": "REV-1", "run_id": "RUN-1"},
            },
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["adapter_id"] == "test-block-ip"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["required_payload_fields"] == ["ip", "duration_seconds"]
    assert result.payload["required_context_refs"] == ["queue_id", "run_id"]


def test_dry_run_adapter_rejects_missing_required_payload_field() -> None:
    registry = SocActionAdapterRegistry([DryRunOnlySocActionAdapter(_block_ip_descriptor())])

    with pytest.raises(SocActionAdapterRegistryError, match="missing required payload fields: duration_seconds"):
        registry.dry_run(
            SocAgentApprovedActionCommand(
                execution_token_id="SAT-test",
                route="response.block_ip",
                action="response.block_ip",
                dry_run=True,
                payload={"ip": "198.51.100.10", "context_refs": {"queue_id": "REV-1", "run_id": "RUN-1"}},
            ),
            context=_context(),
        )


def test_dry_run_only_adapter_execute_returns_not_executed_failure() -> None:
    registry = SocActionAdapterRegistry([DryRunOnlySocActionAdapter(_block_ip_descriptor())])
    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route="response.block_ip",
            action="response.block_ip",
            dry_run=False,
            payload={
                "ip": "198.51.100.10",
                "duration_seconds": 900,
                "context_refs": {"queue_id": "REV-1", "run_id": "RUN-1"},
            },
        ),
        context=_context(),
    )

    assert result.status == "failed"
    assert result.payload["external_side_effect"] == "not_executed"
    assert "dry-run only" in result.message


def _block_ip_descriptor(*, adapter_id: str = "test-block-ip") -> SocAgentActionAdapterDescriptor:
    return SocAgentActionAdapterDescriptor(
        adapter_id=adapter_id,
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        adapter_kind="mcp",
        external_side_effect="write",
        execute_supported=False,
        required_payload_fields=["ip", "duration_seconds"],
        required_context_refs=["queue_id", "run_id"],
        description="Test block-ip adapter descriptor.",
    )


def _context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-admin",
            actor_type=ActorType.USER,
            surface=EntrySurface.TEST,
            roles=["soc_admin"],
        ),
        trace_id="trace-test",
        idempotency_key="action-adapter-test",
    )
