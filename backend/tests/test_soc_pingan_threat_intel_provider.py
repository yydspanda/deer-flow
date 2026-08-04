from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from soc_agent.actions.mcp import (
    SocMcpToolDescriptor,
    build_mcp_action_adapter_registry_from_file,
)
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisRun,
    AnalysisRunStatus,
    EntrySurface,
    ServiceRequestContext,
    SocAgentChatRequest,
    SocDomainFindingDisposition,
    SocDomainName,
    SocDomainTriageRequest,
)
from soc_agent.core import (
    InMemoryInvestigationEvidenceRepository,
    SocAgentActionDispatcher,
    SocAgentCapabilityRouter,
)
from soc_agent.domain import SocDomainTriageService
from soc_agent.integrations.pingan.threat_intel import (
    HttpPingAnZeusThreatIntelPort,
    PingAnThreatIntelConfigurationError,
    PingAnThreatIntelResponseError,
    PingAnThreatIntelService,
    PingAnThreatIntelUnavailableError,
    build_pingan_threat_intel_service_from_env,
)
from soc_agent.integrations.pingan.threat_intel_mcp_server import _handle_message

_IP = "198.51.100.9"
_NOW = datetime(2026, 8, 4, 4, 0, tzinfo=UTC)


def test_http_port_preserves_indicator_search_wire_contract() -> None:
    signed: dict[str, Any] = {}
    sent: dict[str, Any] = {}

    def signer(*, data: dict[str, Any], app_id: str, app_key: str) -> dict[str, str]:
        signed.update({"data": data, "app_id": app_id, "app_key": app_key})
        return {"App-Sign": "signed"}

    def handle(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        sent["headers"] = dict(request.headers)
        return httpx.Response(200, json=_response(_IP))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        port = HttpPingAnZeusThreatIntelPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="not-logged-secret",
            allowed_hosts=["isec.example.internal"],
            signer=signer,
            client=client,
        )
        response = port.query(ip=_IP)

    assert response == _response(_IP)
    assert signed == {
        "data": {"resource": _IP},
        "app_id": "SEC-MODEL",
        "app_key": "not-logged-secret",
    }
    assert sent["url"] == "https://isec.example.internal/public/indicatorSearch"
    assert sent["body"] == {"resource": _IP}
    assert sent["headers"]["app-sign"] == "signed"


def test_http_port_rejects_unsafe_host_and_oversized_response() -> None:
    with pytest.raises(PingAnThreatIntelConfigurationError, match="allowlist"):
        HttpPingAnZeusThreatIntelPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["different.example.internal"],
        )

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_response(_IP)))) as client:
        port = HttpPingAnZeusThreatIntelPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["isec.example.internal"],
            max_response_bytes=10,
            client=client,
        )
        with pytest.raises(PingAnThreatIntelResponseError, match="size limit"):
            port.query(ip=_IP)

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not-json"))) as client:
        port = HttpPingAnZeusThreatIntelPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["isec.example.internal"],
            client=client,
        )
        with pytest.raises(PingAnThreatIntelResponseError, match="invalid JSON"):
            port.query(ip=_IP)


def test_service_maps_reviewed_fields_without_legacy_risk_scoring() -> None:
    service = _internal_service(_response(_IP))

    result = service.lookup({"ip": _IP})

    assert result.reputation_found is True
    assert result.mocked is False
    assert result.provider_mode == "internal"
    assert result.freshness_status == "fresh"
    assert result.evidence_boundary == "investigation_only"
    assert result.decision_impact == "none"
    assert result.automation_eligible is False
    assert result.raw_response_included is False
    assert result.reputation is not None
    assert result.reputation.labels == ["C2", "malware_infrastructure", "Suspicious", "ThreatActor", "Botnet"]
    assert result.reputation.score is None
    assert result.reputation.confidence is None
    assert result.reputation.last_seen is None
    assert result.reputation.geo == "中国 广东 深圳"
    assert result.reputation.source == "pingan_zeus_indicator_search"
    assert result.reputation.attributes["provider_score_mapped"] is False
    assert result.reputation.attributes["provider_confidence_mapped"] is False
    assert result.reputation.attributes["selected_context"]["reputation"] == {
        "scene": "Company",
        "carrier": "example-carrier",
        "location": {"country": "中国", "province": "广东", "city": "深圳"},
    }
    c2 = next(item for item in result.label_evidence if item.label == "C2")
    assert c2.source_paths == [
        f"data.intelligenceInformation.ipAnalyseReport.data[{_IP}].intelligences.threatbook_lab[0].intel_types[0]",
        f"data.intelligenceInformation.ipReputationReport.data[{_IP}].tags_classes[0].tags[1]",
    ]
    dumped = result.model_dump(mode="json")
    assert "raw_response" not in dumped
    assert len(result.response_sha256) == 64
    assert any("unreviewed_internal_blob" in item for item in result.mapping_warnings)
    assert any("provider_score" in item for item in result.mapping_warnings)
    assert any("longitude" in item for item in result.mapping_warnings)


