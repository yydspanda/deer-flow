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
from soc_agent.integrations.pingan.security_tag import (
    HttpPingAnZeusSecurityTagPort,
    PingAnSecurityTagConfigurationError,
    PingAnSecurityTagResponseError,
    PingAnSecurityTagService,
    PingAnSecurityTagUnavailableError,
    build_pingan_security_tag_service_from_env,
)
from soc_agent.integrations.pingan.security_tag_mcp_server import _handle_message

_ENTITY = "198.51.100.9"
_NOW = datetime(2026, 8, 4, 4, 0, tzinfo=UTC)


def test_http_port_preserves_search_tag_content_wire_contract() -> None:
    signed: dict[str, Any] = {}
    sent: dict[str, Any] = {}

    def signer(*, data: dict[str, Any], app_id: str, app_key: str) -> dict[str, str]:
        signed.update({"data": data, "app_id": app_id, "app_key": app_key})
        return {"App-Sign": "signed"}

    def handle(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        sent["headers"] = dict(request.headers)
        return httpx.Response(200, json=_active_response(_ENTITY))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        port = HttpPingAnZeusSecurityTagPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="test-secret",
            allowed_hosts=["isec.example.internal"],
            signer=signer,
            client=client,
        )
        response = port.query(entity_key=_ENTITY)

    assert response == _active_response(_ENTITY)
    assert signed == {
        "data": {"keywords": [_ENTITY]},
        "app_id": "SEC-MODEL",
        "app_key": "test-secret",
    }
    assert sent["url"] == "https://isec.example.internal/public/searchTagContent"
    assert sent["body"] == {"keywords": [_ENTITY]}
    assert sent["headers"]["app-sign"] == "signed"


def test_http_port_rejects_unsafe_host_oversized_and_invalid_json() -> None:
    with pytest.raises(PingAnSecurityTagConfigurationError, match="allowlist"):
        HttpPingAnZeusSecurityTagPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["different.example.internal"],
        )

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_active_response(_ENTITY)))) as client:
        port = HttpPingAnZeusSecurityTagPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["isec.example.internal"],
            max_response_bytes=10,
            client=client,
        )
        with pytest.raises(PingAnSecurityTagResponseError, match="size limit"):
            port.query(entity_key=_ENTITY)

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not-json"))) as client:
        port = HttpPingAnZeusSecurityTagPort(
            base_url="https://isec.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["isec.example.internal"],
            client=client,
        )
        with pytest.raises(PingAnSecurityTagResponseError, match="invalid JSON"):
            port.query(entity_key=_ENTITY)


def test_service_maps_active_tag_without_creating_authorization_fact() -> None:
    result = _internal_service(_active_response(_ENTITY)).lookup({"entity_key": _ENTITY, "entity_type": "ip"})

    assert result.provider_records_found is True
    assert result.security_tag_found is True
    assert result.has_active is True
    assert result.lookup_status == "active"
    assert result.mocked is False
    assert result.provider_mode == "internal"
    assert result.evidence_boundary == "investigation_only"
    assert result.decision_impact == "none"
    assert result.authorization_fact_created is False
    assert result.automation_eligible is False
    assert result.raw_response_included is False
    assert result.source_freshness == "unknown"
    assert result.provider_version is None
    assert len(result.response_sha256) == 64
    assert result.security_tag is not None
    assert result.security_tag.labels == ["authorized_security_test", "red_team"]
    assert result.security_tag.tag_types == ["security_test"]
    assert result.security_tag.is_valid is True
    assert result.records[0].source_path == "data[0]"
    assert result.records[0].exact_entity_match is True
    assert result.records[0].validity_status == "active"
    assert any("internalNote" in warning for warning in result.mapping_warnings)
    dumped = result.model_dump(mode="json")
    assert "raw_response" not in dumped
    assert "must-not-leave-provider" not in json.dumps(dumped)


