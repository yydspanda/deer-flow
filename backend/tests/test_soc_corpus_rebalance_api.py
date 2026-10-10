"""Rebalance transport preserves local authority and fixed experiment scope."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_soc_corpus_experiment_api import client
from test_soc_corpus_experiments import context
from test_soc_corpus_rebalance_plan import corpus

from app.gateway.routers import soc_corpus_experiments as routes
from soc_agent.contracts.corpus_experiments import CorpusExperimentRebalance, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_rebalance_plan import build_corpus_rebalance_plan

ROOT = "/api/soc/dev/corpus-workbench/experiments"


def _plan():
    parent, members, facts = corpus()
    parent = parent.model_copy(update={"experiment_id": "EXP-test"})
    return build_corpus_rebalance_plan(parent, members, facts)


@pytest.mark.parametrize("action", ["facts", "plan", "apply"])
def test_rebalance_posts_require_local_admin_and_strict_bodies(tmp_path, monkeypatch, action):
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    remote, application, calls = client(tmp_path)
    local = TestClient(remote.app, client=("127.0.0.1", 1234))
    body = {} if action == "facts" else {"facts_experiment_id": "EXP-facts"}
    if action == "apply":
        body.update(experiment_id="EXP-child", name="补充学习", plan_id=_plan().plan_id)
    path = f"{ROOT}/EXP-test/rebalance/{action}"
    assert remote.post(path, json=body, headers={"Idempotency-Key": "test", "X-Forwarded-For": "127.0.0.1"}).status_code == 403
    assert local.post(path, json=body, headers={"test-role": "user", "Idempotency-Key": "test"}).status_code == 403
    assert local.post(path, json={**body, "external_action_execution": True}, headers={"Idempotency-Key": "test"}).status_code == 422
    if action != "plan":
        assert local.post(path, json=body).status_code == 400
    assert application.service.store.list_rounds() == []
    assert not calls


def test_rebalance_routes_forward_typed_commands_without_starting_models(tmp_path, monkeypatch):
    http, application, model_calls = client(tmp_path)
    calls = []
    plan = _plan()
    fake = SimpleNamespace(
        prepare_facts=lambda *args, **kwargs: calls.append(("facts", args, kwargs)) or {"experiment_id": "EXP-facts", "rounds": []},
        plan=lambda *args, **kwargs: calls.append(("plan", args, kwargs)) or plan,
        apply=lambda *args, **kwargs: calls.append(("apply", args, kwargs)) or {"experiment": {"experiment_id": "EXP-child"}},
    )
    monkeypatch.setattr(routes, "CorpusRebalanceService", lambda service, profiles: fake)
    facts = http.post(f"{ROOT}/EXP-test/rebalance/facts", json={}, headers={"Idempotency-Key": "prepare"})
    assert facts.status_code == 201, facts.text
    preview = http.post(f"{ROOT}/EXP-test/rebalance/plan", json={"facts_experiment_id": "EXP-facts", "policy": {"holdout_per_pattern": 1}})
    assert preview.status_code == 200, preview.text
    body = {"facts_experiment_id": "EXP-facts", "experiment_id": "EXP-child", "name": "补充学习", "plan_id": plan.plan_id}
    applied = http.post(f"{ROOT}/EXP-test/rebalance/apply", json=body, headers={"Idempotency-Key": "apply"})
    assert applied.status_code == 201, applied.text
    assert calls[0][1] == ("EXP-test",)
    assert calls[0][2]["context"].idempotency_key == "prepare"
    assert calls[1][2]["policy"].holdout_per_pattern == 1
    assert calls[2][1] == ("EXP-test", "EXP-facts")
    assert calls[3][1] == (plan, "EXP-facts")
    assert calls[3][2]["experiment_id"] == "EXP-child"
    assert not model_calls and not application.dispatcher.is_running
    wrong_parent = http.post(f"{ROOT}/EXP-other/rebalance/apply", json=body, headers={"Idempotency-Key": "apply"})
    assert wrong_parent.status_code == 400
    assert len(calls) == 5
    stale = http.post(f"{ROOT}/EXP-test/rebalance/apply", json={**body, "plan_id": "0" * 64}, headers={"Idempotency-Key": "stale"})
    assert stale.status_code == 400
    assert sum(action == "apply" for action, *_ in calls) == 1
    assert http.post(f"{ROOT}/EXP-test/rebalance/apply", json={**body, "plan": plan.model_dump(mode="json")}, headers={"Idempotency-Key": "oversized"}).status_code == 422


def test_explicit_experiment_status_is_scoped_and_disallows_fact_inventory_review(tmp_path):
    http, application, calls = client(tmp_path)
    store = application.service.store
    original = store.get_experiment("EXP-test")
    rows = store.list_members("EXP-test", selection=CorpusRoundSelection(batch="learning"), limit=100).items
    store.prepare(original.model_copy(update={"experiment_id": "EXP-child", "plan_id": "c" * 64}), rows)
    round_ = application.service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), execution_limit=1), context=context())
    application.service.start(round_.round_id, context=context())
    application.service.execute_one(round_.round_id)
    original_state = http.get(f"{ROOT}/EXP-test/status?batch=learning").json()
    child_state = http.get(f"{ROOT}/EXP-child/status?batch=learning").json()
    assert original_state["completed"] == 1
    assert child_state["completed"] == 0
    assert child_state["remaining"] == child_state["total"] == 10
    assert child_state["pending_candidates"] == 0
    assert child_state["review_allowed"] is True
    assert child_state["fact_source_experiment_id"] is None
    assert http.get(f"{ROOT}/EXP-child").json()["plan_id"] == "c" * 64
    inventory = original.model_copy(update={"experiment_id": "EXP-facts", "plan_id": "d" * 64, "fact_source_experiment_id": "EXP-test"})
    validation = store.list_members("EXP-test", selection=CorpusRoundSelection(batch="validation", scope="all"), limit=100).items
    store.prepare(inventory, [*rows, *validation])
    facts = http.get(f"{ROOT}/EXP-facts/status?batch=learning").json()
    assert facts["review_allowed"] is False
    assert facts["fact_source_experiment_id"] == "EXP-test"
    assert http.get(f"{ROOT}/missing/status").status_code == 404
    assert http.get(f"{ROOT}/missing").status_code == 404
    assert http.get(f"{ROOT}/EXP-test/status", headers={"test-role": "user"}).status_code == 403
    assert http.get(f"{ROOT}/EXP-test/status?batch=arbitrary").status_code == 422


def test_rebalanced_results_preserve_stage_scope_without_reading_historical_labels(tmp_path, monkeypatch):
    http, application, _ = client(tmp_path)
    service, store = application.service, application.service.store
    full = service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", scope="all"), execution_limit=100, memory_mode="none"), context=context())
    service.start(full.round_id, context=context())
    while service.execute_one(full.round_id):
        pass
    single = service.create_round(
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", scope="all", alert_ids=["11"]), execution_limit=1, memory_mode="none"),
        context=context().model_copy(update={"idempotency_key": "single"}),
    )
    service.start(single.round_id, context=context())
    service.execute_one(single.round_id)
    original = store.get_experiment("EXP-test")
    rebalance = original.model_copy(update={"plan_id": "c" * 64, "rebalance": CorpusExperimentRebalance(parent_experiment_id="EXP-parent", plan_hash="d" * 64, fact_hashes={"1": "e" * 64}, inherited_memory_ids=["M1"])})
    monkeypatch.setattr(store, "get_experiment", lambda _: rebalance)

    def forbidden_label(*args, **kwargs):
        raise AssertionError("rebalance report read operational labels")

    application.workbench = SimpleNamespace(batch_plan_id=original.plan_id, batch_plan=SimpleNamespace(source_identity=original.source_identity), experiment_label=forbidden_label)
    response = http.get(f"/api/soc/dev/corpus-workbench/rounds/{full.round_id}/results?limit=1")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["experiment"]["plan_id"] == "c" * 64
    assert result["stage_scope"] == {"batch_total": 2, "learning_total": 10, "validation_total": 2, "stage_complete": True}
    assert "fact_hashes" not in result["experiment"]["rebalance"]
    assert result["experiment"]["rebalance"]["inherited_memory_count"] == 1
    assert result["total"] == 2 and len(result["items"]) == 1
    assert "label" not in result["items"][0]
    partial = http.get(f"/api/soc/dev/corpus-workbench/rounds/{single.round_id}/results").json()
    assert partial["stage_scope"] == {"batch_total": 2, "learning_total": 10, "validation_total": 2, "stage_complete": False}
    application.workbench.batch_plan.source_identity = {"sha256": "another corpus"}
    assert http.get(f"/api/soc/dev/corpus-workbench/rounds/{full.round_id}/results").status_code == 409
