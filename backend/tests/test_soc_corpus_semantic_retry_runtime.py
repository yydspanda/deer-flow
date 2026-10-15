"""Same-round recovery through real journals; all provider responses are synthetic."""

from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context
from test_soc_normalization_assistance import Client, alert, fact, payload

from app.gateway.routers.soc_corpus_experiments import round_results
from soc_agent.contracts import ServiceRequestContext
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundRetryCommand, CorpusRoundSelection, CorpusSemanticRetry
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.demo.corpus_experiment_reports import summarize_results
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor
from soc_agent.demo.corpus_experiments import CorpusExecutionError, SocCorpusExperimentService, semantic_retry_key
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.pipeline.analyzer import StubLLMAnalyzer
from soc_agent.utils.hashing import stable_hash


class RecordingAnalyzer(StubLLMAnalyzer):
    step_name = "analyze_llm"

    def __init__(self):
        self.requests = []
        self.fail = False

    def analyze(self, request):
        self.requests.append(request.model_copy(deep=True))
        if self.fail:
            raise TimeoutError("synthetic model timeout")
        return super().analyze(request)


def setup_runtime(tmp_path, *, semantic_error=None, model_failure=False, batch="validation"):
    repo = repository(tmp_path)
    original = alert()
    original.tenant_id = "pingan"
    original.event.event_time = members()[5].event_time
    original.detection.detection_key = "synthetic:observed-file"
    source = payload(original)
    member = members()[5].model_copy(update={"alert_id": source["alert_id"], "payload_hash": stable_hash(source), "batch": batch, "validation_tier": "main" if batch == "validation" else None})
    repo.corpus_experiments().prepare(experiment(), [member])
    client = Client([fact()], error=semantic_error)
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name="synthetic-reviewer")
    analyzer = RecordingAnalyzer()
    analyzer.fail = model_failure
    executed_options = []

    def factory(round_):
        executed_options.append(round_.options.model_copy(deep=True))
        return SocAnalysisService(
            runtime=DeterministicAnalysisRuntime(analyzer=analyzer, normalization_reviewer=reviewer, execution_options=round_.options),
            repository=repo,
            audit_repository=repo,
            analysis_persistence=repo,
        )

    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda _: source, analysis_factory=factory, profile_registry=SocMemoryProfileRegistry())
    service = SocCorpusExperimentService(repository=repo, execute=executor, configuration_provider=lambda _: {"synthetic_runtime": 1})
    round_ = service.create_round(
        CorpusRoundCreateCommand(
            experiment_id=experiment().experiment_id,
            selection=CorpusRoundSelection(batch=batch, scope="reuse" if batch == "learning" else "all"),
            memory_mode="none",
            options={"normalization_review_mode": "apply", "tenant_policy_enabled": False, "tenant_policy_advisor_enabled": False, "tenant_policy_signal_providers_enabled": False},
        ),
        context=context(),
    )
    service.start(round_.round_id, context=context())
    assert service.execute_one(round_.round_id)
    assert not service.execute_one(round_.round_id)
    return SimpleNamespace(repo=repo, service=service, executor=executor, round=round_, member=member, source=source, client=client, analyzer=analyzer, factory=factory, executed_options=executed_options)


def current_job(fixture):
    return fixture.service.store.list_round_items(fixture.round.round_id).items[0].job


def retry_semantics(fixture):
    result = fixture.service.retry_items(fixture.round.round_id, CorpusRoundRetryCommand(kind="semantic_failed", expected_round_version=fixture.service.store.get_round(fixture.round.round_id).version), context=context())
    assert result["retried"] == 1
    assert fixture.service.execute_one(fixture.round.round_id)
    assert not fixture.service.execute_one(fixture.round.round_id)
    return current_job(fixture)