def test_service_preserves_expired_and_inactive_records_instead_of_hiding_them() -> None:
    expired = _tag_record(_ENTITY, is_valid=False, expire_time="2026-07-01 00:00:00")
    result = _internal_service({"data": [expired]}).lookup({"entity_key": _ENTITY, "entity_type": "ip"})

    assert result.security_tag_found is True
    assert result.has_active is False
    assert result.lookup_status == "expired"
    assert result.security_tag is not None and result.security_tag.is_valid is False
    assert result.records[0].validity_status == "expired"

    inactive = _tag_record(_ENTITY, is_valid=False, expire_time="2026-09-01 00:00:00")
    result = _internal_service({"data": [inactive]}).lookup({"entity_key": _ENTITY, "entity_type": "ip"})
    assert result.lookup_status == "inactive"
    assert result.has_active is False


def test_missing_or_invalid_expiry_fails_closed_unless_open_ended_is_explicit() -> None:
    without_expiry = _tag_record(_ENTITY, is_valid=True, expire_time=None)
    result = _internal_service({"data": [without_expiry]}).lookup({"entity_key": _ENTITY})

    assert result.lookup_status == "unknown"
    assert result.has_active is False
    assert result.records[0].open_ended_validity_accepted is False

    result = _internal_service(
        {"data": [without_expiry]},
        allow_open_ended_validity=True,
    ).lookup({"entity_key": _ENTITY})
    assert result.lookup_status == "active"
    assert result.has_active is True
    assert result.records[0].open_ended_validity_accepted is True

    invalid_expiry = _tag_record(_ENTITY, is_valid=True, expire_time="not-a-time")
    result = _internal_service({"data": [invalid_expiry]}).lookup({"entity_key": _ENTITY})
    assert result.lookup_status == "unknown"
    assert result.has_active is False
    assert any("could not be parsed" in warning for warning in result.mapping_warnings)


def test_service_reports_conflicting_validity_and_out_of_scope_records() -> None:
    conflict = _tag_record(_ENTITY, is_valid=True, expire_time="2026-07-01 00:00:00")
    result = _internal_service({"data": [conflict]}).lookup({"entity_key": _ENTITY})

    assert result.lookup_status == "conflicted"
    assert result.has_active is False
    assert result.records[0].validity_status == "conflict"
    assert any("isValid=true" in warning for warning in result.mapping_warnings)

    result = _internal_service({"data": [_tag_record("198.51.100.10")]}).lookup({"entity_key": _ENTITY, "entity_type": "ip"})
    assert result.provider_records_found is True
    assert result.security_tag_found is False
    assert result.lookup_status == "out_of_scope"
    assert result.has_active is False


def test_service_normalizes_ip_scope_even_without_entity_type() -> None:
    result = _internal_service({"data": [_tag_record("2001:db8::1")]}).lookup({"entity_key": "2001:0db8:0:0:0:0:0:1"})

    assert result.lookup_status == "active"
    assert result.has_active is True


def test_service_distinguishes_not_found_unusable_and_invalid_response() -> None:
    result = _internal_service({"data": []}).lookup({"entity_key": _ENTITY})
    assert result.provider_records_found is False
    assert result.security_tag_found is False
    assert result.lookup_status == "not_found"

    result = _internal_service({"data": ["not-an-object"]}).lookup({"entity_key": _ENTITY})
    assert result.provider_records_found is True
    assert result.lookup_status == "unusable"
    assert result.records_omitted_count == 1

    result = _internal_service({"data": [{"isValid": True}]}).lookup({"entity_key": _ENTITY})
    assert result.lookup_status == "unusable"
    assert result.security_tag_found is False

    with pytest.raises(PingAnSecurityTagResponseError, match="omitted data"):
        _internal_service({}).lookup({"entity_key": _ENTITY})
    with pytest.raises(PingAnSecurityTagResponseError, match="must be an array"):
        _internal_service({"data": {}}).lookup({"entity_key": _ENTITY})
    with pytest.raises(PingAnSecurityTagResponseError, match="must be an array"):
        _internal_service({"data": None}).lookup({"entity_key": _ENTITY})
    with pytest.raises(PingAnSecurityTagResponseError, match="non-success response code"):
        _internal_service({"code": 500, "data": []}).lookup({"entity_key": _ENTITY})

    result = _internal_service({"code": 200, "data": []}).lookup({"entity_key": _ENTITY})
    assert result.lookup_status == "not_found"


