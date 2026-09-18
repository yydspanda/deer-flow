from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, repository
from test_soc_corpus_experiments import context, prepare, service

from soc_agent.contracts.corpus_experiments import CorpusQuickCommand
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation


def setup(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id))
    return svc, quick, calls


def test_quick_full_batch_manual_pause_dedup_rerun(tmp_path):
    svc, quick, calls = setup(tmp_path)
    start = CorpusQuickCommand(batch="learning")
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: quick.command(start, context=context()), range(2)))
    assert len(svc.store.list_rounds()) == 1
    round_ = svc.store.list_rounds()[0]
    assert svc.store.round_progress(round_.round_id).selected_count == 10
    quick.command(CorpusQuickCommand(batch="learning", action="pause"), context=context())
    single = CorpusQuickCommand(batch="learning", action="run", alert_id="4")
    quick.command(single, context=context())
    quick.command(single, context=context())
    assert next(item for item in quick.snapshot("learning")["items"] if item["alert_id"] == "4")["manual_dispatch"]
    assert svc.execute_one(round_.round_id)
    assert not svc.execute_one(round_.round_id)
    assert quick.snapshot("learning")["completed"] == 1
    rerun = single.model_copy(update={"action": "rerun"})
    ctx = context().model_copy(update={"idempotency_key": "rerun-4-first"})
    quick.command(rerun, context=ctx)
    quick.command(rerun, context=ctx)
    child = svc.store.list_rounds()[-1]
    assert svc.execute_one(child.round_id)
    quick.command(rerun, context=ctx)  # A network retry after completion is inert.
    assert len(svc.store.list_rounds()) == 2
    history = svc.store.alert_history("EXP-test", "4")
    assert len(history) == 2 and all(job.run_id for job in history)
    assert history[0].run_id != history[1].run_id
    assert quick.snapshot("learning")["total"] == 10
    assert quick.snapshot("learning")["completed"] == 1
    quick.command(start, context=context())
    while svc.execute_one(round_.round_id):
        pass
    assert len(calls) == 11
    assert quick.snapshot("learning")["completed"] == 10


def test_validation_requires_review_and_filters_are_not_commands(tmp_path):
    import pytest

    svc, quick, calls = setup(tmp_path)
    with pytest.raises(ValueError, match="审核经验"):
        quick.command(CorpusQuickCommand(batch="validation"), context=context())
    assert svc.store.list_rounds() == []
    with pytest.raises(ValueError):
        CorpusQuickCommand(batch="learning", group_ids=["group-a"])


def test_subset_resume_never_dispatches_outside_selected_scope(tmp_path):
    svc, quick, calls = setup(tmp_path)
    # A saved all-scope round can later be resumed with a smaller browser scope.
    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection

    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), execution_limit=100), context=context())
    svc.pause(round_.round_id, context=context())
    with svc.repository.mutation_transaction() as tx:
        tx.lock_memory_governance()
        tx.corpus_experiments().set_dispatch_scope(round_.round_id, ["0"])
    svc.start(round_.round_id, context=context())
    assert svc.execute_one(round_.round_id)
    assert not svc.execute_one(round_.round_id)
    assert [call[1] for call in calls] == ["0"]


def test_new_corpus_query_is_read_only_and_start_prepares_automatically(tmp_path):
    from test_soc_corpus_experiment_repository import members

    from soc_agent.demo.corpus_batches import CorpusBatchCase, build_corpus_batch_plan

    svc = service(repository(tmp_path), [])
    plan = build_corpus_batch_plan(
        [CorpusBatchCase(alert_id=m.alert_id, source_index=m.source_index, payload_hash=m.payload_hash, group_id=m.group_id, observed_at=m.event_time.isoformat(), rule_code=m.rule_code, detection_key=m.detection_key) for m in members()],
        source_identity=experiment().source_identity,
    )
    quick = CorpusQuickValidation(svc, plan)
    snapshot = quick.snapshot("learning")
    assert snapshot["total"] == 10
    assert svc.store.list_experiments() == []
    quick.command(CorpusQuickCommand(batch="learning", action="run", alert_id="0"), context=context())
    assert len(svc.store.list_experiments()) == 1
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    for round_ in svc.store.list_rounds():
        while svc.execute_one(round_.round_id):
            pass
    assert quick.snapshot("learning")["completed"] == 10


