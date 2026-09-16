"""Per-run DEV controls must not mutate shared process configuration."""

import os

import pytest
from pydantic import ValidationError
from test_soc_corpus_workbench import _repository
from test_soc_tenant_policy import _run

from soc_agent.application.analysis import build_soc_analysis_service
from soc_agent.contracts import ServiceRequestContext
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.integrations.pingan.tenant_disposition import PINGAN_TENANT_DISPOSITION_POLICY_PATH
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings


def test_explicit_policy_off_does_not_mutate_env_or_another_service(tmp_path, monkeypatch):
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "true")
    monkeypatch.setenv("SOC_TENANT_DISPOSITION_POLICY_PATH", str(PINGAN_TENANT_DISPOSITION_POLICY_PATH))
    monkeypatch.setenv("SOC_TENANT_POLICY_ADVISOR_MODE", "llm")
    monkeypatch.setenv("SOC_TENANT_POLICY_SKILL_PATH", "/unused-for-explicit-off")
    monkeypatch.setenv("SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED", "true")
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "apply")
    monkeypatch.setenv("SOC_DIRECT_RESOLUTION_ENABLED", "true")
    repository = _repository(tmp_path)
    settings = SocLLMSettings(mode=SocAnalyzerMode.STUB)
    off = SocAnalysisExecutionOptions()
    on = off.model_copy(update={"tenant_policy_enabled": True})
    env_before = dict(os.environ)
    services = [build_soc_analysis_service(repository, settings=settings, runtime_environment="dev", execution_options=options, pattern_observation_enabled=False, execute_authorized_actions=False) for options in (off, on)]
    payload = _run(rule_code="RPAADM_000558").input_payload
    runs = [service.analyze(payload, context=ServiceRequestContext(idempotency_key=f"options-{index}")) for index, service in enumerate(services)]
    assert runs[0].analysis is not None
    assert runs[0].direct_resolution is None
    assert runs[1].analysis is None
    assert runs[1].direct_resolution.source_kind == "tenant_policy"
    assert runs[0].execution_options == off
    assert repository.get_run(runs[1].run_id).execution_options == on
    assert dict(os.environ) == env_before


@pytest.mark.parametrize(
    "extra", [{"external_action_execution": True}, {"normalization_review_mode": "invalid"}, {"tenant_policy_enabled": "false"}, {"tenant_policy_advisor_enabled": True}, {"tenant_policy_signal_providers_enabled": True}]
)
def test_options_reject_unknown_authority_and_inconsistent_values(extra):
    with pytest.raises(ValidationError):
        SocAnalysisExecutionOptions.model_validate(extra)


def test_options_are_immutable():
    options = SocAnalysisExecutionOptions()
    with pytest.raises(ValidationError):
        options.tenant_policy_enabled = True


def test_process_api_forwards_typed_settings_and_keeps_admin_boundary():
    from types import SimpleNamespace

    from fastapi import HTTPException
    from test_soc_corpus_workbench import _FakeRequest

    from app.gateway.routers.soc_corpus_workbench import process_corpus_workbench_alert
    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchProcessRequest

    calls = []
    service = SimpleNamespace(start_alert=lambda alert_id, **kwargs: calls.append((alert_id, kwargs)))
    options = SocAnalysisExecutionOptions()
    body = SocCorpusWorkbenchProcessRequest(settings=options)
    process_corpus_workbench_alert("test", request=_FakeRequest(), service=service, body=body)
    assert calls[0][1]["settings"] == options
    assert calls[0][1]["context"].actor.roles == ["soc_admin"]
    with pytest.raises(HTTPException) as error:
        process_corpus_workbench_alert("test", request=_FakeRequest(system_role="user"), service=service, body=body)
    assert error.value.status_code == 403
    assert len(calls) == 1
    with pytest.raises(ValidationError):
        SocCorpusWorkbenchProcessRequest.model_validate({"settings": {}, "external_action_execution": True})


def test_explicit_review_switch_constructs_only_the_selected_reviewer(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from soc_agent.application import analysis

    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "apply")
    client = Mock()
    monkeypatch.setattr(analysis, "get_app_config", lambda: SimpleNamespace(model_dump=lambda **_: {}))
    monkeypatch.setattr(analysis, "build_configured_chat_client", lambda **_: (client, "fixture-model"))
    monkeypatch.setattr(analysis, "build_configured_analysis_nodes", lambda **_: (Mock(), None))
    factory = Mock()
    monkeypatch.setattr(analysis, "JsonLLMNormalizationReviewer", factory)
    repository = _repository(tmp_path)
    services = []
    for mode in ("off", "apply", "shadow"):
        services.append(
            build_soc_analysis_service(
                repository, settings=SocLLMSettings(mode=SocAnalyzerMode.LLM), execution_options=SocAnalysisExecutionOptions(normalization_review_mode=mode), pattern_observation_enabled=False, execute_authorized_actions=False
            )
        )
    assert services[0]._runtime._normalization_reviewer is None
    assert [call.kwargs["mode"] for call in factory.call_args_list] == ["apply", "shadow"]
    assert all(call.kwargs["client"] is client for call in factory.call_args_list)
    client.assert_not_called()


