from types import SimpleNamespace

from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context
from test_soc_memory_patterns import _run

from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiment_runtime import CorpusRuntimeExecutor
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.utils.hashing import stable_hash


def test_runtime_executor_uses_actual_pattern_service_and_second_batch_never_learns(tmp_path):
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
    assert repo.list_memory_pattern_observations(limit=100) == old
    assert all(i.job.result_payload["pattern_reason"] == "validation_learning_disabled" for i in svc.store.list_round_items(validation.round_id).items)


def test_payload_mismatch_blocks_before_runtime(tmp_path):
    import pytest

    from soc_agent.demo.corpus_experiments import CorpusExecutionError

    repo = repository(tmp_path)
    executor = CorpusRuntimeExecutor(repository=repo, load_payload=lambda member: {"changed": True}, analysis_factory=lambda _: pytest.fail("must not invoke Runtime"), profile_registry=SocMemoryProfileRegistry())
    with pytest.raises(CorpusExecutionError, match="payload"):
        executor(None, members()[0], context())


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
