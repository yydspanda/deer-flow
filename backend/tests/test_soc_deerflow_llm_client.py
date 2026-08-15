from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from soc_agent.cli import main
from soc_agent.contracts import SensitiveEvidenceMode
from soc_agent.llm import (
    DeerFlowLLMChatClient,
    JsonLLMAnalyzer,
    JsonLLMRoleVerifier,
    LLMChatResponse,
    SocAnalyzerMode,
    SocLLMAdmissionController,
    SocLLMAdmissionError,
    SocLLMSettings,
    build_configured_analysis_nodes,
    build_configured_analyzer,
    configured_soc_llm_status,
    resolve_soc_model_name,
)
from soc_agent.pipeline.analyzer import StubLLMAnalyzer

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []
        self.request_kwargs: list[dict[str, Any]] = []

    def invoke(self, messages, *, config, **kwargs):
        self.calls.append((messages, config))
        self.request_kwargs.append(kwargs)
        return SimpleNamespace(
            content='{"verdict":"unknown"}',
            usage_metadata={"input_tokens": 11, "output_tokens": 7},
            response_metadata={
                "finish_reason": "stop",
                "model_name": "provider-model-id",
                "headers": {"authorization": "must-not-be-recorded"},
                "token_usage": {"prompt_tokens": 999},
            },
        )


class _NoUsageFakeModel:
    def invoke(self, _messages, *, config):
        assert config["run_name"] == "soc_runtime_analysis"
        return SimpleNamespace(
            content="内网模型没有返回 usage",
            response_metadata={"finish_reason": "stop"},
        )


class _PartialUsageFakeModel:
    def invoke(self, _messages, *, config):
        assert config["run_name"] == "soc_runtime_analysis"
        return SimpleNamespace(
            content="partial usage",
            usage_metadata={"input_tokens": 12},
            response_metadata={"finish_reason": "stop"},
        )


class _EmptyContentReasoningFakeModel:
    def invoke(self, _messages, *, config):
        assert config["run_name"] == "soc_runtime_analysis"
        return SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": "internal reasoning"},
            tool_calls=[{"name": "unexpected_tool"}],
            response_metadata={"finish_reason": "length"},
        )


class _FakeConfig:
    def __init__(self, *names: str) -> None:
        self.models = [SimpleNamespace(name=name) for name in names]

    def get_model_config(self, name: str):
        return SimpleNamespace(name=name) if name in {model.name for model in self.models} else None


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list[Mapping[str, str]], str]] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        self.calls.append((list(messages), model_name))
        return LLMChatResponse(content="{}", model_name=model_name)


def test_deerflow_client_reuses_model_and_bounds_metadata() -> None:
    model = _FakeModel()
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return model

    config = _FakeConfig("deepseek-v4-pro")
    client = DeerFlowLLMChatClient(
        app_config=config,  # type: ignore[arg-type]
        thinking_enabled=False,
        attach_tracing=True,
        model_factory=factory,
    )

    first = client.complete([{"role": "user", "content": "one"}], model_name="deepseek-v4-pro")
    second = client.complete([{"role": "user", "content": "two"}], model_name="deepseek-v4-pro")

    assert len(factory_calls) == 1
    assert factory_calls[0]["name"] == "deepseek-v4-pro"
    assert factory_calls[0]["thinking_enabled"] is False
    assert len(model.calls) == 2
    assert model.calls[0][1]["run_name"] == "soc_runtime_analysis"
    assert first.model_name == "provider-model-id"
    assert first.usage == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    assert first.metadata["finish_reason"] == "stop"
    assert first.metadata["model_name"] == "provider-model-id"
    assert first.metadata["requested_model_name"] == "deepseek-v4-pro"
    assert first.metadata["thinking_enabled_requested"] is False
    assert first.metadata["json_mode_requested"] is False
    assert first.metadata["usage_measurement"]["status"] == "reported"
    assert first.metadata["provider_duration_ms"] >= 0
    assert first.metadata["client_total_duration_ms"] >= 0
    assert "headers" not in first.metadata
    assert second.model_name == "provider-model-id"


