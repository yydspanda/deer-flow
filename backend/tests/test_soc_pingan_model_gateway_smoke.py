from __future__ import annotations

import json

import httpx

from soc_agent.integrations.pingan.model_gateway_smoke import run_pingan_model_gateway_smoke


def test_model_gateway_smoke_calls_chat_completions_without_persisting_content_or_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://localhost:4001/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer local-secret"
        payload = json.loads(request.content)
        assert payload == {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "Reply with exactly the ASCII text OK."}],
            "temperature": 0,
            "max_tokens": 128,
            "stream": False,
            "extra_body": {
                "chat_template_kwargs": {
                    "enable_thinking": False,
                }
            },
        }
        return httpx.Response(
            200,
            json={
                "id": "internal-response-id",
                "model": "deepseek-v4-flash-0731",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 1,
                    "total_tokens": 9,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_model_gateway_smoke(_valid_env(), client=client)

    assert report.outcome == "passed"
    assert report.passed is True
    assert report.endpoint_path == "/v1/chat/completions"
    assert report.model_returned == "deepseek-v4-flash-0731"
    assert report.content_length == 2
    assert report.total_tokens == 9
    assert report.max_tokens_requested == 128
    assert report.thinking_requested is False
    assert report.reasoning_effort_requested is None
    encoded = json.dumps(report.model_dump(mode="json"))
    assert "local-secret" not in encoded
    assert '"OK"' not in encoded
    assert "internal-response-id" not in encoded


def test_model_gateway_smoke_rejects_non_loopback_without_calling_transport() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    env = _valid_env()
    env["PINGAN_MODEL_GATEWAY_BASE_URL"] = "https://model.example/v1/"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_model_gateway_smoke(env, client=client)

    assert report.outcome == "invalid_configuration"
    assert report.passed is False
    assert called is False


def test_model_gateway_smoke_classifies_authentication_failure_without_response_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="credential local-secret rejected")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_model_gateway_smoke(_valid_env(), client=client)

    assert report.outcome == "authentication_failed"
    assert report.http_status == 401
    assert "local-secret" not in json.dumps(report.model_dump(mode="json"))
    assert "credential local-secret rejected" not in report.error_message


def test_model_gateway_smoke_rejects_empty_completion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 512
        assert payload["extra_body"] == {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "reasoning_effort": "high",
            }
        }
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash-0731",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    env = _valid_env()
    env.update(
        {
            "PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED": "true",
            "PINGAN_MODEL_GATEWAY_SMOKE_REASONING_EFFORT": "high",
            "PINGAN_MODEL_GATEWAY_SMOKE_MAX_TOKENS": "512",
        }
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_model_gateway_smoke(env, client=client)

    assert report.outcome == "invalid_response"
    assert report.passed is False
    assert report.max_tokens_requested == 512
    assert report.thinking_requested is True
    assert report.reasoning_effort_requested == "high"


def _valid_env() -> dict[str, str]:
    return {
        "PINGAN_MODEL_GATEWAY_BASE_URL": "http://localhost:4001/v1/",
        "PINGAN_MODEL_GATEWAY_API_KEY": "local-secret",
        "PINGAN_MODEL_GATEWAY_MODEL": "deepseek-v4-flash",
        "PINGAN_MODEL_GATEWAY_SMOKE_TIMEOUT_SECONDS": "10",
        "PINGAN_MODEL_GATEWAY_SMOKE_MAX_RESPONSE_BYTES": "100000",
        "PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED": "false",
        "PINGAN_MODEL_GATEWAY_SMOKE_REASONING_EFFORT": "high",
    }
