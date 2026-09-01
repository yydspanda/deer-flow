"""Safe connectivity smoke for the project-owned PingAn model gateway."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SMOKE_PROMPT = "Reply with exactly the ASCII text OK."


class PingAnModelGatewaySmokeReport(BaseModel):
    """Credential-free evidence for one OpenAI-compatible chat completion."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_model_gateway_smoke.v1"] = "soc.pingan_model_gateway_smoke.v1"
    outcome: Literal[
        "passed",
        "invalid_configuration",
        "authentication_failed",
        "timeout",
        "provider_unavailable",
        "invalid_response",
    ]
    passed: bool
    endpoint_scope: Literal["loopback"] = "loopback"
    endpoint_path: str
    model_requested: str
    model_returned: str | None = None
    duration_ms: int = Field(ge=0)
    http_status: int | None = None
    finish_reason: str | None = None
    content_present: bool = False
    content_length: int = Field(default=0, ge=0)
    content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    max_tokens_requested: int | None = Field(default=None, ge=1)
    thinking_requested: bool = False
    reasoning_effort_requested: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def run_pingan_model_gateway_smoke(
    environ: Mapping[str, str] | None = None,
    *,
    client: httpx.Client | None = None,
) -> PingAnModelGatewaySmokeReport:
    """Issue one fixed, non-business chat completion against the local gateway."""

    env = dict(os.environ if environ is None else environ)
    started = monotonic()
    endpoint_path = "/v1/chat/completions"
    model = env.get("PINGAN_MODEL_GATEWAY_MODEL", "").strip()
    max_tokens_requested: int | None = None
    thinking_requested = False
    reasoning_effort_requested: str | None = None
    try:
        endpoint_url, endpoint_path = _completion_endpoint(env.get("PINGAN_MODEL_GATEWAY_BASE_URL", ""))
        api_key = _required_non_placeholder(env, "PINGAN_MODEL_GATEWAY_API_KEY")
        model = _required_non_placeholder(env, "PINGAN_MODEL_GATEWAY_MODEL")
        timeout_seconds = _positive_float(
            env.get("PINGAN_MODEL_GATEWAY_SMOKE_TIMEOUT_SECONDS", "60"),
            name="PINGAN_MODEL_GATEWAY_SMOKE_TIMEOUT_SECONDS",
        )
        max_response_bytes = _positive_int(
            env.get("PINGAN_MODEL_GATEWAY_SMOKE_MAX_RESPONSE_BYTES", "1000000"),
            name="PINGAN_MODEL_GATEWAY_SMOKE_MAX_RESPONSE_BYTES",
        )
        thinking_requested = _boolean(
            env.get("PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED", "false"),
            name="PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED",
        )
        configured_reasoning_effort = env.get(
            "PINGAN_MODEL_GATEWAY_SMOKE_REASONING_EFFORT",
            "high",
        ).strip()
        if len(configured_reasoning_effort) > 32:
            raise ValueError("PINGAN_MODEL_GATEWAY_SMOKE_REASONING_EFFORT is too long")
        reasoning_effort_requested = configured_reasoning_effort if thinking_requested else None
        max_tokens_requested = _positive_int(
            env.get("PINGAN_MODEL_GATEWAY_SMOKE_MAX_TOKENS", "128"),
            name="PINGAN_MODEL_GATEWAY_SMOKE_MAX_TOKENS",
        )
        if max_tokens_requested > 4096:
            raise ValueError("PINGAN_MODEL_GATEWAY_SMOKE_MAX_TOKENS must not exceed 4096")
    except (TypeError, ValueError) as exc:
        return _failure_report(
            outcome="invalid_configuration",
            endpoint_path=endpoint_path,
            model=model or "<unconfigured>",
            started=started,
            error_type=exc.__class__.__name__,
            error_message="PingAn model gateway smoke configuration is invalid.",
            max_tokens_requested=max_tokens_requested,
            thinking_requested=thinking_requested,
            reasoning_effort_requested=reasoning_effort_requested,
        )

    chat_template_kwargs: dict[str, Any] = {
        "enable_thinking": thinking_requested,
    }
    if reasoning_effort_requested:
        chat_template_kwargs["reasoning_effort"] = reasoning_effort_requested
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _SMOKE_PROMPT}],
        "temperature": 0,
        "max_tokens": max_tokens_requested,
        "stream": False,
        "extra_body": {
            "chat_template_kwargs": chat_template_kwargs,
        },
    }
    owns_client = client is None
    transport = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = transport.post(
            endpoint_url,
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            timeout=timeout_seconds,
        )
        if response.status_code in {401, 403}:
            return _failure_report(
                outcome="authentication_failed",
                endpoint_path=endpoint_path,
                model=model,
                started=started,
                http_status=response.status_code,
                error_type="HTTPStatusError",
                error_message="PingAn model gateway rejected the configured credential.",
                max_tokens_requested=max_tokens_requested,
                thinking_requested=thinking_requested,
                reasoning_effort_requested=reasoning_effort_requested,
            )
        if not 200 <= response.status_code < 300:
            return _failure_report(
                outcome="provider_unavailable",
                endpoint_path=endpoint_path,
                model=model,
                started=started,
                http_status=response.status_code,
                error_type="HTTPStatusError",
                error_message="PingAn model gateway returned a non-success HTTP status.",
                max_tokens_requested=max_tokens_requested,
                thinking_requested=thinking_requested,
                reasoning_effort_requested=reasoning_effort_requested,
            )
        if len(response.content) > max_response_bytes:
            raise ValueError("model gateway response exceeded the configured size limit")
        body = response.json()
        parsed = _parse_completion(body)
    except httpx.TimeoutException as exc:
        return _failure_report(
            outcome="timeout",
            endpoint_path=endpoint_path,
            model=model,
            started=started,
            error_type=exc.__class__.__name__,
            error_message="PingAn model gateway chat completion timed out.",
            max_tokens_requested=max_tokens_requested,
            thinking_requested=thinking_requested,
            reasoning_effort_requested=reasoning_effort_requested,
        )
    except httpx.HTTPError as exc:
        return _failure_report(
            outcome="provider_unavailable",
            endpoint_path=endpoint_path,
            model=model,
            started=started,
            error_type=exc.__class__.__name__,
            error_message="PingAn model gateway chat completion could not be reached.",
            max_tokens_requested=max_tokens_requested,
            thinking_requested=thinking_requested,
            reasoning_effort_requested=reasoning_effort_requested,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _failure_report(
            outcome="invalid_response",
            endpoint_path=endpoint_path,
            model=model,
            started=started,
            http_status=response.status_code if "response" in locals() else None,
            error_type=exc.__class__.__name__,
            error_message="PingAn model gateway returned an invalid chat completion response.",
            max_tokens_requested=max_tokens_requested,
            thinking_requested=thinking_requested,
            reasoning_effort_requested=reasoning_effort_requested,
        )
    finally:
        if owns_client:
            transport.close()

    content = parsed["content"]
    usage = parsed["usage"]
    return PingAnModelGatewaySmokeReport(
        outcome="passed",
        passed=True,
        endpoint_path=endpoint_path,
        model_requested=model,
        model_returned=parsed["model"],
        duration_ms=_elapsed_ms(started),
        http_status=response.status_code,
        finish_reason=parsed["finish_reason"],
        content_present=True,
        content_length=len(content),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        max_tokens_requested=max_tokens_requested,
        thinking_requested=thinking_requested,
        reasoning_effort_requested=reasoning_effort_requested,
    )


def _completion_endpoint(base_url: str) -> tuple[str, str]:
    normalized = base_url.strip()
    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or hostname not in _LOOPBACK_HOSTS:
        raise ValueError("model gateway base URL must be an HTTP(S) loopback endpoint")
    if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("model gateway base URL cannot contain credentials, parameters, query, or fragment")
    base_path = parsed.path.rstrip("/")
    if base_path != "/v1":
        raise ValueError("model gateway base URL must end at the OpenAI-compatible /v1 boundary")
    endpoint_path = f"{base_path}/chat/completions"
    return f"{normalized.rstrip('/')}/chat/completions", endpoint_path


def _required_non_placeholder(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value or (value.startswith("<") and value.endswith(">")):
        raise ValueError(f"{name} is missing")
    return value


def _positive_float(raw: str, *, name: str) -> float:
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_int(raw: str, *, name: str) -> int:
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _boolean(raw: str, *, name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _parse_completion(body: Any) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("completion body must be an object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError("completion choices are missing")
    first = choices[0]
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("completion message is missing")
    content = _content_text(message.get("content"))
    if not content.strip():
        raise ValueError("completion content is empty")
    returned_model = body.get("model")
    finish_reason = first.get("finish_reason")
    usage = body.get("usage")
    return {
        "content": content,
        "model": str(returned_model)[:200] if returned_model is not None else None,
        "finish_reason": str(finish_reason)[:100] if finish_reason is not None else None,
        "usage": _token_usage(usage),
    }


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [str(item["text"]) for item in value if isinstance(item, Mapping) and isinstance(item.get("text"), str)]
        return "".join(parts)
    raise ValueError("completion content must be text")


def _token_usage(value: Any) -> dict[str, int | None]:
    usage = value if isinstance(value, Mapping) else {}
    return {name: item if isinstance(item := usage.get(name), int) and item >= 0 else None for name in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _failure_report(
    *,
    outcome: Literal[
        "invalid_configuration",
        "authentication_failed",
        "timeout",
        "provider_unavailable",
        "invalid_response",
    ],
    endpoint_path: str,
    model: str,
    started: float,
    error_type: str,
    error_message: str,
    http_status: int | None = None,
    max_tokens_requested: int | None = None,
    thinking_requested: bool = False,
    reasoning_effort_requested: str | None = None,
) -> PingAnModelGatewaySmokeReport:
    return PingAnModelGatewaySmokeReport(
        outcome=outcome,
        passed=False,
        endpoint_path=endpoint_path,
        model_requested=model,
        duration_ms=_elapsed_ms(started),
        http_status=http_status,
        max_tokens_requested=max_tokens_requested,
        thinking_requested=thinking_requested,
        reasoning_effort_requested=reasoning_effort_requested,
        error_type=error_type,
        error_message=error_message,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
