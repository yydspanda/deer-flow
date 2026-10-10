"""Fact preparation shares normalization without producing security decisions."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from test_soc_agent_repository import _repository, _sample
from test_soc_normalization_assistance import Client, alert, fact, payload

from soc_agent.application import analysis as composition
from soc_agent.contracts import AnalysisRun, AnalysisRunStatus, ServiceRequestContext
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.core.service import DeterministicAnalysisRuntime, SocAnalysisService, SocServiceConflictError
from soc_agent.llm import SocLLMSettings
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer


def forbidden(*args, **kwargs):
    raise AssertionError("fact preparation invoked security analysis or governance")


def test_fact_runtime_reviews_extracts_and_enriches_without_deciding():
    client = Client([fact()])
    seen = []
    analyzer = SimpleNamespace(model_name="must-not-use", prompt_version="must-not-use", step_name="analyze_llm", analyze=forbidden)

    def enrich(request):
        seen.append(request)
        return request

    runtime = DeterministicAnalysisRuntime(
        analyzer=analyzer,
        role_verifier=SimpleNamespace(evaluate_trigger=forbidden),
        normalization_reviewer=JsonLLMNormalizationReviewer(client=client, model_name="semantic-model"),
        direct_resolution=SimpleNamespace(resolve_policy=forbidden, resolve_memory=forbidden),
        decision_policy=SimpleNamespace(decide=forbidden),
        analysis_request_enricher=enrich,
        facts_only=True,
    )
    run = runtime.analyze(payload(alert()))
    assert run.status is AnalysisRunStatus.FACTS_READY
    assert run.pipeline_version == "soc-runtime-v1-facts"
    assert run.model_name == run.prompt_version == "not_invoked"
    assert run.analysis is run.decision is run.direct_resolution is None
    assert run.normalized_alert.entities.file.file_path == r"D:\tools\yak.exe"
    assert run.entities and run.fact_reconstruction and run.llm_analysis_request
    assert len(client.calls) == len(seen) == 1
    assert run.ended_at and run.total_duration_ms is not None
    assert run.normalization_assistance.changes[0].model_input_status == "present"
    assert {s.step_name for s in run.steps}.isdisjoint({"analyze_llm", "analyze_stub", "decision", "direct_policy", "direct_memory"})
    assert AnalysisRun.model_validate_json(run.model_dump_json()) == run


def test_fact_service_preserves_business_summary_and_skips_all_observers():
    repository = _repository()
    source = _sample("malicious_ioc.json")
    ordinary = SocAnalysisService(repository=repository, summary_repository=repository).analyze(source)
    previous_summary = repository.get_alert_summary(ordinary.run_id)
    assert previous_summary is not None
    observer = SimpleNamespace(observe=forbidden)
    events = []
    service = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(facts_only=True),
        repository=repository,
        audit_repository=repository,
        summary_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=SimpleNamespace(save_analysis_bundle=forbidden),
        normalization_maintenance_monitor=SimpleNamespace(monitor_run=forbidden),
        post_analysis_observers=(observer,),
        event_sink=SimpleNamespace(emit=events.append),
    )
    context = ServiceRequestContext(idempotency_key="facts-only-retry")
    result = service.analyze(source, context=context)
    again = service.analyze(source, context=context)
    assert result.status is AnalysisRunStatus.FACTS_READY
    assert again.run_id == result.run_id
    assert repository.get_run(result.run_id) == result
    assert repository.get_alert_summary(ordinary.run_id) == previous_summary
    assert repository.get_alert_summary(result.run_id) is None
    assert repository.list_alert_summaries() == [previous_summary]
    audit = repository.list_audit_records(result.run_id)
    assert len(audit) == 1
    assert audit[0].final_verdict is None
    assert events == []


def test_fact_review_journal_and_snapshot_are_reusable_by_normal_analysis():
    repository = _repository()
    client = Client([fact()])
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="semantic-model")
    source = payload(alert())
    prepared = SocAnalysisService(repository=repository, runtime=DeterministicAnalysisRuntime(normalization_reviewer=reviewer, facts_only=True)).analyze(source)
    assert len(prepared.provider_request_journals) == 1
    assert prepared.provider_request_journals[0].provider_purpose.value == "normalization_assist"
    assert prepared.provider_request_journals[0].status.value == "completed"
    analyzed = SocAnalysisService(repository=repository, runtime=DeterministicAnalysisRuntime(normalization_reviewer=reviewer)).analyze(source)
    assert analyzed.analysis is not None
    assert analyzed.normalization_assistance.metadata["reused_from_run_id"] == prepared.run_id
    assert len(client.calls) == 1


def test_fact_factory_never_constructs_models_memory_or_policy_except_semantic_reviewer(monkeypatch):
    client = Client([fact()])
    monkeypatch.setattr(composition, "get_app_config", lambda: SimpleNamespace(model_dump=lambda **_: {}))
    monkeypatch.setattr(composition, "build_configured_chat_client", lambda **_: (client, "semantic-model"))
    monkeypatch.setattr(composition, "build_configured_analysis_nodes", forbidden)
    monkeypatch.setattr(composition, "_build_post_analysis_observers", forbidden)
    monkeypatch.setattr(composition, "_build_analysis_request_enricher", forbidden)
    monkeypatch.setattr(composition, "SocDirectResolutionService", forbidden)
    monkeypatch.setattr(composition, "SocNormalizationMaintenanceService", forbidden)
    service = composition.build_soc_analysis_service(
        _repository(),
        facts_only=True,
        settings=SocLLMSettings(mode="llm"),
        runtime_environment="dev-corpus-eval",
        execution_options=SocAnalysisExecutionOptions(normalization_review_mode="apply", tenant_policy_enabled=True, tenant_policy_advisor_enabled=True),
    )
    run = service.analyze(payload(alert()))
    assert run.status is AnalysisRunStatus.FACTS_READY
    assert run.llm_analysis_request.memory_profile
    assert run.llm_analysis_request.environment == "dev-corpus-eval"
    assert not any(item.context_ref.startswith("M-") for item in run.llm_analysis_request.context_catalog)
    assert len(client.calls) == 1


def test_fact_preparation_is_not_public_execution_option():
    with pytest.raises(ValidationError):
        SocAnalysisExecutionOptions.model_validate({"facts_only": True})


def test_fact_status_cannot_be_used_for_an_ordinary_security_decision():
    ordinary = DeterministicAnalysisRuntime().analyze(_sample("malicious_ioc.json"))
    with pytest.raises(ValidationError, match="cannot carry a security decision"):
        AnalysisRun.model_validate({**ordinary.model_dump(), "status": "facts_ready", "pipeline_version": "soc-runtime-v1-facts"})


def test_fact_persistence_rolls_back_run_when_audit_write_fails(monkeypatch):
    repository = _repository()
    service = SocAnalysisService(runtime=DeterministicAnalysisRuntime(facts_only=True), repository=repository, audit_repository=repository)
    monkeypatch.setattr(type(repository), "save_audit_record", forbidden)
    with pytest.raises(AssertionError):
        service.analyze(_sample("malicious_ioc.json"))
    assert repository.list_runs() == []


def test_replay_and_idempotency_cannot_turn_preparation_into_security_analysis():
    repository = _repository()
    context = ServiceRequestContext(idempotency_key="facts-purpose")
    source = _sample("malicious_ioc.json")
    prepared = SocAnalysisService(runtime=DeterministicAnalysisRuntime(facts_only=True), repository=repository, audit_repository=repository).analyze(source, context=context)
    normal_service = SocAnalysisService(repository=repository, audit_repository=repository)
    with pytest.raises(SocServiceConflictError, match="separate execution identities"):
        normal_service.replay(prepared.run_id)
    with pytest.raises(SocServiceConflictError, match="separate execution identities"):
        normal_service.analyze(source, context=context)


def test_fact_failures_do_not_overwrite_business_summary():
    repository = _repository()
    source = _sample("malicious_ioc.json")
    ordinary = SocAnalysisService(repository=repository, summary_repository=repository).analyze(source)
    previous_summary = repository.get_alert_summary(ordinary.run_id)
    assert previous_summary is not None
    runtime = DeterministicAnalysisRuntime(facts_only=True, analysis_request_enricher=Mock(side_effect=ValueError("invalid fact projection")))
    failed = SocAnalysisService(repository=repository, summary_repository=repository, runtime=runtime).analyze(source)
    assert failed.status is AnalysisRunStatus.FAILED
    assert failed.pipeline_version == "soc-runtime-v1-facts"
    assert repository.get_run(failed.run_id).failure
    assert repository.get_alert_summary(ordinary.run_id) == previous_summary
    assert repository.get_alert_summary(failed.run_id) is None
