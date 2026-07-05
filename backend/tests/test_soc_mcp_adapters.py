from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from soc_agent.action_adapters import SocActionAdapterRegistry, SocActionAdapterRegistryError
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocAgentActionCommand,
    SocAgentRiskLevel,
)
from soc_agent.mcp_adapters import (
    SocMcpToolActionAdapter,
    SocMcpToolDescriptor,
    SocMcpToolNotFoundError,
    SocMcpToolProviderError,
    mcp_read_only_adapter_descriptor,
)


def test_mcp_read_only_descriptor_sets_adapter_contract() -> None:
    descriptor = _asset_lookup_descriptor()

    assert descriptor.adapter_id == "asset-lookup-cmdb-mcp"
    assert descriptor.route == "asset.lookup"
    assert descriptor.action == "asset.lookup"
    assert descriptor.risk_level is SocAgentRiskLevel.READ_ONLY
    assert descriptor.adapter_kind == "mcp"
    assert descriptor.external_side_effect == "read"
    assert descriptor.execute_supported is True
    assert descriptor.idempotency_required is False
    assert descriptor.required_payload_fields == ["asset_key"]
    assert descriptor.required_context_refs == ["thread_id"]


def test_mcp_adapter_dry_run_validates_tool_without_invoking_provider() -> None:
    provider = FakeSocMcpToolProvider({"cmdb_asset_lookup": {"asset_found": True}})
    registry = SocActionAdapterRegistry([_asset_lookup_adapter(provider)])

    result = registry.dry_run(
        SocAgentActionCommand(
            route="asset.lookup",
            action="asset.lookup",
            dry_run=True,
            payload={
                "asset_key": "10.10.1.5",
                "context_refs": {"thread_id": "SOC-THREAD-1"},
            },
        ),
        context=_context(),
    )

    assert result.status == "success"
    assert result.payload["adapter_id"] == "asset-lookup-cmdb-mcp"
    assert result.payload["adapter_kind"] == "mcp"
    assert result.payload["tool_name"] == "cmdb_asset_lookup"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["read_only"] is True
    assert provider.invocations == []


def test_mcp_adapter_execute_invokes_provider_with_mapped_payload_and_filtered_result() -> None:
    provider = FakeSocMcpToolProvider(
        {
            "cmdb_asset_lookup": {
                "asset_found": True,
                "asset_record": {"asset_id": "asset-001", "owner": "payments-sre"},
                "raw_secret": "must-not-leak",
            }
        }
    )
    registry = SocActionAdapterRegistry([_asset_lookup_adapter(provider)])

    result = registry.execute(
        SocAgentActionCommand(
            route="asset.lookup",
            action="asset.lookup",
            dry_run=False,
            payload={
                "asset_key": "10.10.1.5",
                "context_refs": {"thread_id": "SOC-THREAD-1"},
                "ignored": "not-sent",
            },
        ),
        context=_context(),
    )

    assert provider.invocations == [
        {
            "tool_name": "cmdb_asset_lookup",
            "payload": {"query": "10.10.1.5"},
            "timeout_seconds": 5,
        }
    ]
    assert result.status == "success"
    assert result.payload["adapter_id"] == "asset-lookup-cmdb-mcp"
    assert result.payload["adapter_kind"] == "mcp"
    assert result.payload["tool_name"] == "cmdb_asset_lookup"
    assert result.payload["external_side_effect"] == "read"
    assert result.payload["read_only"] is True
    assert result.payload["result_schema_version"] == "soc.asset_lookup_result.v1"
    assert result.payload["mcp_result"] == {
        "asset_found": True,
        "asset_record": {"asset_id": "asset-001", "owner": "payments-sre"},
    }
    assert "raw_secret" not in result.payload["mcp_result"]


def test_mcp_adapter_rejects_missing_context_refs() -> None:
    provider = FakeSocMcpToolProvider({"cmdb_asset_lookup": {"asset_found": True}})
    registry = SocActionAdapterRegistry([_asset_lookup_adapter(provider)])

    with pytest.raises(SocActionAdapterRegistryError, match="missing required context_refs fields: thread_id"):
        registry.dry_run(
            SocAgentActionCommand(
                route="asset.lookup",
                action="asset.lookup",
                dry_run=True,
                payload={"asset_key": "10.10.1.5"},
            ),
            context=_context(),
        )

    assert provider.invocations == []


