from __future__ import annotations

import pytest

from soc_agent.actions.adapters import (
    ASSET_LOOKUP_ACTION,
    SECURITY_TAG_LOOKUP_ACTION,
    THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION,
    DryRunOnlySocActionAdapter,
    InMemoryAssetLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    InMemoryThreatIntelIpReputationLookupActionAdapter,
    SocActionAdapterNotFoundError,
    SocActionAdapterRegistry,
    SocActionAdapterRegistryError,
    asset_lookup_adapter_descriptor,
    security_tag_lookup_adapter_descriptor,
    threat_intel_ip_reputation_lookup_adapter_descriptor,
)
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocAgentActionAdapterDescriptor,
    SocAgentActionResult,
    SocAgentApprovedActionCommand,
    SocAgentRiskLevel,
    SocAssetLookupRecord,
    SocSecurityTagRecord,
    SocThreatIntelReputationRecord,
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


def test_registry_execute_preflight_validates_without_calling_adapter_execute() -> None:
    adapter = _ExecutableAdapter(_block_ip_descriptor(adapter_id="exec-block-ip", execute_supported=True))
    registry = SocActionAdapterRegistry([adapter])

    result = registry.preflight_execute(
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

    assert adapter.execute_calls == 0
    assert result.status == "success"
    assert result.payload["adapter_id"] == "exec-block-ip"
    assert result.payload["adapter_execute_supported"] is True
    assert result.payload["adapter_external_side_effect"] == "write"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["preflight_only"] is True


def test_registry_execute_preflight_rejects_dry_run_only_adapter() -> None:
    registry = SocActionAdapterRegistry([DryRunOnlySocActionAdapter(_block_ip_descriptor())])

    with pytest.raises(SocActionAdapterRegistryError, match="does not support execute"):
        registry.preflight_execute(
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


def test_asset_lookup_adapter_descriptor_is_read_only() -> None:
    descriptor = asset_lookup_adapter_descriptor()

    assert descriptor.route == ASSET_LOOKUP_ACTION
    assert descriptor.action == ASSET_LOOKUP_ACTION
    assert descriptor.risk_level is SocAgentRiskLevel.READ_ONLY
    assert descriptor.external_side_effect == "read"
    assert descriptor.execute_supported is True
    assert descriptor.required_payload_fields == ["asset_key"]


def test_asset_lookup_adapter_dry_run_validates_query_without_reading_inventory() -> None:
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[_asset_record()])])

    result = registry.dry_run(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=ASSET_LOOKUP_ACTION,
            action=ASSET_LOOKUP_ACTION,
            dry_run=True,
            payload={"asset_key": "10.10.1.5"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["asset_key"] == "10.10.1.5"
    assert result.payload["asset_found"] is None
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["read_only"] is True


def test_asset_lookup_adapter_execute_returns_matching_asset_record() -> None:
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[_asset_record()])])

    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=ASSET_LOOKUP_ACTION,
            action=ASSET_LOOKUP_ACTION,
            dry_run=False,
            payload={"asset_key": "srv-payments-01"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["asset_found"] is True
    assert result.payload["asset_record"]["asset_id"] == "asset-001"
    assert result.payload["asset_record"]["business_unit"] == "payments"
    assert result.payload["external_side_effect"] == "read"
    assert result.payload["read_only"] is True


def test_asset_lookup_adapter_execute_preflight_validates_without_inventory_read() -> None:
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[])])

    result = registry.preflight_execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=ASSET_LOOKUP_ACTION,
            action=ASSET_LOOKUP_ACTION,
            dry_run=False,
            payload={"asset_key": "10.10.1.5"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["adapter_id"] == "asset-lookup-in-memory"
    assert result.payload["adapter_execute_supported"] is True
    assert result.payload["adapter_external_side_effect"] == "read"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["preflight_only"] is True


def test_asset_lookup_adapter_execute_not_found_is_successful_read() -> None:
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[_asset_record()])])

    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=ASSET_LOOKUP_ACTION,
            action=ASSET_LOOKUP_ACTION,
            dry_run=False,
            payload={"asset_key": "missing-host"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["asset_found"] is False
    assert result.payload["asset_record"] is None
    assert result.payload["external_side_effect"] == "read"


def test_threat_intel_ip_reputation_adapter_descriptor_is_read_only() -> None:
    descriptor = threat_intel_ip_reputation_lookup_adapter_descriptor()

    assert descriptor.route == THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION
    assert descriptor.action == THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION
    assert descriptor.risk_level is SocAgentRiskLevel.READ_ONLY
    assert descriptor.external_side_effect == "read"
    assert descriptor.execute_supported is True
    assert descriptor.required_payload_fields == ["ip"]


def test_threat_intel_ip_reputation_adapter_execute_returns_matching_mock_record() -> None:
    registry = SocActionAdapterRegistry([InMemoryThreatIntelIpReputationLookupActionAdapter(records=[_threat_intel_record()])])

    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION,
            action=THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION,
            dry_run=False,
            payload={"ip": "203.0.113.10"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["reputation_found"] is True
    assert result.payload["reputation"]["score"] == 82
    assert "c2_candidate" in result.payload["reputation"]["labels"]
    assert result.payload["external_side_effect"] == "read"
    assert result.payload["read_only"] is True


def test_security_tag_adapter_descriptor_is_read_only() -> None:
    descriptor = security_tag_lookup_adapter_descriptor()

    assert descriptor.route == SECURITY_TAG_LOOKUP_ACTION
    assert descriptor.action == SECURITY_TAG_LOOKUP_ACTION
    assert descriptor.risk_level is SocAgentRiskLevel.READ_ONLY
    assert descriptor.external_side_effect == "read"
    assert descriptor.execute_supported is True
    assert descriptor.required_payload_fields == ["entity_key"]


def test_security_tag_adapter_execute_returns_matching_mock_record() -> None:
    registry = SocActionAdapterRegistry([InMemorySecurityTagLookupActionAdapter(records=[_security_tag_record()])])

    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=SECURITY_TAG_LOOKUP_ACTION,
            action=SECURITY_TAG_LOOKUP_ACTION,
            dry_run=False,
            payload={"entity_key": "host:web-01"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["security_tag_found"] is True
    assert result.payload["has_active"] is True
    assert result.payload["security_tag"]["labels"] == ["authorized_maintenance"]
    assert result.payload["external_side_effect"] == "read"
    assert result.payload["read_only"] is True


class _ExecutableAdapter:
    def __init__(self, descriptor: SocAgentActionAdapterDescriptor) -> None:
        self.descriptor = descriptor
        self.execute_calls = 0

    def dry_run(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="test dry-run",
            payload={"adapter_id": self.descriptor.adapter_id},
        )

    def execute(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        self.execute_calls += 1
        raise AssertionError("preflight_execute must not call adapter.execute")


def _block_ip_descriptor(
    *,
    adapter_id: str = "test-block-ip",
    execute_supported: bool = False,
) -> SocAgentActionAdapterDescriptor:
    return SocAgentActionAdapterDescriptor(
        adapter_id=adapter_id,
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        adapter_kind="mcp",
        external_side_effect="write",
        execute_supported=execute_supported,
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


def _asset_record() -> SocAssetLookupRecord:
    return SocAssetLookupRecord(
        asset_key="srv-payments-01",
        asset_id="asset-001",
        hostname="srv-payments-01",
        primary_ip="10.10.1.5",
        owner="payments-sre",
        business_unit="payments",
        environment="prod",
        criticality="critical",
        source="unit-test",
    )


def _threat_intel_record() -> SocThreatIntelReputationRecord:
    return SocThreatIntelReputationRecord(
        ip="203.0.113.10",
        labels=["c2_candidate"],
        confidence=0.82,
        score=82,
        source="unit-test",
        mocked=True,
    )


def _security_tag_record() -> SocSecurityTagRecord:
    return SocSecurityTagRecord(
        entity_key="host:web-01",
        entity_type="host",
        labels=["authorized_maintenance"],
        tag_types=["maintenance"],
        is_valid=True,
        source="unit-test",
        mocked=True,
    )
