"""A remote stale submission must not undo an owner's concurrent saved settings."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from test_soc_corpus_batch_workbench import workbench as workbench
from test_soc_corpus_experiment_api import client
from test_soc_corpus_experiment_repository import experiment

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions

ROOT = "/api/soc/dev/corpus-workbench"


@pytest.mark.parametrize("entry", ["quick", "legacy"])
def test_owner_configuration_commit_wins_over_a_stale_remote_admission(tmp_path, monkeypatch, entry):
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    remote, application, calls = client(tmp_path)
    application.workbench = SimpleNamespace(
        batch_plan_id=experiment().plan_id,
        batch_plan=SimpleNamespace(plan_id=experiment().plan_id),
        experiment_preparation_guard=nullcontext,
    )
    application.dispatcher.start = lambda: None
    local = TestClient(remote.app, client=("127.0.0.1", 1234))
    stale = application.defaults.model_dump(mode="json")
    replacement = SocAnalysisExecutionOptions(normalization_review_mode="apply").model_dump(mode="json")
    if entry == "quick":
        path = ROOT + "/quick-validation"
        owner_body = {"batch": "learning", "action": "run", "alert_id": "0", "options": replacement}
        remote_body = {**owner_body, "alert_id": "1", "options": stale}
        owner_status = 202
    else:
        path = ROOT + "/rounds"
        owner_body = {"experiment_id": "EXP-test", "selection": {"batch": "learning", "alert_ids": ["0"]}, "options": replacement}
        remote_body = {**owner_body, "selection": {"batch": "learning", "alert_ids": ["1"]}, "options": stale}
        owner_status = 201

    repository = application.service.repository
    transaction = repository.mutation_transaction
    interleaved = False

    @contextmanager
    def owner_commits_before_remote_acquires_the_transaction():
        nonlocal interleaved
        if not interleaved:
            # Reproduce a completed owner submission after remote route admission
            # but before its transaction starts, without timing-dependent threads.
            interleaved = True
            owner_response = local.post(path, json=owner_body, headers={"Idempotency-Key": "owner-replacement"})
            assert owner_response.status_code == owner_status, owner_response.text
        with transaction() as tx:
            yield tx

    monkeypatch.setattr(repository, "mutation_transaction", owner_commits_before_remote_acquires_the_transaction)
    response = remote.post(path, json=remote_body, headers={"Idempotency-Key": "remote-stale"})

    assert interleaved
    assert response.status_code == 403, response.text
    config = remote.get(ROOT + "/experiments/configuration?batch=learning").json()
    assert config["saved_options"] == replacement
    rounds = application.service.store.list_rounds()
    assert len(rounds) == 1
    assert rounds[0].options.model_dump(mode="json") == replacement
    assert rounds[0].selection.alert_ids == ["0"]
    assert not calls


@pytest.mark.parametrize("explicit_defaults", [False, True])
def test_remote_legacy_entry_cannot_bypass_saved_batch_options(workbench, monkeypatch, explicit_defaults):
    from test_soc_corpus_experiments import context, service

    from app.gateway.routers.soc_corpus_workbench import process_corpus_workbench_alert
    from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
    from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchProcessRequest, SocCorpusWorkbenchRunControls

    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    workbench._run_controls = SocCorpusWorkbenchRunControls(defaults=SocAnalysisExecutionOptions())
    calls = []
    batches = service(workbench._repository, calls)
    batches.prepare(workbench.batch_plan, experiment_id="EXP-current", name="Current corpus", context=context())
    batches.create_round(
        CorpusRoundCreateCommand(
            experiment_id="EXP-current",
            selection=CorpusRoundSelection(batch="learning"),
            options=SocAnalysisExecutionOptions(normalization_review_mode="apply"),
        ),
        context=context(),
    )
    monkeypatch.setattr(workbench, "_select_run_service", lambda *args: pytest.fail("legacy admission must fail before Runtime construction"))
    request = Request({"type": "http", "client": ("192.0.2.10", 1234), "headers": []})
    request.state.user = SimpleNamespace(id="test-operator", system_role="admin")
    request.state.auth_source = "session"
    body = SocCorpusWorkbenchProcessRequest(settings=workbench.run_controls.defaults) if explicit_defaults else None
    with pytest.raises(HTTPException) as denied:
        process_corpus_workbench_alert("0", request, workbench, body)
    assert denied.value.status_code == 400
    assert "不能使用旧入口绕过固定经验与配置" in denied.value.detail
    assert workbench.get_activity().active_count == 0
    assert not calls
