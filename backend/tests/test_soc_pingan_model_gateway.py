from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.pingan_model_gateway import create_pingan_model_gateway_app
from soc_agent.integrations.pingan.model_gateway import (
    PingAnModelGateway,
    PingAnModelGatewayBusyError,
    PingAnModelGatewaySettings,
    PingAnModelGatewayUpstreamError,
    PingAnModelGatewayUpstreamTimeoutError,
    PingAnModelProvider,
    PingAnModelRoute,
)


def _chat_request(*, stream: bool = False) -> dict:
    return {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "return API_OK"}],
        "stream": stream,
        "chat_template_kwargs": {
            "enable_thinking": True,
            "reasoning_effort": "high",
        },
    }


def _chat_response(*, include_usage: bool = True) -> dict:
    response = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-flash-0731",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "API_OK"},
                "finish_reason": "stop",
            }
        ],
    }
    if include_usage:
        response["usage"] = {
            "prompt_tokens": 8,
            "completion_tokens": 2,
            "total_tokens": 10,
        }
    return response


@pytest.mark.asyncio
async def test_openai_route_maps_public_alias_and_preserves_reasoning_extension() -> None:
    sent: dict = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["headers"] = dict(request.headers)
        sent["body"] = request.content.decode()
        return httpx.Response(200, json=_chat_response(include_usage=False))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = PingAnModelGateway(
            routes=[
                PingAnModelRoute(
                    alias="deepseek-v4-flash",
                    upstream_model="deepseek-v4-flash-0731",
                    provider=PingAnModelProvider.OPENAI,
                    base_url="https://model.example.internal/v1",
                    allowed_hosts=("model.example.internal",),
                    api_key="upstream-secret",
                )
            ],
            client=client,
            max_concurrency=1,
            admission_timeout_seconds=0.1,
            upstream_timeout_seconds=1,
        )
        response = await gateway.complete(_chat_request())

    assert sent["url"] == "https://model.example.internal/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer upstream-secret"
    assert '"model":"deepseek-v4-flash-0731"' in sent["body"]
    assert '"enable_thinking":true' in sent["body"]
    assert '"reasoning_effort":"high"' in sent["body"]
    assert "usage" not in response.body
    assert response.metadata["usage_measurement_status"] == "unavailable"
    assert response.metadata["thinking_enabled_requested"] is True
    assert response.metadata["reasoning_effort_requested"] == "high"


