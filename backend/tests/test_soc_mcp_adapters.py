from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import soc_agent.cli as soc_cli
from soc_agent.actions.adapters import SocActionAdapterRegistry, SocActionAdapterRegistryError
from soc_agent.actions.mcp import (
    DeerFlowCachedMcpToolProvider,
    SocMcpActionAdapterConfig,
    SocMcpToolActionAdapter,
    SocMcpToolDescriptor,
    SocMcpToolNotFoundError,
    SocMcpToolProviderError,
    build_mcp_action_adapter,
    build_mcp_action_adapter_registry,
    build_mcp_action_adapter_registry_from_file,
    build_mcp_action_adapter_registry_from_files,
    inspect_mcp_tool_inventory,
    load_mcp_action_adapter_configs,
    mcp_read_only_adapter_descriptor,
    run_mcp_action_adapter_smoke,
)
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocAgentActionCommand,
    SocAgentRiskLevel,
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


def test_mcp_adapter_config_builder_creates_read_only_asset_lookup_registry() -> None:
    provider = FakeSocMcpToolProvider(
        {
            "cmdb_asset_lookup": {
                "asset_found": True,
                "asset_record": {"asset_id": "asset-001", "owner": "payments-sre"},
                "raw_secret": "must-not-leak",
            }
        }
    )

    registry = build_mcp_action_adapter_registry([_asset_lookup_config()], provider)
    [descriptor] = registry.list_descriptors()

    assert descriptor.adapter_id == "asset-lookup-cmdb-mcp"
    assert descriptor.route == "asset.lookup"
    assert descriptor.action == "asset.lookup"
    assert descriptor.risk_level is SocAgentRiskLevel.READ_ONLY
    assert descriptor.adapter_kind == "mcp"
    assert descriptor.external_side_effect == "read"
    assert descriptor.required_payload_fields == ["asset_key"]
    assert descriptor.required_context_refs == ["thread_id"]
    assert descriptor.metadata["mcp"] == {
        "server": "cmdb",
        "tool": "cmdb_asset_lookup",
        "timeout_seconds": 7,
        "result_schema_version": "soc.asset_lookup_result.v1",
        "output_fields": ["asset_found", "asset_record"],
    }
    assert descriptor.metadata["config"]["owner"] == "soc-platform"
    assert descriptor.metadata["config"]["environment"] == "dev"

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
            "server_name": "cmdb",
            "payload": {"query": "10.10.1.5"},
            "timeout_seconds": 7,
        }
    ]
    assert result.status == "success"
    assert result.payload["mcp_result"] == {
        "asset_found": True,
        "asset_record": {"asset_id": "asset-001", "owner": "payments-sre"},
    }
    assert "raw_secret" not in result.payload["mcp_result"]


def test_mcp_adapter_config_builder_skips_disabled_configs() -> None:
    provider = FakeSocMcpToolProvider({"cmdb_asset_lookup": {"asset_found": True}})
    config = _asset_lookup_config() | {"enabled": False}

    registry = build_mcp_action_adapter_registry([config], provider)

    assert registry.list_descriptors() == []


def test_mcp_adapter_config_builder_rejects_disabled_direct_build() -> None:
    provider = FakeSocMcpToolProvider({"cmdb_asset_lookup": {"asset_found": True}})
    config = _asset_lookup_config() | {"enabled": False}

    with pytest.raises(SocActionAdapterRegistryError, match="disabled MCP action adapter config"):
        build_mcp_action_adapter(config, provider)


def test_mcp_adapter_config_builder_rejects_duplicate_route_action() -> None:
    provider = FakeSocMcpToolProvider({"cmdb_asset_lookup": {"asset_found": True}})
    duplicate_config = _asset_lookup_config() | {"adapter_id": "asset-lookup-cmdb-mcp-2"}

    with pytest.raises(SocActionAdapterRegistryError, match="already registered"):
        build_mcp_action_adapter_registry([_asset_lookup_config(), duplicate_config], provider)


