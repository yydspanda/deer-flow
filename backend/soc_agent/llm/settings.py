"""Configuration and assembly for the bounded SOC LLM analyzer."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from soc_agent.contracts import SensitiveEvidenceMode
from soc_agent.llm.analyzer import JsonLLMAnalyzer, LLMChatClient
from soc_agent.llm.deerflow_client import DeerFlowLLMChatClient
from soc_agent.llm.role_verifier import JsonLLMRoleVerifier
from soc_agent.pipeline.analyzer import StubLLMAnalyzer
from soc_agent.pipeline.role_verification import DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE
from soc_agent.protocols import LLMAnalyzer, RoleAdjudicationVerifier


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
    json_mode_enabled: bool = False
    attach_tracing: bool = True
    max_concurrency: int = 1
    requests_per_minute: int = 0
    admission_timeout_seconds: float = 5.0
    call_timeout_seconds: float = 180.0
    output_retry_attempts: int = 1
    output_fallback_model_name: str | None = None
    sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.REDACT
    role_verifier_enabled: bool = False
    role_verifier_model_name: str | None = None
    role_verifier_minimum_confidence: float = DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE

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
        output_fallback_model_name = values.get("SOC_LLM_OUTPUT_FALLBACK_MODEL")
        role_verifier_model_name = values.get("SOC_ROLE_VERIFIER_MODEL")
        try:
            sensitive_evidence_mode = SensitiveEvidenceMode(
                values.get(
                    "SOC_LLM_SENSITIVE_EVIDENCE_MODE",
                    SensitiveEvidenceMode.REDACT.value,
                )
                .strip()
                .lower()
            )
        except ValueError as exc:
            raise ValueError("SOC_LLM_SENSITIVE_EVIDENCE_MODE must be 'redact' or 'full'") from exc
        return cls(
            mode=mode,
            model_name=model_name.strip() if model_name and model_name.strip() else None,
            thinking_enabled=_parse_bool(values.get("SOC_LLM_THINKING_ENABLED", "false"), name="SOC_LLM_THINKING_ENABLED"),
            json_mode_enabled=_parse_bool(
                values.get("SOC_LLM_JSON_MODE_ENABLED", "false"),
                name="SOC_LLM_JSON_MODE_ENABLED",
            ),
            attach_tracing=_parse_bool(values.get("SOC_LLM_ATTACH_TRACING", "true"), name="SOC_LLM_ATTACH_TRACING"),
            max_concurrency=_parse_int(
                values.get("SOC_LLM_MAX_CONCURRENCY", "1"),
                name="SOC_LLM_MAX_CONCURRENCY",
                minimum=1,
            ),
            requests_per_minute=_parse_int(
                values.get("SOC_LLM_REQUESTS_PER_MINUTE", "0"),
                name="SOC_LLM_REQUESTS_PER_MINUTE",
                minimum=0,
            ),
            admission_timeout_seconds=_parse_float(
                values.get("SOC_LLM_ADMISSION_TIMEOUT_SECONDS", "5"),
                name="SOC_LLM_ADMISSION_TIMEOUT_SECONDS",
                minimum=0.0,
            ),
            call_timeout_seconds=_parse_float(
                values.get("SOC_LLM_CALL_TIMEOUT_SECONDS", "180"),
                name="SOC_LLM_CALL_TIMEOUT_SECONDS",
                minimum=0.001,
            ),
            output_retry_attempts=_parse_int(
                values.get("SOC_LLM_OUTPUT_RETRY_ATTEMPTS", "1"),
                name="SOC_LLM_OUTPUT_RETRY_ATTEMPTS",
                minimum=0,
                maximum=1,
            ),
            output_fallback_model_name=(output_fallback_model_name.strip() if output_fallback_model_name and output_fallback_model_name.strip() else None),
            sensitive_evidence_mode=sensitive_evidence_mode,
            role_verifier_enabled=_parse_bool(
                values.get("SOC_ROLE_VERIFIER_ENABLED", "false"),
                name="SOC_ROLE_VERIFIER_ENABLED",
            ),
            role_verifier_model_name=(role_verifier_model_name.strip() if role_verifier_model_name and role_verifier_model_name.strip() else None),
            role_verifier_minimum_confidence=_parse_float(
                values.get(
                    "SOC_ROLE_VERIFIER_MIN_CONFIDENCE",
                    str(DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE),
                ),
                name="SOC_ROLE_VERIFIER_MIN_CONFIDENCE",
                minimum=0.0,
                maximum=1.0,
            ),
        )

    def with_overrides(
        self,
        *,
        mode: str | SocAnalyzerMode | None = None,
        model_name: str | None = None,
        thinking_enabled: bool | None = None,
        json_mode_enabled: bool | None = None,
        role_verifier_enabled: bool | None = None,
        role_verifier_model_name: str | None = None,
    ) -> SocLLMSettings:
        resolved_mode = SocAnalyzerMode(mode) if mode is not None else self.mode
        resolved_model = model_name.strip() if model_name and model_name.strip() else self.model_name
        resolved_role_verifier_model = role_verifier_model_name.strip() if role_verifier_model_name and role_verifier_model_name.strip() else self.role_verifier_model_name
        return replace(
            self,
            mode=resolved_mode,
            model_name=resolved_model,
            thinking_enabled=(thinking_enabled if thinking_enabled is not None else self.thinking_enabled),
            json_mode_enabled=(json_mode_enabled if json_mode_enabled is not None else self.json_mode_enabled),
            role_verifier_enabled=(role_verifier_enabled if role_verifier_enabled is not None else self.role_verifier_enabled),
            role_verifier_model_name=resolved_role_verifier_model,
        )


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

    config = app_config or get_app_config()
    chat_client, model_name = build_configured_chat_client(
        settings=resolved,
        app_config=config,
        client=client,
    )
    return JsonLLMAnalyzer(
        client=chat_client,
        model_name=model_name,
        output_retry_attempts=resolved.output_retry_attempts,
        output_fallback_model_name=(
            resolve_soc_model_name(
                resolved.output_fallback_model_name,
                app_config=config,
            )
            if resolved.output_fallback_model_name
            else None
        ),
    )


def build_configured_analysis_nodes(
    *,
    settings: SocLLMSettings | None = None,
    app_config: AppConfig | None = None,
    client: LLMChatClient | None = None,
) -> tuple[LLMAnalyzer, RoleAdjudicationVerifier | None]:
    """Build primary analyzer and optional verifier with one shared client."""

    resolved = settings or SocLLMSettings.from_env()
    if resolved.mode is SocAnalyzerMode.STUB:
        if resolved.role_verifier_enabled:
            raise ValueError("SOC_ROLE_VERIFIER_ENABLED=true requires SOC_ANALYZER_MODE=llm")
        return StubLLMAnalyzer(), None

    config = app_config or get_app_config()
    chat_client, model_name = build_configured_chat_client(
        settings=resolved,
        app_config=config,
        client=client,
    )
    analyzer = JsonLLMAnalyzer(
        client=chat_client,
        model_name=model_name,
        output_retry_attempts=resolved.output_retry_attempts,
        output_fallback_model_name=(
            resolve_soc_model_name(
                resolved.output_fallback_model_name,
                app_config=config,
            )
            if resolved.output_fallback_model_name
            else None
        ),
    )
    if not resolved.role_verifier_enabled:
        return analyzer, None
    verifier_model_name = resolve_soc_model_name(
        resolved.role_verifier_model_name or model_name,
        app_config=config,
    )
    return (
        analyzer,
        JsonLLMRoleVerifier(
            client=chat_client,
            model_name=verifier_model_name,
            minimum_confidence=resolved.role_verifier_minimum_confidence,
            output_retry_attempts=resolved.output_retry_attempts,
        ),
    )


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
            json_mode_enabled=resolved.json_mode_enabled,
            attach_tracing=resolved.attach_tracing,
            max_concurrency=resolved.max_concurrency,
            requests_per_minute=resolved.requests_per_minute,
            acquire_timeout_seconds=resolved.admission_timeout_seconds,
            call_timeout_seconds=resolved.call_timeout_seconds,
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
    role_verifier_model_name = None
    output_fallback_model_name = None
    if resolved.output_fallback_model_name and resolved.mode is SocAnalyzerMode.LLM:
        output_fallback_model_name = resolve_soc_model_name(
            resolved.output_fallback_model_name,
            app_config=config,
        )
    if resolved.role_verifier_enabled and resolved.mode is SocAnalyzerMode.LLM:
        role_verifier_model_name = resolve_soc_model_name(
            resolved.role_verifier_model_name or model_name,
            app_config=config,
        )
    return {
        "schema_version": "soc.llm_runtime_status.v3",
        "mode": resolved.mode.value,
        "requested_model_name": resolved.model_name,
        "resolved_model_name": model_name,
        "configured_model_names": [model.name for model in config.models],
        "thinking_enabled": resolved.thinking_enabled,
        "json_mode_enabled": resolved.json_mode_enabled,
        "attach_tracing": resolved.attach_tracing,
        "max_concurrency": resolved.max_concurrency,
        "requests_per_minute": resolved.requests_per_minute,
        "admission_timeout_seconds": resolved.admission_timeout_seconds,
        "call_timeout_seconds": resolved.call_timeout_seconds,
        "output_retry_attempts": resolved.output_retry_attempts,
        "output_fallback_requested_model_name": resolved.output_fallback_model_name,
        "output_fallback_resolved_model_name": output_fallback_model_name,
        "sensitive_evidence_mode": resolved.sensitive_evidence_mode.value,
        "role_verifier_enabled": resolved.role_verifier_enabled,
        "role_verifier_requested_model_name": resolved.role_verifier_model_name,
        "role_verifier_resolved_model_name": role_verifier_model_name,
        "role_verifier_minimum_confidence": resolved.role_verifier_minimum_confidence,
        "secrets_included": False,
    }


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _parse_int(
    value: str,
    *,
    name: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not isfinite(parsed) or parsed < minimum or (maximum is not None and parsed > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be a finite number >= {minimum}{suffix}")
    return parsed


def _parse_float(
    value: str,
    *,
    name: str,
    minimum: float,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not isfinite(parsed) or parsed < minimum or (maximum is not None and parsed > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be a finite number >= {minimum}{suffix}")
    return parsed