@pytest.mark.asyncio
async def test_eagw_route_signs_request_and_uses_scene_instead_of_public_model() -> None:
    sent: dict = {}
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_hex = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()

    async def handle(request: httpx.Request) -> httpx.Response:
        sent["headers"] = dict(request.headers)
        sent["body"] = request.content.decode()
        return httpx.Response(200, json=_chat_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = PingAnModelGateway(
            routes=[
                PingAnModelRoute(
                    alias="deepseek-v4-flash",
                    upstream_model="deepseek-v4-flash-0731",
                    provider=PingAnModelProvider.EAGW,
                    base_url="https://eagw.example.internal/pingan/bigModel/api/v1",
                    allowed_hosts=("eagw.example.internal",),
                    app_key="app-key",
                    app_secret="app-secret",
                    scene_id="1737",
                    openapi_code="API035059",
                    openapi_credential="CRE00000054",
                    rsa_private_key_hex=private_key_hex,
                )
            ],
            client=client,
        )
        response = await gateway.complete(_chat_request())

    assert '"model"' not in sent["body"]
    assert sent["headers"]["scene_id"] == "1737"
    assert sent["headers"]["openapicode"] == "API035059"
    assert sent["headers"]["gpt_app_key"] == "app-key"
    assert sent["headers"]["gpt_signature"]
    assert re.fullmatch(r"[0-9A-F]+", sent["headers"]["openapisignature"])
    assert response.metadata["usage_measurement_status"] == "reported"


@pytest.mark.asyncio
async def test_gateway_rejects_second_call_when_model_capacity_is_full() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handle(_: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json=_chat_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = PingAnModelGateway(
            routes=[
                PingAnModelRoute(
                    alias="deepseek-v4-flash",
                    upstream_model="deepseek-v4-flash-0731",
                    provider=PingAnModelProvider.OPENAI,
                    base_url="https://model.example.internal/v1",
                    allowed_hosts=("model.example.internal",),
                    api_key="secret",
                )
            ],
            client=client,
            max_concurrency=1,
            admission_timeout_seconds=0.01,
        )
        first = asyncio.create_task(gateway.complete(_chat_request()))
        await started.wait()

        with pytest.raises(PingAnModelGatewayBusyError):
            await gateway.complete(_chat_request())

        release.set()
        await first

    snapshot = gateway.capacity_snapshot()
    assert snapshot["max_concurrency"] == 1
    assert snapshot["in_flight"] == 0
    assert snapshot["rejected_total"] == 1


@pytest.mark.asyncio
async def test_fastapi_surface_is_authenticated_and_rejects_streaming() -> None:
    async def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        gateway = PingAnModelGateway(
            routes=[
                PingAnModelRoute(
                    alias="deepseek-v4-flash",
                    upstream_model="deepseek-v4-flash-0731",
                    provider=PingAnModelProvider.OPENAI,
                    base_url="https://model.example.internal/v1",
                    allowed_hosts=("model.example.internal",),
                    api_key="upstream-secret",
                )
            ],
            client=upstream,
        )
        app = create_pingan_model_gateway_app(
            gateway=gateway,
            service_api_keys=("local-service-key",),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            unauthenticated = await client.get("/v1/models")
            models = await client.get(
                "/v1/models",
                headers={"Authorization": "Bearer local-service-key"},
            )
            streaming = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer local-service-key"},
                json=_chat_request(stream=True),
            )

    assert unauthenticated.status_code == 401
    assert models.json()["data"][0]["id"] == "deepseek-v4-flash"
    assert streaming.status_code == 400
    assert streaming.json()["error"]["code"] == "streaming_not_enabled"


@pytest.mark.asyncio
async def test_fastapi_surface_rejects_oversized_body_before_model_call() -> None:
    upstream_calls = 0

    async def handle(_: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json=_chat_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        gateway = PingAnModelGateway(
            routes=[
                PingAnModelRoute(
                    alias="deepseek-v4-flash",
                    upstream_model="deepseek-v4-flash-0731",
                    provider=PingAnModelProvider.OPENAI,
                    base_url="https://model.example.internal/v1",
                    allowed_hosts=("model.example.internal",),
                    api_key="upstream-secret",
                )
            ],
            client=upstream,
        )
        app = create_pingan_model_gateway_app(
            gateway=gateway,
            service_api_keys=("local-service-key",),
            max_request_bytes=32,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer local-service-key"},
                content=b"{" + b"x" * 64 + b"}",
            )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert upstream_calls == 0


@pytest.mark.asyncio
async def test_gateway_classifies_timeout_and_malformed_upstream_response() -> None:
    async def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream")

    route = PingAnModelRoute(
        alias="deepseek-v4-flash",
        upstream_model="deepseek-v4-flash-0731",
        provider=PingAnModelProvider.OPENAI,
        base_url="https://model.example.internal/v1",
        allowed_hosts=("model.example.internal",),
        api_key="upstream-secret",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        gateway = PingAnModelGateway(routes=[route], client=client)
        with pytest.raises(PingAnModelGatewayUpstreamTimeoutError):
            await gateway.complete(_chat_request())

    async def malformed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(malformed)) as client:
        gateway = PingAnModelGateway(routes=[route], client=client)
        with pytest.raises(PingAnModelGatewayUpstreamError, match="malformed JSON"):
            await gateway.complete(_chat_request())


def test_gateway_rejects_zero_admission_timeout() -> None:
    with pytest.raises(ValueError, match="admission_timeout_seconds"):
        PingAnModelGateway(
            routes=[
                PingAnModelRoute(
                    alias="deepseek-v4-flash",
                    upstream_model="deepseek-v4-flash-0731",
                    provider=PingAnModelProvider.OPENAI,
                    base_url="https://model.example.internal/v1",
                    allowed_hosts=("model.example.internal",),
                    api_key="upstream-secret",
                )
            ],
            client=httpx.AsyncClient(),
            admission_timeout_seconds=0,
        )


def test_settings_build_eagw_route_from_private_key_file(tmp_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_path = tmp_path / "eagw-key.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    settings = PingAnModelGatewaySettings.from_env(
        {
            "SOC_PINGAN_MODEL_GATEWAY_API_KEYS": "service-a,service-b",
            "SOC_PINGAN_MODEL_GATEWAY_PROVIDER": "eagw",
            "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_BASE_URL": ("http://eagw.internal:10086/pingan/bigModel/api/v1"),
            "SOC_PINGAN_MODEL_GATEWAY_ALLOWED_HOSTS": "eagw.internal",
            "SOC_PINGAN_MODEL_GATEWAY_ALLOW_INSECURE_HTTP": "true",
            "SOC_PINGAN_MODEL_GATEWAY_APP_KEY": "app-key",
            "SOC_PINGAN_MODEL_GATEWAY_APP_SECRET": "app-secret",
            "SOC_PINGAN_MODEL_GATEWAY_SCENE_ID": "1737",
            "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CODE": "API035059",
            "SOC_PINGAN_MODEL_GATEWAY_OPENAPI_CREDENTIAL": "credential",
            "SOC_PINGAN_MODEL_GATEWAY_RSA_PRIVATE_KEY_FILE": str(private_key_path),
            "SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY": "2",
        }
    )

    assert settings.bind_host == "127.0.0.1"
    assert settings.port == 4001
    assert settings.service_api_keys == ("service-a", "service-b")
    assert settings.max_concurrency == 2
    assert settings.route.alias == "deepseek-v4-flash"
    assert settings.route.upstream_model == "deepseek-v4-flash-0731"
    assert settings.route.provider is PingAnModelProvider.EAGW
    assert settings.route.rsa_private_key_hex
    assert "app-secret" not in repr(settings)


def test_settings_reject_missing_service_auth() -> None:
    with pytest.raises(ValueError, match="API_KEYS"):
        PingAnModelGatewaySettings.from_env(
            {
                "SOC_PINGAN_MODEL_GATEWAY_PROVIDER": "openai",
                "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_BASE_URL": ("https://model.example.internal/v1"),
                "SOC_PINGAN_MODEL_GATEWAY_ALLOWED_HOSTS": ("model.example.internal"),
                "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_API_KEY": "secret",
            }
        )
