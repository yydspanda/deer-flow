"""Batch operations preserve latest attempts without asking operators for round IDs."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, repository
from test_soc_corpus_experiments import context, prepare, reviewed_memory, service

from soc_agent.contracts import SocMemoryRetrievalActivationCommand
from soc_agent.contracts.corpus_experiments import CorpusBatchRetryCommand, CorpusQuickCommand
from soc_agent.core import SocMemoryService
from soc_agent.db.corpus_experiments import CorpusExperimentConflict
from soc_agent.demo.corpus_batch_operations import CorpusBatchOperations
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation


def setup(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    plan = SimpleNamespace(plan_id=experiment().plan_id)
    quick = CorpusQuickValidation(svc, plan)
    return svc, quick, CorpusBatchOperations(svc, plan), calls


def change_governance(repo, memory_id):
    memory = SocMemoryService(record_repository=repo)
    record = repo.get_memory_record(memory_id)
    disabled = memory.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(memory_id=memory_id, action="disable", expected_record_version=record.version, reason="Operator temporary revocation."),
        context=context(),
    ).record
    memory.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=memory_id, action="enable", expected_record_version=disabled.version, reason="Operator restored the reviewed record.", activation_valid_until=datetime.now(UTC) + timedelta(days=30), review_after_days=7
        ),
        context=context(),
    )


def test_batch_results_token_and_latest_failure_never_fall_back_to_old_success(tmp_path):
    svc, quick, batch, calls = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    original = svc.store.list_rounds()[0]
    while svc.execute_one(original.round_id):
        pass
    before = batch.snapshot("learning")
    assert before["completed"] == 10
    assert before["stage_scope"]["stage_complete"]
    quick.command(CorpusQuickCommand(batch="learning", action="rerun", alert_id="0"), context=context().model_copy(update={"idempotency_key": "second"}))
    latest = svc.store.list_rounds()[-1]
    svc._execute = lambda *args: (_ for _ in ()).throw(ValueError("no Run was saved"))
    assert svc.execute_one(latest.round_id)
    after = batch.snapshot("learning")
    assert after["failed"] == 1 and after["completed"] == 9
    assert after["report_token"] != before["report_token"]
    assert not after["stage_scope"]["stage_complete"]
    rows = batch.results("learning", limit=100)["items"]
    assert rows[0]["status"] == "failed" and rows[0]["run_id"] is None
    assert rows[0]["source_round_id"] == latest.round_id
    with pytest.raises(CorpusExperimentConflict, match="变化"):
        batch.results("learning", report_token=before["report_token"])


def test_continue_recovers_memory_change_without_rerunning_saved_success(tmp_path):
    svc, quick, batch, calls = setup(tmp_path)
    record = reviewed_memory(svc.repository)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    parent = svc.store.list_rounds()[-1]
    assert svc.execute_one(parent.round_id)
    memory = SocMemoryService(record_repository=svc.repository)
    memory.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(memory_id=record.memory_id, action="disable", expected_record_version=record.version, reason="Operator revoked reviewed memory."),
        context=context(),
    )
    assert not svc.execute_one(parent.round_id)
    pending = [item.alert_id for item in svc.store.list_round_items(parent.round_id).items if item.job.status.value == "queued"]
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    child = svc.store.list_rounds()[-1]
    assert child.parent_round_id == parent.round_id
    assert child.selection.alert_ids == pending
    assert child.memory_mode == "none"
    assert child.governance_hash == svc.store.governance_hash("pingan")
    assert svc.execute_one(child.round_id)
    final = batch.snapshot("validation", include_sources=True)
    assert final["completed"] == 2 and final["remaining"] == 0
    assert final["stage_scope"]["stage_complete"]
    assert len(final["source_rounds"]) == 2
    assert len(calls) == 2 and len({call[1] for call in calls}) == 2
    assert svc.store.get_round(parent.round_id).memory_snapshot == parent.memory_snapshot


def test_batch_retry_uses_only_latest_failed_jobs_and_selected_concurrency(tmp_path):
    from soc_agent.demo.corpus_experiments import CorpusExecutionError

    svc, quick, batch, calls = setup(tmp_path)
    execute = svc._execute
    svc._execute = lambda *args: (_ for _ in ()).throw(CorpusExecutionError("temporary model failure", retryable=True))
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    original = svc.store.list_rounds()[0]
    assert svc.execute_one(original.round_id)
    failed_alert = next(item.alert_id for item in svc.store.list_round_items(original.round_id).items if item.job.status.value == "failed")
    quick.command(CorpusQuickCommand(batch="learning", action="pause"), context=context())
    svc._execute = execute
    quick.command(CorpusQuickCommand(batch="learning", action="rerun", alert_id=failed_alert), context=context().model_copy(update={"idempotency_key": "manual-success"}))
    manual = svc.store.list_rounds()[-1]
    assert svc.execute_one(manual.round_id)
    assert batch.retry("learning", CorpusBatchRetryCommand(kind="failed", concurrency=2), context=context())["retried"] == 0
    assert svc.store.round_jobs_for_alerts(original.round_id, [failed_alert])[failed_alert].status.value == "failed"


def test_restart_masks_all_old_results_in_batch_report(tmp_path):
    svc, quick, batch, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    old = svc.store.list_rounds()[0]
    while svc.execute_one(old.round_id):
        pass
    token = quick.snapshot("learning")["restart_token"]
    quick.command(CorpusQuickCommand(batch="learning", action="restart_learning", restart_token=token), context=context().model_copy(update={"idempotency_key": f"restart-learning-{token}"}))
    snapshot = batch.snapshot("learning")
    assert snapshot["completed"] == 0 and snapshot["remaining"] == 10
    assert all(row["run_id"] is None for row in batch.results("learning")["items"])


@pytest.mark.parametrize("explicit_pause", [False, True])
def test_dispatcher_recovers_changed_memory_but_respects_explicit_batch_pause(tmp_path, explicit_pause):
    from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher

    svc, quick, batch, calls = setup(tmp_path)
    record = reviewed_memory(svc.repository)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    parent = svc.store.list_rounds()[-1]
    assert svc.execute_one(parent.round_id)
    SocMemoryService(record_repository=svc.repository).set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(memory_id=record.memory_id, action="disable", expected_record_version=record.version, reason="Operator disabled this memory."),
        context=context(),
    )
    assert not svc.execute_one(parent.round_id)
    if explicit_pause:
        quick.command(CorpusQuickCommand(batch="validation", action="pause"), context=context())
    dispatcher = CorpusExperimentDispatcher(svc)
    dispatcher._continue_changed_memory()
    assert len(svc.store.list_rounds()) == (1 if explicit_pause else 2)
    if not explicit_pause:
        child = svc.store.list_rounds()[-1]
        assert child.state == "running"
        assert svc.store.list_memory_continuation_rounds() == []
        assert svc.execute_one(child.round_id)
        assert batch.snapshot("validation")["completed"] == 2
        dispatcher._continue_changed_memory()
        assert len(svc.store.list_rounds()) == 2


@pytest.mark.parametrize("kind", ["failed", "semantic_failed"])
def test_batch_retry_targets_current_failure_and_honors_concurrency(tmp_path, kind):
    from soc_agent.demo.corpus_experiments import CorpusExecutionError

    svc, quick, batch, calls = setup(tmp_path)
    execute = svc._execute

    def initial(round_, member, request):
        if kind == "failed":
            raise CorpusExecutionError("temporary model error", retryable=True)
        outcome = execute(round_, member, request)
        return outcome.model_copy(update={"summary": {"normalization_assist_status": "failed"}})

    svc._execute = initial
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    saved = svc.store.list_rounds()[0]
    assert svc.execute_one(saved.round_id)
    quick.command(CorpusQuickCommand(batch="learning", action="pause"), context=context())
    svc._execute = lambda round_, member, request, **kwargs: execute(round_, member, request)
    result = batch.retry("learning", CorpusBatchRetryCommand(kind=kind, concurrency=2), context=context())
    assert result["retried"] == 1
    assert svc.store.get_round(saved.round_id).concurrency == 2
    assert len(svc.store.list_rounds()) == 1
    assert svc.execute_one(saved.round_id)
    final = batch.snapshot("learning")
    assert final["failed"] == 0 and final["semantic_failed_count"] == 0
    assert final["completed"] == 1 and final["remaining"] == 9


def test_snapshot_changed_during_analysis_is_automatically_replaced_not_reported_stable(tmp_path):
    from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher

    svc, quick, batch, _ = setup(tmp_path)
    record = reviewed_memory(svc.repository)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    parent = svc.store.list_rounds()[-1]
    execute = svc._execute

    def change_during_analysis(*args):
        SocMemoryService(record_repository=svc.repository).set_retrieval_activation(
            SocMemoryRetrievalActivationCommand(memory_id=record.memory_id, action="disable", expected_record_version=record.version, reason="Operator disabled memory while a call was running."),
            context=context(),
        )
        return execute(*args)

    svc._execute = change_during_analysis
    assert svc.execute_one(parent.round_id)
    assert not batch.snapshot("validation")["stage_scope"]["stage_complete"]
    svc._execute = execute
    CorpusExperimentDispatcher(svc)._continue_changed_memory()
    child = svc.store.list_rounds()[-1]
    assert len(child.selection.alert_ids) == 2
    while svc.execute_one(child.round_id):
        pass
    assert batch.snapshot("validation")["stage_scope"]["stage_complete"]
    assert all(not item["snapshot_changed_during_run"] for item in batch.results("validation")["items"])


def test_batch_api_keeps_source_details_optional_and_checks_token(tmp_path):
    from test_soc_corpus_experiment_api import client

    http, app, _ = client(tmp_path)
    plan = SimpleNamespace(plan_id=experiment().plan_id)
    app.workbench = SimpleNamespace(batch_plan=plan)
    quick = CorpusQuickValidation(app.service, plan)
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    root = "/api/soc/dev/corpus-workbench/batches/learning"
    initial = http.get(root).json()
    assert initial["total"] == 10
    assert "memory_snapshot" not in initial["source_rounds"][0]
    assert "memory_snapshot" in http.get(root, params={"include_sources": "true"}).json()["source_rounds"][0]
    assert http.get(root + "/results", params={"report_token": initial["report_token"]}).status_code == 200
    assert http.get(root, headers={"test-role": "user"}).status_code == 403
    assert app.service.execute_one(app.service.store.list_rounds()[0].round_id)
    assert http.get(root + "/results", params={"report_token": initial["report_token"]}).status_code == 409


def test_automatic_continuations_are_bounded_but_unclaimed_work_can_continue_explicitly(tmp_path):
    from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher

    svc, quick, _, calls = setup(tmp_path)
    record = reviewed_memory(svc.repository)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    dispatcher = CorpusExperimentDispatcher(svc)
    for depth in range(4):
        current = svc.store.list_rounds()[-1]
        assert current.memory_continuation_depth == depth
        change_governance(svc.repository, record.memory_id)
        assert not svc.execute_one(current.round_id)
        dispatcher._continue_changed_memory()
    assert len(svc.store.list_rounds()) == 4
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    resumed = svc.store.list_rounds()[-1]
    assert resumed.memory_continuation_depth == 4 and resumed.state == "running"
    assert all(item.job.attempt_count == item.job.metadata["inherited_corpus_attempt_count"] == 0 for item in svc.store.list_round_items(resumed.round_id).items)
    assert calls == []


def test_governance_changes_cannot_reset_executed_member_budget_or_block_unstarted_members(tmp_path):
    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
    from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher

    svc, quick, batch, calls = setup(tmp_path)
    record = reviewed_memory(svc.repository)
    first = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", alert_ids=["5"])), context=context())
    svc.start(first.round_id, context=context())
    execute = svc._execute

    def drifting(*args):
        outcome = execute(*args)
        change_governance(svc.repository, record.memory_id)
        return outcome

    svc._execute = drifting
    dispatcher = CorpusExperimentDispatcher(svc)
    for _ in range(3):
        latest = svc.store.list_rounds()[-1]
        assert svc.execute_one(latest.round_id)
        dispatcher._continue_changed_memory()
    assert len(calls) == 3 and len(svc.store.list_rounds()) == 3
    assert svc.store.list_memory_continuation_rounds() == []
    assert batch.snapshot("validation")["memory_recovery_exhausted_count"] == 1
    svc._execute = execute
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    fresh = svc.store.list_rounds()[-1]
    assert fresh.selection.alert_ids == ["11"]
    assert svc.execute_one(fresh.round_id)
    assert len(calls) == 4
    assert not batch.snapshot("validation")["stage_scope"]["stage_complete"]