def test_load_mcp_action_adapter_configs_from_json_object(tmp_path: Path) -> None:
    config_path = tmp_path / "soc_action_adapters.json"
    config_path.write_text(json.dumps({"adapters": [_asset_lookup_config()]}), encoding="utf-8")

    [config] = load_mcp_action_adapter_configs(config_path)

    assert config.adapter_id == "asset-lookup-cmdb-mcp"
    assert config.route == "asset.lookup"
    assert config.mcp.tool == "cmdb_asset_lookup"


def test_load_mcp_action_adapter_configs_from_yaml_list(tmp_path: Path) -> None:
    config_path = tmp_path / "soc_action_adapters.yaml"
    config_path.write_text(
        """
- enabled: true
  owner: soc-platform
  environment: dev
  adapter_id: asset-lookup-cmdb-mcp
  route: asset.lookup
  action: asset.lookup
  required_payload_fields:
    - asset_key
  required_context_refs:
    - thread_id
  description: Read-only CMDB asset lookup through MCP.
  mcp:
    server: cmdb
    tool: cmdb_asset_lookup
    timeout_seconds: 7
    input_mapping:
      asset_key: query
    output_fields:
      - asset_found
      - asset_record
    result_schema_version: soc.asset_lookup_result.v1
""".strip(),
        encoding="utf-8",
    )

    [config] = load_mcp_action_adapter_configs(config_path)

    assert config.adapter_id == "asset-lookup-cmdb-mcp"
    assert config.mcp.input_mapping == {"asset_key": "query"}
    assert config.mcp.output_fields == ["asset_found", "asset_record"]


def test_load_mcp_action_adapter_configs_rejects_invalid_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "soc_action_adapters.json"
    config_path.write_text(json.dumps({"adapter_id": "missing-adapters-list"}), encoding="utf-8")

    with pytest.raises(SocActionAdapterRegistryError, match="must contain an adapters list"):
        load_mcp_action_adapter_configs(config_path)


def test_build_mcp_action_adapter_registry_from_file_executes_cached_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "soc_action_adapters.json"
    config_path.write_text(json.dumps({"adapters": [_asset_lookup_config()]}), encoding="utf-8")
    tool = FakeCachedMcpTool(
        name="cmdb_asset_lookup",
        result={
            "asset_found": True,
            "asset_record": {"asset_id": "asset-001"},
            "raw_secret": "must-not-leak",
        },
    )
    provider = DeerFlowCachedMcpToolProvider(lambda: [tool])

    registry = build_mcp_action_adapter_registry_from_file(config_path, provider)
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

    assert tool.invocations == [{"query": "10.10.1.5"}]
    assert result.status == "success"
    assert result.payload["mcp_result"] == {
        "asset_found": True,
        "asset_record": {"asset_id": "asset-001"},
    }


def test_build_mcp_action_adapter_registry_from_files_combines_explicit_allowlists(
    tmp_path: Path,
) -> None:
    asset_path = tmp_path / "asset.json"
    tag_path = tmp_path / "tag.json"
    asset_path.write_text(json.dumps({"adapters": [_asset_lookup_config()]}), encoding="utf-8")
    tag_path.write_text(
        json.dumps({"adapters": [_security_tag_lookup_config()]}),
        encoding="utf-8",
    )

    registry = build_mcp_action_adapter_registry_from_files(
        [asset_path, tag_path],
        FakeSocMcpToolProvider({}),
    )

    assert [(item.route, item.action) for item in registry.list_descriptors()] == [
        ("asset.lookup", "asset.lookup"),
        ("security_tag.lookup", "security_tag.lookup"),
    ]


