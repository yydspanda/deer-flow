"""Pattern progress follows its exact durable attempt, never absence of output."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import test_soc_corpus_batch_workbench as batch_fixtures
from sqlalchemy import update
from test_soc_corpus_experiments import context, service
from test_soc_pingan_memory_profile import _run as _pingan_run

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import AnalysisRequestJournal, AuditAction, MemoryPatternDataClass, MemoryPatternSourceType, NormalizationAssistResult
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.core import SocAnalysisService, SocMemoryPatternService
from soc_agent.db.models import SocProcessingJobRow
from soc_agent.demo.corpus_workbench import _observation_matches_run, _runtime_feature_case

workbench = batch_fixtures.workbench


def _saved_run(workbench, alert_id="0", *, run_id="RUN-pattern-test", idempotency_key=None):
    payload = json.loads((Path(__file__).resolve().parents[1] / "samples/alerts/approved_scanner.json").read_text(encoding="utf-8"))
    run = SocAnalysisService().analyze(payload)
    run.run_id = run_id
    run.alert_id = alert_id
    run.input_hash = workbench._cases[alert_id].payload_hash
    run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"environment": "dev-corpus-eval"})
    if idempotency_key is not None:
        run.request_journal = AnalysisRequestJournal(
            action=AuditAction.ANALYSIS,
            request_id="pattern-progress-test",
            actor=context().actor,
            idempotency_key_hash=hashlib.sha256(json.dumps(idempotency_key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            request_schema_version="fixture",
            request_hash="fixture",
            model_name="offline-stub",
            prompt_version="fixture",
            provider_step_name="analyze_llm",
        )
    workbench._repository.save_run(run)
    return run


def _job(workbench, *, batch="learning"):
    batches = service(workbench._repository, [])
    batches.prepare(workbench.batch_plan, experiment_id="EXP-pattern", name="Pattern progress", context=context())
    alert_id = next(member.alert_id for member in workbench.batch_plan.members if member.batch == batch)
    round_ = batches.create_round(CorpusRoundCreateCommand(experiment_id="EXP-pattern", selection=CorpusRoundSelection(batch=batch, alert_ids=[alert_id]), memory_mode="none"), context=context())
    return batches.store.list_round_items(round_.round_id).items[0].job


def _update_job(workbench, job, **values):
    with workbench._repository._session_factory() as session:
        session.execute(update(SocProcessingJobRow).where(SocProcessingJobRow.job_id == job.job_id).values(**values))
        session.commit()


def _memory(execution):
    return next(phase for phase in execution.phases if phase.phase == "memory")


@pytest.mark.parametrize(
    ("batch", "reason", "expected", "phase_status"),
    [
        ("learning", "1 validation error for MemoryPatternSignature\nfacets\nValue error, memory pattern facet values must be 1-512 characters [input_value='PRIVATE-RAW-VALUE']", "模式特征超过长度限制", "failed"),
        ("learning", "canonical alert event_time must be timezone-aware", "不满足模式积累条件", "skipped"),
        ("validation", "validation_learning_disabled", "第二批仅验证经验效果", "skipped"),
    ],
)
def test_completed_pattern_omission_has_terminal_safe_reason(workbench, batch, reason, expected, phase_status):
    job = _job(workbench, batch=batch)
    run = _saved_run(workbench, job.alert_id)
    _update_job(workbench, job, status="completed", run_id=run.run_id, result_payload={"pattern_reason": reason})

    execution = workbench.get_execution(job.alert_id)

    assert execution.status == ("failed" if phase_status == "failed" else "analysis_complete")
    assert execution.current_phase == ("memory" if phase_status == "failed" else None)
    assert execution.run_status == run.status.value
    memory = _memory(execution)
    assert memory.status == phase_status
    assert memory.steps[0].status == phase_status
    assert expected in memory.summary
    assert "PRIVATE-RAW-VALUE" not in execution.model_dump_json()
    audit = workbench.get_audit_bundle(job.alert_id, context=context(), run_id=run.run_id)
    assert _memory(audit.execution).summary == memory.summary


def test_active_pattern_write_matches_request_before_job_run_id_is_saved(workbench):
    job = _job(workbench)
    run = _saved_run(workbench, job.alert_id, idempotency_key=job.idempotency_key)
    _update_job(workbench, job, status="analyzing", started_at=run.started_at)

    execution = workbench.get_execution(job.alert_id)

    assert execution.status == "analysis_complete"
    assert execution.current_phase == "memory"
    assert _memory(execution).status == "running"
    assert workbench._repository.processing_jobs().get(job.job_id).run_id is None
    _update_job(workbench, job, status="completed", run_id=run.run_id, result_payload={"pattern_reason": "ineligible"})
    assert _memory(workbench.get_execution(job.alert_id)).status == "skipped"


def test_validation_claim_never_claims_to_be_writing_new_patterns(workbench):
    job = _job(workbench, batch="validation")
    run = _saved_run(workbench, job.alert_id, idempotency_key=job.idempotency_key)
    _update_job(workbench, job, status="analyzing", started_at=run.started_at)

    memory = _memory(workbench.get_execution(job.alert_id))

    assert memory.status == "skipped"
    assert "第二批仅验证经验效果" in memory.summary


def test_new_attempt_does_not_make_fixed_historical_audit_run_active(workbench):
    old = _saved_run(workbench, run_id="RUN-older")
    job = _job(workbench)
    current = _saved_run(workbench, job.alert_id, idempotency_key=job.idempotency_key)
    current.started_at = old.started_at + timedelta(seconds=1)
    workbench._repository.save_run(current)
    _update_job(workbench, job, status="analyzing", started_at=current.started_at)

    assert _memory(workbench.get_execution(job.alert_id)).status == "running"
    audit = workbench.get_audit_bundle(job.alert_id, context=context(), run_id=old.run_id)
    assert audit.execution.current_phase is None
    assert _memory(audit.execution).status == "skipped"


def test_old_completed_job_reason_does_not_override_new_unlinked_claim(workbench):
    old_job = _job(workbench)
    old = _saved_run(workbench, old_job.alert_id, run_id="RUN-old-completed")
    _update_job(workbench, old_job, status="completed", run_id=old.run_id, result_payload={"pattern_reason": "old omission"})
    batches = service(workbench._repository, [])
    round_ = batches.create_round(CorpusRoundCreateCommand(experiment_id="EXP-pattern", selection=CorpusRoundSelection(batch="learning", alert_ids=[old.alert_id])), context=context())
    new_job = batches.store.list_round_items(round_.round_id).items[0].job
    current = _saved_run(workbench, old.alert_id, idempotency_key=new_job.idempotency_key)
    _update_job(workbench, new_job, status="analyzing", started_at=current.started_at)

    assert _memory(workbench.get_execution(old.alert_id)).status == "running"
    assert _memory(workbench.get_audit_bundle(old.alert_id, context=context(), run_id=old.run_id).execution).status == "skipped"


@pytest.mark.parametrize("field", ["plan_id", "tenant_id", "environment", "alert_id", "run_id", "idempotency_key_hash"])
def test_pattern_job_lookup_does_not_cross_run_or_corpus_scope(workbench, field):
    job = _job(workbench)
    run = _saved_run(workbench, job.alert_id, idempotency_key=job.idempotency_key)
    _update_job(workbench, job, status="analyzing", started_at=run.started_at)
    query = dict(plan_id=workbench.batch_plan.plan_id, tenant_id="pingan", environment="dev-corpus-eval", alert_id=job.alert_id, run_id=run.run_id, idempotency_key_hash=run.request_journal.idempotency_key_hash)
    assert workbench._experiment_store.run_job(**query) is not None
    if field == "run_id":
        # After it has a Run identity, even a matching request cannot replace it.
        _update_job(workbench, job, run_id=run.run_id)
    query[field] = "other"
    assert workbench._experiment_store.run_job(**query) is None


def test_failed_pattern_job_retains_successful_analysis_and_safe_failure(workbench):
    job = _job(workbench)
    run = _saved_run(workbench, job.alert_id)
    _update_job(workbench, job, status="failed", run_id=run.run_id, error_code="CorpusExecutionError", error_message="pattern accumulation failed: PRIVATE-RAW-VALUE")

    execution = workbench.get_execution(job.alert_id)

    assert execution.status == "failed"
    assert execution.run_status == run.status.value
    assert execution.current_phase == "memory"
    memory = _memory(execution)
    assert memory.status == "failed"
    assert "研判结果已保存" in memory.summary
    assert "PRIVATE-RAW-VALUE" not in execution.model_dump_json()
    assert next(phase for phase in execution.phases if phase.phase == "reasoning").status == "success"
    audit = workbench.get_audit_bundle(job.alert_id, context=context(), run_id=run.run_id)
    assert audit.execution.status == "failed"
    assert _memory(audit.execution).status == "failed"


def test_finished_run_without_any_pattern_job_is_not_still_running(workbench):
    run = _saved_run(workbench)
    execution = workbench.get_execution(run.alert_id)
    assert execution.status == "analysis_complete"
    assert execution.current_phase is None
    assert _memory(execution).status == "skipped"


def test_active_local_claim_only_marks_its_new_run_as_writing_pattern(workbench):
    old = _saved_run(workbench, run_id="RUN-before-claim")
    claim = workbench._reserve_execution(old.alert_id, context=context())
    try:
        assert _memory(workbench.get_execution(old.alert_id)).status == "skipped"
        current = _saved_run(workbench, run_id="RUN-in-claim")
        current.started_at = old.started_at + timedelta(seconds=1)
        workbench._repository.save_run(current)
        workbench._active_executions[current.alert_id] = replace(claim, pattern_run_id=current.run_id)
        assert _memory(workbench.get_execution(current.alert_id)).status == "running"
        assert _memory(workbench.get_audit_bundle(old.alert_id, context=context(), run_id=old.run_id).execution).status == "skipped"
    finally:
        workbench._release_execution(claim)
    assert _memory(workbench.get_execution(current.alert_id)).status == "skipped"


@pytest.mark.parametrize(
    ("saved_version", "review_mode"),
    [(None, None), ("7", "apply"), ("8", "shadow"), ("9", None), ("9", "shadow")],
)
def test_saved_observation_remains_visible_in_execution_audit_and_list(workbench, saved_version, review_mode):
    job = _job(workbench)
    long_url = "https://example.test/query?sql=" + "x" * 700
    run = _pingan_run(1, service_url=long_url)
    run.alert_id = job.alert_id
    run.input_hash = workbench._cases[job.alert_id].payload_hash
    run.llm_analysis_request.alert_id = job.alert_id
    run.llm_analysis_request.environment = "dev-corpus-eval"
    if saved_version is not None:
        run.llm_analysis_request.memory_profile = {
            "profile_id": "pingan.soc",
            "profile_version": saved_version,
            "feature_schema_version": {"7": "pingan.soc.memory_features.v5", "8": "pingan.soc.memory_features.v6", "9": "pingan.soc.memory_features.v7"}[saved_version],
        }
    if review_mode is not None:
        run.normalization_assistance = NormalizationAssistResult(mode=review_mode, status="applied" if review_mode == "apply" else "shadow", request_hash="fixture", model_name="fixture", prompt_version="fixture")
    workbench._repository.save_run(run)
    frozen_run = run.model_dump(mode="json")
    patterns = SocMemoryPatternService(repository=workbench._repository, candidate_repository=workbench._repository, profile_registry=build_soc_memory_profile_registry())
    workbench._pattern_service = patterns
    result = patterns.observe_run(run, source_type=MemoryPatternSourceType.BATCH_ALERT, transport_ref="filtered-display", environment="dev-corpus-eval", data_class=MemoryPatternDataClass.OPERATIONAL, context=context())
    observation = result.observation
    assert "url:" + long_url not in observation.signature.facets.get("entity", [])
    assert workbench._repository.list_memory_pattern_observations(alert_id=run.alert_id)[0].observation_id == observation.observation_id
    _update_job(workbench, job, status="completed", run_id=run.run_id, result_payload={"observation_id": observation.observation_id})

    # The capacity filter must not relax profile or behavior identity guards.
    wrong_profile = observation.model_copy(update={"profile_version": "unknown-profile-version"})
    wrong_behavior = observation.model_copy(update={"signature": observation.signature.model_copy(update={"value": "unrelated-behavior"})})
    assert not _observation_matches_run(wrong_profile, run)
    assert not _observation_matches_run(wrong_behavior, run)
    wrong_run = run.model_copy(deep=True)
    wrong_run.llm_analysis_request.memory_profile = {"profile_id": "foreign.soc", "profile_version": "9", "feature_schema_version": "pingan.soc.memory_features.v7"}
    assert not _observation_matches_run(observation, wrong_run)

    execution = workbench.get_execution(job.alert_id)
    assert execution.status == "completed"
    assert execution.observation_id == observation.observation_id
    assert _memory(execution).status == "success"
    assert execution.current_phase is None

    audit = workbench.get_audit_bundle(job.alert_id, context=context(), run_id=run.run_id)
    assert audit.execution.status == "completed"
    assert _memory(audit.execution).status == "success"
    memory_artifact = next(item for item in audit.artifacts if item.artifact_id == "memory-pattern-write")
    assert memory_artifact.payload["pattern_observation"]["observation_id"] == observation.observation_id

    state = workbench.get_state(batch="learning", search=job.alert_id, unprocessed_only=False, include_group_catalog=False, include_rehearsal=False)
    row = next(item for item in state.alerts if item.alert_id == job.alert_id)
    assert row.workflow_state == "completed"
    assert row.observation_id == observation.observation_id
    if saved_version is not None:
        assert row.behavior_fingerprint == observation.signature.facets["behavior_fingerprint"][0]
        summary = workbench._list_queries.rows(workbench._list_catalog_id)[job.alert_id][1]
        assert summary.semantic_features_applied
        assert summary.behavior_fingerprint == row.behavior_fingerprint
    case = workbench._cases[job.alert_id]
    # A damaged frozen identity cannot relabel the run or break the whole list.
    assert _runtime_feature_case(case, wrong_run, support_count=1) == (case, case.readiness)
    assert workbench._repository.get_run(run.run_id).model_dump(mode="json") == frozen_run
