from __future__ import annotations

import pytest

from soc_agent.actions.adapters import (
    ASSET_LOOKUP_ACTION,
    ENDPOINT_PROCESS_TREE_LOOKUP_ACTION,
    HOST_EVENT_CONTEXT_LOOKUP_ACTION,
    SECURITY_TAG_LOOKUP_ACTION,
    THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION,
    DryRunOnlySocActionAdapter,
    InMemoryAssetLookupActionAdapter,
    InMemoryEndpointProcessTreeLookupActionAdapter,
    InMemoryHostEventContextLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    InMemoryThreatIntelIpReputationLookupActionAdapter,
    SocActionAdapterNotFoundError,
    SocActionAdapterRegistry,
    SocActionAdapterRegistryError,
    asset_lookup_adapter_descriptor,
    endpoint_process_tree_lookup_adapter_descriptor,
    host_event_context_lookup_adapter_descriptor,
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
    SocEndpointProcessTreeRecord,
    SocHostEventContextRecord,
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


def test_endpoint_process_tree_adapter_descriptor_is_read_only() -> None:
    descriptor = endpoint_process_tree_lookup_adapter_descriptor()

    assert descriptor.route == ENDPOINT_PROCESS_TREE_LOOKUP_ACTION
    assert descriptor.action == ENDPOINT_PROCESS_TREE_LOOKUP_ACTION
    assert descriptor.risk_level is SocAgentRiskLevel.READ_ONLY
    assert descriptor.external_side_effect == "read"
    assert descriptor.execute_supported is True
    assert descriptor.required_payload_fields == ["host_key"]


def test_endpoint_process_tree_adapter_execute_returns_matching_mock_record() -> None:
    registry = SocActionAdapterRegistry([InMemoryEndpointProcessTreeLookupActionAdapter(records=[_process_tree_record()])])

    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=ENDPOINT_PROCESS_TREE_LOOKUP_ACTION,
            action=ENDPOINT_PROCESS_TREE_LOOKUP_ACTION,
            dry_run=False,
            payload={"host_key": "endpoint-1"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["process_tree_found"] is True
    assert result.payload["process_tree"]["process_tree_id"] == "ptree-unit-001"
    assert result.payload["process_tree"]["processes"][0]["process_name"] == "powershell.exe"
    assert result.payload["external_side_effect"] == "read"
    assert result.payload["read_only"] is True


def test_endpoint_process_tree_adapter_execute_not_found_is_successful_read() -> None:
    registry = SocActionAdapterRegistry([InMemoryEndpointProcessTreeLookupActionAdapter(records=[])])

    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=ENDPOINT_PROCESS_TREE_LOOKUP_ACTION,
            action=ENDPOINT_PROCESS_TREE_LOOKUP_ACTION,
            dry_run=False,
            payload={"host_key": "missing-host"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["process_tree_found"] is False
    assert result.payload["process_tree"] is None
    assert result.payload["external_side_effect"] == "read"


def test_host_event_context_adapter_descriptor_is_read_only() -> None:
    descriptor = host_event_context_lookup_adapter_descriptor()

    assert descriptor.route == HOST_EVENT_CONTEXT_LOOKUP_ACTION
    assert descriptor.action == HOST_EVENT_CONTEXT_LOOKUP_ACTION
    assert descriptor.risk_level is SocAgentRiskLevel.READ_ONLY
    assert descriptor.external_side_effect == "read"
    assert descriptor.execute_supported is True
    assert descriptor.required_payload_fields == ["host_key"]


def test_host_event_context_adapter_execute_returns_matching_mock_record() -> None:
    registry = SocActionAdapterRegistry([InMemoryHostEventContextLookupActionAdapter(records=[_host_context_record()])])

    result = registry.execute(
        SocAgentApprovedActionCommand(
            execution_token_id="SAT-test",
            route=HOST_EVENT_CONTEXT_LOOKUP_ACTION,
            action=HOST_EVENT_CONTEXT_LOOKUP_ACTION,
            dry_run=False,
            payload={"host_key": "web-01"},
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["host_event_context_found"] is True
    assert result.payload["host_event_context"]["host_key"] == "web-01"
    assert result.payload["host_event_context"]["related_commands"][0]["command"] == "whoami"
    assert result.payload["external_side_effect"] == "read"
    assert result.payload["read_only"] is True


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


def _process_tree_record() -> SocEndpointProcessTreeRecord:
    return SocEndpointProcessTreeRecord(
        host_key="endpoint-1",
        hostname="endpoint-1",
        primary_ip="10.10.1.5",
        process_tree_id="ptree-unit-001",
        processes=[
            {
                "pid": 4200,
                "parent_pid": 700,
                "process_name": "powershell.exe",
                "command_line": "powershell.exe -nop -w hidden",
                "user": "UM001",
                "risk_tags": ["suspicious_powershell"],
            }
        ],
        network_connections=[
            {
                "process_name": "powershell.exe",
                "remote_ip": "203.0.113.10",
                "remote_port": 80,
                "direction": "outbound",
                "protocol": "tcp",
            }
        ],
        source="unit-test",
        mocked=True,
    )


def _host_context_record() -> SocHostEventContextRecord:
    return SocHostEventContextRecord(
        host_key="web-01",
        hostname="web-01",
        primary_ip="10.10.2.15",
        recent_logins=[{"user": "enterprise-user-1", "source_ip": "10.20.0.15", "result": "success"}],
        related_commands=[{"user": "enterprise-user-1", "command": "whoami", "process_name": "bash"}],
        source_ips=["10.20.0.15"],
        related_events=[{"event_type": "process_execution", "summary": "Command executed during alert window."}],
        source="unit-test",
        mocked=True,
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
