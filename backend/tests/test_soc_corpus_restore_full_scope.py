"""Explicit round recovery restores dispatch scope without replacing saved work."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from test_soc_corpus_experiment_api import client
from test_soc_corpus_experiment_repository import repository
from test_soc_corpus_experiments import context, prepare, service

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiment_cli import main
from soc_agent.llm import SocLLMSettings


def _narrowed_round(svc):
    round_ = svc.create_round(
        CorpusRoundCreateCommand(
            experiment_id="EXP-test",
            selection=CorpusRoundSelection(batch="learning"),
            options=SocAnalysisExecutionOptions(normalization_review_mode="apply"),
            execution_limit=100,
        ),
        context=context(),
    )
    with svc.repository.mutation_transaction() as tx:
        tx.lock_memory_governance()
        tx.corpus_experiments().set_dispatch_scope(round_.round_id, [item.alert_id for item in svc.store.list_round_items(round_.round_id).items if item.alert_id not in {"0", "1"}])
    svc.pause(round_.round_id, context=context())
    return svc.store.get_round(round_.round_id)


def _setup(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls, config = [], {"model": "fake", "version": 1}
    svc = service(repo, calls, config=config)
    return svc, _narrowed_round(svc), calls, config


def _jobs(svc, round_id):
    return {item.alert_id: item.model_dump(mode="json") for item in svc.store.list_round_items(round_id).items}


def test_default_round_resume_preserves_narrowed_dispatch_scope(tmp_path):
    svc, round_, calls, _ = _setup(tmp_path)
    resumed = svc.start(round_.round_id, context=context(), execution_limit=2_147_483_647)
    assert resumed.dispatch_alert_ids == round_.dispatch_alert_ids
    while svc.execute_one(round_.round_id):
        pass
    assert len(calls) == 8
    assert {alert_id for alert_id, item in _jobs(svc, round_.round_id).items() if item["job"]["status"] == "queued"} == {"0", "1"}


def test_explicit_restore_runs_only_two_excluded_jobs_and_retains_eight_completed(tmp_path):
    svc, round_, calls, _ = _setup(tmp_path)
    svc.start(round_.round_id, context=context())
    while svc.execute_one(round_.round_id):
        pass
    assert len(calls) == 8
    before_jobs = _jobs(svc, round_.round_id)
    before_round = svc.store.get_round(round_.round_id)
    mutable_fields = {"state", "state_reason", "version", "updated_at", "state_history", "dispatch_alert_ids"}

    restored = svc.start(round_.round_id, context=context(), restore_full_scope=True)

    assert restored.dispatch_alert_ids is None
    assert restored.model_dump(exclude=mutable_fields) == before_round.model_dump(exclude=mutable_fields)
    assert restored.state_history[:-1] == before_round.state_history
    assert restored.state_history[-1]["reason"] == "full_dispatch_scope_restored"
    assert _jobs(svc, round_.round_id) == before_jobs
    while svc.execute_one(round_.round_id):
        pass
    assert [call[1] for call in calls[8:]] == ["0", "1"]
    after_jobs = _jobs(svc, round_.round_id)
    assert all(after_jobs[alert_id] == before_jobs[alert_id] for alert_id in round_.dispatch_alert_ids)
    assert svc.store.round_progress(round_.round_id).completed_count == 10
    assert len({call[1] for call in calls}) == len(calls) == 10


@pytest.mark.parametrize("invalid", [{"execution_limit": 1}, {"concurrency": 17}])
def test_rejected_restore_preserves_scope_state_and_jobs(tmp_path, invalid):
    svc, round_, calls, _ = _setup(tmp_path)
    before_jobs = _jobs(svc, round_.round_id)

    with pytest.raises(ValueError):
        svc.start(round_.round_id, context=context(), restore_full_scope=True, **invalid)

    assert svc.store.get_round(round_.round_id) == round_
    assert _jobs(svc, round_.round_id) == before_jobs
    assert calls == []


def test_restore_write_failure_rolls_back_scope_and_state_together(tmp_path):
    svc, round_, calls, _ = _setup(tmp_path)
    before_jobs = _jobs(svc, round_.round_id)

    def fail_write(_):
        raise RuntimeError("injected round write failure")

    svc.repository._mutation_write_hook = fail_write
    with pytest.raises(RuntimeError, match="injected round write failure"):
        svc.start(round_.round_id, context=context(), restore_full_scope=True)

    assert svc.store.get_round(round_.round_id) == round_
    assert _jobs(svc, round_.round_id) == before_jobs
    assert calls == []


def test_restore_does_not_bypass_configuration_drift(tmp_path):
    svc, round_, calls, config = _setup(tmp_path)
    before_jobs = _jobs(svc, round_.round_id)
    config["version"] = 2

    with pytest.raises(ValueError, match="configuration_changed"):
        svc.start(round_.round_id, context=context(), restore_full_scope=True)

    blocked = svc.store.get_round(round_.round_id)
    assert blocked.state == "blocked"
    assert blocked.dispatch_alert_ids == round_.dispatch_alert_ids
    assert blocked.options == round_.options
    assert _jobs(svc, round_.round_id) == before_jobs
    assert calls == []
    with pytest.raises(ValueError, match="configuration_changed"):
        svc.start(round_.round_id, context=context(), restore_full_scope=True)
    assert svc.store.get_round(round_.round_id) == blocked


def test_real_implementation_upgrade_blocks_old_round_scope_restoration(tmp_path, monkeypatch):
    from soc_agent.application import corpus_experiments as application

    root = tmp_path / "deployment"
    source = root / "backend" / "soc_agent" / "runtime.py"
    source.parent.mkdir(parents=True)
    source.write_text("BEHAVIOR = 'before'\n", encoding="utf-8")
    monkeypatch.setattr(application, "__file__", str(source.parent / "application" / "corpus_experiments.py"))
    monkeypatch.setattr(application, "get_app_config", lambda: SimpleNamespace(models=[]))
    application._implementation_hash.cache_clear()
    try:
        repo = repository(tmp_path)
        prepare(repo)
        calls = []
        svc = service(repo, calls)
        svc._configuration_provider = lambda options: application.corpus_configuration_snapshot(SocLLMSettings(), options)
        round_ = _narrowed_round(svc)
        before_jobs = _jobs(svc, round_.round_id)
        previous_hash = round_.config_snapshot["implementation_hash"]

        source.write_text("BEHAVIOR = 'after-upgrade'\n", encoding="utf-8")
        # A deployment restart recomputes the real cached source-content hash.
        application._implementation_hash.cache_clear()
        assert application.corpus_configuration_snapshot(SocLLMSettings(), round_.options)["implementation_hash"] != previous_hash
        with pytest.raises(ValueError, match="configuration_changed"):
            svc.start(round_.round_id, context=context(), restore_full_scope=True)

        blocked = svc.store.get_round(round_.round_id)
        assert blocked.state == "blocked"
        assert blocked.config_hash == round_.config_hash
        assert blocked.config_snapshot == round_.config_snapshot
        assert blocked.dispatch_alert_ids == round_.dispatch_alert_ids
        assert _jobs(svc, round_.round_id) == before_jobs
        assert calls == []
    finally:
        application._implementation_hash.cache_clear()


@pytest.mark.parametrize("restore", [False, True])
def test_round_start_api_restores_scope_only_with_explicit_flag(tmp_path, restore):
    http, app, calls = client(tmp_path)
    round_ = _narrowed_round(app.service)
    app.dispatcher.start = Mock()
    path = f"/api/soc/dev/corpus-workbench/rounds/{round_.round_id}/start"
    body = {"restore_full_scope": True} if restore else {}
    before_jobs = _jobs(app.service, round_.round_id)

    denied = http.post(path, json=body, headers={"test-role": "user"})
    assert denied.status_code == 403
    assert app.service.store.get_round(round_.round_id) == round_
    response = http.post(path, json=body)

    assert response.status_code == 202, response.text
    assert response.json()["dispatch_alert_ids"] == (None if restore else round_.dispatch_alert_ids)
    assert _jobs(app.service, round_.round_id) == before_jobs
    app.dispatcher.start.assert_called_once()
    assert calls == []


@pytest.mark.parametrize("value", ["true", 1])
def test_scope_restore_api_requires_a_boolean_opt_in(tmp_path, value):
    http, app, calls = client(tmp_path)
    round_ = _narrowed_round(app.service)
    app.dispatcher.start = Mock()
    response = http.post(f"/api/soc/dev/corpus-workbench/rounds/{round_.round_id}/start", json={"restore_full_scope": value})
    assert response.status_code == 422
    assert app.service.store.get_round(round_.round_id) == round_
    app.dispatcher.start.assert_not_called()
    assert calls == []


@pytest.mark.parametrize("restore", [False, True])
def test_cli_resume_transmits_scope_restore_only_when_requested(restore):
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(202, json={"round_id": "ROUND-full", "state": "running"})

    args = ["resume", "--round", "ROUND-full", "--limit", "all"]
    if restore:
        args.append("--restore-full-scope")
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as http:
        assert main(args, client=http) == 0

    expected = {"execution_limit": 2_147_483_647}
    if restore:
        expected["restore_full_scope"] = True
    assert requests == [("POST", "/api/soc/dev/corpus-workbench/rounds/ROUND-full/start", expected)]