def _workbench(tmp_path, monkeypatch, *, factory_wrapper=None):
    import pandas as pd

    from soc_agent.application.memory import build_soc_memory_profile_registry
    from soc_agent.core import SocMemoryPatternService
    from soc_agent.demo.corpus_workbench import CORPUS_WORKBENCH_ENVIRONMENT, SocCorpusWorkbenchRunControls, SocCorpusWorkbenchService

    monkeypatch.setenv("SOC_TENANT_DISPOSITION_POLICY_PATH", str(PINGAN_TENANT_DISPOSITION_POLICY_PATH))
    repository = _repository(tmp_path)
    settings = SocLLMSettings(mode=SocAnalyzerMode.STUB, max_concurrency=2)
    defaults = SocAnalysisExecutionOptions(tenant_policy_enabled=True)
    built = []

    def factory(options):
        built.append(options)
        service = build_soc_analysis_service(repository, settings=settings, runtime_environment=CORPUS_WORKBENCH_ENVIRONMENT, execution_options=options, pattern_observation_enabled=False, execute_authorized_actions=False)
        return factory_wrapper(service, options) if factory_wrapper else service

    rows = []
    for alert_id in ("options-a", "options-b", "options-c"):
        payload = _run(rule_code="RPAADM_000558").input_payload
        payload["alert_id"] = alert_id
        payload["event"]["event_time"] = "2026-09-15T08:00:00Z"
        rows.append({"alert_id": alert_id, "alert_full_data": {"alert_data": payload}})
    source = tmp_path / "corpus.pkl"
    with pd.option_context("future.infer_string", False):
        pd.DataFrame(rows, dtype=object).to_pickle(source)
    workbench = SocCorpusWorkbenchService(
        repository=repository,
        analysis_service=factory(defaults),
        analysis_service_factory=factory,
        run_controls=SocCorpusWorkbenchRunControls(defaults=defaults, tenant_policy_available=True),
        pattern_service=SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=build_soc_memory_profile_registry()),
        source_path=source,
        settings=settings,
        database_file="test.sqlite",
    )
    built.clear()
    return workbench, repository, built


def test_workbench_rerun_records_new_options_without_rewriting_history(tmp_path, monkeypatch):
    from test_soc_corpus_workbench import _admin_context

    workbench, repository, built = _workbench(tmp_path, monkeypatch)
    context = _admin_context("settings-run", actor_id="operator")
    first = workbench.process_alert("options-a", context=context)
    first_saved = repository.get_run(first.run_id).model_dump_json()
    off = SocAnalysisExecutionOptions()
    second = workbench.process_alert("options-a", context=context, settings=off)
    assert second.run_id != first.run_id
    assert second.replay_of_run_id == first.run_id
    assert repository.get_run(first.run_id).model_dump_json() == first_saved
    assert repository.get_run(second.run_id).execution_options == off
    assert workbench.get_execution("options-a").execution_options == off
    audit = workbench.get_audit_bundle("options-a", context=context)
    assert audit.artifacts[0].payload["run_identity"]["execution_options"] == off.model_dump(mode="json")
    assert built == [SocAnalysisExecutionOptions(tenant_policy_enabled=True), off]
    assert workbench.get_state().run_controls.defaults.tenant_policy_enabled is True
    workbench._execution_executor.shutdown(wait=True)


def test_parallel_options_share_duplicate_and_capacity_guards(tmp_path, monkeypatch):
    from threading import Event

    from test_soc_corpus_workbench import _admin_context

    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchBusyError, SocCorpusWorkbenchCapacityError

    release = Event()
    entered = {False: Event(), True: Event()}

    class Blocked:
        def __init__(self, service, options):
            self.service, self.options = service, options

        def analyze(self, payload, *, context):
            entered[self.options.tenant_policy_enabled].set()
            assert release.wait(10)
            return self.service.analyze(payload, context=context)

    workbench, repository, built = _workbench(tmp_path, monkeypatch, factory_wrapper=Blocked)
    context = _admin_context("parallel-settings", actor_id="operator")
    off = SocAnalysisExecutionOptions()
    try:
        workbench.start_alert("options-a", context=context)
        workbench.start_alert("options-b", context=context, settings=off)
        assert all(event.wait(10) for event in entered.values())
        assert workbench.get_activity().active_count == 2
        with pytest.raises(SocCorpusWorkbenchBusyError):
            workbench.start_alert("options-a", context=context, settings=off)
        with pytest.raises(SocCorpusWorkbenchCapacityError):
            workbench.start_alert("options-c", context=context, settings=off)
        assert len(built) == 2
    finally:
        release.set()
        workbench._execution_executor.shutdown(wait=True)
    assert workbench.get_activity().active_count == 0
    assert workbench.get_execution("options-a").execution_options.tenant_policy_enabled is True
    assert workbench.get_execution("options-b").execution_options == off


def test_unavailable_option_releases_claim_without_building_service(tmp_path, monkeypatch):
    from test_soc_corpus_workbench import _admin_context

    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchError

    workbench, _, built = _workbench(tmp_path, monkeypatch)
    with pytest.raises(SocCorpusWorkbenchError, match="当前部署未配置或未允许"):
        workbench.start_alert("options-a", context=_admin_context("unavailable", actor_id="operator"), settings=SocAnalysisExecutionOptions(tenant_policy_enabled=True, tenant_policy_advisor_enabled=True))
    assert built == []
    assert workbench.get_activity().active_count == 0
    workbench._execution_executor.shutdown(wait=True)


def test_missing_run_config_releases_claim(tmp_path, monkeypatch):
    from test_soc_corpus_workbench import _admin_context

    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchError

    workbench, _, _ = _workbench(tmp_path, monkeypatch)

    def missing(_):
        raise FileNotFoundError("missing policy file")

    workbench._analysis_service_factory = missing
    with pytest.raises(SocCorpusWorkbenchError, match="运行设置不可用"):
        workbench.start_alert("options-a", context=_admin_context("missing-file", actor_id="operator"))
    assert workbench.get_activity().active_count == 0
    workbench._execution_executor.shutdown(wait=True)
