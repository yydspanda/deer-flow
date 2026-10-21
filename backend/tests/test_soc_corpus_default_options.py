"""New corpus runs default to semantic review without enterprise policy."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusQuickCommand, CorpusRound, CorpusRoundCreateCommand
from soc_agent.llm import SocLLMSettings

EXPECTED = {
    "normalization_review_mode": "apply",
    "refresh_normalization": False,
    "tenant_policy_enabled": False,
    "tenant_policy_advisor_enabled": False,
    "tenant_policy_signal_providers_enabled": False,
}


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_new_batch_commands_default_to_semantic_review(batch):
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection={"batch": batch})
    quick = CorpusQuickCommand(batch=batch)
    assert command.options.model_dump() == EXPECTED
    assert quick.options.model_dump() == EXPECTED


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_explicit_batch_settings_are_preserved(batch):
    for options in (SocAnalysisExecutionOptions(), SocAnalysisExecutionOptions(**{**EXPECTED, "tenant_policy_enabled": True, "tenant_policy_advisor_enabled": True, "tenant_policy_signal_providers_enabled": True})):
        command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection={"batch": batch}, purpose="full_flow", options=options)
        assert command.options == options
        assert CorpusQuickCommand(batch=batch, options=options).options == options


def test_legacy_saved_round_and_generic_runtime_keep_their_original_default():
    round_ = CorpusRound(round_id="ROUND-old", experiment_id="EXP-test", selection={"batch": "learning"}, config_hash="a" * 64, config_snapshot={}, created_by="operator", created_at=datetime.now(UTC))
    assert round_.options == SocAnalysisExecutionOptions()
    assert round_.options.normalization_review_mode == "off"


def test_gateway_corpus_defaults_are_independent_of_runtime_policy_settings(tmp_path, monkeypatch):
    from app.gateway.routers import soc_corpus_workbench as gateway

    for name, value in {
        "SOC_NORMALIZATION_ASSIST_MODE": "off",
        "SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY": "true",
        "SOC_TENANT_DISPOSITION_POLICY_PATH": "/configured-policy",
        "SOC_TENANT_POLICY_SKILL_PATH": "/configured-skill",
        "SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH": "/configured-catalog",
    }.items():
        monkeypatch.setenv(name, value)
    runtime = SimpleNamespace(repository=Mock(), settings=SocLLMSettings(), database_file=tmp_path / "soc.db", tenant_policy="deterministic_and_llm", software_path_fast_policy=True)
    monkeypatch.setattr(gateway, "resolve_soc_dev_workbench_runtime", lambda *args, **kwargs: runtime)
    build_analysis = Mock()
    build_workbench = Mock()
    monkeypatch.setattr(gateway, "build_soc_analysis_service", build_analysis)
    monkeypatch.setattr(gateway, "SocCorpusWorkbenchService", build_workbench)
    monkeypatch.setattr(gateway, "SocMemoryPatternService", Mock())
    monkeypatch.setattr(gateway, "build_soc_memory_profile_registry", Mock())
    before = dict(os.environ)

    gateway.get_soc_corpus_workbench_service(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))

    controls = build_workbench.call_args.kwargs["run_controls"]
    assert controls.defaults.model_dump() == EXPECTED
    assert controls.tenant_policy_available
    assert controls.tenant_policy_advisor_available
    assert controls.tenant_policy_signal_providers_available
    assert build_analysis.call_args.kwargs["execution_options"].model_dump() == EXPECTED
    assert build_workbench.call_args.kwargs["normalization_review_mode"] == "apply"
    assert build_workbench.call_args.kwargs["tenant_policy"] == "disabled"
    assert build_workbench.call_args.kwargs["software_path_fast_policy"] is False
    assert dict(os.environ) == before


def test_corpus_application_without_controls_uses_same_defaults(monkeypatch):
    from soc_agent.application import corpus_experiments as application

    for name in ("CorpusRuntimeExecutor", "build_soc_memory_profile_registry", "SocCorpusExperimentService", "SocMemoryDraftJobService", "CorpusExperimentDispatcher"):
        monkeypatch.setattr(application, name, Mock())
    workbench = SimpleNamespace(run_controls=None, load_experiment_payload=Mock(), capacity=Mock())
    result = application.build_corpus_experiment_application(repository=Mock(), workbench=workbench, settings=SocLLMSettings())
    assert result.defaults.model_dump() == EXPECTED
    assert result.full_flow_defaults.model_dump() == EXPECTED


def test_remote_permission_fallback_agrees_with_new_defaults_and_preserves_saved_options():
    from soc_agent.demo.corpus_experiments import _require_saved_options

    store = SimpleNamespace(latest_run_options=Mock(return_value=None))
    defaults = SocAnalysisExecutionOptions(**EXPECTED)
    _require_saved_options(store, "EXP-test", "learning", defaults, None)
    store.latest_run_options.return_value = SocAnalysisExecutionOptions()
    with pytest.raises(PermissionError):
        _require_saved_options(store, "EXP-test", "learning", defaults, None)
    _require_saved_options(store, "EXP-test", "learning", SocAnalysisExecutionOptions(), None)