def test_quick_api_authority_and_scope(tmp_path):
    from contextlib import nullcontext

    from test_soc_corpus_experiment_api import client

    http, app, calls = client(tmp_path)
    app.workbench = SimpleNamespace(batch_plan=SimpleNamespace(plan_id=experiment().plan_id), experiment_preparation_guard=nullcontext)
    app.dispatcher.start = lambda: None
    root = "/api/soc/dev/corpus-workbench/quick-validation"
    assert http.get(root, headers={"test-role": "user"}).status_code == 403
    assert http.get(root).json()["total"] == 10
    response = http.post(root, json={"batch": "learning", "action": "run", "alert_id": "4"})
    assert response.status_code == 202, response.text
    assert http.post(root, json={"batch": "learning", "group_ids": ["group-a"]}).status_code == 422
    assert calls == []


def test_configuration_drift_stops_saved_tasks_and_explicit_rerun_uses_new_config(tmp_path):
    import pytest

    repo = repository(tmp_path)
    prepare(repo)
    config = {"model": "before"}
    svc = service(repo, [], config=config)
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id))
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    quick.command(CorpusQuickCommand(batch="learning", action="pause"), context=context())
    old = svc.store.list_rounds()[0]
    config["model"] = "after"
    with pytest.raises(ValueError, match="设置"):
        quick.command(CorpusQuickCommand(batch="learning", action="run", alert_id="4"), context=context())
    assert svc.store.get_round(old.round_id).state == "blocked"
    assert quick.snapshot("learning")["items"][0]["blocked_reason"] == "configuration_changed"
    quick.command(CorpusQuickCommand(batch="learning", action="rerun", alert_id="4"), context=context().model_copy(update={"idempotency_key": "new-config"}))
    new = svc.store.list_rounds()[-1]
    assert new.config_hash != old.config_hash
    assert svc.execute_one(new.round_id)
    assert len(svc.store.alert_history("EXP-test", "4")) == 2
    assert quick.snapshot("learning")["total"] == 10


def test_recovery_detects_manual_active_job_in_paused_batch(tmp_path):
    from datetime import UTC, datetime, timedelta

    from soc_agent.demo.corpus_experiments import CORPUS_QUEUE, CORPUS_WORKLOAD

    svc, quick, calls = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    quick.command(CorpusQuickCommand(batch="learning", action="pause"), context=context())
    quick.command(CorpusQuickCommand(batch="learning", action="run", alert_id="4"), context=context())
    round_ = svc.store.list_rounds()[0]
    job = svc.jobs.claim_next(queue_name=CORPUS_QUEUE, workload_kind=CORPUS_WORKLOAD, worker_id="crashed-gateway", lease_seconds=1, job_ids=svc.store.eligible_job_ids(round_.round_id))
    assert job.alert_id == "4"
    assert svc.store.has_manual_work()  # Gateway must boot dispatcher for active leases too.
    now = datetime.now(UTC) + timedelta(seconds=2)
    assert svc.jobs.recover_expired_leases(queue_name=CORPUS_QUEUE, now=now) == [job.job_id]
    recovered = svc.jobs.get(job.job_id)
    assert recovered.metadata["manual_dispatch"] is True
    assert svc.store.get_round(round_.round_id).state == "paused"
    assert svc.store.round_progress(round_.round_id).counts["queued"] == 10


def test_all_reuse_and_explore_scopes_and_no_memory_exploration(tmp_path):
    import pytest
    from test_soc_corpus_experiment_repository import members

    repo = repository(tmp_path)
    manifest = members(13)
    manifest[-1] = manifest[-1].model_copy(update={"batch": "validation", "validation_tier": "supplementary"})
    repo.corpus_experiments().prepare(experiment(), manifest)
    calls = []
    svc = service(repo, calls)
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id))
    assert [quick.snapshot("validation", scope)["total"] for scope in ("all", "reuse", "explore")] == [3, 2, 1]
    quick.command(CorpusQuickCommand(batch="validation", scope="explore"), context=context())
    round_ = svc.store.list_rounds()[0]
    assert round_.memory_mode == "none"
    assert svc.execute_one(round_.round_id)
    assert [call[1] for call in calls] == ["12"]
    assert quick.snapshot("validation", "all")["completed"] == 1
    with pytest.raises(ValueError, match="审核经验"):
        quick.command(CorpusQuickCommand(batch="validation", scope="reuse"), context=context())
    assert len(svc.store.list_rounds()) == 1


