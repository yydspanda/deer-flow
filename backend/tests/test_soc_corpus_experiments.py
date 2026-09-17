"""No live calls: exercise two-stage scheduling through the durable SOC queue."""

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository

from soc_agent.contracts import ActorContext, EntrySurface, ServiceRequestContext
from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusRetestProvenance, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService
from soc_agent.utils.hashing import stable_hash


def context():
    return ServiceRequestContext(actor=ActorContext(actor_id="operator", roles=["soc_admin"], surface=EntrySurface.TEST))


def service(repo, calls, config=None):
    config = config if config is not None else {"model": "fake", "version": 1}

    def execute(round_, member, request_context):
        calls.append((round_.selection.batch, member.alert_id, tuple(e.memory_id for e in round_.memory_snapshot), request_context.idempotency_key))
        return CorpusExecutionOutcome(run_id=f"RUN-{round_.round_id}-{member.alert_id}", observation_id=f"OBS-{member.alert_id}" if round_.selection.batch == "learning" else None, summary={"mocked": True, "final_verdict": "false_positive"})

    return SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda options: config)


def prepare(repo):
    repo.corpus_experiments().prepare(experiment(), members())


def test_retest_parent_must_exist_in_the_same_experiment(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    svc = service(repo, [])
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"))
    with pytest.raises(ValueError, match="parent round"):
        svc.create_round(command.model_copy(update={"parent_round_id": "ROUND-missing"}), context=context())
    assert repo.corpus_experiments().list_rounds() == []
    parent = svc.create_round(command, context=context())
    child = svc.create_round(command.model_copy(update={"parent_round_id": parent.round_id}), context=context())
    assert child.parent_round_id == parent.round_id
    repo.corpus_experiments().prepare(experiment().model_copy(update={"experiment_id": "EXP-other"}), members())
    with pytest.raises(ValueError, match="parent round must belong to this experiment"):
        svc.create_round(command.model_copy(update={"experiment_id": "EXP-other", "parent_round_id": parent.round_id}), context=context())


def test_report_retest_provenance_is_persisted_and_checked_before_submission(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    selection = CorpusRoundSelection(batch="learning", alert_ids=["0"])
    parent = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=selection), context=context())
    provenance = CorpusRetestProvenance(parent_round_id=parent.round_id, source_identity=experiment().source_identity, report_sha256="f" * 64, selection_hash=stable_hash(selection.model_dump(mode="json")))
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=selection, parent_round_id=parent.round_id, retest_provenance=provenance)
    child = svc.create_round(command, context=context())
    assert svc.store.get_round(child.round_id).retest_provenance == provenance
    assert svc.store.list_round_items(child.round_id).items[0].alert_id == "0"
    for changed in (
        command.model_copy(update={"retest_provenance": provenance.model_copy(update={"source_identity": {"sha256": "wrong-data"}})}),
        command.model_copy(update={"selection": CorpusRoundSelection(batch="learning", alert_ids=["1"])}),
        command.model_copy(update={"selection": CorpusRoundSelection(batch="validation", alert_ids=["5"]), "memory_mode": "none"}),
        command.model_copy(update={"parent_round_id": None}),
    ):
        with pytest.raises(ValueError, match="retest"):
            svc.create_round(changed, context=context())
    assert len(svc.store.list_rounds()) == 2
    assert calls == []


def test_learning_budget_pause_resume_and_recovery_do_not_create_second_task(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), execution_limit=2)
    first = svc.create_round(command, context=context())
    assert first.memory_snapshot == []
    svc.start(first.round_id, context=context())
    assert svc.execute_one(first.round_id)
    svc.pause(first.round_id, context=context())
    assert svc.execute_one(first.round_id) is False
    resumed = service(repo, calls)
    resumed.start(first.round_id, context=context())
    assert resumed.execute_one(first.round_id)
    assert resumed.execute_one(first.round_id) is False
    progress = repo.corpus_experiments().round_progress(first.round_id)
    assert progress.round.state == "completed"
    assert progress.completed_count == 2
    assert progress.selected_count == 10
    resumed.start(first.round_id, context=context(), execution_limit=5)
    while resumed.execute_one(first.round_id):
        pass
    assert len(calls) == 5
    assert len({row[1] for row in calls}) == 5
    assert len({row[3] for row in calls}) == 5
    assert repo.corpus_experiments().round_progress(first.round_id).completed_count == 5