def test_mcp_adapter_dry_run_rejects_missing_mcp_tool() -> None:
    provider = FakeSocMcpToolProvider({})
    registry = SocActionAdapterRegistry([_asset_lookup_adapter(provider)])

    with pytest.raises(SocActionAdapterRegistryError, match="MCP tool 'cmdb_asset_lookup' is not available"):
        registry.dry_run(
            SocAgentActionCommand(
                route="asset.lookup",
                action="asset.lookup",
                dry_run=True,
                payload={
                    "asset_key": "10.10.1.5",
                    "context_refs": {"thread_id": "SOC-THREAD-1"},
                },
            ),
            context=_context(),
        )


def test_mcp_adapter_execute_maps_provider_error_to_failed_result() -> None:
    provider = FakeSocMcpToolProvider({"cmdb_asset_lookup": SocMcpToolProviderError("provider timeout")})
    registry = SocActionAdapterRegistry([_asset_lookup_adapter(provider)])

    result = registry.execute(
        SocAgentActionCommand(
            route="asset.lookup",
            action="asset.lookup",
            dry_run=False,
            payload={
                "asset_key": "10.10.1.5",
                "context_refs": {"thread_id": "SOC-THREAD-1"},
            },
        ),
        context=_context(),
    )

    assert result.status == "failed"
    assert result.payload["adapter_id"] == "asset-lookup-cmdb-mcp"
    assert result.payload["tool_name"] == "cmdb_asset_lookup"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["error_type"] == "SocMcpToolProviderError"
    assert "provider timeout" in result.message


def test_mcp_adapter_constructor_rejects_non_read_only_descriptor() -> None:
    descriptor = mcp_read_only_adapter_descriptor(
        adapter_id="bad",
        route="asset.lookup",
        required_payload_fields=["asset_key"],
    ).model_copy(update={"risk_level": SocAgentRiskLevel.HIGH_RISK})

    with pytest.raises(SocActionAdapterRegistryError, match="read-only actions only"):
        SocMcpToolActionAdapter(
            descriptor=descriptor,
            provider=FakeSocMcpToolProvider({"cmdb_asset_lookup": {}}),
            tool_name="cmdb_asset_lookup",
        )


class FakeSocMcpToolProvider:
    def __init__(self, tools: Mapping[str, Mapping[str, Any] | Exception]) -> None:
        self._tools = dict(tools)
        self.invocations: list[dict[str, Any]] = []

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        return [SocMcpToolDescriptor(name=name, server="fake") for name in sorted(self._tools)]

    def invoke(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        self.invocations.append(
            {
                "tool_name": tool_name,
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
            }
        )
        result = self._tools.get(tool_name)
        if result is None:
            raise SocMcpToolNotFoundError(f"missing fake MCP tool {tool_name}")
        if isinstance(result, Exception):
            raise result
        return result


def _asset_lookup_adapter(provider: FakeSocMcpToolProvider) -> SocMcpToolActionAdapter:
    return SocMcpToolActionAdapter(
        descriptor=_asset_lookup_descriptor(),
        provider=provider,
        tool_name="cmdb_asset_lookup",
        timeout_seconds=5,
        input_mapping={"asset_key": "query"},
        output_fields=["asset_found", "asset_record"],
        result_schema_version="soc.asset_lookup_result.v1",
    )


def _asset_lookup_descriptor():
    return mcp_read_only_adapter_descriptor(
        adapter_id="asset-lookup-cmdb-mcp",
        route="asset.lookup",
        required_payload_fields=["asset_key"],
        required_context_refs=["thread_id"],
        description="Read-only CMDB asset lookup through MCP.",
    )


def _context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-analyst",
            actor_type=ActorType.USER,
            surface=EntrySurface.TEST,
            roles=["analyst"],
        ),
        trace_id="trace-test",
        idempotency_key="mcp-adapter-test",
    )
