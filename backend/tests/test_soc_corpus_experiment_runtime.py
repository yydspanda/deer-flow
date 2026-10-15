from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context
from test_soc_memory_patterns import _run

from soc_agent.contracts.corpus_experiments import CorpusRound, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor, accumulate_corpus_run, corpus_accumulation_scope
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


def test_runtime_executor_uses_actual_pattern_service_across_both_batches(tmp_path):
    repo = repository(tmp_path)
    rows = members()
    runs = {}
    payloads = {}
    for row in rows:
        run = _run(row.source_index, tenant_id="pingan")
        run.alert_id = row.alert_id
        run.input_payload = {"alert_id": row.alert_id, "event_time": row.event_time.isoformat()}
        run.llm_analysis_request.alert_id = row.alert_id
        run.llm_analysis_request.environment = "dev-corpus-eval"
        run.input_hash = stable_hash(run.input_payload)
        runs[row.alert_id] = run
        payloads[row.alert_id] = run.input_payload
    repo.corpus_experiments().prepare(experiment(), [row.model_copy(update={"payload_hash": runs[row.alert_id].input_hash}) for row in rows])
    calls = []
    saved_keys = {}

    def analyze(payload, *, context):
        if context.idempotency_key in saved_keys:
            return saved_keys[context.idempotency_key]
        run = runs[payload["alert_id"]].model_copy(deep=True)
        calls.append(context.idempotency_key)
        repo.save_run(run)
        saved_keys[context.idempotency_key] = run
        return run

    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda member: payloads[member.alert_id], analysis_factory=lambda round_: SimpleNamespace(analyze=analyze), profile_registry=SocMemoryProfileRegistry())
    svc = SocCorpusExperimentService(repository=repo, execute=executor, configuration_provider=lambda options: {"mock": True})
    learn = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", group_ids=["group-a"])), context=context())
    svc.start(learn.round_id, context=context())
    while svc.execute_one(learn.round_id):
        pass
    items = svc.store.list_round_items(learn.round_id).items
    assert all(item.job.status.value == "completed" for item in items)
    assert [item.job.result_payload["summary"]["pattern_support_count"] for item in items] == [1, 2, 3, 4, 5]
    assert items[-1].job.result_payload["candidate_id"]
    old = repo.list_memory_pattern_observations(limit=100)
    candidate = repo.get_memory_candidate(items[-1].job.result_payload["candidate_id"])
    assert candidate.source.metadata["experiment_id"] == "EXP-test"
    assert candidate.status.value == "pending_review"
    validation = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation"), memory_mode="none"), context=context())
    svc.start(validation.round_id, context=context())
    while svc.execute_one(validation.round_id):
        pass
    assert len(calls) == 7
    validated = svc.store.list_round_items(validation.round_id).items
    assert sorted(item.job.result_payload["summary"]["pattern_support_count"] for item in validated) == [6, 7]
    observations = repo.list_memory_pattern_observations(limit=100)
    assert len(observations) == 7
    assert {item.source.alert_id for item in observations} == {"0", "1", "2", "3", "4", "5", "11"}
    assert len({item.aggregation_key for item in observations}) == 1
    assert all(item.lesson is not None for item in observations)
    assert all(repo.get_memory_pattern_observation(item.observation_id) == item for item in old)
    assert validation.pattern_accumulation_scope == learn.pattern_accumulation_scope
    assert repo.list_memory_records() == []


def test_manually_saved_legacy_validation_round_does_not_accumulate(tmp_path):
    repo = repository(tmp_path)
    legacy = CorpusRound(
        round_id="ROUND-legacy-validation",
        experiment_id="EXP-test",
        selection=CorpusRoundSelection(batch="validation"),
        config_hash="d" * 64,
        config_snapshot={},
        created_by="operator",
        created_at=experiment().created_at,
    )
    repo.corpus_experiments().prepare(experiment(), members())
    repo.corpus_experiments().create_round(legacy)
    assert repo.corpus_experiments().get_round(legacy.round_id).pattern_accumulation_scope is None
    result = accumulate_corpus_run(repository=repo, profiles=SocMemoryProfileRegistry(), round_=legacy, member=members()[5], run=_run(5, tenant_id="pingan"), context=context())
    assert result.pattern_reason == "validation_learning_disabled"
    assert result.observation_id is None and result.candidate_id is None
    assert repo.list_memory_pattern_observations(limit=100) == []


