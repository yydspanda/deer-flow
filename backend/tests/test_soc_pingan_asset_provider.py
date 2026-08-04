from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from soc_agent import cli as soc_cli
from soc_agent.integrations.pingan.asset_location import (
    HttpPingAnZeusAssetSearchPort,
    PingAnAssetLocationQuery,
    PingAnAssetLocatorService,
    PingAnAssetOwnershipOverride,
    PingAnAssetProviderConfigurationError,
    PingAnAssetProviderUnavailableError,
    PingAnAssetType,
    PingAnAssetWorkflowConfig,
    StaticPingAnAssetSearchPort,
    StaticPingAnAssetWorkflowPort,
    build_pingan_asset_locator_from_env,
)

_WORKFLOW_CONFIG = PingAnAssetWorkflowConfig(
    app_id="YHSYS",
    operator="analyst001",
    terminal_workflow_id=1087710,
    datacenter_workflow_id=1087787,
    user_workflow_id=1092332,
)


def test_http_search_port_preserves_legacy_signing_and_wire_contract() -> None:
    signed: dict[str, Any] = {}
    sent: dict[str, Any] = {}

    def signer(*, data: dict[str, Any], app_id: str, app_key: str) -> dict[str, str]:
        signed.update({"data": data, "app_id": app_id, "app_key": app_key})
        return {"x-isec-signature": "signed-value"}

    def handle(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["headers"] = dict(request.headers)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": "200", "data": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        port = HttpPingAnZeusAssetSearchPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="not-logged-secret",
            signer=signer,
            client=client,
        )
        response = port.search(keyword="10.10.1.5", asset_types=[PingAnAssetType.IP])

    expected_body = {
        "assetTypeList": ["IP"],
        "param": {"keyword": "10.10.1.5", "queryType": 1},
        "pageNum": 1,
        "pageSize": 10,
    }
    assert response == {"code": "200", "data": []}
    assert signed == {
        "data": expected_body,
        "app_id": "SEC-MODEL",
        "app_key": "not-logged-secret",
    }
    assert sent["url"] == "https://isec.example.internal/public/searchAssetInfo"
    assert sent["body"] == expected_body
    assert sent["headers"]["x-isec-signature"] == "signed-value"
    assert sent["headers"]["companycode"] == "all"


def test_locator_uses_search_asset_info_first_and_returns_bounded_result() -> None:
    search = StaticPingAnAssetSearchPort(
        {
            ("10.10.1.5", ("IP",)): _search_response(
                company_code="PA011",
                company_name="PingAn Technology",
                biz_group="Payment Engineering",
            )
        }
    )
    workflow = StaticPingAnAssetWorkflowPort({})
    locator = _locator(search=search, workflow=workflow)

    result = locator.locate(
        PingAnAssetLocationQuery(
            query="10.10.1.5",
            asset_type="IP",
            role="victim",
        )
    )

    assert result.found is True
    assert result.resolved is True
    assert result.ambiguous is False
    assert result.company_code == "PA011"
    assert result.source == "zeus_search_asset_info"
    assert result.mocked is True
    assert result.decision_impact == "none"
    assert result.raw_response_included is False
    assert [item.stage for item in result.attempts] == ["search_asset_info"]
    assert all(item.duration_ms >= 0 for item in result.attempts)
    assert workflow.calls == []


def test_locator_preserves_domain_to_web_search_fallback() -> None:
    search = StaticPingAnAssetSearchPort(
        {
            ("portal.example.com", ("DOMAIN",)): {"code": 200, "data": []},
            ("portal.example.com", ("WEB",)): _search_response(
                company_code="PA003",
                company_name="Portal Team",
                asset_type="WEB",
            ),
        }
    )
    locator = _locator(search=search, workflow=StaticPingAnAssetWorkflowPort({}))

    result = locator.locate({"query": "portal.example.com", "asset_type": "DOMAIN"})

    assert result.resolved is True
    assert result.company_code == "PA003"
    assert [call["asset_types"] for call in search.calls] == [["DOMAIN"], ["WEB"]]


