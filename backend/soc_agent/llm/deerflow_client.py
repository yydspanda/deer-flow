"""DeerFlow model-factory adapter for bounded SOC LLM calls."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from threading import Lock
from typing import Any

from langchain.chat_models import BaseChatModel

from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from soc_agent.llm.admission import SocLLMAdmissionController
from soc_agent.llm.analyzer import LLMChatResponse

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
    ) -> None:
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
        self._models: dict[str, BaseChatModel] = {}
        self._models_lock = Lock()

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        model = self._get_model(model_name)
        with self._admission.admit():
            response = model.invoke(
                [dict(message) for message in messages],
                config={
                    "run_name": self._run_name,
                    "tags": ["soc-agent", "soc-runtime", "bounded-analysis"],
                    "metadata": {"soc_model_name": model_name},
                },
            )
        response_metadata = _mapping(getattr(response, "response_metadata", None))
        return LLMChatResponse(
            content=getattr(response, "content", response),
            model_name=_response_model_name(response_metadata) or model_name,
            usage=_response_usage(response, response_metadata),
            metadata={key: response_metadata[key] for key in _SAFE_RESPONSE_METADATA_KEYS if key in response_metadata},
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
    for key in ("token_usage", "usage"):
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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
