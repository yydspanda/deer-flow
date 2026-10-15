"""A displayed failed task can be recovered without mutating neighboring rounds."""

from types import SimpleNamespace

import pytest
from test_soc_corpus_experiments import context
from test_soc_corpus_same_round_retry import fixture

from soc_agent.contracts.corpus_experiments import CorpusQuickCommand, CorpusRoundCreateCommand
from soc_agent.demo.corpus_quick_validation import CorpusQuickValidation


@pytest.mark.parametrize("conflict", ["stale_job", "stale_attempt", "target_snapshot"])
def test_rejected_single_retry_does_not_block_other_drifted_rounds(tmp_path, conflict):
    svc, original, calls, config = fixture(tmp_path, {"5": "runtime_failed", "6": "success"})
    sibling = svc.create_round(
        CorpusRoundCreateCommand(experiment_id=original.experiment_id, selection=original.selection.model_copy(update={"alert_ids": ["6"]}), memory_mode="none"),
        context=context(),
    )
    target = svc.store.list_round_items(original.round_id).items[0].job
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id="a" * 64))
    before_rounds = svc.store.list_rounds()
    before_items = svc.store.list_round_items(original.round_id)
    before_calls = list(calls)
    config["model"] = "changed-snapshot"
    command = CorpusQuickCommand(
        batch="validation",
        action="retry",
        alert_id="5",
        job_id="JOB-obsolete" if conflict == "stale_job" else target.job_id,
        expected_attempt_count=target.attempt_count + (conflict == "stale_attempt"),
    )

    with pytest.raises(ValueError, match="configuration_changed" if conflict == "target_snapshot" else "刷新"):
        quick.command(command, context=context())

    assert svc.store.list_rounds() == before_rounds
    assert svc.store.get_round(sibling.round_id).state == "prepared"
    assert svc.store.list_round_items(original.round_id) == before_items
    assert calls == before_calls


def test_manual_model_retry_leaves_same_and_other_round_queues_paused(tmp_path):
    svc, original, calls, _ = fixture(tmp_path, {"5": "runtime_failed", "6": "success", "7": "success"})
    selected = svc.create_round(
        CorpusRoundCreateCommand(experiment_id=original.experiment_id, selection=original.selection.model_copy(update={"alert_ids": ["5", "6"]}), memory_mode="none"),
        context=context(),
    )
    svc.pause(selected.round_id, context=context())
    svc.request_manual(selected.round_id, "5", context=context())
    assert svc.execute_one(selected.round_id)
    sibling = svc.create_round(
        CorpusRoundCreateCommand(experiment_id=original.experiment_id, selection=original.selection.model_copy(update={"alert_ids": ["7"]}), memory_mode="none"),
        context=context(),
    )
    svc.pause(sibling.round_id, context=context())
    target, queued = svc.store.list_round_items(selected.round_id).items
    sibling_before = svc.store.list_round_items(sibling.round_id)
    calls_before = len(calls)
    quick = CorpusQuickValidation(svc, SimpleNamespace(plan_id="a" * 64))
    command = CorpusQuickCommand(batch="validation", action="retry", alert_id="5", job_id=target.job_id, expected_attempt_count=target.job.attempt_count)

    assert quick.command(command, context=context())["round_id"] == selected.round_id
    assert svc.store.get_round(selected.round_id).state == "paused"
    assert svc.execute_one(selected.round_id)
    assert not svc.execute_one(selected.round_id)
    assert not svc.execute_one(sibling.round_id)

    assert len(calls) == calls_before + 1
    assert svc.store.list_round_items(selected.round_id).items[1].job == queued.job
    assert svc.store.list_round_items(sibling.round_id) == sibling_before
    assert svc.store.get_round(sibling.round_id).state == "paused"