def test_locator_falls_back_from_search_to_datacenter_then_terminal_workflow() -> None:
    search = StaticPingAnAssetSearchPort({})
    workflow = StaticPingAnAssetWorkflowPort(
        {
            (1087710, "203.0.113.20"): {
                "company_code": "PA009",
                "company_name": "Endpoint Operations",
            }
        }
    )
    locator = _locator(search=search, workflow=workflow)

    result = locator.locate({"query": "203.0.113.20", "asset_type": "IP"})

    assert result.resolved is True
    assert result.source == "agent_asset_to_bu"
    assert [item.lookup_kind for item in result.attempts] == ["IP", "datacenter", "terminal"]
    assert [call["workflow_id"] for call in workflow.calls] == [1087787, 1087710]
    assert workflow.calls[0]["query_data"]["args"] == {"ip": "203.0.113.20"}
    assert workflow.calls[0]["query_data"]["message"]["by"] == "analyst001"


def test_locator_uses_um_only_after_asset_sources_miss() -> None:
    search = StaticPingAnAssetSearchPort({})
    workflow = StaticPingAnAssetWorkflowPort(
        {
            (1092332, "UM001"): {
                "company_code": "PA011",
                "company_name": "User Business Unit",
            }
        }
    )
    locator = _locator(search=search, workflow=workflow)

    result = locator.locate(
        {
            "query": "198.51.100.40",
            "asset_type": "IP",
            "um": "UM001",
        }
    )

    assert result.resolved is True
    assert result.source == "agent_locate_user"
    assert [item.stage for item in result.attempts] == [
        "search_asset_info",
        "asset_to_bu",
        "asset_to_bu",
        "um",
    ]
    assert workflow.calls[-1]["query_data"]["args"] == {"ums": "UM001"}


def test_locator_exposes_ambiguous_ownership_without_selecting_first_record() -> None:
    search = StaticPingAnAssetSearchPort(
        {
            ("shared.example.com", ("DOMAIN",)): {
                "code": 200,
                "data": [
                    {
                        "type": "DOMAIN",
                        "data": [
                            {"companyCode": "PA001", "companyName": "Owner One"},
                            {"companyCode": "PA002", "companyName": "Owner Two"},
                        ],
                    }
                ],
            }
        }
    )
    locator = _locator(search=search, workflow=StaticPingAnAssetWorkflowPort({}))

    result = locator.locate({"query": "shared.example.com", "asset_type": "DOMAIN"})

    assert result.found is True
    assert result.resolved is False
    assert result.ambiguous is True
    assert result.company_code == ""
    assert {item.company_code for item in result.candidates} == {"PA001", "PA002"}


def test_locator_applies_tenant_owned_legacy_ownership_override() -> None:
    search = StaticPingAnAssetSearchPort(
        {
            ("desktop.example.internal", ("DOMAIN",)): _search_response(
                company_code="",
                company_name="",
                biz_group="云桌面分组",
                asset_type="DOMAIN",
            )
        }
    )
    locator = PingAnAssetLocatorService(
        search_port=search,
        workflow_port=StaticPingAnAssetWorkflowPort({}),
        workflow_config=_WORKFLOW_CONFIG,
        provider_mode="fake",
        ownership_overrides=[
            PingAnAssetOwnershipOverride(
                match_biz_group="云桌面分组",
                company_code="PA011",
                company_name="平安科技",
            )
        ],
    )

    result = locator.locate({"query": "desktop.example.internal", "asset_type": "DOMAIN"})

    assert result.resolved is True
    assert result.company_code == "PA011"
    assert result.company_name == "平安科技"
    assert result.biz_group == "云桌面分组"


def test_locator_fails_when_every_configured_provider_errors() -> None:
    search = StaticPingAnAssetSearchPort({("192.0.2.50", ("IP",)): RuntimeError("search unavailable")})
    workflow = StaticPingAnAssetWorkflowPort(
        {
            (1087787, "192.0.2.50"): RuntimeError("datacenter unavailable"),
            (1087710, "192.0.2.50"): RuntimeError("terminal unavailable"),
        }
    )
    locator = _locator(search=search, workflow=workflow)

    with pytest.raises(PingAnAssetProviderUnavailableError, match="search_asset_info") as exc_info:
        locator.locate({"query": "192.0.2.50", "asset_type": "IP"})

    assert [attempt.status for attempt in exc_info.value.attempts] == ["failed"]
    assert workflow.calls == []