def test_deerflow_client_requests_json_mode_only_when_enabled() -> None:
    model = _FakeModel()
    client = DeerFlowLLMChatClient(
        app_config=_FakeConfig("deepseek-v4-flash"),  # type: ignore[arg-type]
        json_mode_enabled=True,
        model_factory=lambda **_kwargs: model,
    )

    response = client.complete(
        [{"role": "user", "content": "Return JSON"}],
        model_name="deepseek-v4-flash",
    )

    assert model.request_kwargs == [{"response_format": {"type": "json_object"}}]
    assert response.metadata["json_mode_requested"] is True


def test_deerflow_client_estimates_missing_intranet_usage() -> None:
    client = DeerFlowLLMChatClient(
        app_config=_FakeConfig("internal-model"),  # type: ignore[arg-type]
        model_factory=lambda **_kwargs: _NoUsageFakeModel(),
    )

    response = client.complete(
        [{"role": "user", "content": "分析这条告警"}],
        model_name="internal-model",
    )

    assert response.usage["input_tokens"] > 0
    assert response.usage["output_tokens"] > 0
    assert response.usage["total_tokens"] == (response.usage["input_tokens"] + response.usage["output_tokens"])
    assert response.metadata["usage_measurement"]["status"] == "estimated"
    assert response.metadata["usage_measurement"]["estimated"] is True


def test_deerflow_client_marks_partial_provider_usage_as_mixed() -> None:
    client = DeerFlowLLMChatClient(
        app_config=_FakeConfig("internal-model"),  # type: ignore[arg-type]
        model_factory=lambda **_kwargs: _PartialUsageFakeModel(),
    )

    response = client.complete(
        [{"role": "user", "content": "alert"}],
        model_name="internal-model",
    )

    assert response.usage["input_tokens"] == 12
    assert response.usage["output_tokens"] > 0
    assert response.metadata["usage_measurement"]["status"] == "mixed"
    assert response.metadata["usage_measurement"]["estimated_fields"] == ["output_tokens"]


def test_deerflow_client_records_empty_response_shape_without_retaining_text() -> None:
    client = DeerFlowLLMChatClient(
        app_config=_FakeConfig("internal-model"),  # type: ignore[arg-type]
        thinking_enabled=True,
        model_factory=lambda **_kwargs: _EmptyContentReasoningFakeModel(),
    )

    response = client.complete(
        [{"role": "user", "content": "alert"}],
        model_name="internal-model",
    )

    assert response.content == ""
    assert response.metadata["finish_reason"] == "length"
    assert response.metadata["response_content_kind"] == "text"
    assert response.metadata["response_visible_text_chars"] == 0
    assert response.metadata["response_visible_text_empty"] is True
    assert response.metadata["response_reasoning_present"] is True
    assert response.metadata["response_reasoning_chars"] == len("internal reasoning")
    assert response.metadata["thinking_enabled_requested"] is True
    assert response.metadata["response_tool_call_count"] == 1
    assert "internal reasoning" not in json.dumps(response.metadata)