def test_payload_mismatch_blocks_before_runtime(tmp_path):
    import pytest

    from soc_agent.demo.corpus_experiments import CorpusExecutionError

    repo = repository(tmp_path)
    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda member: {"changed": True}, analysis_factory=lambda _: pytest.fail("must not invoke Runtime"), profile_registry=SocMemoryProfileRegistry())
    with pytest.raises(CorpusExecutionError, match="payload"):
        executor(None, members()[0], context())


def _failed_normalization_execution():
    from soc_agent.core.runtime import analyze_alert

    payload = {"alert_id": "test-invalid", "entities": {"supplementary_facts": [{"invalid": True}]}}
    run = analyze_alert(payload)
    assert run.alert_id == "unknown" and run.failure.step_name == "normalize"
    member = members()[0].model_copy(update={"alert_id": "test-invalid", "payload_hash": stable_hash(payload)})
    store = SimpleNamespace(get_experiment=lambda _: None)
    repo = SimpleNamespace(corpus_experiments=lambda: store)
    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda _: payload, analysis_factory=lambda _: SimpleNamespace(analyze=lambda *a, **k: run), profile_registry=SocMemoryProfileRegistry())
    return executor, SimpleNamespace(experiment_id="EXP-test"), member, run


def test_pre_identity_normalization_failure_reports_real_error_without_blocking_batch():
    from soc_agent.demo.corpus_experiments import CorpusExecutionError

    executor, round_, member, run = _failed_normalization_execution()
    with pytest.raises(CorpusExecutionError) as raised:
        executor(round_, member, context())
    assert str(raised.value) == run.failure.message
    assert raised.value.run_id == run.run_id
    assert not raised.value.block_round
    assert not raised.value.retryable
    assert run.alert_id == "unknown"  # Never manufacture a normalized identity.


@pytest.mark.parametrize("change", ["foreign_identity", "foreign_hash", "foreign_payload", "later_failure", "missing_failure", "normalized_result", "successful_run"])
def test_pre_identity_exception_cannot_hide_foreign_or_inconsistent_results(change):
    from soc_agent.contracts import AnalysisRunStatus
    from soc_agent.demo.corpus_experiments import CorpusExecutionError

    executor, round_, member, run = _failed_normalization_execution()
    if change == "foreign_identity":
        run.alert_id = "another-alert"
    elif change == "foreign_hash":
        run.input_hash = "0" * 64
    elif change == "foreign_payload":
        run.input_payload = {"alert_id": "another-alert"}
    elif change == "later_failure":
        run.failure = run.failure.model_copy(update={"step_name": "analyze_llm"})
    elif change == "missing_failure":
        run.failure = None
    elif change == "normalized_result":
        from soc_agent.normalizers import normalize_alert_payload

        run.normalized_alert = normalize_alert_payload({"alert_id": "test-invalid"})
    else:
        run.status = AnalysisRunStatus.SUCCESS
    with pytest.raises(CorpusExecutionError, match="different alert identity") as raised:
        executor(round_, member, context())
    assert raised.value.block_round


