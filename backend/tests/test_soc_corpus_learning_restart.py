"""First-batch restarts isolate new learning while keeping fixed history."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from test_soc_corpus_experiment_repository import experiment
from test_soc_corpus_experiments import context
from test_soc_corpus_validation_restart import setup
from test_soc_memory_lesson_drafting import _candidate
from test_soc_memory_retrieval_v2 import _record

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusQuickCommand
from soc_agent.db.corpus_experiments import CorpusExperimentConflict
from soc_agent.db.models import SocProcessingJobRow


def restart(quick, *, mode="apply", token=None):
    token = token or quick.snapshot("learning")["restart_token"]
    command = CorpusQuickCommand(batch="learning", action="restart_learning", scope="all", restart_token=token, options=SocAnalysisExecutionOptions(normalization_review_mode=mode))
    return command, context().model_copy(update={"idempotency_key": f"restart-learning-{token}"})


def test_learning_restart_replaces_both_dispatch_queues_and_isolates_review(tmp_path):
    svc, quick, calls, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    old_learning = svc.store.list_rounds()[-1]
    assert svc.execute_one(old_learning.round_id)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    old_validation = svc.store.list_rounds()[-1]
    assert svc.execute_one(old_validation.round_id)
    old_jobs = svc.store.list_round_items(old_validation.round_id)
    old_memory = svc.repository.list_memory_records()
    command, request = restart(quick)
    assert quick.command(command, context=request) == {"accepted": True}
    fresh = svc.store.list_rounds()[-1]
    assert fresh.learning_generation == fresh.round_id
    assert fresh.options.normalization_review_mode == "apply"
    assert fresh.memory_mode == "none" and not fresh.memory_snapshot
    assert len(calls) == 2  # Admission makes no model calls.
    assert quick.snapshot("learning")["completed"] == 0
    assert quick.snapshot("learning")["remaining"] == 1
    assert quick.snapshot("learning")["pending_candidates"] == 0
    assert quick.snapshot("validation")["completed"] == 0
    assert quick.snapshot("validation")["remaining"] == 5
    assert svc.store.learning_memory_records("EXP-test") == []
    assert svc.repository.list_memory_records() == old_memory
    assert svc.store.list_round_items(old_validation.round_id) == old_jobs
    assert len(svc.store.alert_history("EXP-test", "0")) == 2
    for old in (old_learning, old_validation):
        assert svc.store.get_round(old.round_id).superseded_by_round_id == fresh.round_id
        assert not svc.execute_one(old.round_id)
        with pytest.raises(CorpusExperimentConflict):
            svc.start(old.round_id, context=context())
    with pytest.raises(ValueError, match="审核经验"):
        quick.command(CorpusQuickCommand(batch="validation"), context=context())
    assert svc.execute_one(fresh.round_id)
    assert quick.snapshot("learning")["completed"] == 1
    rerun = CorpusQuickCommand(batch="learning", action="rerun", alert_id="0", options=command.options)
    quick.command(rerun, context=context().model_copy(update={"idempotency_key": "single-new-generation"}))
    assert svc.store.list_rounds()[-1].learning_generation == fresh.learning_generation


def test_learning_restart_same_options_creates_new_generation_and_retries_are_inert(tmp_path):
    svc, quick, _, _ = setup(tmp_path)
    command, request = restart(quick)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: quick.command(command, context=request), range(2)))
    first = svc.store.list_rounds()[-1]
    second, second_request = restart(quick)
    quick.command(second, context=second_request)
    latest = svc.store.list_rounds()[-1]
    assert first.config_hash == latest.config_hash
    assert first.learning_generation != latest.learning_generation
    assert len(svc.store.list_rounds()) == 2
    quick.command(command, context=request)
    assert len(svc.store.list_rounds()) == 2
    assert svc.store.get_round(latest.round_id).state == "running"
    with pytest.raises(CorpusExperimentConflict):
        quick.command(command.model_copy(update={"options": SocAnalysisExecutionOptions()}), context=request)


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_learning_restart_rejects_active_claims_in_either_batch(tmp_path, batch):
    svc, quick, _, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch=batch), context=context())
    old = svc.store.list_rounds()[-1]
    item = svc.store.list_round_items(old.round_id).items[0]
    with svc.repository._session_factory() as session:
        session.execute(update(SocProcessingJobRow).where(SocProcessingJobRow.job_id == item.job_id).values(status="analyzing"))
        session.commit()
    command, request = restart(quick)
    assert quick.snapshot("learning")["restart_active"] == 1
    with pytest.raises(CorpusExperimentConflict, match="暂停"):
        quick.command(command, context=request)
    assert svc.store.get_round(old.round_id) == old


def test_learning_restart_rejects_stale_token_and_lan_option_override(tmp_path):
    svc, quick, _, _ = setup(tmp_path)
    command, request = restart(quick)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    with pytest.raises(CorpusExperimentConflict, match="新.*提交"):
        quick.command(command, context=request)
    command, request = restart(quick)
    with pytest.raises(PermissionError):
        quick.command(command, context=request, allow_options_override=False)
    allowed, allowed_request = restart(quick, mode="off")
    quick.command(allowed, context=allowed_request, allow_options_override=False)
    assert svc.store.list_rounds()[-1].options.normalization_review_mode == "off"


def test_learning_restart_rollback_keeps_old_rounds_and_current_memory(tmp_path, monkeypatch):
    svc, quick, _, _ = setup(tmp_path)
    quick.command(CorpusQuickCommand(batch="learning"), context=context())
    old = svc.store.list_rounds()
    command, request = restart(quick)

    def fail(*args, **kwargs):
        raise RuntimeError("queue write failed")

    monkeypatch.setattr(type(svc), "create_round", fail)
    with pytest.raises(RuntimeError, match="queue write failed"):
        quick.command(command, context=request)
    assert svc.store.list_rounds() == old
    assert len(svc.store.learning_memory_records("EXP-test")) == 1


def test_current_candidate_scope_requires_current_learning_generation(tmp_path):
    svc, quick, _, _ = setup(tmp_path)
    command, request = restart(quick)
    quick.command(command, context=request)
    generation = svc.store.list_rounds()[-1].learning_generation
    candidate = _candidate().model_copy(update={"candidate_id": "MC-NEW", "idempotency_key": "new-generation-candidate"})
    candidate.metadata.update(experiment_id="EXP-test", accumulation_scope={"experiment_id": "EXP-test", "learning_generation": generation})
    svc.repository.save_memory_candidate(candidate)
    assert [c.candidate_id for c in svc.store.list_learning_candidates("EXP-test", review_stage="all").items] == ["MC-NEW"]
    current = _record("MEM-NEW", facets={"environment": ["dev-corpus-eval"]}).model_copy(update={"source_candidate_id": candidate.candidate_id})
    svc.repository.save_memory_record(current)
    quick.command(CorpusQuickCommand(batch="validation"), context=context())
    validation = svc.store.list_rounds()[-1]
    assert validation.learning_generation == generation
    assert [entry.memory_id for entry in validation.memory_snapshot] == ["MEM-NEW"]
    assert svc.repository.get_memory_record("MEM-LEARNING") is not None


def test_learning_restart_api_authority_and_idempotency(tmp_path):
    from test_soc_corpus_experiment_api import client

    http, application, calls = client(tmp_path)
    application.workbench = SimpleNamespace(batch_plan=SimpleNamespace(plan_id=experiment().plan_id), experiment_preparation_guard=nullcontext)
    application.dispatcher.start = Mock()
    endpoint = "/api/soc/dev/corpus-workbench/quick-validation"
    state = http.get(endpoint, params={"batch": "learning"}).json()
    token = state["restart_token"]
    assert state["restart_active"] == 0
    body = {"batch": "learning", "scope": "all", "action": "restart_learning", "restart_token": token, "options": {"normalization_review_mode": "apply"}}
    headers = {"Idempotency-Key": f"restart-learning-{token}"}
    assert http.post(endpoint, json=body, headers={**headers, "test-role": "user"}).status_code == 403
    assert http.post(endpoint, json=body).status_code == 400
    assert http.post(endpoint, json={**body, "batch": "validation"}, headers=headers).status_code == 422
    assert http.post(endpoint, json=body, headers=headers).status_code == 202
    assert http.post(endpoint, json=body, headers=headers).status_code == 202
    assert http.post(endpoint, json={**body, "options": {}}, headers=headers).status_code == 409
    assert len(application.service.store.list_rounds()) == 1
    assert application.service.store.list_rounds()[0].options.normalization_review_mode == "apply"
    assert not calls


@pytest.mark.parametrize("changes", [{"batch": "validation"}, {"scope": "reuse"}, {"alert_id": "0"}, {"restart_token": None}])
def test_learning_restart_contract_requires_explicit_all_scope(changes):
    with pytest.raises(ValidationError):
        CorpusQuickCommand.model_validate({"batch": "learning", "scope": "all", "action": "restart_learning", "restart_token": "a" * 64, **changes})