def report_row(fixture):
    application = SimpleNamespace(
        service=fixture.service,
        workbench=SimpleNamespace(batch_plan_id=experiment().plan_id, experiment_label=lambda *a, **k: {}),
    )
    report = round_results(fixture.round.round_id, application)
    assert report["round_id"] == fixture.round.round_id
    assert report["total"] == 1
    assert len(report["items"]) == 1
    return report["items"][0]


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_semantic_retry_replays_downstream_bypasses_other_run_cache_and_keeps_one_report_row(tmp_path, batch):
    f = setup_runtime(tmp_path, semantic_error=TimeoutError("synthetic review timeout"), batch=batch)
    before_job = current_job(f)
    before = f.repo.get_run(before_job.run_id)
    frozen = before.model_dump(mode="json")
    assert before_job.status.value == "completed"
    assert before.normalization_assistance.status == "failed"
    assert before_job.result_payload["observation_id"] is None
    assert f.repo.list_memory_pattern_observations(limit=100) == []
    assert f.analyzer.requests[0].canonical_entities.file.file_path is None

    # A different run has usable cached facts. Explicit semantic recovery must
    # call the reviewer again, and its report must exclude this other run's cost.
    f.client.error = None
    f.client.facts = [fact("entities.host.host_name", "example", 'host="example"')]
    unrelated = f.factory(f.round).analyze(f.source, context=ServiceRequestContext(idempotency_key="unrelated-round"))
    assert unrelated.normalization_assistance.status == "applied"
    f.client.facts = [fact()]
    after_job = retry_semantics(f)
    after = f.repo.get_run(after_job.run_id)

    assert after_job.job_id == before_job.job_id
    assert after_job.attempt_count == 2
    assert after.run_id != before.run_id
    assert after.replay_of_run_id == before.run_id
    assert f.repo.get_run(before.run_id).model_dump(mode="json") == frozen
    assert after.normalization_assistance.status == "applied"
    assert "reused_from_run_id" not in after.normalization_assistance.metadata
    assert len(f.client.calls) == 3
    assert len(f.analyzer.requests) == 3
    assert f.analyzer.requests[-1].canonical_entities.file.file_path == r"D:\tools\yak.exe"
    assert after.analysis is not None and after.decision is not None
    assert f.executed_options[-1].refresh_normalization is True
    saved_round = f.service.store.get_round(f.round.round_id)
    assert saved_round.options == f.round.options
    assert saved_round.options.refresh_normalization is False
    assert len(f.service.store.list_rounds()) == 1
    assert after_job.result_payload["observation_id"]
    assert len(f.repo.list_memory_pattern_observations(limit=100)) == 1

    # Re-entering the same semantic attempt after a worker interruption reuses
    # the completed journal instead of charging the reviewer/analyzer twice.
    retry = CorpusSemanticRetry.model_validate(after_job.metadata["semantic_retry"])
    again = f.executor(f.round, f.member, context().model_copy(update={"idempotency_key": semantic_retry_key(after_job.idempotency_key, retry.generation)}), semantic_retry=retry)
    assert again.run_id == after.run_id
    assert len(f.client.calls) == len(f.analyzer.requests) == 3
    assert len(f.repo.list_memory_pattern_observations(limit=100)) == 1

    row = report_row(f)
    assert row["run_id"] == after.run_id
    assert {measurement["run_id"] for measurement in row["attempt_measurements"]} == {before.run_id, after.run_id}
    assert {measurement["run_id"]: measurement["total_duration_ms"] for measurement in row["attempt_measurements"]} == {run.run_id: run.total_duration_ms for run in (before, after)}
    metrics = summarize_results([row])["reuse"]
    assert metrics["completed"] == 1
    assert metrics["measured_run_attempts"] == 2
    assert metrics["provider_calls"] == sum(f.repo.get_run_measurements(run.run_id)["provider_call_count"] for run in (before, after))


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_repeat_semantic_failure_stays_visible_and_retains_every_attempt(tmp_path, batch):
    f = setup_runtime(tmp_path, semantic_error=TimeoutError("synthetic review timeout"), batch=batch)
    first = f.repo.get_run(current_job(f).run_id)
    second_job = retry_semantics(f)
    second = f.repo.get_run(second_job.run_id)
    third_job = retry_semantics(f)
    third = f.repo.get_run(third_job.run_id)

    assert second.replay_of_run_id == first.run_id
    assert third.replay_of_run_id == second.run_id
    assert all(run.normalization_assistance.status == "failed" and run.analysis is not None for run in (first, second, third))
    assert third_job.status.value == "completed" and third_job.attempt_count == 3
    assert f.repo.list_memory_pattern_observations(limit=100) == []
    row = report_row(f)
    assert row["run_id"] == third.run_id
    assert {m["run_id"] for m in row["attempt_measurements"]} == {first.run_id, second.run_id, third.run_id}
    assert next(step for step in row["summary"]["phase_timings"] if step["phase"] == "normalization_assist")["status"] == "failed"
    assert summarize_results([row])["reuse"]["measured_run_attempts"] == 3
    result = f.service.retry_items(f.round.round_id, CorpusRoundRetryCommand(kind="semantic_failed", expected_round_version=f.service.store.get_round(f.round.round_id).version), context=context())
    assert result["retried"] == 0 and result["not_retryable"] == 1
    assert len(f.client.calls) == len(f.analyzer.requests) == 3


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_model_failure_retry_keeps_round_creates_new_run_and_reuses_successful_semantic_facts(tmp_path, batch):
    f = setup_runtime(tmp_path, model_failure=True, batch=batch)
    before_job = current_job(f)
    before = f.repo.get_run(before_job.run_id)
    frozen = before.model_dump(mode="json")
    assert before_job.status.value == "failed"
    assert before.failure.retryable
    assert before.normalization_assistance.status == "applied"

    f.analyzer.fail = False
    result = f.service.retry_items(f.round.round_id, CorpusRoundRetryCommand(kind="failed", expected_round_version=f.service.store.get_round(f.round.round_id).version), context=context())
    assert result["retried"] == 1
    assert f.service.execute_one(f.round.round_id)
    assert not f.service.execute_one(f.round.round_id)
    after_job = current_job(f)
    after = f.repo.get_run(after_job.run_id)
    assert after_job.job_id == before_job.job_id
    assert after_job.status.value == "completed"
    assert after.run_id != before.run_id
    assert f.repo.get_run(before.run_id).model_dump(mode="json") == frozen
    assert len(f.service.store.list_rounds()) == 1
    assert len(f.analyzer.requests) == 2
    assert len(f.client.calls) == 1
    assert after.normalization_assistance.metadata["reused_from_run_id"] == before.run_id
    assert {m["run_id"] for m in report_row(f)["attempt_measurements"]} == {before.run_id, after.run_id}