def test_service_bounds_records_and_labels() -> None:
    response = {
        "data": [
            {
                **_tag_record(_ENTITY),
                "labels": [f"label-{index}" for index in range(120)],
            },
            *[_tag_record(f"198.51.100.{index}") for index in range(10, 115)],
        ]
    }
    result = _internal_service(response).lookup({"entity_key": _ENTITY, "entity_type": "ip"})

    assert len(result.records) == 100
    assert len(result.records[0].labels) == 100
    assert result.records_omitted_count == 6
    assert any("excess records" in warning for warning in result.mapping_warnings)
    assert any("excess labels" in warning for warning in result.mapping_warnings)


def test_service_fails_closed_on_transport_failure() -> None:
    service = _internal_service(httpx.ReadTimeout("provider timed out"))

    with pytest.raises(PingAnSecurityTagUnavailableError) as exc_info:
        service.lookup({"entity_key": _ENTITY})

    assert exc_info.value.error_type == "ReadTimeout"
    assert "provider timed out" not in str(exc_info.value)


def test_environment_builder_never_falls_back_from_internal_to_fake() -> None:
    with pytest.raises(PingAnSecurityTagConfigurationError, match="SOC_PINGAN_ZEUS_BASE_URL"):
        build_pingan_security_tag_service_from_env({"SOC_PINGAN_SECURITY_TAG_PROVIDER_MODE": "internal"})

    with pytest.raises(PingAnSecurityTagConfigurationError, match="must be a boolean"):
        build_pingan_security_tag_service_from_env(
            {
                "SOC_PINGAN_SECURITY_TAG_PROVIDER_MODE": "fake",
                "SOC_PINGAN_SECURITY_TAG_ALLOW_OPEN_ENDED_VALIDITY": "sometimes",
            }
        )

    fake = build_pingan_security_tag_service_from_env({"SOC_PINGAN_SECURITY_TAG_PROVIDER_MODE": "fake"}).lookup({"entity_key": "203.0.113.10", "entity_type": "ip"})
    assert fake.mocked is True
    assert fake.provider_mode == "fake"
    assert fake.has_active is True


def test_mcp_returns_bounded_structured_result() -> None:
    response = _handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "security_tag_lookup",
                "arguments": {"entity_key": _ENTITY, "entity_type": "ip"},
            },
        },
        service=_internal_service(_active_response(_ENTITY)),
        startup_error=None,
    )

    assert response is not None
    structured = response["result"]["structuredContent"]
    assert structured["security_tag_found"] is True
    assert structured["has_active"] is True
    assert structured["mocked"] is False
    assert structured["decision_impact"] == "none"
    assert structured["authorization_fact_created"] is False
    assert structured["raw_response_included"] is False