def test_normalization_failure_keeps_next_validation_member_dispatchable(tmp_path):
    from soc_agent.core import SocAnalysisService

    repo = repository(tmp_path)
    payloads = {
        "5": {"alert_id": "5", "tenant_id": "pingan", "entities": {"supplementary_facts": [{"invalid": True}]}},
        "11": {"alert_id": "11", "tenant_id": "pingan"},
    }
    rows = [row.model_copy(update={"payload_hash": stable_hash(payloads[row.alert_id])}) for row in members() if row.batch == "validation"]
    repo.corpus_experiments().prepare(experiment(), rows)
    runtime = SocAnalysisService(repository=repo, audit_repository=repo, analysis_persistence=repo)
    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda member: payloads[member.alert_id], analysis_factory=lambda _: runtime, profile_registry=SocMemoryProfileRegistry())
    service = SocCorpusExperimentService(repository=repo, execute=executor, configuration_provider=lambda _: {})
    round_ = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation"), memory_mode="none"), context=context())
    service.start(round_.round_id, context=context())
    assert service.execute_one(round_.round_id)
    assert service.store.get_round(round_.round_id).state == "running"
    assert service.execute_one(round_.round_id)
    items = {item.alert_id: item.job for item in service.store.list_round_items(round_.round_id).items}
    assert items["5"].status.value == "failed"
    assert items["11"].status.value == "completed"
    failed_run = next(run for run in repo.list_runs(limit=10) if run.status.value == "failed")
    assert failed_run.failure.step_name == "normalize"
    assert items["5"].error_message == failed_run.failure.message[:2000]
    assert items["5"].run_id == failed_run.run_id


def test_runtime_accumulation_scope_uses_frozen_continuous_scope_and_legacy_restart_generation(tmp_path):
    repo = repository(tmp_path)
    repo.corpus_experiments().prepare(experiment(), members())
    service = SocCorpusExperimentService(repository=repo, execute=lambda *_: None, configuration_provider=lambda _: {})
    round_ = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning")), context=context())
    continuous = corpus_accumulation_scope(repo, round_)
    assert continuous == round_.pattern_accumulation_scope
    assert continuous.accumulation_mode == "continuous"
    assert continuous.configuration_hash is None
    legacy = round_.model_copy(update={"pattern_accumulation_scope": None})
    original = corpus_accumulation_scope(repo, legacy)
    generation = "ROUND-RESTART-learning"
    restarted = corpus_accumulation_scope(repo, legacy.model_copy(update={"learning_generation": generation}))
    assert original.learning_generation is None
    assert restarted.learning_generation == generation
    assert restarted.experiment_id == original.experiment_id
    assert restarted.configuration_hash == original.configuration_hash
    assert (restarted.event_start, restarted.event_end) == (original.event_start, original.event_end)


def test_runtime_executor_recovers_durable_journal_without_repeating_saved_result(tmp_path):
    import pytest
    from test_soc_agent_repository import _sample

    from soc_agent.contracts import AnalysisRunStatus, ServiceRequestContext
    from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService

    class ProcessLost(BaseException):
        pass

    class InterruptedAnalyzer:
        step_name = "analyze_llm"
        model_name = "mock-interrupted"
        prompt_version = "mock-v1"

        def analyze(self, request):
            raise ProcessLost

    repo = repository(tmp_path)
    payload = _sample("approved_scanner.json")
    request = ServiceRequestContext(idempotency_key="corpus:round:中文恢复")
    crashed = SocAnalysisService(runtime=DeterministicAnalysisRuntime(analyzer=InterruptedAnalyzer()), repository=repo, audit_repository=repo, analysis_persistence=repo)
    with pytest.raises(ProcessLost):
        crashed.analyze(payload, context=request)
    original = repo.list_runs(limit=1)[0]
    member = members()[0].model_copy(update={"alert_id": original.alert_id, "payload_hash": stable_hash(payload)})
    repo.corpus_experiments().prepare(experiment(), [member])
    coordinator = SocCorpusExperimentService(repository=repo, execute=lambda *_: None, configuration_provider=lambda _: {})
    round_ = coordinator.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning")), context=context())
    round_ = round_.model_copy(update={"selection": CorpusRoundSelection(batch="validation")})
    service = SocAnalysisService(repository=repo, audit_repository=repo, analysis_persistence=repo)
    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda _: payload, analysis_factory=lambda _: service, profile_registry=SocMemoryProfileRegistry())

    recovered = executor(round_, member, request)
    assert recovered.run_id != original.run_id
    assert repo.get_run(original.run_id).status == AnalysisRunStatus.INTERRUPTED
    assert repo.get_run(recovered.run_id).replay_of_run_id == original.run_id
    again = executor(round_, member, request)
    assert again.run_id == recovered.run_id
    assert len(repo.list_runs(limit=10)) == 2
