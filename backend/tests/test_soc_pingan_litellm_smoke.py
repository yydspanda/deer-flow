from __future__ import annotations

import json

import httpx

from soc_agent.integrations.pingan.litellm_smoke import run_pingan_litellm_smoke


def test_litellm_smoke_calls_chat_completions_without_persisting_content_or_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://localhost:4001/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer local-secret"
        payload = json.loads(request.content)
        assert payload == {
            "model": "DeepSeek_V4_Flash",
            "messages": [{"role": "user", "content": "Reply with exactly the ASCII text OK."}],
            "temperature": 0,
            "max_tokens": 8,
            "stream": False,
        }
        return httpx.Response(
            200,
            json={
                "id": "internal-response-id",
                "model": "DeepSeek_V4_Flash",
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
        report = run_pingan_litellm_smoke(_valid_env(), client=client)

    assert report.outcome == "passed"
    assert report.passed is True
    assert report.endpoint_path == "/v1/chat/completions"
    assert report.model_returned == "DeepSeek_V4_Flash"
    assert report.content_length == 2
    assert report.total_tokens == 9
    encoded = json.dumps(report.model_dump(mode="json"))
    assert "local-secret" not in encoded
    assert '"OK"' not in encoded
    assert "internal-response-id" not in encoded


def test_litellm_smoke_rejects_non_loopback_without_calling_transport() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    env = _valid_env()
    env["PINGAN_LITELLM_BASE_URL"] = "https://model.example/v1/"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_litellm_smoke(env, client=client)

    assert report.outcome == "invalid_configuration"
    assert report.passed is False
    assert called is False


def test_litellm_smoke_classifies_authentication_failure_without_response_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="credential local-secret rejected")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_litellm_smoke(_valid_env(), client=client)

    assert report.outcome == "authentication_failed"
    assert report.http_status == 401
    assert "local-secret" not in json.dumps(report.model_dump(mode="json"))
    assert "credential local-secret rejected" not in report.error_message


def test_litellm_smoke_rejects_empty_completion() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "DeepSeek_V4_Flash",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_litellm_smoke(_valid_env(), client=client)

    assert report.outcome == "invalid_response"
    assert report.passed is False


def _valid_env() -> dict[str, str]:
    return {
        "PINGAN_LITELLM_BASE_URL": "http://localhost:4001/v1/",
        "PINGAN_LITELLM_API_KEY": "local-secret",
        "PINGAN_LITELLM_MODEL": "DeepSeek_V4_Flash",
        "PINGAN_LITELLM_SMOKE_TIMEOUT_SECONDS": "10",
        "PINGAN_LITELLM_SMOKE_MAX_RESPONSE_BYTES": "100000",
    }