def test_build_mcp_action_adapter_registry_from_files_rejects_cross_file_duplicate(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps({"adapters": [_asset_lookup_config()]}), encoding="utf-8")
    second_path.write_text(
        json.dumps({"adapters": [_asset_lookup_config() | {"adapter_id": "asset-lookup-duplicate-mcp"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SocActionAdapterRegistryError, match="already registered"):
        build_mcp_action_adapter_registry_from_files(
            [first_path, second_path],
            FakeSocMcpToolProvider({}),
        )


def test_run_mcp_action_adapter_smoke_reports_live_metrics(tmp_path: Path) -> None:
    config_path = tmp_path / "soc_action_adapters.json"
    config_path.write_text(json.dumps({"adapters": [_asset_lookup_config()]}), encoding="utf-8")
    tool = FakeCachedMcpTool(
        name="cmdb_asset_lookup",
        result={
            "asset_found": True,
            "asset_record": {"asset_id": "asset-001"},
            "raw_secret": "must-not-leak",
        },
    )
    provider = DeerFlowCachedMcpToolProvider(lambda: [tool])

    report = run_mcp_action_adapter_smoke(
        config_path,
        provider,
        command=SocAgentActionCommand(
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

    assert tool.invocations == [{"query": "10.10.1.5"}]
    assert report.schema_version == "soc.mcp_action_smoke_report.v1"
    assert report.status == "success"
    assert report.result_status == "success"
    assert report.route == "asset.lookup"
    assert report.action == "asset.lookup"
    assert report.tool_name == "cmdb_asset_lookup"
    assert report.mcp_server == "cmdb"
    assert report.timeout_seconds == 7
    assert report.duration_ms >= 0
    assert report.action_payload_bytes > 0
    assert report.action_result_bytes > 0
    assert report.mcp_result_bytes is not None and report.mcp_result_bytes > 0
    assert report.output_fields == ["asset_found", "asset_record"]
    assert report.output_filter_applied is True
    assert report.mcp_result_keys == ["asset_found", "asset_record"]
    assert report.action_result["payload"]["mcp_result"] == {
        "asset_found": True,
        "asset_record": {"asset_id": "asset-001"},
    }
    assert "raw_secret" not in report.action_result["payload"]["mcp_result"]


def test_run_mcp_action_adapter_smoke_reports_config_failure(tmp_path: Path) -> None:
    config_path = tmp_path / "soc_action_adapters.json"
    config_path.write_text(json.dumps({"adapter_id": "missing-adapters-list"}), encoding="utf-8")

    report = run_mcp_action_adapter_smoke(
        config_path,
        DeerFlowCachedMcpToolProvider(lambda: []),
        command=SocAgentActionCommand(
            route="asset.lookup",
            action="asset.lookup",
            dry_run=False,
            payload={"asset_key": "10.10.1.5"},
        ),
        context=_context(),
    )

    assert report.status == "failed"
    assert report.result_status is None
    assert report.error_type == "SocActionAdapterRegistryError"
    assert "must contain an adapters list" in (report.error_message or "")
    assert report.action_result == {}


def test_inspect_mcp_tool_inventory_reports_tools_without_invoking_them() -> None:
    tool = FakeCachedMcpTool(
        name="cmdb_asset_lookup",
        result={"asset_found": True},
        description="Lookup asset ownership.",
        args={"query": {"type": "string"}},
    )

    report = inspect_mcp_tool_inventory(
        DeerFlowCachedMcpToolProvider(lambda: [tool]),
        include_input_schema=True,
    )

    assert report.schema_version == "soc.mcp_tool_inventory.v1"
    assert report.status == "success"
    assert report.tool_count == 1
    assert report.tools[0].name == "cmdb_asset_lookup"
    assert report.tools[0].description == "Lookup asset ownership."
    assert report.tools[0].input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    assert tool.invocations == []


def test_inspect_mcp_tool_inventory_reports_loader_failure() -> None:
    def load_tools():
        raise RuntimeError("cache unavailable")

    report = inspect_mcp_tool_inventory(DeerFlowCachedMcpToolProvider(load_tools))

    assert report.status == "failed"
    assert report.tool_count == 0
    assert report.error_type == "SocMcpToolProviderError"
    assert "cache unavailable" in (report.error_message or "")


def test_cli_mcp_smoke_executes_configured_read_only_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "soc_action_adapters.json"
    report_path = tmp_path / "smoke-report.json"
    config_path.write_text(json.dumps({"adapters": [_asset_lookup_config()]}), encoding="utf-8")
    tool = FakeCachedMcpTool(
        name="cmdb_asset_lookup",
        result={
            "asset_found": True,
            "asset_record": {"asset_id": "asset-001"},
            "raw_secret": "must-not-leak",
        },
    )
    provider = DeerFlowCachedMcpToolProvider(lambda: [tool])
    monkeypatch.setattr(soc_cli, "DeerFlowCachedMcpToolProvider", lambda *_, **__: provider)

    exit_code = soc_cli.main(
        [
            "mcp",
            "smoke",
            str(config_path),
            "--route",
            "asset.lookup",
            "--json",
            json.dumps(
                {
                    "asset_key": "10.10.1.5",
                    "context_refs": {"thread_id": "SOC-THREAD-1"},
                }
            ),
            "--report-path",
            str(report_path),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    saved_output = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved_output == output
    assert tool.invocations == [{"query": "10.10.1.5"}]
    assert output["schema_version"] == "soc.mcp_action_smoke_report.v1"
    assert output["status"] == "success"
    assert output["result_status"] == "success"
    assert output["adapter_kind"] == "mcp"
    assert output["tool_name"] == "cmdb_asset_lookup"
    assert output["output_filter_applied"] is True
    assert output["mcp_result_keys"] == ["asset_found", "asset_record"]
    assert output["action_result"]["payload"]["mcp_result"] == {
        "asset_found": True,
        "asset_record": {"asset_id": "asset-001"},
    }


def test_cli_mcp_tools_outputs_inventory_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "tools-report.json"
    provider = DeerFlowCachedMcpToolProvider(
        lambda: [
            FakeCachedMcpTool(
                name="cmdb_asset_lookup",
                result={"asset_found": True},
                description="Lookup asset ownership.",
                args={"query": {"type": "string"}},
            )
        ]
    )
    monkeypatch.setattr(soc_cli, "DeerFlowCachedMcpToolProvider", lambda: provider)

    exit_code = soc_cli.main(
        [
            "mcp",
            "tools",
            "--include-schema",
            "--report-path",
            str(report_path),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    saved_output = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved_output == output
    assert output["schema_version"] == "soc.mcp_tool_inventory.v1"
    assert output["status"] == "success"
    assert output["tool_count"] == 1
    assert output["tools"][0]["name"] == "cmdb_asset_lookup"
    assert output["tools"][0]["input_schema"] == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }


def test_cli_mcp_smoke_executes_local_real_stdio_mcp_fixture(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from deerflow.mcp.cache import reset_mcp_tools_cache

    backend_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("SOC_DEV_MCP_PYTHON", sys.executable)
    monkeypatch.setenv("SOC_DEV_MCP_SERVER", str(backend_root / "scripts" / "soc_dev_mcp_server.py"))
    monkeypatch.setenv(
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH",
        str(backend_root / "samples" / "mcp" / "soc_dev_extensions_config.json"),
    )
    reset_mcp_tools_cache()

    try:
        exit_code = soc_cli.main(
            [
                "mcp",
                "smoke",
                str(backend_root / "samples" / "mcp" / "soc_dev_action_adapters.json"),
                "--route",
                "asset.lookup",
                "--json",
                json.dumps(
                    {
                        "asset_key": "10.10.1.5",
                        "context_refs": {"thread_id": "SOC-THREAD-1"},
                    }
                ),
            ]
        )
    finally:
        reset_mcp_tools_cache()

    output = json.loads(capfd.readouterr().out)
    assert exit_code == 0
    assert output["schema_version"] == "soc.mcp_action_smoke_report.v1"
    assert output["status"] == "success"
    assert output["tool_name"] == "soc_dev_asset_lookup"
    assert output["action_result"]["payload"]["mcp_result"]["asset_found"] is True
    assert output["action_result"]["payload"]["mcp_result"]["asset_record"]["asset_id"] == "asset-001"


def test_cli_mcp_smoke_executes_local_asset_locate_mock_fixture(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from deerflow.mcp.cache import reset_mcp_tools_cache

    backend_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("SOC_DEV_MCP_PYTHON", sys.executable)
    monkeypatch.setenv("SOC_DEV_MCP_SERVER", str(backend_root / "scripts" / "soc_dev_mcp_server.py"))
    monkeypatch.setenv(
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH",
        str(backend_root / "samples" / "mcp" / "soc_dev_extensions_config.json"),
    )
    reset_mcp_tools_cache()

    try:
        exit_code = soc_cli.main(
            [
                "mcp",
                "smoke",
                str(backend_root / "samples" / "mcp" / "soc_dev_action_adapters.json"),
                "--route",
                "asset.locate",
                "--json",
                json.dumps(
                    {
                        "asset_key": "10.10.1.5",
                        "asset_type": "IP",
                        "role": "target",
                        "context_refs": {"thread_id": "SOC-THREAD-1"},
                    }
                ),
            ]
        )
    finally:
        reset_mcp_tools_cache()

    output = json.loads(capfd.readouterr().out)
    assert exit_code == 0
    assert output["schema_version"] == "soc.mcp_action_smoke_report.v1"
    assert output["status"] == "success"
    assert output["tool_name"] == "soc_dev_asset_locate"
    result = output["action_result"]["payload"]["mcp_result"]
    assert result["found"] is True
    assert result["company_code"] == "PA011"
    assert result["biz_group"] == "平安科技/支付研发"
    assert result["mocked"] is True


def test_mcp_adapter_config_rejects_non_read_only_risk() -> None:
    with pytest.raises(ValueError, match="risk_level=read_only"):
        SocMcpActionAdapterConfig.model_validate(_asset_lookup_config() | {"risk_level": "high_risk"})


def test_deerflow_cached_mcp_provider_loads_tools_from_lazy_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeCachedMcpTool(
        name="cmdb_asset_lookup",
        result={"asset_found": True, "asset_record": {"asset_id": "asset-001"}},
        description="Lookup asset ownership.",
        args={"query": {"type": "string"}},
    )
    monkeypatch.setattr("deerflow.mcp.cache.get_cached_mcp_tools", lambda: [tool])
    provider = DeerFlowCachedMcpToolProvider()

    [descriptor] = provider.list_tools()
    result = provider.invoke("cmdb_asset_lookup", {"query": "10.10.1.5"}, timeout_seconds=5)

    assert descriptor.name == "cmdb_asset_lookup"
    assert descriptor.description == "Lookup asset ownership."
    assert descriptor.input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    assert tool.invocations == [{"query": "10.10.1.5"}]
    assert result == {"asset_found": True, "asset_record": {"asset_id": "asset-001"}}


def test_deerflow_cached_mcp_provider_normalizes_content_and_artifact_result() -> None:
    tool = FakeCachedMcpTool(
        name="cmdb_asset_lookup",
        result=(
            [{"type": "text", "text": "asset found"}],
            {"structured_content": {"asset_found": True}},
        ),
    )
    provider = DeerFlowCachedMcpToolProvider(lambda: [tool])

    result = provider.invoke("cmdb_asset_lookup", {"query": "10.10.1.5"}, timeout_seconds=5)

    assert result == {
        "asset_found": True,
        "content": [{"type": "text", "text": "asset found"}],
        "artifact": {"structured_content": {"asset_found": True}},
    }


def test_deerflow_cached_mcp_provider_normalizes_call_tool_result_with_structured_content() -> None:
    class CallToolResultLike:
        structuredContent = {"asset_found": True, "asset_record": {"asset_id": "asset-001"}}
        content = [{"type": "text", "text": "asset found"}]

    tool = FakeCachedMcpTool(
        name="cmdb_asset_lookup",
        result=CallToolResultLike(),
    )
    provider = DeerFlowCachedMcpToolProvider(lambda: [tool])

    result = provider.invoke("cmdb_asset_lookup", {"query": "10.10.1.5"}, timeout_seconds=5)

    assert result == {
        "asset_found": True,
        "asset_record": {"asset_id": "asset-001"},
        "content": [{"type": "text", "text": "asset found"}],
        "artifact": {
            "structured_content": {
                "asset_found": True,
                "asset_record": {"asset_id": "asset-001"},
            }
        },
    }


def test_deerflow_cached_mcp_provider_rejects_missing_tool() -> None:
    provider = DeerFlowCachedMcpToolProvider(lambda: [])

    with pytest.raises(SocMcpToolNotFoundError, match="cmdb_asset_lookup"):
        provider.invoke("cmdb_asset_lookup", {"query": "10.10.1.5"}, timeout_seconds=5)


def test_mcp_one_shot_target_resolution_prefers_explicit_server_for_prefix_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    import soc_agent.actions.mcp as soc_mcp

    servers_config = {
        "web": {"transport": "stdio", "command": "web", "args": []},
        "web_scraper": {"transport": "stdio", "command": "scraper", "args": []},
    }
    monkeypatch.setattr("deerflow.config.extensions_config.ExtensionsConfig.from_file", lambda: object())
    monkeypatch.setattr("deerflow.mcp.client.build_servers_config", lambda _: servers_config)

    server_name, original_tool_name, connection = soc_mcp._resolve_mcp_tool_target(
        "web_scraper_search",
        server_name="web_scraper",
    )

    assert server_name == "web_scraper"
    assert original_tool_name == "search"
    assert connection == servers_config["web_scraper"]


def test_deerflow_cached_mcp_provider_maps_loader_failure() -> None:
    def load_tools():
        raise RuntimeError("cache unavailable")

    provider = DeerFlowCachedMcpToolProvider(load_tools)

    with pytest.raises(SocMcpToolProviderError, match="cache unavailable"):
        provider.list_tools()


def test_mcp_adapter_executes_against_deerflow_cached_provider() -> None:
    provider = DeerFlowCachedMcpToolProvider(
        lambda: [
            FakeCachedMcpTool(
                name="cmdb_asset_lookup",
                result={
                    "asset_found": True,
                    "asset_record": {"asset_id": "asset-001"},
                    "raw_secret": "must-not-leak",
                },
            )
        ]
    )
    registry = build_mcp_action_adapter_registry([_asset_lookup_config()], provider)

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

    assert result.status == "success"
    assert result.payload["mcp_result"] == {
        "asset_found": True,
        "asset_record": {"asset_id": "asset-001"},
    }


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


def test_mcp_adapter_execute_maps_mcp_is_error_result_to_failed_result() -> None:
    provider = FakeSocMcpToolProvider(
        {
            "cmdb_asset_lookup": {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": "provider configuration is unavailable",
                    }
                ],
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
            },
        ),
        context=_context(),
    )

    assert result.status == "failed"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["error_type"] == "SocMcpToolProviderError"
    assert "provider configuration is unavailable" in result.message


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
        server_name: str | None = None,
    ) -> Mapping[str, Any]:
        invocation = {
            "tool_name": tool_name,
            "payload": dict(payload),
            "timeout_seconds": timeout_seconds,
        }
        if server_name is not None:
            invocation["server_name"] = server_name
        self.invocations.append(invocation)
        result = self._tools.get(tool_name)
        if result is None:
            raise SocMcpToolNotFoundError(f"missing fake MCP tool {tool_name}")
        if isinstance(result, Exception):
            raise result
        return result


class FakeCachedMcpTool:
    def __init__(
        self,
        *,
        name: str,
        result: Any,
        description: str = "",
        args: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.args = dict(args or {})
        self.result = result
        self.metadata = {}
        self.invocations: list[dict[str, Any]] = []

    def invoke(self, payload: Mapping[str, Any]) -> Any:
        self.invocations.append(dict(payload))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


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


def _asset_lookup_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "owner": "soc-platform",
        "environment": "dev",
        "adapter_id": "asset-lookup-cmdb-mcp",
        "route": "asset.lookup",
        "action": "asset.lookup",
        "required_payload_fields": ["asset_key"],
        "required_context_refs": ["thread_id"],
        "description": "Read-only CMDB asset lookup through MCP.",
        "mcp": {
            "server": "cmdb",
            "tool": "cmdb_asset_lookup",
            "timeout_seconds": 7,
            "input_mapping": {"asset_key": "query"},
            "output_fields": ["asset_found", "asset_record"],
            "result_schema_version": "soc.asset_lookup_result.v1",
        },
    }


def _security_tag_lookup_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "owner": "soc-platform",
        "environment": "dev",
        "adapter_id": "security-tag-mcp",
        "route": "security_tag.lookup",
        "action": "security_tag.lookup",
        "required_payload_fields": ["entity_key", "entity_type"],
        "required_context_refs": ["thread_id"],
        "description": "Read-only security-tag lookup through MCP.",
        "mcp": {
            "server": "security-tags",
            "tool": "security_tag_lookup",
            "timeout_seconds": 7,
            "input_mapping": {
                "entity_key": "query",
                "entity_type": "entity_type",
            },
            "output_fields": ["security_tag_found", "records"],
            "result_schema_version": "soc.security_tag_lookup_result.v1",
        },
    }


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
