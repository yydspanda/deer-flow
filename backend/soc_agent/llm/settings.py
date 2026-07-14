"""Configuration and assembly for the bounded SOC LLM analyzer."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from soc_agent.llm.analyzer import JsonLLMAnalyzer, LLMChatClient
from soc_agent.llm.deerflow_client import DeerFlowLLMChatClient
from soc_agent.pipeline.analyzer import StubLLMAnalyzer
from soc_agent.protocols import LLMAnalyzer


class SocAnalyzerMode(StrEnum):
    """Configured analyzer implementation for the fixed Runtime analysis node."""

    STUB = "stub"
    LLM = "llm"


@dataclass(frozen=True)
class SocLLMSettings:
    """Runtime-selectable SOC analyzer settings.

    The default remains deterministic for tests and replay. Production entry
    points can opt in with ``SOC_ANALYZER_MODE=llm`` and optionally select a
    registered DeerFlow model with ``SOC_LLM_MODEL``.
    """

    mode: SocAnalyzerMode = SocAnalyzerMode.STUB
    model_name: str | None = None
    thinking_enabled: bool = False
    attach_tracing: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SocLLMSettings:
        values = os.environ if environ is None else environ
        raw_mode = values.get("SOC_ANALYZER_MODE")
        if raw_mode is None and "SOC_LLM_ENABLED" in values:
            raw_mode = "llm" if _parse_bool(values["SOC_LLM_ENABLED"], name="SOC_LLM_ENABLED") else "stub"
        try:
            mode = SocAnalyzerMode((raw_mode or SocAnalyzerMode.STUB.value).strip().lower())
        except ValueError as exc:
            raise ValueError("SOC_ANALYZER_MODE must be 'stub' or 'llm'") from exc

        model_name = values.get("SOC_LLM_MODEL")
        return cls(
            mode=mode,
            model_name=model_name.strip() if model_name and model_name.strip() else None,
            thinking_enabled=_parse_bool(values.get("SOC_LLM_THINKING_ENABLED", "false"), name="SOC_LLM_THINKING_ENABLED"),
            attach_tracing=_parse_bool(values.get("SOC_LLM_ATTACH_TRACING", "true"), name="SOC_LLM_ATTACH_TRACING"),
        )

    def with_overrides(
        self,
        *,
        mode: str | SocAnalyzerMode | None = None,
        model_name: str | None = None,
    ) -> SocLLMSettings:
        resolved_mode = SocAnalyzerMode(mode) if mode is not None else self.mode
        resolved_model = model_name.strip() if model_name and model_name.strip() else self.model_name
        return replace(self, mode=resolved_mode, model_name=resolved_model)


def resolve_soc_model_name(
    requested_name: str | None,
    *,
    app_config: AppConfig | None = None,
) -> str:
    """Resolve one configured DeerFlow model name without provider fallbacks."""

    config = app_config or get_app_config()
    model_name = requested_name or (config.models[0].name if config.models else None)
    if not model_name:
        raise ValueError("SOC LLM analyzer requires at least one configured DeerFlow model")
    if config.get_model_config(model_name) is None:
        configured = ", ".join(model.name for model in config.models) or "<none>"
        raise ValueError(f"SOC LLM model {model_name!r} is not configured; available models: {configured}")
    return model_name


def build_configured_analyzer(
    *,
    settings: SocLLMSettings | None = None,
    app_config: AppConfig | None = None,
    client: LLMChatClient | None = None,
) -> LLMAnalyzer:
    """Build the analyzer selected for a Runtime entry point."""

    resolved = settings or SocLLMSettings.from_env()
    if resolved.mode is SocAnalyzerMode.STUB:
        return StubLLMAnalyzer()

    chat_client, model_name = build_configured_chat_client(
        settings=resolved,
        app_config=app_config,
        client=client,
    )
    return JsonLLMAnalyzer(client=chat_client, model_name=model_name)


def build_configured_chat_client(
    *,
    settings: SocLLMSettings | None = None,
    app_config: AppConfig | None = None,
    client: LLMChatClient | None = None,
) -> tuple[LLMChatClient, str]:
    """Build a DeerFlow client and resolve its registered model name."""

    resolved = settings or SocLLMSettings.from_env()
    config = app_config or get_app_config()
    model_name = resolve_soc_model_name(resolved.model_name, app_config=config)
    return (
        client
        or DeerFlowLLMChatClient(
            app_config=config,
            thinking_enabled=resolved.thinking_enabled,
            attach_tracing=resolved.attach_tracing,
        ),
        model_name,
    )


def configured_soc_llm_status(
    *,
    settings: SocLLMSettings | None = None,
    app_config: AppConfig | None = None,
) -> dict[str, object]:
    """Return a secret-free configuration status for CLI/operations checks."""

    resolved = settings or SocLLMSettings.from_env()
    config = app_config or get_app_config()
    model_name = resolve_soc_model_name(resolved.model_name, app_config=config) if resolved.mode is SocAnalyzerMode.LLM else resolved.model_name
    return {
        "schema_version": "soc.llm_runtime_status.v1",
        "mode": resolved.mode.value,
        "requested_model_name": resolved.model_name,
        "resolved_model_name": model_name,
        "configured_model_names": [model.name for model in config.models],
        "thinking_enabled": resolved.thinking_enabled,
        "attach_tracing": resolved.attach_tracing,
        "secrets_included": False,
    }


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")