def test_config_drift_blocks_new_claims_and_cannot_resume_old_snapshot(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls, config = [], {"model": "fake", "version": 1}
    svc = service(repo, calls, config)
    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning")), context=context())
    svc.start(round_.round_id, context=context())
    config["version"] = 2
    assert svc.execute_one(round_.round_id) is False
    assert not calls
    assert repo.corpus_experiments().get_round(round_.round_id).state == "blocked"
    with pytest.raises(ValueError, match="new round"):
        svc.start(round_.round_id, context=context())


def test_validation_requires_reviewed_first_batch_memory_or_explicit_baseline(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", scope="reuse"))
    with pytest.raises(ValueError, match="reviewed"):
        svc.create_round(command, context=context())
    baseline = svc.create_round(command.model_copy(update={"memory_mode": "none"}), context=context())
    svc.start(baseline.round_id, context=context())
    while svc.execute_one(baseline.round_id):
        pass
    items = repo.corpus_experiments().list_round_items(baseline.round_id).items
    assert len(items) == 2
    assert all(item.job.result_payload["observation_id"] is None for item in items)
    assert all(c[0] == "validation" for c in calls)
    assert all(not repo.processing_jobs().list_callbacks(item.job_id) for item in items)


def test_non_admin_cannot_start_experiment_and_prepared_job_is_not_consumed(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    svc = service(repo, [])
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"))
    with pytest.raises(PermissionError):
        svc.create_round(command, context=ServiceRequestContext())
    round_ = svc.create_round(command, context=context())
    assert svc.execute_one(round_.round_id) is False


def test_create_round_request_replay_never_submits_another_batch(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    svc = service(repo, [])
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"))
    request = context().model_copy(update={"idempotency_key": "browser-submit-1"})
    first = svc.create_round(command, context=request)
    assert svc.create_round(command, context=request) == first
    assert len(svc.store.list_rounds(experiment_id="EXP-test")) == 1
    with pytest.raises(ValueError, match="different"):
        svc.create_round(command.model_copy(update={"execution_limit": 50}), context=request)


def test_bulk_round_creation_rolls_back_all_chunks_on_failure(tmp_path, monkeypatch):
    from soc_agent.db.corpus_experiments import SqlAlchemyCorpusExperimentRepository

    repo = repository(tmp_path)
    base = members()[0]
    rows = [base.model_copy(update={"alert_id": str(i), "source_index": i, "sequence_number": i}) for i in range(501)]
    repo.corpus_experiments().prepare(experiment(), rows)
    svc = service(repo, [])
    original = SqlAlchemyCorpusExperimentRepository.attach_jobs
    chunks = []

    def fail_second_chunk(self, round_id, assignments):
        chunks.append(len(assignments))
        if len(chunks) == 2:
            raise RuntimeError("synthetic interrupted bulk insert")
        return original(self, round_id, assignments)

    request = context().model_copy(update={"idempotency_key": "atomic-round"})
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"))
    monkeypatch.setattr(SqlAlchemyCorpusExperimentRepository, "attach_jobs", fail_second_chunk)
    with pytest.raises(RuntimeError, match="interrupted"):
        svc.create_round(command, context=request)
    assert chunks == [500, 1]
    assert svc.store.list_rounds(experiment_id="EXP-test") == []
    assert repo.processing_jobs().claim_next(queue_name="deepseek-v4-flash", workload_kind="corpus_experiment", worker_id="check", lease_seconds=30) is None
    monkeypatch.setattr(SqlAlchemyCorpusExperimentRepository, "attach_jobs", original)
    round_ = svc.create_round(command, context=request)
    assert svc.store.round_progress(round_.round_id).selected_count == 501


def test_multiple_dispatchers_share_global_batch_budget(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    repo = repository(tmp_path)
    prepare(repo)
    entered, release = Event(), Event()

    def execute(round_, member, request_context):
        entered.set()
        assert release.wait(10)
        return CorpusExecutionOutcome(run_id="RUN-mock")

    svc = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda _: {}, max_concurrency=1)
    rounds = [svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", group_ids=[group]), concurrency=1), context=context()) for group in ["group-a", "group-b"]]
    for round_ in rounds:
        svc.start(round_.round_id, context=context())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(svc.execute_one, rounds[0].round_id)
        assert entered.wait(10)
        try:
            peer = SocCorpusExperimentService(repository=repo, execute=lambda *_: pytest.fail("shared server capacity exceeded"), configuration_provider=lambda _: {}, max_concurrency=1)
            assert peer.execute_one(rounds[1].round_id) is False
        finally:
            release.set()
        assert first.result(10)


def reviewed_memory(repo):
    from test_soc_memory_experiment_accumulation import _experimental
    from test_soc_memory_patterns import _observe, _run
    from test_soc_memory_retrieval_v2 import _record

    pattern = _experimental(repo)
    for index in range(5):
        result = _observe(pattern, _run(index, tenant_id="pingan"), transport_ref=f"mock:{index}")
    assert result.candidate is not None
    record = _record("MEM-LEARNING", facets={"environment": ["dev-corpus-eval"]}).model_copy(update={"source_candidate_id": result.candidate.candidate_id})
    repo.save_memory_record(record)
    return record


def test_validation_freezes_reviewed_collection_and_governance_changes_stop_dispatch(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    record = reviewed_memory(repo)
    calls = []
    svc = service(repo, calls)
    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation")), context=context())
    assert [e.memory_id for e in round_.memory_snapshot] == [record.memory_id]
    svc.start(round_.round_id, context=context())
    assert svc.execute_one(round_.round_id)
    repo.save_memory_record(record.model_copy(update={"retrieval_enabled": False, "version": record.version + 1}))
    assert svc.execute_one(round_.round_id) is False
    assert svc.store.get_round(round_.round_id).state == "blocked"
    assert len(calls) == 1
    assert svc.store.round_progress(round_.round_id).completed_count == 1


def test_new_reviewed_memory_does_not_change_running_learning_collection(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning")), context=context())
    svc.start(round_.round_id, context=context())
    reviewed_memory(repo)
    assert svc.execute_one(round_.round_id)
    assert calls[0][2] == ()


def test_midflight_snapshot_change_preserves_result_but_marks_it_incomparable(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    config = {"version": 1}

    def execute(round_, member, ctx):
        config["version"] = 2
        return CorpusExecutionOutcome(run_id="RUN-audit-kept")

    svc = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda options: config)
    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning")), context=context())
    svc.start(round_.round_id, context=context())
    assert svc.execute_one(round_.round_id)
    item = svc.store.list_round_items(round_.round_id).items[0]
    assert item.job.run_id == "RUN-audit-kept"
    assert item.job.result_payload["snapshot_changed_during_run"] is True
    assert svc.store.get_round(round_.round_id).state == "blocked"


def test_server_dispatcher_continues_without_a_cli_and_restart_does_not_repeat_completed_jobs(tmp_path):
    import time

    from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher

    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), execution_limit=2), context=context())
    svc.start(round_.round_id, context=context())
    dispatcher = CorpusExperimentDispatcher(svc, interval_seconds=0.05)
    try:
        dispatcher.start()
        deadline = time.monotonic() + 15
        while svc.store.get_round(round_.round_id).state != "completed" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert svc.store.get_round(round_.round_id).state == "completed"
    finally:
        dispatcher.stop()
    assert len(calls) == 2
    try:
        dispatcher.start()
        time.sleep(0.15)
    finally:
        dispatcher.stop()
    assert len(calls) == 2


def test_failed_retry_keeps_job_identity_and_obeys_attempt_budget(tmp_path):
    from soc_agent.demo.corpus_experiments import CorpusExecutionError

    repo = repository(tmp_path)
    prepare(repo)
    calls = []

    def execute(round_, member, ctx):
        calls.append(ctx.idempotency_key)
        raise CorpusExecutionError("temporary failure", retryable=True, run_id="RUN-saved")

    svc = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda options: {"mock": True})
    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=["0"])), context=context())
    svc.start(round_.round_id, context=context())
    assert svc.execute_one(round_.round_id)
    original = svc.store.list_round_items(round_.round_id).items[0].job_id
    for _ in range(2):
        assert svc.retry_failed(round_.round_id, context=context())["retried"] == 1
        assert svc.execute_one(round_.round_id)
    assert svc.retry_failed(round_.round_id, context=context()) == {"retried": 0, "not_retryable": 1}
    item = svc.store.list_round_items(round_.round_id).items[0]
    assert item.job_id == original
    assert item.job.run_id == "RUN-saved"
    assert len(calls) == 3
    assert len(set(calls)) == 1
    assert [event.event_type for event in svc.jobs.list_events(original)].count("operator_retry") == 2
