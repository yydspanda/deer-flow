"""Host-only configuration does not remove colleagues' saved-config run controls."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from test_soc_corpus_batch_workbench import workbench as workbench
from test_soc_corpus_experiment_api import client
from test_soc_corpus_experiment_repository import experiment
from test_soc_corpus_experiments import context

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection

ROOT = "/api/soc/dev/corpus-workbench"


@pytest.mark.parametrize("host,allowed", [("127.0.0.1", True), ("::1", True), ("192.0.2.10", False), ("unknown", False), (None, False)])
def test_local_configuration_uses_server_client_identity_not_claimed_headers(monkeypatch, host, allowed):
    from app.gateway.soc_corpus_control import can_configure_corpus

    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    request = Request({"type": "http", "client": (host, 1234) if host else None, "headers": [(b"host", b"localhost:2026"), (b"x-forwarded-for", b"127.0.0.1"), (b"x-real-ip", b"127.0.0.1")]})
    assert can_configure_corpus(request) is allowed
    monkeypatch.delenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY")
    assert can_configure_corpus(request) is True


def test_lan_runs_pause_and_reruns_use_saved_options_but_cannot_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    remote, app, calls = client(tmp_path)
    app.workbench = SimpleNamespace(batch_plan_id=experiment().plan_id, batch_plan=SimpleNamespace(plan_id=experiment().plan_id), experiment_preparation_guard=nullcontext)
    app.dispatcher.start = lambda: None
    local = TestClient(remote.app, client=("127.0.0.1", 1234))
    saved = SocAnalysisExecutionOptions(normalization_review_mode="apply").model_dump(mode="json")
    defaults = app.defaults.model_dump(mode="json")
    config_path = ROOT + "/experiments/configuration?batch=learning"
    assert remote.get(config_path).json()["can_configure"] is False
    assert local.get(config_path).json()["can_configure"] is True
    assert remote.get(config_path).json()["saved_options"] == defaults
    path = ROOT + "/quick-validation"
    body = {"batch": "learning", "action": "run", "alert_id": "0", "options": saved}
    assert remote.post(path, json=body).status_code == 403
    assert app.service.store.list_rounds() == []
    assert local.post(path, json=body).status_code == 202
    assert remote.get(config_path).json()["saved_options"] == saved
    assert remote.get(ROOT + "/experiments/configuration?batch=validation").json()["saved_options"] == defaults
    assert remote.post(path, json={**body, "alert_id": "1"}).status_code == 202
    assert remote.post(path, json={"batch": "learning", "action": "start", "options": saved}).status_code == 202
    assert remote.post(path, json={"batch": "learning", "action": "pause", "options": defaults}).status_code == 202
    rounds = app.service.store.list_rounds()
    assert all(r.options.model_dump(mode="json") == saved and r.state == "paused" for r in rounds)
    assert remote.post(path, json={**body, "alert_id": "2", "options": defaults}).status_code == 403
    assert remote.post(path, json={**body, "action": "rerun"}, headers={"Idempotency-Key": "saved-rerun"}).status_code == 202
    assert not calls


def test_legacy_round_creation_cannot_bypass_readonly_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    remote, app, calls = client(tmp_path)
    command = CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), options=SocAnalysisExecutionOptions(normalization_review_mode="apply"))
    denied = remote.post(ROOT + "/rounds", json=command.model_dump(mode="json"), headers={"Idempotency-Key": "override"})
    assert denied.status_code == 403
    assert app.service.store.list_rounds() == []
    app.service.create_round(command, context=context())
    allowed = remote.post(ROOT + "/rounds", json=command.model_dump(mode="json"), headers={"Idempotency-Key": "saved"})
    assert allowed.status_code == 201
    assert not calls


def test_lan_can_rerun_completed_alert_with_saved_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    remote, app, calls = client(tmp_path)
    app.workbench = SimpleNamespace(batch_plan_id=experiment().plan_id, batch_plan=SimpleNamespace(plan_id=experiment().plan_id), experiment_preparation_guard=nullcontext)
    app.dispatcher.start = lambda: None
    options = SocAnalysisExecutionOptions(normalization_review_mode="apply")
    first = app.service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning", alert_ids=["0"]), options=options), context=context())
    app.service.start(first.round_id, context=context())
    assert app.service.execute_one(first.round_id)
    previous = app.service.store.list_round_items(first.round_id).items[0].job
    response = remote.post(ROOT + "/quick-validation", json={"batch": "learning", "action": "rerun", "alert_id": "0", "options": options.model_dump(mode="json")}, headers={"Idempotency-Key": "completed-rerun"})
    assert response.status_code == 202, response.text
    rounds = app.service.store.list_rounds()
    assert len(rounds) == 2 and all(r.options == options for r in rounds)
    assert app.service.store.list_round_items(first.round_id).items[0].job == previous
    assert len(calls) == 1  # Only the explicit simulated first execution ran.


def test_legacy_workbench_projects_request_permission_and_rejects_custom_options(workbench, monkeypatch):
    from app.gateway.routers.soc_corpus_workbench import get_corpus_workbench_state, process_corpus_workbench_alert
    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchProcessRequest, SocCorpusWorkbenchRunControls

    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    workbench._run_controls = SocCorpusWorkbenchRunControls(defaults=SocAnalysisExecutionOptions())
    requests = []
    for host in ("192.0.2.10", "127.0.0.1"):
        request = Request({"type": "http", "client": (host, 1234), "headers": []})
        request.state.user = SimpleNamespace(id="test", system_role="admin")
        request.state.auth_source = "session"
        requests.append(request)
    remote, local = requests
    assert get_corpus_workbench_state(workbench, remote, include_rehearsal=False).run_controls.can_configure is False
    assert get_corpus_workbench_state(workbench, local, include_rehearsal=False).run_controls.can_configure is True
    assert workbench.run_controls.can_configure is True
    calls = []
    monkeypatch.setattr(workbench, "start_alert", lambda *args, **kwargs: calls.append(kwargs))
    custom = SocCorpusWorkbenchProcessRequest(settings=SocAnalysisExecutionOptions(normalization_review_mode="apply"))
    with pytest.raises(HTTPException) as denied:
        process_corpus_workbench_alert("0", remote, workbench, custom)
    assert denied.value.status_code == 403 and calls == []
    process_corpus_workbench_alert("0", local, workbench, custom)
    process_corpus_workbench_alert("0", remote, workbench, SocCorpusWorkbenchProcessRequest(settings=workbench.run_controls.defaults))
    process_corpus_workbench_alert("0", remote, workbench)
    assert len(calls) == 3