@pytest.mark.parametrize("field,value", [("alert_id", "foreign-alert"), ("input_hash", "0" * 64), ("input_payload", {"alert_id": "foreign-alert"})])
@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_semantic_retry_rejects_source_identity_changes_before_provider(tmp_path, field, value, batch):
    f = setup_runtime(tmp_path, semantic_error=TimeoutError("synthetic review timeout"), batch=batch)
    run = f.repo.get_run(current_job(f).run_id)
    setattr(run, field, value)
    f.repo.save_run(run)
    with pytest.raises(CorpusExecutionError, match="source does not match") as raised:
        f.executor(f.round, f.member, context(), semantic_retry=CorpusSemanticRetry(generation=1, previous_run_id=run.run_id))
    assert raised.value.block_round
    assert len(f.client.calls) == len(f.analyzer.requests) == 1


def test_inherited_learning_semantic_failure_is_detectable_and_replayed(tmp_path, monkeypatch):
    from soc_agent.contracts.corpus_experiments import CorpusExperimentRebalance
    from soc_agent.demo.corpus_experiments import semantic_failed
    from soc_agent.demo.corpus_rebalance import learning_run_hash

    f = setup_runtime(tmp_path, semantic_error=TimeoutError("synthetic review timeout"), batch="learning")
    original = f.repo.get_run(current_job(f).run_id)
    original.model_name = "synthetic-reviewed-model"
    f.repo.save_run(original)
    store = f.repo.corpus_experiments()
    saved_experiment = store.get_experiment(f.round.experiment_id)
    imported_experiment = saved_experiment.model_copy(
        update={
            "rebalance": CorpusExperimentRebalance(
                parent_experiment_id="EXP-parent",
                plan_hash="a" * 64,
                learning_run_imports={f.member.alert_id: original.run_id},
                learning_run_hashes={f.member.alert_id: learning_run_hash(original)},
            )
        }
    )
    monkeypatch.setattr(store, "get_experiment", lambda _: imported_experiment)
    monkeypatch.setattr(f.repo, "corpus_experiments", lambda: store)
    imported = f.executor(f.round, f.member, context())
    assert imported.summary["phase_timings"] == []
    assert imported.summary["total_duration_ms"] == 0
    assert semantic_failed(SimpleNamespace(result_payload={"summary": imported.summary}))
    old_calls = len(f.analyzer.requests)
    f.client.error = None
    replayed = f.executor(f.round, f.member, context().model_copy(update={"idempotency_key": "inherited-semantic-retry"}), semantic_retry=CorpusSemanticRetry(generation=1, previous_run_id=original.run_id))
    assert replayed.run_id != original.run_id
    assert len(f.analyzer.requests) == old_calls + 1
    assert "inherited_from_run_id" not in replayed.summary
    assert f.repo.get_run(replayed.run_id).normalization_assistance.status == "applied"
