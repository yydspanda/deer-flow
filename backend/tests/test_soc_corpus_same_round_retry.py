"""Recovery stays in its fixed round without changing the experiment settings."""

from types import SimpleNamespace

import pytest
from test_soc_corpus_experiment_repository import experiment, members, repository
from test_soc_corpus_experiments import context

from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusQuickCommand, CorpusRoundCreateCommand, CorpusRoundRetryCommand, CorpusRoundSelection
from soc_agent.demo.corpus_experiments import CorpusExecutionError, SocCorpusExperimentService
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation


def fixture(tmp_path, statuses=None):
    repo = repository(tmp_path)
    repo.corpus_experiments().prepare(experiment(), [member.model_copy(update={"batch": "validation", "validation_tier": "supplementary"}) for member in members()])
    calls = []
    statuses = statuses or {"5": "failed", "6": "success", "7": "skipped"}
    config = {"model": "fixture"}

    def execute(round_, member, ctx, *, semantic_retry=None):
        calls.append((member.alert_id, ctx.idempotency_key, semantic_retry))
        if statuses[member.alert_id] == "runtime_failed":
            raise CorpusExecutionError("model unavailable", retryable=True, run_id="RUN-failed")
        return CorpusExecutionOutcome(
            run_id=f"RUN-{member.alert_id}-{len(calls)}",
            summary={"phase_timings": [{"phase": "normalization_assist", "status": "success" if semantic_retry else statuses[member.alert_id]}]},
        )

    svc = SocCorpusExperimentService(repository=repo, execute=execute, configuration_provider=lambda _: config)
    round_ = svc.create_round(
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=CorpusRoundSelection(batch="validation", scope="all", alert_ids=list(statuses)), memory_mode="none", options={"normalization_review_mode": "apply"}),
        context=context(),
    )
    svc.start(round_.round_id, context=context())
    while svc.execute_one(round_.round_id):
        pass
    return svc, round_, calls, config


def test_semantic_retry_changes_attempt_not_round_or_other_results(tmp_path):
    svc, round_, calls, _ = fixture(tmp_path)
    before = svc.store.list_round_items(round_.round_id).items
    result = svc.retry_items(round_.round_id, CorpusRoundRetryCommand(expected_round_version=svc.store.get_round(round_.round_id).version, kind="semantic_failed", concurrency=2), context=context())
    assert result["retried"] == 1
    assert svc.execute_one(round_.round_id)
    assert not svc.execute_one(round_.round_id)
    after = svc.store.list_round_items(round_.round_id).items
    assert [item.job_id for item in after] == [item.job_id for item in before]
    assert after[0].job.run_id != before[0].job.run_id
    assert after[0].job.attempt_count == 2
    assert [item.job for item in after[1:]] == [item.job for item in before[1:]]
    assert calls[-1][1] != next(key for alert, key, _ in calls if alert == "5")
    assert calls[-1][2].previous_run_id == before[0].job.run_id
    assert len(svc.store.list_rounds()) == 1
    saved = svc.store.get_round(round_.round_id)
    assert saved.options == round_.options and saved.config_hash == round_.config_hash
    assert saved.memory_snapshot == round_.memory_snapshot and saved.concurrency == 2
    assert svc.store.round_progress(round_.round_id).completed_count == 3
    events = svc.jobs.list_events(before[0].job_id)
    recovery = next(event for event in events if event.event_type == "corpus_semantic_retry")
    assert recovery.details["previous_result"] == before[0].job.result_payload


@pytest.mark.parametrize("status", ["success", "skipped", "partial"])
def test_semantic_retry_does_not_retry_nonfailed_semantics(tmp_path, status):
    svc, round_, calls, _ = fixture(tmp_path, {"5": status})
    assert svc.retry_items(round_.round_id, CorpusRoundRetryCommand(expected_round_version=svc.store.get_round(round_.round_id).version, kind="semantic_failed"), context=context())["retried"] == 0
    assert len(calls) == 1


def test_retry_is_atomic_on_snapshot_or_selection_drift(tmp_path):
    svc, round_, _, config = fixture(tmp_path)
    before = svc.store.list_round_items(round_.round_id)
    for command in (
        CorpusRoundRetryCommand(expected_round_version=svc.store.get_round(round_.round_id).version, kind="semantic_failed", alert_ids=["5", "not-a-member"]),
        CorpusRoundRetryCommand(expected_round_version=svc.store.get_round(round_.round_id).version, kind="semantic_failed", concurrency=4),
    ):
        with pytest.raises(ValueError):
            svc.retry_items(round_.round_id, command, context=context())
        assert svc.store.list_round_items(round_.round_id) == before
    config["model"] = "changed"
    with pytest.raises(ValueError, match="configuration_changed"):
        svc.retry_items(round_.round_id, CorpusRoundRetryCommand(expected_round_version=svc.store.get_round(round_.round_id).version, kind="semantic_failed"), context=context())
    assert svc.store.list_round_items(round_.round_id) == before


