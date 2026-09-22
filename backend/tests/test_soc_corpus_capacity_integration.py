"""Host throughput changes fence admission without rewriting queued work."""

from threading import Event, Lock
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from test_soc_corpus_experiment_api import client
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context

from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.demo.corpus_capacity import CorpusCapacity
from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService

ROOT = "/api/soc/dev/corpus-workbench"


def wait_for(predicate):
    deadline = monotonic() + 10
    while not predicate():
        assert monotonic() < deadline
        sleep(0.01)


def test_host_capacity_save_survives_restart_and_needs_no_database_writer(tmp_path, monkeypatch):
    remote, app, calls = client(tmp_path)
    local = TestClient(remote.app, client=("127.0.0.1", 1234))
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    capacity = CorpusCapacity(ceiling=3, path=tmp_path / "capacity.json")
    app.service.capacity = capacity
    # Hold SQLite's exclusive writer: throughput settings must not need any DB access.
    with app.service.repository._session_factory() as session:
        session.connection().exec_driver_sql("BEGIN EXCLUSIVE")
        response = local.post(ROOT + "/experiments/concurrency", json={"max_concurrency": 2})
        assert response.status_code == 200
        assert response.json() == {"max_concurrency": 2, "concurrency_limit": 3}
        session.rollback()
    assert CorpusCapacity(ceiling=3, path=tmp_path / "capacity.json").max_concurrency == 2
    for batch in ("learning", "validation"):
        data = remote.get(ROOT + "/experiments/configuration?batch=" + batch).json()
        assert (data["max_concurrency"], data["concurrency_limit"]) == (2, 3)
        assert data["can_configure"] is False
    assert not calls and not app.dispatcher.is_running


@pytest.mark.parametrize("value", [0, 9, 2.5, "2", True])
def test_invalid_capacity_never_changes_active_value(tmp_path, value):
    http, app, _ = client(tmp_path)
    response = http.post(ROOT + "/experiments/concurrency", json={"max_concurrency": value})
    assert response.status_code in (400, 422)
    assert app.service.capacity.max_concurrency == 3


def test_lan_cannot_override_capacity_even_with_spoofed_headers(tmp_path, monkeypatch):
    remote, app, _ = client(tmp_path)
    monkeypatch.setenv("SOC_DEV_CORPUS_LOCAL_CONTROL_ONLY", "true")
    response = remote.post(ROOT + "/experiments/concurrency", json={"max_concurrency": 1}, headers={"X-Forwarded-For": "127.0.0.1", "Host": "localhost"})
    assert response.status_code == 403
    assert app.service.capacity.max_concurrency == 3
    denied = remote.post(ROOT + "/experiments/concurrency", json={"max_concurrency": 1}, headers={"test-role": "user"})
    assert denied.status_code == 403


def test_lowering_running_eight_to_two_drains_then_raising_uses_existing_queue(tmp_path):
    repo = repository(tmp_path)
    repo.corpus_experiments().prepare(experiment(), [m.model_copy(update={"group_id": "group-" + m.alert_id}) for m in members(24)])
    capacity = CorpusCapacity(ceiling=8)
    release_all, lock = Event(), Lock()
    started, release = [], {}

    def execute(round_, member, request):
        event = Event()
        with lock:
            started.append(member.alert_id)
            release[member.alert_id] = event
        deadline = monotonic() + 15
        while not (event.wait(0.02) or release_all.is_set()):
            assert monotonic() < deadline
        return CorpusExecutionOutcome(run_id="RUN-" + member.alert_id)

    svc = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda _: {"model": "fake"}, max_concurrency=8, capacity=capacity)
    round_ = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="learning"), concurrency=8, execution_limit=100), context=context())
    svc.start(round_.round_id, context=context())
    before = svc.store.get_round(round_.round_id)
    jobs_before = [item.job_id for item in svc.store.list_round_items(round_.round_id).items]
    dispatcher = CorpusExperimentDispatcher(svc, max_concurrency=8, interval_seconds=0.02)
    try:
        dispatcher.start()
        wait_for(lambda: len(started) == 8)
        capacity.set_limit(2, actor_id="host")
        assert svc.store.active_job_count() == 8
        assert not svc.execute_one(round_.round_id)
        for alert_id in started[:6]:
            release[alert_id].set()
        wait_for(lambda: svc.store.active_job_count() == 2)
        sleep(0.1)
        assert len(started) == 8
        release[started[6]].set()
        wait_for(lambda: len(started) == 9)
        assert svc.store.active_job_count() == 2
        capacity.set_limit(8, actor_id="host")
        wait_for(lambda: svc.store.active_job_count() == 8)
        assert svc.store.get_round(round_.round_id) == before
        assert [item.job_id for item in svc.store.list_round_items(round_.round_id).items] == jobs_before
    finally:
        svc.pause(round_.round_id, context=context())
        release_all.set()
        dispatcher.stop()
    assert len(started) == len(set(started))
    assert svc.store.round_progress(round_.round_id).counts.get("failed", 0) == 0