def test_service_distinguishes_explicit_not_found_from_invalid_response() -> None:
    service = _internal_service(_empty_response())

    result = service.lookup({"ip": _IP})

    assert result.reputation_found is False
    assert result.reputation is None
    assert result.freshness_status == "not_found"
    assert result.mocked is False

    with pytest.raises(PingAnThreatIntelResponseError, match="both reviewed report branches"):
        _internal_service({"data": {"intelligenceInformation": {}}}).lookup({"ip": _IP})


def test_service_preserves_partial_report_and_unknown_freshness() -> None:
    response = _empty_response()
    intelligence = response["data"]["intelligenceInformation"]
    intelligence.pop("ipAnalyseReport")
    intelligence["ipReputationReport"]["data"][_IP] = {
        "judgments": ["Suspicious"],
        "update_time": "unparseable-provider-time",
    }

    result = _internal_service(response).lookup({"ip": _IP})

    assert result.reputation_found is True
    assert result.freshness_status == "unknown"
    assert result.reputation is not None and result.reputation.stale is True
    assert any("omitted reviewed report branch ipAnalyseReport" in item for item in result.mapping_warnings)
    assert any("freshness remains unknown" in item for item in result.mapping_warnings)


def test_service_bounds_large_label_arrays_and_reports_trimming() -> None:
    response = _empty_response()
    response["data"]["intelligenceInformation"]["ipReputationReport"]["data"][_IP] = {
        "judgments": [f"label-{index}" for index in range(120)],
        "update_time": "2026-08-02 12:00:00",
    }

    result = _internal_service(response).lookup({"ip": _IP})

    assert result.reputation is not None
    assert len(result.reputation.labels) == 100
    assert any("exceeded 100 values" in item for item in result.mapping_warnings)


def test_service_fails_closed_on_transport_failure() -> None:
    service = _internal_service(httpx.ReadTimeout("provider timed out"))

    with pytest.raises(PingAnThreatIntelUnavailableError) as exc_info:
        service.lookup({"ip": _IP})

    assert exc_info.value.error_type == "ReadTimeout"
    assert "provider timed out" not in str(exc_info.value)


def test_environment_builder_never_falls_back_from_internal_to_fake() -> None:
    with pytest.raises(PingAnThreatIntelConfigurationError, match="SOC_PINGAN_ZEUS_ALLOWED_HOSTS"):
        build_pingan_threat_intel_service_from_env({"SOC_PINGAN_THREAT_INTEL_PROVIDER_MODE": "internal"})

    fake = build_pingan_threat_intel_service_from_env({"SOC_PINGAN_THREAT_INTEL_PROVIDER_MODE": "fake"}).lookup({"ip": "203.0.113.10"})
    assert fake.mocked is True
    assert fake.provider_mode == "fake"


def test_mcp_returns_bounded_structured_result() -> None:
    response = _handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "ip_reputation_lookup",
                "arguments": {"ip": _IP},
            },
        },
        service=_internal_service(_response(_IP)),
        startup_error=None,
    )

    assert response is not None
    structured = response["result"]["structuredContent"]
    assert structured["reputation_found"] is True
    assert structured["mocked"] is False
    assert structured["decision_impact"] == "none"
    assert structured["raw_response_included"] is False
    assert structured["reputation"]["score"] is None


def test_mcp_action_persists_real_shaped_result_and_domain_reads_nested_payload() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config_path = backend_root / "samples" / "mcp" / "pingan_threat_intel" / "action_adapters.json"
    provider_result = _internal_service(_response(_IP)).lookup({"ip": _IP}).model_dump(mode="json")
    provider = _FakeMcpProvider(provider_result)
    registry = build_mcp_action_adapter_registry_from_file(config_path, provider)
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    dispatcher = SocAgentActionDispatcher(
        action_adapter_registry=registry,
        evidence_repository=evidence_repository,
    )
    request = SocAgentChatRequest(
        message="lookup threat intelligence",
        thread_id="THR-TI-1",
        run_id="RUN-TI-1",
        allowed_routes=["threat_intel.ip_reputation.lookup"],
        metadata={
            "soc_route": "threat_intel.ip_reputation.lookup",
            "action_payload": {
                "ip": _IP,
                "context_refs": {
                    "thread_id": "THR-TI-1",
                    "run_id": "RUN-TI-1",
                    "alert_id": "ALT-TI-1",
                },
            },
        },
    )
    context = _context()
    route = SocAgentCapabilityRouter(allowed_routes={"threat_intel.ip_reputation.lookup"}).route(request)
    permission = dispatcher.check_permission(request, route, context=context)

    result = dispatcher.dispatch(request, route, context=context, permission_decision=permission)

    assert result.status == "success"
    evidence = evidence_repository.list_evidence(thread_id="THR-TI-1")
    assert len(evidence) == 1
    assert evidence[0].mocked is False
    assert evidence[0].result_payload["mcp_result"]["reputation_found"] is True
    assert provider.invocations == [{"ip": _IP}]

    run = AnalysisRun(
        run_id="RUN-TI-1",
        alert_id="ALT-TI-1",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload={},
    )
    triage = SocDomainTriageService().triage(
        SocDomainTriageRequest(
            run=run,
            domain=SocDomainName.APT,
            investigation_evidence=evidence,
        )
    )
    primary = next(item for item in triage.findings if item.title == "APT/network direction and reputation triage")
    assert primary.disposition is SocDomainFindingDisposition.SUSPICIOUS
    assert evidence[0].evidence_id in primary.evidence_refs


