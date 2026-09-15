"""Exercise semantic adoption through persisted workbench and candidate consumers."""

from datetime import UTC, datetime

import pandas as pd
from test_soc_corpus_workbench import _admin_context, _repository
from test_soc_normalization_observations import Client, file_output, source

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import SocMemoryRunPromotionCommand
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService, SocMemoryPatternService, SocMemoryService
from soc_agent.demo.corpus_workbench import CORPUS_WORKBENCH_ENVIRONMENT, SocCorpusWorkbenchService
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer
from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher
from soc_agent.memory.sources import memory_candidate_command_from_run_promotion


def test_shadow_to_apply_preserves_history_and_projects_real_memory_conditions(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    text, output = file_output()
    output["events"][0]["detector_id"] = "vendor-event-123"
    output["additional_facts"] = [{"name": "uuid", "value": "unique-event-123", "meaning": "原始事件编号。", "source_quote": text}]
    alert = source(text)
    alert.tenant_id = "pingan"
    alert.source.source_system = "synthetic-edr"
    alert.source.product = "Synthetic EDR"
    alert.detection.rule_code = "SYNTHETIC-1"
    alert.detection.rule_name = "File detection"
    alert.event.event_time = datetime(2026, 8, 12, tzinfo=UTC)
    payload = {**alert.model_dump(mode="json"), "message": text}
    path = tmp_path / "semantic.pkl"
    with pd.option_context("future.infer_string", False):
        pd.DataFrame([{"alert_id": alert.alert_id, "alert_full_data": {"alert_data": payload}}], dtype=object).to_pickle(path)

    def workbench(mode):
        monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", mode)
        profiles = build_soc_memory_profile_registry()
        runtime = DeterministicAnalysisRuntime(
            normalization_reviewer=JsonLLMNormalizationReviewer(client=Client(output), model_name="synthetic", mode=mode),
            analysis_request_enricher=ConfirmedMemoryAnalysisRequestEnricher(SocMemoryService(record_repository=repository, profile_registry=profiles), profile_registry=profiles, environment=CORPUS_WORKBENCH_ENVIRONMENT),
        )
        service = SocAnalysisService(runtime=runtime, repository=repository, summary_repository=repository, audit_repository=repository, analysis_persistence=repository)
        return SocCorpusWorkbenchService(
            repository=repository,
            analysis_service=service,
            pattern_service=SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=profiles),
            source_path=path,
            settings=SocLLMSettings(mode=SocAnalyzerMode.STUB),
            database_file="synthetic.sqlite",
            normalization_review_mode=mode,
        )

    context = _admin_context("before-semantic-apply", actor_id="tester")
    before = workbench("shadow").process_alert(alert.alert_id, context=context)
    old_run = repository.get_run(before.run_id).model_dump_json()
    applied_workbench = workbench("apply")
    old_view = applied_workbench.get_state(unprocessed_only=False).alerts[0]
    assert old_view.run_id == before.run_id
    assert old_view.observation_id == before.observation_id
    after = applied_workbench.process_alert(alert.alert_id, context=context.model_copy(update={"request_id": "after-semantic-apply"}))
    assert after.alert.workflow_state == "completed"
    assert after.observation_id != before.observation_id
    assert after.alert.observation_id == after.observation_id
    assert after.alert.pattern_support_count == 1
    assert applied_workbench.get_execution(alert.alert_id).status == "completed"
    assert repository.get_run(before.run_id).model_dump_json() == old_run

    run = repository.get_run(after.run_id)
    assert run.normalization_assistance.mode == "apply"
    assert all(change.canonical_status == "applied" for change in run.normalization_assistance.observation_changes)
    assert run.llm_analysis_request.canonical_entities.detections[0].name == "GoExec.a"
    profiles = build_soc_memory_profile_registry()
    facets = profiles.resolve_run(run).project_run_facets(run)
    assert "detected_file:yak.exe" in facets["behavior_component_core"]
    assert after.alert.behavior_fingerprint == facets["behavior_fingerprint"][0]
    assert set(after.alert.behavior_components) == set(facets["behavior_component"])
    assert after.alert.decision_eligible is True
    assert after.alert.readiness == "singleton_strong"
    assert applied_workbench.get_state(unprocessed_only=False).readiness.decision_eligible_alert_count == 1
    assert not applied_workbench.get_state(unprocessed_only=False, readiness="fingerprint_missing").alerts
    assert applied_workbench.get_state(unprocessed_only=False, readiness="fingerprint_missing", focus_alert_id=alert.alert_id).alerts[0].run_id == after.run_id
    assert applied_workbench.get_state(unprocessed_only=False, readiness="singleton_strong").alerts[0].alert_id == alert.alert_id
    assert all(value not in " ".join(facets["behavior_component_core"]) for value in ["vendor-event-123", "unique-event-123", "a" * 32])
    command = memory_candidate_command_from_run_promotion(run, SocMemoryRunPromotionCommand(run_id=run.run_id), profile_registry=profiles)
    assert command.applicability.profile_version == "8"
    assert command.applicability.required_facets["behavior_fingerprint"] == facets["behavior_fingerprint"]
    assert "detected_file:yak.exe" in command.facets["behavior_component_core"]
    replayed = applied_workbench.process_alert(alert.alert_id, context=context.model_copy(update={"request_id": "repeat-semantic-apply"}))
    assert replayed.alert.workflow_state == "completed"
    assert replayed.observation_id == after.observation_id
    assert replayed.alert.pattern_support_count == 1
