from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from soc_agent.integrations.pingan.agent_workflow import (
    HttpPingAnAgentWorkflowPort,
    PingAnAgentWorkflowConfigurationError,
    PingAnAgentWorkflowHttpConfig,
    PingAnAgentWorkflowResponseError,
)


def test_http_workflow_port_replays_reviewed_auth_create_poll_contract() -> None:
    requests: list[httpx.Request] = []
    result_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal result_calls
        requests.append(request)
        if request.url.path == "/appid/auth/login":
            assert json.loads(request.content) == {"appId": "YHSYS", "appSecret": "secret"}
            return httpx.Response(200, json={"code": 0, "data": "token-1"})
        assert request.headers["auth-token"] == "token-1"
        if request.url.path == "/api/v1/workflows/1087787/runs":
            assert request.url.params["is_async"] == "true"
            assert request.url.params["streaming"] == "false"
            assert request.url.params["workflow_version"] == "default"
            assert json.loads(request.content)["args"] == {"ip": "10.0.0.8"}
            return httpx.Response(200, json={"code": 0, "data": {"workflow_run_id": "run-1"}})
        if request.url.path == "/api/v1/workflows/1087787/runs/run-1/result":
            result_calls += 1
            if result_calls == 1:
                return httpx.Response(200, json={"code": 0, "data": {"status": "processing"}})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": "completed",
                        "outputs": [{"content": '{"company_code":"PA011"}'}],
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    port = HttpPingAnAgentWorkflowPort(_config(), client=client, sleep=lambda _: None)

    result = port.run(
        app_id="YHSYS",
        workflow_id=1087787,
        query_data={"message": {"by": "UM001"}, "args": {"ip": "10.0.0.8"}},
    )

    assert result == '{"company_code":"PA011"}'
    assert [request.url.path for request in requests].count("/appid/auth/login") == 1
    assert result_calls == 2


def test_http_workflow_port_refreshes_token_once_after_unauthorized() -> None:
    auth_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal auth_count
        if request.url.path == "/appid/auth/login":
            auth_count += 1
            return httpx.Response(200, json={"code": 0, "data": f"token-{auth_count}"})
        if request.url.path.endswith("/runs") and request.headers["auth-token"] == "token-1":
            return httpx.Response(401, json={"code": 401})
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"code": 0, "data": {"workflow_run_id": "run-2"}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "completed", "outputs": []}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    port = HttpPingAnAgentWorkflowPort(_config(), client=client)

    assert port.run(app_id="YHSYS", workflow_id=1087710, query_data={"args": {"host": "host-a"}}) is None
    assert auth_count == 2


def test_http_workflow_port_rejects_failed_workflow_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/appid/auth/login":
            return httpx.Response(200, json={"code": 0, "data": "token"})
        if request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"code": 0, "data": {"workflow_run_id": "run-3"}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "failed"}})

    port = HttpPingAnAgentWorkflowPort(
        _config(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(PingAnAgentWorkflowResponseError, match="status failed"):
        port.run(app_id="YHSYS", workflow_id=1087710, query_data={"args": {"host": "host-a"}})


def test_http_workflow_config_requires_allowlisted_https_and_explicit_prd() -> None:
    with pytest.raises(ValidationError, match="allowlist"):
        _config(base_url="https://other.example")
    with pytest.raises(ValidationError, match="explicit production confirmation"):
        _config(environment="prd")
    assert _config(environment="prd", allow_prd=True).environment == "prd"


def test_http_workflow_port_rejects_mismatched_app_id() -> None:
    port = HttpPingAnAgentWorkflowPort(_config())
    with pytest.raises(PingAnAgentWorkflowConfigurationError, match="app ID"):
        port.run(app_id="OTHER", workflow_id=1087710, query_data={})


def _config(**overrides: object) -> PingAnAgentWorkflowHttpConfig:
    values: dict[str, object] = {
        "environment": "stg",
        "base_url": "https://agent-stg.example",
        "allowed_hosts": ("agent-stg.example",),
        "app_id": "YHSYS",
        "app_secret": "secret",
        "poll_interval_seconds": 0.01,
    }
    values.update(overrides)
    return PingAnAgentWorkflowHttpConfig.model_validate(values)