def test_gateway_lifespan_starts_for_paused_manual_work(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import Mock

    from fastapi import FastAPI

    from app.gateway.routers import soc_corpus_experiments as routes

    svc, quick, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="learning", action="run", alert_id="4"), context=context())
    dispatcher = SimpleNamespace(start=Mock(), stop=Mock())
    application = SimpleNamespace(dispatcher=dispatcher)
    app = FastAPI()
    app.state.soc_corpus_experiment_application = application
    monkeypatch.setenv("SOC_DEV_CORPUS_WORKBENCH_ENABLED", "true")
    monkeypatch.setattr(routes, "get_or_create_soc_repository", lambda _: svc.repository)
    monkeypatch.setattr(routes, "get_corpus_experiment_application", lambda _: application)
    assert svc.store.list_rounds(state="running") == []

    async def restart():
        async with routes.experiment_lifespan(app):
            dispatcher.start.assert_called_once()

    asyncio.run(restart())
    dispatcher.stop.assert_called_once()


@pytest.mark.parametrize("restricted_scope", [False, True], ids=["legacy-limit", "scoped-resume"])
def test_manual_request_reopens_completed_round_without_resuming_bulk(tmp_path, restricted_scope):
    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection

    svc, quick, calls = setup(tmp_path)
    round_ = svc.create_round(
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), execution_limit=100 if restricted_scope else 1),
        context=context(),
    )
    if restricted_scope:
        with svc.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            tx.corpus_experiments().set_dispatch_scope(round_.round_id, ["0"])
    svc.start(round_.round_id, context=context())
    assert svc.execute_one(round_.round_id)
    assert svc.store.get_round(round_.round_id).state == "completed"
    old_job = svc.store.alert_history("EXP-test", "4")[0]
    command = CorpusQuickCommand(batch="learning", action="run", alert_id="4")
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: quick.command(command, context=context()), range(2)))
    admitted = svc.store.get_round(round_.round_id)
    assert admitted.state == "paused"
    assert admitted.execution_limit == round_.execution_limit
    assert admitted.config_hash == round_.config_hash
    assert admitted.dispatch_alert_ids == (["0"] if restricted_scope else None)
    assert admitted.state_history[-1]["reason"] == "manual_requested"
    # Recreate the service just as Gateway recovery does; intent and state are durable.
    recovered = service(svc.repository, calls)
    assert recovered.store.has_manual_work()
    assert recovered.store.list_interactive_round_ids() == [round_.round_id]
    assert recovered.execute_one(round_.round_id)
    assert not recovered.execute_one(round_.round_id)
    assert recovered.store.get_round(round_.round_id).state == "paused"
    assert recovered.store.alert_history("EXP-test", "4")[0].job_id == old_job.job_id
    assert len(recovered.store.alert_history("EXP-test", "4")) == 1
    assert [call[1] for call in calls] == ["0", "4"]
    assert quick.snapshot("learning")["completed"] == 2
    assert quick.snapshot("learning")["remaining"] == 8


@pytest.mark.parametrize("blocked_alerts", [["4"], ["4", "3"]], ids=["single-round", "partially-superseded"])
def test_blocked_state_only_tracks_unsuperseded_jobs(tmp_path, blocked_alerts):
    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection

    repo = repository(tmp_path)
    prepare(repo)
    config = {"model": "before"}
    svc = service(repo, [], config=config)
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id))
    old = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=blocked_alerts)), context=context())
    svc.start(old.round_id, context=context())
    config["model"] = "after"
    assert not svc.execute_one(old.round_id)
    assert quick.snapshot("learning")["blocked_reason"] == "configuration_changed"
    for index, alert_id in enumerate(blocked_alerts):
        quick.command(CorpusQuickCommand(batch="learning", action="rerun", alert_id=alert_id), context=context().model_copy(update={"idempotency_key": f"recover-{alert_id}"}))
        expected = "configuration_changed" if index < len(blocked_alerts) - 1 else None
        assert quick.snapshot("learning")["blocked_reason"] == expected
        replacement = svc.store.list_rounds()[-1]
        assert svc.execute_one(replacement.round_id)
        assert quick.snapshot("learning")["blocked_reason"] == expected
        assert len(svc.store.alert_history("EXP-test", alert_id)) == 2
    historical = svc.store.get_round(old.round_id)
    assert historical.state == "blocked"
    assert historical.state_reason == "configuration_changed"
    assert quick.snapshot("learning")["completed"] == len(blocked_alerts)
