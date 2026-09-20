"""A failed post-analysis task must retain the saved Runtime result and usage."""

from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context, prepare
from test_soc_memory_patterns import _run

from soc_agent.contracts import AnalysisRunStatus, PipelineStepTrace, RuntimeFailure
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.contracts.schemas import SocMemoryReuseCondition
from soc_agent.core import SocMemoryPatternService
from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor
from soc_agent.demo.corpus_experiments import CorpusExecutionError, SocCorpusExperimentService
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


def _dispatch_to_terminal(dispatcher, service, round_id):
    dispatcher.start()
    try:
        deadline = monotonic() + 10
        while service.store.get_round(round_id).state != "completed":
            assert monotonic() < deadline, "dispatcher did not finish the single task"
            sleep(0.01)
    finally:
        dispatcher.stop()


@pytest.mark.parametrize("status", [AnalysisRunStatus.SUCCESS, AnalysisRunStatus.NEEDS_REVIEW])
def test_pattern_failure_preserves_saved_analysis_and_retry_reuses_it(tmp_path, monkeypatch, status):
    repo = repository(tmp_path)
    member = members()[0]
    run = _run(member.source_index, tenant_id="pingan")
    run.status = status
    run.alert_id = member.alert_id
    run.input_payload = {"alert_id": member.alert_id, "event_time": member.event_time.isoformat()}
    run.input_hash = stable_hash(run.input_payload)
    run.llm_analysis_request.alert_id = member.alert_id
    run.llm_analysis_request.environment = "dev-corpus-eval"
    run.steps = [
        PipelineStepTrace(
            step_name="analyze_llm",
            status="success",
            metadata={"provider_call_count": 1, "usage": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500}, "usage_measurement": {"status": "reported"}},
        )
    ]
    repo.corpus_experiments().prepare(experiment(), [member.model_copy(update={"payload_hash": run.input_hash})])
    model_calls = []
    request_keys = []

    def analyze(payload, *, context):
        request_keys.append(context.idempotency_key)
        saved = repo.get_run(run.run_id)
        if saved is not None:
            return saved
        model_calls.append(payload["alert_id"])
        repo.save_run(run)
        return run

    original_observe = SocMemoryPatternService.observe_run
    pattern_calls = []

    def fail_first_pattern(self, *args, **kwargs):
        pattern_calls.append(args[0].run_id)
        if len(pattern_calls) == 1:
            # Simulate the real contract error after the successful Run is saved.
            SocMemoryReuseCondition(facet_key="entity", value_prefix="url", values=["url:" + "a" * 600])
        return original_observe(self, *args, **kwargs)

    monkeypatch.setattr(SocMemoryPatternService, "observe_run", fail_first_pattern)
    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda _: run.input_payload, analysis_factory=lambda _: SimpleNamespace(analyze=analyze), profile_registry=SocMemoryProfileRegistry())
    service = SocCorpusExperimentService(repository=repo, execute=executor, configuration_provider=lambda _: {})
    round_ = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=[member.alert_id])), context=context())
    service.start(round_.round_id, context=context())
    dispatcher = CorpusExperimentDispatcher(service, max_concurrency=1, interval_seconds=0.01)
    _dispatch_to_terminal(dispatcher, service, round_.round_id)
    failed = service.store.list_round_items(round_.round_id).items[0].job
    assert failed.status.value == "failed"
    assert failed.run_id == run.run_id
    assert failed.error_code == "CorpusExecutionError"
    assert "pattern accumulation failed: ValidationError" in failed.error_message
    assert failed.result_payload["retryable"] is True
    assert failed.result_payload["summary"]["analysis_status"] == status.value
    measurements = repo.get_run_measurements(run.run_id)
    assert failed.result_payload["summary"]["measurements"] == measurements
    assert measurements["total_tokens"] == 1500
    assert repo.get_run(run.run_id).status is status

    assert service.retry_failed(round_.round_id, context=context()) == {"retried": 1, "not_retryable": 0}
    _dispatch_to_terminal(dispatcher, service, round_.round_id)
    completed = service.store.list_round_items(round_.round_id).items[0].job
    assert completed.status.value == "completed"
    assert completed.job_id == failed.job_id
    assert completed.run_id == run.run_id
    assert completed.attempt_count == 2
    assert completed.result_payload["observation_id"]
    assert completed.result_payload["summary"]["analysis_status"] == status.value
    assert completed.result_payload["summary"]["measurements"] == measurements
    assert len(model_calls) == 1
    assert len(request_keys) == 2 and len(set(request_keys)) == 1
    assert len(repo.list_runs(limit=10)) == 1


@pytest.mark.parametrize("saved_run", ["failed", "missing", "none"])
def test_ordinary_failed_execution_keeps_its_existing_summary_semantics(tmp_path, saved_run):
    repo = repository(tmp_path)
    prepare(repo)
    run_id = None if saved_run == "none" else "RUN-failed"
    if saved_run == "failed":
        run = _run(0).model_copy(
            update={
                "run_id": run_id,
                "status": AnalysisRunStatus.FAILED,
                "failure": RuntimeFailure(step_name="analyze_llm", kind="analyzer_timeout", error_type="TimeoutError", message="Synthetic model timeout"),
            }
        )
        repo.save_run(run)

    def execute(*_):
        raise CorpusExecutionError("Synthetic task failure", run_id=run_id)

    service = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda _: {})
    round_ = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=["0"])), context=context())
    service.start(round_.round_id, context=context())
    assert service.execute_one(round_.round_id)
    job = service.store.list_round_items(round_.round_id).items[0].job
    assert job.status.value == "failed"
    assert job.run_id == run_id
    assert job.result_payload["retryable"] is False
    if run_id:
        assert job.result_payload["summary"]["analysis_status"] == "failed"
        assert job.result_payload["summary"]["measurements"] == repo.get_run_measurements(run_id)
    else:
        assert "summary" not in job.result_payload
