"""Explicit saved-Run continuation never reruns providers or rewrites reports."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_soc_corpus_continuous_learning import _finish, _fixture, _validation
from test_soc_corpus_experiments import context

from soc_agent.contracts import AlertInput
from soc_agent.contracts.corpus_experiments import CorpusPatternBackfillCommand, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.db.corpus_experiments import CorpusExperimentConflict, SqlAlchemyCorpusExperimentRepository
from soc_agent.demo.corpus_experiment_cli import main
from soc_agent.demo.corpus_pattern_continuation import SocCorpusPatternContinuationService
from soc_agent.memory.profiles import SocMemoryProfileRegistry


def _legacy_fixture(tmp_path, monkeypatch):
    create = SqlAlchemyCorpusExperimentRepository.create_round
    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyCorpusExperimentRepository, "create_round", lambda store, round_: create(store, round_.model_copy(update={"pattern_accumulation_scope": None})))
        fixture = _fixture(tmp_path)
        validation = _validation(fixture)
        _finish(fixture.service, validation)
    fixture.learning = fixture.service.store.get_round(fixture.learning.round_id)
    fixture.validation = fixture.service.store.get_round(validation.round_id)
    fixture.continuation = SocCorpusPatternContinuationService(coordinator=fixture.service, profiles=SocMemoryProfileRegistry())
    assert fixture.learning.pattern_accumulation_scope is None
    assert fixture.validation.pattern_accumulation_scope is None
    return fixture


def _command(fixture, round_):
    return CorpusPatternBackfillCommand(
        expected_round_version=fixture.service.store.get_round(round_.round_id).version,
        source_runs={item.alert_id: item.job.run_id for item in fixture.service.store.list_round_items(round_.round_id).items if item.job.status.value == "completed"},
    )


def _continuous_observations(fixture):
    return [item for item in fixture.repo.list_memory_pattern_observations(limit=100) if item.accumulation_scope and item.accumulation_scope.accumulation_mode == "continuous"]


def test_legacy_learning_and_validation_join_one_stream_without_models_or_report_changes(tmp_path, monkeypatch):
    fixture = _legacy_fixture(tmp_path, monkeypatch)
    before_rounds = fixture.service.store.list_rounds()
    before_jobs = {round_.round_id: fixture.service.store.list_round_items(round_.round_id) for round_ in before_rounds}
    before_runs = fixture.repo.list_runs(limit=100)
    calls = list(fixture.calls)
    fixture.service._configuration_provider = lambda _: {"code": "new-deployment"}
    learning_command = _command(fixture, fixture.learning)
    dry = fixture.continuation.accumulate(fixture.learning.round_id, learning_command, context=context(), dry_run=True)
    assert dry["counts"] == {"eligible": 4}
    assert _continuous_observations(fixture) == []

    learned = fixture.continuation.accumulate(fixture.learning.round_id, learning_command, context=context())
    assert learned["counts"] == {"accumulated": 4}
    validated = fixture.continuation.accumulate(fixture.validation.round_id, _command(fixture, fixture.validation), context=context())
    assert validated["counts"] == {"accumulated": 1}
    candidate_id = validated["items"][0]["candidate_id"]
    assert fixture.repo.get_memory_candidate(candidate_id).status.value == "pending_review"
    observations = _continuous_observations(fixture)
    assert len(observations) == 5
    assert len({item.aggregation_key for item in observations}) == 1
    assert fixture.service.store.list_rounds() == before_rounds
    assert {round_.round_id: fixture.service.store.list_round_items(round_.round_id) for round_ in before_rounds} == before_jobs
    assert fixture.repo.list_runs(limit=100) == before_runs
    assert fixture.calls == calls
    repeated = fixture.continuation.accumulate(fixture.learning.round_id, learning_command, context=context())
    assert repeated["counts"] == {"accumulated": 4}
    assert len(_continuous_observations(fixture)) == 5


def test_backfill_rejects_stale_version_and_foreign_sources_atomically(tmp_path, monkeypatch):
    fixture = _legacy_fixture(tmp_path, monkeypatch)
    command = _command(fixture, fixture.learning)
    with pytest.raises(CorpusExperimentConflict, match="刷新|version"):
        fixture.continuation.accumulate(fixture.learning.round_id, command.model_copy(update={"expected_round_version": command.expected_round_version + 1}), context=context())
    foreign = command.model_copy(update={"source_runs": {**command.source_runs, "4": fixture.runs["4"].run_id}})
    with pytest.raises(ValueError, match="member|成员"):
        fixture.continuation.accumulate(fixture.learning.round_id, foreign, context=context())
    corrupted = fixture.repo.get_run(command.source_runs["1"])
    corrupted.input_hash = "f" * 64
    fixture.repo.save_run(corrupted)
    with pytest.raises(CorpusExperimentConflict, match="identity|身份"):
        fixture.continuation.accumulate(fixture.learning.round_id, command, context=context())
    assert _continuous_observations(fixture) == []


def test_newer_current_job_excludes_old_result_and_other_running_round_blocks_backfill(tmp_path, monkeypatch):
    fixture = _legacy_fixture(tmp_path, monkeypatch)
    command = _command(fixture, fixture.validation)
    newer = fixture.service.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", alert_ids=["4"]), memory_mode="none"), context=context())
    result = fixture.continuation.accumulate(fixture.validation.round_id, command, context=context())
    assert result["counts"] == {"skipped": 1}
    assert result["items"][0]["reason"] == "newer_batch_result_exists"
    fixture.service.start(newer.round_id, context=context())
    with pytest.raises(CorpusExperimentConflict, match="暂停|quiescent"):
        fixture.continuation.accumulate(fixture.learning.round_id, _command(fixture, fixture.learning), context=context())
    assert _continuous_observations(fixture) == []


@pytest.mark.parametrize("source", ["request", "canonical"])
def test_saved_run_tenant_must_match_experiment_before_any_observation_write(tmp_path, monkeypatch, source):
    fixture = _legacy_fixture(tmp_path, monkeypatch)
    command = _command(fixture, fixture.learning)
    run = fixture.repo.get_run(command.source_runs["1"])
    if source == "request":
        run.llm_analysis_request.tenant_id = "foreign-tenant"
    else:
        run.normalized_alert = AlertInput(alert_id=run.alert_id, tenant_id="foreign-tenant")
    fixture.repo.save_run(run)
    with pytest.raises(CorpusExperimentConflict, match="tenant|租户"):
        fixture.continuation.accumulate(fixture.learning.round_id, command, context=context())
    assert _continuous_observations(fixture) == []


def test_accumulate_api_requires_admin_and_never_starts_dispatcher(tmp_path, monkeypatch):
    from test_soc_corpus_experiment_api import client

    from app.gateway.routers import soc_corpus_experiments as routes

    fixture = _legacy_fixture(tmp_path, monkeypatch)
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    http, application, _ = client(api_dir)
    application.service = fixture.service
    application.dispatcher = SimpleNamespace(start=lambda: pytest.fail("saved-Run continuation must not dispatch Runtime"))
    monkeypatch.setattr(routes, "build_soc_memory_profile_registry", SocMemoryProfileRegistry)
    command = _command(fixture, fixture.learning).model_dump(mode="json")
    endpoint = f"/api/soc/dev/corpus-workbench/rounds/{fixture.learning.round_id}/accumulate"
    assert http.post(endpoint, json=command, headers={"test-role": "user"}).status_code == 403
    assert http.post(endpoint, json={"source_runs": command["source_runs"]}).status_code == 422
    assert http.post(endpoint, json={**command, "expected_round_version": command["expected_round_version"] + 1}).status_code == 409
    planned = http.post(endpoint, json=command, params={"dry_run": True})
    assert planned.status_code == 200 and planned.json()["counts"] == {"eligible": 4}
    assert _continuous_observations(fixture) == []
    applied = http.post(endpoint, json=command)
    assert applied.status_code == 200, applied.text
    assert applied.json()["counts"] == {"accumulated": 4}
    assert applied.json()["model_calls"] == 0


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_accumulate_pages_explicit_saved_results_without_start_or_model(dry_run, capsys):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/items"):
            offset = int(request.url.params["offset"])
            assert request.url.params["limit"] == "100"
            return httpx.Response(200, json={"total": 101, "items": [{"alert_id": str(i), "job": {"status": "completed" if i != 100 else "failed", "run_id": f"RUN-{i}"}} for i in range(offset, min(offset + 100, 101))]})
        if request.method == "GET":
            return httpx.Response(200, json={"round": {"round_id": "ROUND-old", "version": 9, "state": "completed"}, "active_count": 0})
        assert request.url.path.endswith("/rounds/ROUND-old/accumulate")
        body = json.loads(request.content)
        assert body == {"expected_round_version": 9, "source_runs": {str(i): f"RUN-{i}" for i in range(100)}}
        assert request.url.params.get("dry_run") == ("true" if dry_run else "false")
        return httpx.Response(
            200,
            json={
                "round_id": "ROUND-old",
                "counts": {"eligible" if dry_run else "accumulated": 98, "skipped": 2},
                "items": [{"run_id": f"RUN-{i}", "status": "skipped", "reason": "newer_batch_result_exists"} for i in range(2)],
                "model_calls": 0,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost") as client:
        assert main(["accumulate", "--round", "ROUND-old", *(["--dry-run"] if dry_run else [])], client=client) == 0
    assert len([request for request in calls if request.method == "POST"]) == 1
    output = capsys.readouterr().out
    assert "RUN-" not in output
    assert json.loads(output) == {
        "round_id": "ROUND-old",
        "dry_run": dry_run,
        "counts": {"eligible" if dry_run else "accumulated": 98, "skipped": 2},
        "model_calls": 0,
        "processed": 100,
        "skipped_reasons": {"newer_batch_result_exists": 2},
    }