def test_soc_llm_settings_are_explicit_and_validate_values() -> None:
    assert SocLLMSettings.from_env({}).mode is SocAnalyzerMode.STUB
    settings = SocLLMSettings.from_env(
        {
            "SOC_ANALYZER_MODE": "llm",
            "SOC_LLM_MODEL": "deepseek-v4-pro",
            "SOC_LLM_THINKING_ENABLED": "false",
            "SOC_LLM_JSON_MODE_ENABLED": "true",
            "SOC_LLM_ATTACH_TRACING": "true",
            "SOC_LLM_MAX_CONCURRENCY": "3",
            "SOC_LLM_REQUESTS_PER_MINUTE": "20",
            "SOC_LLM_ADMISSION_TIMEOUT_SECONDS": "0.25",
            "SOC_LLM_CALL_TIMEOUT_SECONDS": "30",
            "SOC_LLM_OUTPUT_RETRY_ATTEMPTS": "0",
            "SOC_LLM_OUTPUT_FALLBACK_MODEL": "deepseek-v4-pro",
            "SOC_LLM_SENSITIVE_EVIDENCE_MODE": "full",
            "SOC_ROLE_VERIFIER_ENABLED": "true",
            "SOC_ROLE_VERIFIER_MODEL": "deepseek-v4-pro",
            "SOC_ROLE_VERIFIER_MIN_CONFIDENCE": "0.7",
        }
    )
    assert settings.mode is SocAnalyzerMode.LLM
    assert settings.model_name == "deepseek-v4-pro"
    assert settings.json_mode_enabled is True
    assert settings.max_concurrency == 3
    assert settings.requests_per_minute == 20
    assert settings.admission_timeout_seconds == 0.25
    assert settings.call_timeout_seconds == 30
    assert settings.output_retry_attempts == 0
    assert settings.output_fallback_model_name == "deepseek-v4-pro"
    assert settings.sensitive_evidence_mode is SensitiveEvidenceMode.FULL
    assert settings.role_verifier_enabled is True
    assert settings.role_verifier_model_name == "deepseek-v4-pro"
    assert settings.role_verifier_minimum_confidence == 0.7

    overridden = settings.with_overrides(
        thinking_enabled=True,
        role_verifier_enabled=True,
        role_verifier_model_name="globalai-deepseek-v4-pro",
    )
    assert overridden.thinking_enabled is True
    assert overridden.role_verifier_enabled is True
    assert overridden.role_verifier_model_name == "globalai-deepseek-v4-pro"

    with pytest.raises(ValueError, match="SOC_ANALYZER_MODE"):
        SocLLMSettings.from_env({"SOC_ANALYZER_MODE": "automatic"})
    with pytest.raises(ValueError, match="SOC_LLM_THINKING_ENABLED"):
        SocLLMSettings.from_env({"SOC_LLM_THINKING_ENABLED": "sometimes"})
    with pytest.raises(ValueError, match="SOC_LLM_JSON_MODE_ENABLED"):
        SocLLMSettings.from_env({"SOC_LLM_JSON_MODE_ENABLED": "sometimes"})
    with pytest.raises(ValueError, match="SOC_LLM_MAX_CONCURRENCY"):
        SocLLMSettings.from_env({"SOC_LLM_MAX_CONCURRENCY": "0"})
    with pytest.raises(ValueError, match="SOC_LLM_ADMISSION_TIMEOUT_SECONDS"):
        SocLLMSettings.from_env({"SOC_LLM_ADMISSION_TIMEOUT_SECONDS": "nan"})
    with pytest.raises(ValueError, match="SOC_LLM_CALL_TIMEOUT_SECONDS"):
        SocLLMSettings.from_env({"SOC_LLM_CALL_TIMEOUT_SECONDS": "0"})
    with pytest.raises(ValueError, match="SOC_LLM_SENSITIVE_EVIDENCE_MODE"):
        SocLLMSettings.from_env({"SOC_LLM_SENSITIVE_EVIDENCE_MODE": "unsafe"})
    with pytest.raises(ValueError, match="SOC_LLM_OUTPUT_RETRY_ATTEMPTS"):
        SocLLMSettings.from_env({"SOC_LLM_OUTPUT_RETRY_ATTEMPTS": "2"})
    with pytest.raises(ValueError, match="SOC_ROLE_VERIFIER_MIN_CONFIDENCE"):
        SocLLMSettings.from_env({"SOC_ROLE_VERIFIER_MIN_CONFIDENCE": "1.1"})


def test_deerflow_client_enforces_model_call_timeout() -> None:
    class SlowModel(_FakeModel):
        def invoke(self, messages, *, config):
            time.sleep(0.05)
            return super().invoke(messages, config=config)

    client = DeerFlowLLMChatClient(
        app_config=_FakeConfig("deepseek-v4-pro"),  # type: ignore[arg-type]
        model_factory=lambda **_: SlowModel(),
        call_timeout_seconds=0.001,
    )

    with pytest.raises(TimeoutError, match="SOC LLM call exceeded"):
        client.complete([{"role": "user", "content": "slow"}], model_name="deepseek-v4-pro")


def test_llm_admission_controller_enforces_concurrency_and_rate_budget() -> None:
    concurrency = SocLLMAdmissionController(max_concurrency=1, acquire_timeout_seconds=0)
    with concurrency.admit():
        with pytest.raises(SocLLMAdmissionError, match="concurrency"):
            with concurrency.admit():
                pass

    rate = SocLLMAdmissionController(requests_per_minute=1)
    with rate.admit():
        pass
    with pytest.raises(SocLLMAdmissionError, match="requests-per-minute"):
        with rate.admit():
            pass


def test_model_resolution_has_no_unknown_provider_fallback() -> None:
    config = _FakeConfig("deepseek-v4-flash", "deepseek-v4-pro")
    assert resolve_soc_model_name(None, app_config=config) == "deepseek-v4-flash"  # type: ignore[arg-type]
    assert resolve_soc_model_name("deepseek-v4-pro", app_config=config) == "deepseek-v4-pro"  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not configured"):
        resolve_soc_model_name("missing", app_config=config)  # type: ignore[arg-type]


