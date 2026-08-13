"""DeerFlow model-factory adapter for bounded SOC LLM calls."""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from threading import Lock
from typing import Any

from langchain.chat_models import BaseChatModel

from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from soc_agent.llm.admission import SocLLMAdmissionController
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.usage import resolve_chat_usage

_SAFE_RESPONSE_METADATA_KEYS = (
    "finish_reason",
    "model_name",
    "model_provider",
    "stop_reason",
    "system_fingerprint",
)


class DeerFlowLLMChatClient:
    """Invoke models registered in DeerFlow's ``config.yaml``.

    Model instances are cached per configured name. The adapter deliberately
    returns only bounded usage and provider metadata; raw provider responses,
    request headers, and credentials never enter SOC run records.
    """

    def __init__(
        self,
        *,
        app_config: AppConfig,
        thinking_enabled: bool = False,
        attach_tracing: bool = True,
        run_name: str = "soc_runtime_analysis",
        model_factory: Callable[..., BaseChatModel] = create_chat_model,
        admission_controller: SocLLMAdmissionController | None = None,
        max_concurrency: int = 1,
        requests_per_minute: int = 0,
        acquire_timeout_seconds: float = 5.0,
        call_timeout_seconds: float = 180.0,
    ) -> None:
        if not isfinite(call_timeout_seconds) or call_timeout_seconds <= 0:
            raise ValueError("call_timeout_seconds must be a finite positive number")
        self._app_config = app_config
        self._thinking_enabled = thinking_enabled
        self._attach_tracing = attach_tracing
        self._run_name = run_name
        self._model_factory = model_factory
        self._admission = admission_controller or SocLLMAdmissionController(
            max_concurrency=max_concurrency,
            requests_per_minute=requests_per_minute,
            acquire_timeout_seconds=acquire_timeout_seconds,
        )
        self._call_timeout_seconds = call_timeout_seconds
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="soc-llm-call",
        )
        self._models: dict[str, BaseChatModel] = {}
        self._models_lock = Lock()

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        client_started = time.monotonic()
        model = self._get_model(model_name)
        admission_started = time.monotonic()
        with self._admission.admit():
            admission_wait_duration_ms = round(
                (time.monotonic() - admission_started) * 1000,
                3,
            )
            provider_started = time.monotonic()
            future = self._executor.submit(
                model.invoke,
                [dict(message) for message in messages],
                config={
                    "run_name": self._run_name,
                    "tags": ["soc-agent", "soc-runtime", "bounded-analysis"],
                    "metadata": {"soc_model_name": model_name},
                },
            )
            try:
                response = future.result(timeout=self._call_timeout_seconds)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                timeout_error = TimeoutError(f"SOC LLM call exceeded {self._call_timeout_seconds:g} seconds")
                _attach_client_failure_timing(
                    timeout_error,
                    admission_wait_duration_ms=admission_wait_duration_ms,
                    provider_duration_ms=round(
                        (time.monotonic() - provider_started) * 1000,
                        3,
                    ),
                    client_total_duration_ms=round(
                        (time.monotonic() - client_started) * 1000,
                        3,
                    ),
                )
                raise timeout_error from exc
            except Exception as exc:
                _attach_client_failure_timing(
                    exc,
                    admission_wait_duration_ms=admission_wait_duration_ms,
                    provider_duration_ms=round(
                        (time.monotonic() - provider_started) * 1000,
                        3,
                    ),
                    client_total_duration_ms=round(
                        (time.monotonic() - client_started) * 1000,
                        3,
                    ),
                )
                raise
            provider_duration_ms = round(
                (time.monotonic() - provider_started) * 1000,
                3,
            )
        response_metadata = _mapping(getattr(response, "response_metadata", None))
        usage, usage_measurement = resolve_chat_usage(
            messages=messages,
            response_content=getattr(response, "content", response),
            provider_usage=_response_usage(response, response_metadata),
        )
        bounded_metadata = {key: response_metadata[key] for key in _SAFE_RESPONSE_METADATA_KEYS if key in response_metadata}
        bounded_metadata.update(_response_shape_metadata(response))
        bounded_metadata.update(
            {
                "usage_measurement": usage_measurement,
                "admission_wait_duration_ms": admission_wait_duration_ms,
                "provider_duration_ms": provider_duration_ms,
                "client_total_duration_ms": round(
                    (time.monotonic() - client_started) * 1000,
                    3,
                ),
            }
        )
        return LLMChatResponse(
            content=getattr(response, "content", response),
            model_name=_response_model_name(response_metadata) or model_name,
            usage=usage,
            metadata=bounded_metadata,
        )

    def _get_model(self, model_name: str) -> BaseChatModel:
        with self._models_lock:
            model = self._models.get(model_name)
            if model is None:
                model = self._model_factory(
                    name=model_name,
                    thinking_enabled=self._thinking_enabled,
                    app_config=self._app_config,
                    attach_tracing=self._attach_tracing,
                )
                self._models[model_name] = model
            return model


def _response_usage(response: Any, response_metadata: Mapping[str, Any]) -> dict[str, Any]:
    usage = _mapping(getattr(response, "usage_metadata", None))
    if usage:
        return dict(usage)
    for attribute in ("token_usage", "usage"):
        candidate = _mapping(getattr(response, attribute, None))
        if candidate:
            return dict(candidate)
    for key in ("token_usage", "usage", "usage_metadata", "usageMetadata"):
        candidate = _mapping(response_metadata.get(key))
        if candidate:
            return dict(candidate)
    return {}


def _response_model_name(response_metadata: Mapping[str, Any]) -> str | None:
    for key in ("model_name", "model"):
        value = response_metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _response_shape_metadata(response: Any) -> dict[str, Any]:
    """Record response shape only, never model text or provider-private values."""

    content = getattr(response, "content", response)
    visible_chars = 0
    content_block_count = 0
    if isinstance(content, str):
        visible_chars = len(content)
        content_kind = "text"
    elif isinstance(content, list):
        content_kind = "blocks"
        content_block_count = len(content)
        for block in content:
            if isinstance(block, str):
                visible_chars += len(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                visible_chars += len(block["text"])
    else:
        content_kind = type(content).__name__

    additional_kwargs = _mapping(getattr(response, "additional_kwargs", None))
    reasoning = additional_kwargs.get("reasoning_content")
    if not isinstance(reasoning, str):
        reasoning = additional_kwargs.get("reasoning")
    tool_calls = getattr(response, "tool_calls", None)
    if not isinstance(tool_calls, list):
        tool_calls = additional_kwargs.get("tool_calls")
    return {
        "response_content_kind": content_kind,
        "response_content_block_count": content_block_count,
        "response_visible_text_chars": visible_chars,
        "response_visible_text_empty": visible_chars == 0,
        "response_reasoning_present": isinstance(reasoning, str) and bool(reasoning),
        "response_reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
        "response_tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _attach_client_failure_timing(
    error: Exception,
    *,
    admission_wait_duration_ms: float,
    provider_duration_ms: float,
    client_total_duration_ms: float,
) -> None:
    error.soc_llm_client_measurement = {  # type: ignore[attr-defined]
        "usage_measurement": {
            "status": "unavailable",
            "method": None,
            "estimated": False,
        },
        "admission_wait_duration_ms": admission_wait_duration_ms,
        "provider_duration_ms": provider_duration_ms,
        "client_total_duration_ms": client_total_duration_ms,
    }