def test_cli_fake_mcp_smoke_keeps_mock_provenance_visible() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    sample_root = backend_root / "samples" / "mcp" / "pingan_threat_intel"
    env = {
        **os.environ,
        "SOC_PINGAN_THREAT_INTEL_MCP_PYTHON": sys.executable,
        "SOC_PINGAN_THREAT_INTEL_MCP_SERVER": str(backend_root / "scripts" / "soc_pingan_threat_intel_mcp_server.py"),
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": str(sample_root / "extensions.fake.json"),
    }
    completed = subprocess.run(  # noqa: S603 - exact local interpreter and reviewed script path
        [
            sys.executable,
            "-m",
            "soc_agent.cli",
            "mcp",
            "smoke",
            str(sample_root / "action_adapters.json"),
            "--route",
            "threat_intel.ip_reputation.lookup",
            "--json",
            json.dumps(
                {
                    "ip": "203.0.113.10",
                    "context_refs": {"thread_id": "PI-01A-FAKE"},
                }
            ),
        ],
        cwd=backend_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    output = json.loads(completed.stdout)
    assert output["status"] == "success"
    result = output["action_result"]["payload"]["mcp_result"]
    assert result["mocked"] is True
    assert result["provider_mode"] == "fake"
    assert result["reputation_found"] is True
    assert result["decision_impact"] == "none"


class _StaticInternalPort:
    mocked = False

    def __init__(self, response: Mapping[str, Any] | Exception) -> None:
        self._response = response

    def query(self, *, ip: str) -> Mapping[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeMcpProvider:
    def __init__(self, result: Mapping[str, Any]) -> None:
        self._result = dict(result)
        self.invocations: list[dict[str, Any]] = []

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        return [SocMcpToolDescriptor(name="pingan_threat_intel_ip_reputation_lookup")]

    def invoke(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
        server_name: str | None = None,
    ) -> Mapping[str, Any]:
        self.invocations.append(dict(payload))
        return self._result


def _internal_service(response: Mapping[str, Any] | Exception) -> PingAnThreatIntelService:
    return PingAnThreatIntelService(
        search_port=_StaticInternalPort(response),
        provider_mode="internal",
        freshness_days=180,
        now=lambda: _NOW,
    )


def _context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-ti-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
        )
    )


def _empty_response() -> dict[str, Any]:
    return {
        "data": {
            "intelligenceInformation": {
                "ipAnalyseReport": {"data": {}},
                "ipReputationReport": {"data": {}},
            }
        }
    }


def _response(ip: str) -> dict[str, Any]:
    return {
        "data": {
            "intelligenceInformation": {
                "ipAnalyseReport": {
                    "data": {
                        ip: {
                            "intelligences": {
                                "threatbook_lab": [
                                    {
                                        "intel_types": ["C2"],
                                        "intel_tags": ["malware_infrastructure"],
                                    }
                                ]
                            },
                            "update_time": "2026-08-01 12:00:00",
                            "unreviewed_internal_blob": "must-not-leave-provider",
                        }
                    }
                },
                "ipReputationReport": {
                    "data": {
                        ip: {
                            "judgments": ["Suspicious"],
                            "tags_classes": [
                                {
                                    "tags_type": "ThreatActor",
                                    "tags": ["Botnet", "C2"],
                                }
                            ],
                            "scene": "Company",
                            "basic": {
                                "carrier": "example-carrier",
                                "location": {
                                    "country": "中国",
                                    "province": "广东",
                                    "city": "深圳",
                                    "longitude": "must-not-leave-provider",
                                },
                            },
                            "update_time": "2026-08-02 12:00:00",
                            "provider_score": 99,
                        }
                    }
                },
            }
        }
    }