def test_mcp_action_persists_real_shaped_result_and_domain_reads_nested_payload() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config_path = backend_root / "samples" / "mcp" / "pingan_security_tag" / "action_adapters.json"
    provider_result = _internal_service(_active_response(_ENTITY)).lookup({"entity_key": _ENTITY, "entity_type": "ip"}).model_dump(mode="json")
    provider = _FakeMcpProvider(provider_result)
    registry = build_mcp_action_adapter_registry_from_file(config_path, provider)
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    dispatcher = SocAgentActionDispatcher(
        action_adapter_registry=registry,
        evidence_repository=evidence_repository,
    )
    request = SocAgentChatRequest(
        message="lookup security tag",
        thread_id="THR-TAG-1",
        run_id="RUN-TAG-1",
        allowed_routes=["security_tag.lookup"],
        metadata={
            "soc_route": "security_tag.lookup",
            "action_payload": {
                "entity_key": _ENTITY,
                "entity_type": "ip",
                "context_refs": {
                    "thread_id": "THR-TAG-1",
                    "run_id": "RUN-TAG-1",
                    "alert_id": "ALT-TAG-1",
                },
            },
        },
    )
    context = _context()
    route = SocAgentCapabilityRouter(allowed_routes={"security_tag.lookup"}).route(request)
    permission = dispatcher.check_permission(request, route, context=context)

    result = dispatcher.dispatch(request, route, context=context, permission_decision=permission)

    assert result.status == "success"
    evidence = evidence_repository.list_evidence(thread_id="THR-TAG-1")
    assert len(evidence) == 1
    assert evidence[0].mocked is False
    assert evidence[0].result_payload["mcp_result"]["has_active"] is True
    assert provider.invocations == [{"entity_key": _ENTITY, "entity_type": "ip"}]

    run = AnalysisRun(
        run_id="RUN-TAG-1",
        alert_id="ALT-TAG-1",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload={},
    )
    triage = SocDomainTriageService().triage(
        SocDomainTriageRequest(
            run=run,
            domain=SocDomainName.HIDS,
            investigation_evidence=evidence,
        )
    )
    finding = next(item for item in triage.findings if item.title == "HIDS alert-native triage")
    assert finding.disposition is SocDomainFindingDisposition.BENIGN_AUTHORIZED_CANDIDATE
    assert evidence[0].evidence_id in finding.evidence_refs
    assert finding.current_conclusion.recommended_action == "manual_review"


def test_cli_fake_mcp_smoke_keeps_mock_and_governance_boundaries_visible() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    sample_root = backend_root / "samples" / "mcp" / "pingan_security_tag"
    env = {
        **os.environ,
        "SOC_PINGAN_SECURITY_TAG_MCP_PYTHON": sys.executable,
        "SOC_PINGAN_SECURITY_TAG_MCP_SERVER": str(backend_root / "scripts" / "soc_pingan_security_tag_mcp_server.py"),
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
            "security_tag.lookup",
            "--json",
            json.dumps(
                {
                    "entity_key": "203.0.113.10",
                    "entity_type": "ip",
                    "context_refs": {"thread_id": "PI-01B1-FAKE"},
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
    assert result["has_active"] is True
    assert result["authorization_fact_created"] is False
    assert result["decision_impact"] == "none"


class _StaticInternalPort:
    mocked = False

    def __init__(self, response: Mapping[str, Any] | Exception) -> None:
        self._response = response

    def query(self, *, entity_key: str) -> Mapping[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeMcpProvider:
    def __init__(self, result: Mapping[str, Any]) -> None:
        self._result = dict(result)
        self.invocations: list[dict[str, Any]] = []

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        return [SocMcpToolDescriptor(name="pingan_security_tag_security_tag_lookup")]

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


def _internal_service(
    response: Mapping[str, Any] | Exception,
    *,
    allow_open_ended_validity: bool = False,
) -> PingAnSecurityTagService:
    return PingAnSecurityTagService(
        search_port=_StaticInternalPort(response),
        provider_mode="internal",
        allow_open_ended_validity=allow_open_ended_validity,
        now=lambda: _NOW,
    )


def _context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-tag-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
        )
    )


def _active_response(entity_key: str) -> dict[str, Any]:
    return {
        "data": [
            {
                **_tag_record(entity_key),
                "internalNote": "must-not-leave-provider",
            }
        ]
    }


def _tag_record(
    entity_key: str,
    *,
    is_valid: bool = True,
    expire_time: str | None = "2026-09-01 00:00:00",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tagValue": entity_key,
        "tagType": "security_test",
        "tagCode": "DEV-AUTHORIZED-TEST",
        "isValid": is_valid,
        "labels": ["authorized_security_test", "red_team"],
    }
    if expire_time is not None:
        result["expireTime"] = expire_time
    return result
