"""Explicit second-batch restarts preserve history and replace only current work."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context, service
from test_soc_memory_lesson_drafting import _candidate
from test_soc_memory_retrieval_v2 import _record

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusQuickCommand, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.db.corpus_experiments import CorpusExperimentConflict, SqlAlchemyCorpusExperimentRepository
from soc_agent.db.models import SocProcessingJobRow
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation


def setup(tmp_path, *, reviewed=True):
    repo = repository(tmp_path)
    manifest = [m.model_copy(update={"batch": "learning" if i == 0 else "validation", "validation_tier": None if i == 0 else "main" if i < 4 else "supplementary"}) for i, m in enumerate(members(6))]
    repo.corpus_experiments().prepare(experiment(), manifest)
    if reviewed:
        candidate = _candidate()
        candidate.metadata["experiment_id"] = "EXP-test"
        repo.save_memory_candidate(candidate)
        record = _record("MEM-LEARNING", facets={"environment": ["dev-corpus-eval"]}).model_copy(update={"source_candidate_id": candidate.candidate_id})
        repo.save_memory_record(record)
    calls, config = [], {"model": "fake", "version": 1}
    svc = service(repo, calls, config)
    return svc, CorpusQuickValidation(svc, SimpleNamespace(plan_id=experiment().plan_id)), calls, config


def restart(quick, *, mode="apply", actor="operator", token=None):
    token = token or quick.snapshot("validation")["restart_token"]
    command = CorpusQuickCommand(batch="validation", action="restart_validation", restart_token=token, options=SocAnalysisExecutionOptions(normalization_review_mode=mode))
    request = context().model_copy(update={"actor": context().actor.model_copy(update={"actor_id": actor}), "idempotency_key": f"restart-validation-{token}"})
    return command, request


def test_restart_replaces_success_failure_queued_and_blocked_work_preserving_history(tmp_path):
    svc, quick, calls, config = setup(tmp_path)
    options = SocAnalysisExecutionOptions(normalization_review_mode="apply")
    quick.command(CorpusQuickCommand(batch="learning", options=options), context=context())
    learning = svc.store.list_rounds()[0]
    assert svc.execute_one(learning.round_id)
    saved_learning = svc.store.get_round(learning.round_id)
    saved_learning_jobs = svc.store.list_round_items(learning.round_id)
    saved_memory = svc.repository.list_memory_records()
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    old = svc.store.list_rounds()[-1]
    original = svc._execute

    def fail_second(round_, member, request):
        if member.alert_id == "2" and round_.options.normalization_review_mode == "off":
            raise ValueError("failed before saving a Runtime run")
        return original(round_, member, request)

    svc._execute = fail_second
    token = quick.snapshot("validation")["restart_token"]
    assert svc.execute_one(old.round_id)
    assert svc.execute_one(old.round_id)
    assert quick.snapshot("validation")["restart_token"] == token
    quick.command(CorpusQuickCommand(batch="validation", action="pause"), context=context())
    svc.request_manual(old.round_id, "5", context=context())
    config["version"] = 2
    assert not svc.execute_one(old.round_id)
    assert svc.store.get_round(old.round_id).state == "blocked"
    old_jobs = svc.store.list_round_items(old.round_id)
    assert [i.job.status.value for i in old_jobs.items] == ["completed", "failed", "queued", "queued", "queued"]

    command, request = restart(quick, token=token)
    before_calls = list(calls)
    assert quick.command(command, context=request) == {"accepted": True}
    assert calls == before_calls
    current = svc.store.list_rounds()[-1]
    assert current.round_id != old.round_id
    assert current.options == options
    assert current.selection == CorpusRoundSelection(batch="validation", scope="all")
    assert current.created_by == request.actor.actor_id
    assert [m.memory_id for m in current.memory_snapshot] == ["MEM-LEARNING"]
    assert current.state == "running"
    snapshot = quick.snapshot("validation")
    assert {key: snapshot[key] for key in ("total", "completed", "failed", "active", "remaining", "blocked_reason")} == {"total": 5, "completed": 0, "failed": 0, "active": 0, "remaining": 5, "blocked_reason": None}
    assert not svc.store.has_manual_work()
    assert svc.store.get_round(learning.round_id) == saved_learning
    assert svc.store.list_round_items(learning.round_id) == saved_learning_jobs
    assert svc.repository.list_memory_records() == saved_memory
    assert svc.store.get_round(old.round_id).state == "blocked"
    for item in old_jobs.items:
        historical = svc.store.alert_history("EXP-test", item.alert_id)[1]
        assert (historical.job_id, historical.status, historical.run_id, historical.result_payload) == (item.job_id, item.job.status, item.job.run_id, item.job.result_payload)
        assert not historical.metadata.get("manual_dispatch")

    recovered = service(svc.repository, calls, config)
    while recovered.execute_one(current.round_id):
        pass
    assert not recovered.execute_one(old.round_id)
    assert quick.snapshot("validation")["completed"] == 5
    assert len(svc.store.alert_history("EXP-test", "2")) == 2
    assert {row[1] for row in calls[len(before_calls) :]} == {"1", "2", "3", "4", "5"}
    assert all(row[0] == "validation" and row[2] == ("MEM-LEARNING",) for row in calls[len(before_calls) :])


def test_restart_deduplicates_tabs_actors_and_retries_after_new_submissions(tmp_path):
    svc, quick, calls, _ = setup(tmp_path)
    command, request = restart(quick)
    peer = request.model_copy(update={"actor": request.actor.model_copy(update={"actor_id": "peer"})})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda ctx: quick.command(command, context=ctx), [request, peer]))
    assert results == [{"accepted": True}, {"accepted": True}]
    assert len(svc.store.list_rounds()) == 1
    first = svc.store.list_rounds()[0]
    assert first.created_by in {"operator", "peer"}
    assert svc.execute_one(first.round_id)
    with pytest.raises(CorpusExperimentConflict, match="另一组配置"):
        quick.command(command.model_copy(update={"options": SocAnalysisExecutionOptions()}), context=request)
    second, second_request = restart(quick, mode="off")
    quick.command(second, context=second_request)
    # A retry of an already accepted command is inert even after a later restart
    # changed settings; it neither resumes the old round nor rechecks old snapshots.
    assert quick.command(command, context=peer, allow_options_override=False) == {"accepted": True}
    assert len(svc.store.list_rounds()) == 2
    assert svc.store.get_round(first.round_id).state == "paused"
    assert not svc.execute_one(first.round_id)
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["claimed", "prechecking", "analyzing", "projecting"])
def test_restart_rejects_any_active_historical_job_even_when_latest_is_queued(tmp_path, status):
    svc, quick, calls, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="validation", action="run", alert_id="5"), context=context())
    first = svc.store.list_rounds()[0]
    first_job = svc.store.list_round_items(first.round_id).items[0].job
    with svc.repository._session_factory() as session:
        session.execute(update(SocProcessingJobRow).where(SocProcessingJobRow.job_id == first_job.job_id).values(status=status))
        session.commit()
    svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", scope="all", alert_ids=["5"])), context=context())
    assert quick.snapshot("validation")["active"] == 0  # Superseding round hides this old claim.
    command, request = restart(quick)
    rounds = svc.store.list_rounds()
    with pytest.raises(CorpusExperimentConflict, match="等待运行中的告警结束"):
        quick.command(command, context=request)
    assert svc.store.list_rounds() == rounds
    assert svc.jobs.get(first_job.job_id) == svc.store.list_round_items(first.round_id).items[0].job
    assert calls == []


def test_restart_rejects_stale_token_without_stopping_a_new_submission(tmp_path):
    svc, quick, _, config = setup(tmp_path)
    command, request = restart(quick)
    quick.command(CorpusQuickCommand(batch="validation", action="run", alert_id="5"), context=context())
    rounds = svc.store.list_rounds()
    config["version"] = 2  # Token conflicts must not run the ordinary drift/block handler.
    with pytest.raises(CorpusExperimentConflict, match="已有新的提交"):
        quick.command(command, context=request)
    assert svc.store.list_rounds() == rounds


def test_restart_checks_saved_settings_and_requires_reviewed_memory(tmp_path):
    svc, quick, _, _ = setup(tmp_path, reviewed=False)
    command, request = restart(quick)
    with pytest.raises(PermissionError, match="部署本机"):
        quick.command(command, context=request, allow_options_override=False)
    with pytest.raises(ValueError, match="审核经验"):
        quick.command(command, context=request)
    assert svc.store.list_rounds() == []
    no_options = command.model_copy(update={"options": SocAnalysisExecutionOptions()})
    with pytest.raises(ValueError, match="审核经验"):
        quick.command(no_options, context=request, allow_options_override=False)


def test_restart_allows_lan_saved_options_and_uses_current_server_capacity(tmp_path):
    svc, quick, _, _ = setup(tmp_path)
    options = SocAnalysisExecutionOptions(normalization_review_mode="apply")
    quick.command(CorpusQuickCommand(batch="validation", options=options), context=context())
    command, request = restart(quick)
    quick.command(command, context=request, allow_options_override=False)
    current = svc.store.list_rounds()[-1]
    assert current.options == options
    assert current.concurrency == svc._max_concurrency


def test_restart_rolls_back_old_pause_and_manual_intents_when_new_queue_fails(tmp_path, monkeypatch):
    svc, quick, _, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    old = svc.store.list_rounds()[0]
    svc.request_manual(old.round_id, "5", context=context())
    saved_round, saved_jobs = svc.store.get_round(old.round_id), svc.store.list_round_items(old.round_id)
    command, request = restart(quick)

    def fail(*args, **kwargs):
        raise RuntimeError("simulated new queue failure")

    monkeypatch.setattr(SqlAlchemyCorpusExperimentRepository, "attach_jobs", fail)
    with pytest.raises(RuntimeError, match="new queue failure"):
        quick.command(command, context=request)
    assert svc.store.list_rounds() == [saved_round]
    assert svc.store.list_round_items(old.round_id) == saved_jobs


def test_restart_stops_prepared_paused_and_running_manual_queues(tmp_path):
    svc, quick, calls, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="validation", action="run", alert_id="5"), context=context())
    prepared = svc.store.list_rounds()[0]
    svc.pause(prepared.round_id, context=context())
    quick.command(CorpusQuickCommand(batch="validation", action="run", alert_id="4"), context=context())
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    old_ids = [r.round_id for r in svc.store.list_rounds()]
    command, request = restart(quick)
    quick.command(command, context=request)
    assert not svc.store.has_manual_work()
    assert svc.store.list_interactive_round_ids() == []
    for round_id in old_ids:
        assert not svc.execute_one(round_id)
    assert calls == []


def test_restart_retired_round_cannot_be_resumed_manually_or_retried(tmp_path):
    svc, quick, calls, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    old = svc.store.list_rounds()[0]
    command, request = restart(quick)
    quick.command(command, context=request)
    replacement = svc.store.list_rounds()[-1]
    saved_old = svc.store.list_round_items(old.round_id)
    for operation in (
        lambda: svc.start(old.round_id, context=context()),
        lambda: svc.request_manual(old.round_id, "5", context=context()),
        lambda: svc.retry_failed(old.round_id, context=context()),
    ):
        with pytest.raises(CorpusExperimentConflict, match="已被第二批全部重跑替换"):
            operation()
    assert not svc.execute_one(old.round_id)
    assert not svc.store.eligible_job_ids(old.round_id)
    assert not svc.store.list_interactive_round_ids()
    assert svc.store.list_round_items(old.round_id) == saved_old
    assert svc.store.get_round(old.round_id).superseded_by_round_id == replacement.round_id
    assert calls == []


@pytest.mark.parametrize("changes", [{"batch": "learning"}, {"scope": "reuse"}, {"scope": "explore"}, {"alert_id": "1"}, {"restart_token": None}, {"restart_token": "bad"}, {"action": "start"}])
def test_restart_contract_is_explicit_whole_validation_only(changes):
    with pytest.raises(ValidationError):
        CorpusQuickCommand.model_validate({"batch": "validation", "action": "restart_validation", "restart_token": "a" * 64, **changes})


@pytest.mark.parametrize("key", [None, "unrelated-request"])
def test_restart_idempotency_header_must_be_bound_to_token(tmp_path, key):
    svc, quick, _, _ = setup(tmp_path)
    command, request = restart(quick)
    with pytest.raises(ValueError, match="Idempotency-Key"):
        quick.command(command, context=request.model_copy(update={"idempotency_key": key}))
    assert svc.store.list_rounds() == []


def test_restart_api_uses_existing_authority_and_has_no_model_side_effect(tmp_path):
    from contextlib import nullcontext
    from unittest.mock import Mock

    from test_soc_corpus_experiment_api import client

    http, application, calls = client(tmp_path)
    application.workbench = SimpleNamespace(batch_plan=SimpleNamespace(plan_id=experiment().plan_id), experiment_preparation_guard=nullcontext)
    application.dispatcher.start = Mock()
    candidate = _candidate()
    candidate.metadata["experiment_id"] = "EXP-test"
    application.service.repository.save_memory_candidate(candidate)
    application.service.repository.save_memory_record(_record("MEM-LEARNING", facets={"environment": ["dev-corpus-eval"]}).model_copy(update={"source_candidate_id": candidate.candidate_id}))
    endpoint = "/api/soc/dev/corpus-workbench/quick-validation"
    token = http.get(endpoint, params={"batch": "validation", "scope": "explore", "alert_ids": "11"}).json()["restart_token"]
    assert token == http.get(endpoint, params={"batch": "validation", "scope": "all"}).json()["restart_token"]
    body = {"batch": "validation", "action": "restart_validation", "restart_token": token}
    headers = {"Idempotency-Key": f"restart-validation-{token}"}
    assert http.post(endpoint, json=body, headers={**headers, "test-role": "user"}).status_code == 403
    assert http.post(endpoint, json={**body, "batch": "learning"}, headers=headers).status_code == 422
    assert http.post(endpoint, json=body).status_code == 400
    first = http.post(endpoint, json=body, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json() == {"accepted": True}
    assert http.post(endpoint, json=body, headers=headers).status_code == 202
    changed = http.post(endpoint, json={**body, "options": {"normalization_review_mode": "apply"}}, headers=headers)
    assert changed.status_code == 409
    assert isinstance(changed.json()["detail"], str)
    assert len(application.service.store.list_rounds()) == 1
    assert not calls
    application.dispatcher.start.assert_called()
    retired_id = application.service.store.list_rounds()[0].round_id
    fresh_token = http.get(endpoint, params={"batch": "validation"}).json()["restart_token"]
    response = http.post(endpoint, json={**body, "restart_token": fresh_token}, headers={"Idempotency-Key": f"restart-validation-{fresh_token}"})
    assert response.status_code == 202, response.text
    round_endpoint = f"/api/soc/dev/corpus-workbench/rounds/{retired_id}"
    assert http.post(round_endpoint + "/start", json={}).status_code == 409
    assert http.post(round_endpoint + "/retry-failed").status_code == 409
    assert http.get(round_endpoint).status_code == 200
    assert http.get(round_endpoint + "/items").json()["total"] == 2


def test_restart_is_latest_even_after_host_clock_moves_backwards(tmp_path):
    from datetime import UTC, datetime, timedelta

    from soc_agent.db.models import SocCorpusRoundRow

    svc, quick, _, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    old = svc.store.list_rounds()[0]
    future = datetime.now(UTC) + timedelta(days=1)
    with svc.repository._session_factory() as session:
        row = session.get(SocCorpusRoundRow, old.round_id)
        row.created_at = future
        row.record_payload = {**row.record_payload, "created_at": future.isoformat()}
        session.commit()
    command, request = restart(quick)
    quick.command(command, context=request)
    newest = svc.store.latest_round_identity("EXP-test", "validation")
    assert newest[0] != old.round_id
    assert newest[1] > future
    assert quick.snapshot("validation")["restart_token"] != command.restart_token
    assert {i[1].input_payload["round_id"] for i in svc.store.batch_jobs("EXP-test", CorpusRoundSelection(batch="validation", scope="all"))} == {newest[0]}
    pending_command, pending_request = restart(quick)
    quick.command(CorpusQuickCommand(batch="validation", action="rerun", alert_id="5"), context=context().model_copy(update={"idempotency_key": "later-single"}))
    # Explicit single reruns of queued work reuse that queue. A completed member
    # makes the next manual submission create a genuinely new round instead.
    current = svc.store.get_round(newest[0])
    while svc.execute_one(current.round_id):
        pass
    quick.command(CorpusQuickCommand(batch="validation", action="rerun", alert_id="5"), context=context().model_copy(update={"idempotency_key": "later-completed-single"}))
    after_single = svc.store.latest_round_identity("EXP-test", "validation")
    assert after_single[0] != newest[0]
    assert after_single[1] > newest[1]
    assert quick.snapshot("validation")["restart_token"] != pending_command.restart_token
    with pytest.raises(CorpusExperimentConflict, match="已有新的提交"):
        quick.command(pending_command, context=pending_request)
    assert svc.store.has_manual_work()


def test_latest_queued_projection_ignores_foreign_scope_and_old_superseded_queues(tmp_path):
    svc, quick, _, _ = setup(tmp_path)
    command, request = restart(quick)
    quick.command(command, context=request)
    first = svc.store.list_rounds()[0]
    args = dict(plan_id=experiment().plan_id, tenant_id="pingan", environment="dev-corpus-eval")
    assert svc.store.latest_queued_alert_ids(**args) == {"1", "2", "3", "4", "5"}
    assert svc.store.latest_queued_alert_ids(**args, alert_id="2") == {"2"}
    for key, value in (("plan_id", "c" * 64), ("tenant_id", "other-tenant"), ("environment", "foreign")):
        assert not svc.store.latest_queued_alert_ids(**{**args, key: value})
    assert svc.execute_one(first.round_id)
    assert "1" not in svc.store.latest_queued_alert_ids(**args)
    next_command, next_request = restart(quick)
    quick.command(next_command, context=next_request)
    current = svc.store.list_rounds()[-1]
    while svc.execute_one(current.round_id):
        pass
    assert not svc.store.latest_queued_alert_ids(**args)
    assert svc.store.round_progress(first.round_id).counts["queued"] == 4


def test_old_round_payload_without_restart_field_remains_idempotently_readable(tmp_path):
    from soc_agent.db.models import SocCorpusRoundRow

    svc, quick, _, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    original = svc.store.list_rounds()[0]
    with svc.repository._session_factory() as session:
        row = session.get(SocCorpusRoundRow, original.round_id)
        old_payload = {key: value for key, value in row.record_payload.items() if key != "superseded_by_round_id"}
        row.record_payload = old_payload
        session.commit()
    restored = svc.store.get_round(original.round_id)
    assert restored == original
    svc.store.create_round(restored)
    with svc.repository._session_factory() as session:
        assert session.get(SocCorpusRoundRow, original.round_id).record_payload == old_payload


@pytest.mark.parametrize("completed", [False, True])
def test_retiring_validation_preserves_valid_historical_timings(tmp_path, completed):
    from soc_agent.demo.corpus_experiment_timing import round_timing

    svc, quick, _, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    old = svc.store.list_rounds()[0]
    if completed:
        while svc.execute_one(old.round_id):
            pass
    before = round_timing(svc.store.round_progress(old.round_id))
    command, request = restart(quick)
    quick.command(command, context=request)
    after = round_timing(svc.store.round_progress(old.round_id))
    assert after.estimate_status != "unavailable_history"
    if completed:
        assert after == before