def test_stale_bulk_retry_cannot_consume_another_attempt(tmp_path):
    svc, round_, _, _ = fixture(tmp_path, {"5": "runtime_failed"})
    command = CorpusRoundRetryCommand(kind="failed", expected_round_version=svc.store.get_round(round_.round_id).version)
    assert svc.retry_items(round_.round_id, command, context=context())["retried"] == 1
    assert svc.execute_one(round_.round_id)
    before = svc.store.list_round_items(round_.round_id)
    with pytest.raises(ValueError, match="刷新"):
        svc.retry_items(round_.round_id, command, context=context())
    assert svc.store.list_round_items(round_.round_id) == before


def test_semantic_recovery_rejects_facts_and_disabled_review(tmp_path):
    svc, round_, _, _ = fixture(tmp_path)
    svc.store.prepare(experiment().model_copy(update={"experiment_id": "EXP-facts", "fact_source_experiment_id": "EXP-test"}), [member.model_copy(update={"batch": "validation", "validation_tier": "supplementary"}) for member in members()])
    for command in (
        CorpusRoundCreateCommand(experiment_id="EXP-test", selection=round_.selection, memory_mode="none"),
        CorpusRoundCreateCommand(experiment_id="EXP-facts", selection=round_.selection, purpose="facts", memory_mode="none", options={"normalization_review_mode": "apply"}),
    ):
        target = svc.create_round(command, context=context())
        with pytest.raises(ValueError):
            svc.retry_items(target.round_id, CorpusRoundRetryCommand(kind="semantic_failed", expected_round_version=target.version), context=context())


def test_quick_model_retry_keeps_original_job_and_frozen_settings(tmp_path):
    svc, round_, calls, _ = fixture(tmp_path, {"5": "runtime_failed", "6": "success"})
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id="a" * 64))
    job = svc.store.list_round_items(round_.round_id).items[0].job
    cmd = CorpusQuickCommand(batch="validation", action="retry", alert_id="5", job_id=job.job_id, expected_attempt_count=1)
    assert quick.command(cmd, context=context())["accepted"]
    assert quick.command(cmd, context=context())["accepted"]  # Duplicate delivery before execution.
    assert svc.execute_one(round_.round_id)
    assert not svc.execute_one(round_.round_id)
    assert len(svc.store.list_rounds()) == 1
    after = svc.store.list_round_items(round_.round_id).items[0].job
    assert after.job_id == job.job_id and after.attempt_count == 2
    assert svc.store.get_round(round_.round_id).options == round_.options
    assert len({key for alert, key, _ in calls if alert == "5"}) == 1  # Ordinary recovery retains the Runtime journal chain.
    with pytest.raises(ValueError, match="刷新"):
        quick.command(cmd, context=context())  # A delayed first-attempt click cannot retry the next failure.


def test_bulk_retry_does_not_resume_unrelated_queued_work(tmp_path):
    svc, round_, calls, _ = fixture(tmp_path, {"5": "runtime_failed", "6": "success", "7": "success"})
    # Preserve a queued sibling by preparing a fresh budget-limited round.
    fresh = svc.create_round(CorpusRoundCreateCommand(experiment_id="EXP-test", selection=round_.selection, memory_mode="none", execution_limit=1), context=context())
    svc.start(fresh.round_id, context=context())
    assert svc.execute_one(fresh.round_id)
    count = len(calls)
    assert svc.retry_items(fresh.round_id, CorpusRoundRetryCommand(expected_round_version=svc.store.get_round(fresh.round_id).version, kind="failed"), context=context())["retried"] == 1
    assert svc.execute_one(fresh.round_id)
    assert not svc.execute_one(fresh.round_id)
    assert len(calls) == count + 1
    assert sum(item.job.status.value == "queued" for item in svc.store.list_round_items(fresh.round_id).items) == 2


def test_retry_api_requires_authority_version_and_dispatches_only_after_acceptance(tmp_path):
    from test_soc_corpus_experiment_api import client

    from app.gateway.routers import soc_corpus_experiments as routes

    svc, round_, _, _ = fixture(tmp_path)
    api_path = tmp_path / "api"
    api_path.mkdir()
    http, application, _ = client(api_path)
    started = []
    application.service = svc
    application.dispatcher = SimpleNamespace(start=lambda: started.append(True))
    http.app.dependency_overrides[routes.get_corpus_experiment_application] = lambda: application
    path = f"/api/soc/dev/corpus-workbench/rounds/{round_.round_id}/retry"
    body = {"kind": "semantic_failed", "expected_round_version": svc.store.get_round(round_.round_id).version}
    assert http.post(path, json=body, headers={"test-role": "user"}).status_code == 403
    assert http.post(path, json={"kind": "semantic_failed"}).status_code == 422
    assert http.post(path, json={**body, "expected_round_version": 999}).status_code == 409
    assert started == []
    response = http.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {"round_id": round_.round_id, "retried": 1, "not_retryable": 0}
    assert started == [True]
    assert http.post(path, json=body).status_code == 409
    assert started == [True]