def test_locator_does_not_treat_workflow_failure_as_a_normal_miss() -> None:
    search = StaticPingAnAssetSearchPort({})
    workflow = StaticPingAnAssetWorkflowPort(
        {
            (1087787, "192.0.2.60"): RuntimeError("datacenter unavailable"),
            (1087710, "192.0.2.60"): {
                "company_code": "PA011",
                "company_name": "must not be reached",
            },
        }
    )
    locator = _locator(search=search, workflow=workflow)

    with pytest.raises(PingAnAssetProviderUnavailableError, match="asset_to_bu") as exc_info:
        locator.locate({"query": "192.0.2.60", "asset_type": "IP"})

    assert [attempt.status for attempt in exc_info.value.attempts] == [
        "not_found",
        "failed",
    ]
    assert [call["workflow_id"] for call in workflow.calls] == [1087787]


def test_environment_builder_never_silently_falls_back_from_internal_to_fake() -> None:
    with pytest.raises(PingAnAssetProviderConfigurationError, match="SOC_PINGAN_ZEUS_SIGNER_IMPORT"):
        build_pingan_asset_locator_from_env({"SOC_PINGAN_ASSET_PROVIDER_MODE": "internal"})

    result = build_pingan_asset_locator_from_env({"SOC_PINGAN_ASSET_PROVIDER_MODE": "fake"}).locate({"query": "10.10.1.5", "asset_type": "IP"})
    assert result.mocked is True
    assert result.provider_mode == "fake"


def test_cli_mcp_smoke_keeps_d12_fake_provenance_visible(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from deerflow.mcp.cache import reset_mcp_tools_cache

    backend_root = Path(__file__).resolve().parents[1]
    sample_root = backend_root / "samples" / "mcp" / "pingan_asset"
    monkeypatch.setenv("SOC_PINGAN_ASSET_MCP_PYTHON", sys.executable)
    monkeypatch.setenv(
        "SOC_PINGAN_ASSET_MCP_SERVER",
        str(backend_root / "scripts" / "soc_pingan_asset_mcp_server.py"),
    )
    monkeypatch.setenv(
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH",
        str(sample_root / "extensions.fake.json"),
    )
    reset_mcp_tools_cache()

    try:
        exit_code = soc_cli.main(
            [
                "mcp",
                "smoke",
                str(sample_root / "action_adapters.json"),
                "--route",
                "asset.locate",
                "--json",
                json.dumps(
                    {
                        "asset_key": "10.10.1.5",
                        "asset_type": "IP",
                        "role": "victim",
                        "context_refs": {"thread_id": "D12-FAKE"},
                    }
                ),
            ]
        )
    finally:
        reset_mcp_tools_cache()

    output = json.loads(capfd.readouterr().out)
    assert exit_code == 0
    assert output["status"] == "success"
    result = output["action_result"]["payload"]["mcp_result"]
    assert result["mocked"] is True
    assert result["provider_mode"] == "fake"
    assert result["evidence_boundary"] == "investigation_only"
    assert result["decision_impact"] == "none"


def _locator(
    *,
    search: StaticPingAnAssetSearchPort,
    workflow: StaticPingAnAssetWorkflowPort,
) -> PingAnAssetLocatorService:
    return PingAnAssetLocatorService(
        search_port=search,
        workflow_port=workflow,
        workflow_config=_WORKFLOW_CONFIG,
        provider_mode="fake",
    )


def _search_response(
    *,
    company_code: str,
    company_name: str,
    biz_group: str = "",
    asset_type: str = "IP",
) -> dict[str, Any]:
    return {
        "code": 200,
        "data": [
            {
                "type": asset_type,
                "data": [
                    {
                        "companyCode": company_code,
                        "companyName": company_name,
                        "bizGroup": biz_group,
                    }
                ],
            }
        ],
    }