def test_configured_analyzer_keeps_stub_default_and_builds_live_analyzer() -> None:
    config = _FakeConfig("deepseek-v4-pro")
    assert isinstance(
        build_configured_analyzer(
            settings=SocLLMSettings(mode=SocAnalyzerMode.STUB),
            app_config=config,  # type: ignore[arg-type]
        ),
        StubLLMAnalyzer,
    )

    client = _RecordingClient()
    analyzer = build_configured_analyzer(
        settings=SocLLMSettings(mode=SocAnalyzerMode.LLM, model_name="deepseek-v4-pro"),
        app_config=config,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
    )
    assert isinstance(analyzer, JsonLLMAnalyzer)
    assert analyzer.model_name == "deepseek-v4-pro"


def test_configured_analysis_nodes_share_client_and_resolve_optional_verifier() -> None:
    config = _FakeConfig("deepseek-v4-flash", "deepseek-v4-pro")
    client = _RecordingClient()
    analyzer, verifier = build_configured_analysis_nodes(
        settings=SocLLMSettings(
            mode=SocAnalyzerMode.LLM,
            model_name="deepseek-v4-flash",
            role_verifier_enabled=True,
            role_verifier_model_name="deepseek-v4-pro",
            output_fallback_model_name="deepseek-v4-pro",
            role_verifier_minimum_confidence=0.72,
        ),
        app_config=config,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
    )

    assert isinstance(analyzer, JsonLLMAnalyzer)
    assert isinstance(verifier, JsonLLMRoleVerifier)
    assert analyzer.model_name == "deepseek-v4-flash"
    assert analyzer.output_fallback_model_name == "deepseek-v4-pro"
    assert verifier.model_name == "deepseek-v4-pro"
    assert verifier.minimum_confidence == 0.72
    assert analyzer.output_retry_attempts == 1
    assert verifier.output_retry_attempts == 1

    with pytest.raises(ValueError, match="requires SOC_ANALYZER_MODE=llm"):
        build_configured_analysis_nodes(
            settings=SocLLMSettings(role_verifier_enabled=True),
            app_config=config,  # type: ignore[arg-type]
        )


def test_secret_free_status_lists_configured_models() -> None:
    config = _FakeConfig("deepseek-v4-flash", "deepseek-v4-pro")
    status = configured_soc_llm_status(
        settings=SocLLMSettings(
            mode=SocAnalyzerMode.LLM,
            model_name="deepseek-v4-flash",
            role_verifier_enabled=True,
            role_verifier_model_name="deepseek-v4-pro",
            output_fallback_model_name="deepseek-v4-pro",
        ),
        app_config=config,  # type: ignore[arg-type]
    )

    assert status["schema_version"] == "soc.llm_runtime_status.v3"
    assert status["resolved_model_name"] == "deepseek-v4-flash"
    assert status["configured_model_names"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert status["secrets_included"] is False
    assert status["max_concurrency"] == 1
    assert status["call_timeout_seconds"] == 180.0
    assert status["output_retry_attempts"] == 1
    assert status["output_fallback_resolved_model_name"] == "deepseek-v4-pro"
    assert status["role_verifier_enabled"] is True
    assert status["role_verifier_resolved_model_name"] == "deepseek-v4-pro"
    assert status["json_mode_enabled"] is False
    assert "api_key" not in status


def test_cli_analyze_passes_explicit_model_selection_to_runtime(monkeypatch, capsys) -> None:
    captured: list[SocLLMSettings] = []

    def fake_build(*, settings):
        captured.append(settings)
        return StubLLMAnalyzer(), None

    monkeypatch.setattr(
        "soc_agent.application.analysis.build_configured_analysis_nodes",
        fake_build,
    )

    exit_code = main(
        [
            "analyze",
            str(SAMPLES / "malicious_ioc.json"),
            "--analyzer-mode",
            "llm",
            "--model-name",
            "deepseek-v4-pro",
        ]
    )

    assert exit_code == 0
    assert captured[0].mode is SocAnalyzerMode.LLM
    assert captured[0].model_name == "deepseek-v4-pro"
    assert json.loads(capsys.readouterr().out)["model_name"] == "stub"
