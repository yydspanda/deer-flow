from datetime import UTC, datetime, timedelta

import pytest

from soc_agent.contracts.corpus_experiments import CorpusRound, CorpusRoundProgress, CorpusRoundSelection
from soc_agent.demo.corpus_experiment_timing import item_timing, round_timing

START = datetime(2026, 9, 18, tzinfo=UTC)


def at(seconds):
    return START + timedelta(seconds=seconds)


def round_(state="running", history=None):
    return CorpusRound(
        round_id="ROUND-timing",
        experiment_id="EXP-timing",
        selection=CorpusRoundSelection(batch="learning"),
        config_hash="a" * 64,
        config_snapshot={},
        created_by="tester",
        created_at=START,
        execution_limit=10,
        state=state,
        state_history=history
        if history is not None
        else [
            {"from": "prepared", "to": "running", "at": at(10).isoformat(), "execution_limit": 5},
            {"from": "running", "to": "paused", "at": at(70).isoformat(), "execution_limit": 5},
            {"from": "paused", "to": "running", "at": at(130).isoformat(), "execution_limit": 10},
        ],
    )


def progress(round_value=None, completed=5, failed=1):
    return CorpusRoundProgress(round=round_value or round_(), selected_count=100, admitted_count=10, counts={"completed": completed, "failed": failed}, active_count=1, completed_count=completed, failed_count=failed)


def test_estimate_only_current_admitted_budget_excludes_pauses():
    result = round_timing(progress(), now=at(190))
    assert result.elapsed_ms == 190000
    assert result.running_ms == 120000
    assert result.paused_ms == 60000
    assert result.terminal_count == 6
    assert result.processed_per_minute == 3
    assert result.estimated_remaining_seconds == 80
    assert result.estimate_status == "estimated"


def test_small_sample_pause_and_missing_history_do_not_invent_eta():
    assert round_timing(progress(completed=2, failed=0), now=at(190)).estimate_status == "insufficient_samples"
    paused = round_(state="paused", history=round_().state_history[:2])
    result = round_timing(progress(paused), now=at(190))
    assert result.paused_ms == 120000
    assert result.estimated_remaining_seconds is None
    assert result.estimate_status == "not_running"
    result = round_timing(progress(round_(history=[])), now=at(190))
    assert result.running_ms is None
    assert result.estimate_status == "unavailable_history"


def test_completed_round_clock_stops_and_budget_change_does_not_count_idle_time():
    value = round_()
    value.state_history.append({"from": "running", "to": "completed", "at": at(190).isoformat(), "execution_limit": 10})
    value.state = "completed"
    result = round_timing(progress(value, completed=9), now=at(999))
    assert result.elapsed_ms == 190000
    assert result.estimated_remaining_seconds == 0
    assert result.estimate_status == "finished"
    value.state_history.append({"from": "completed", "to": "running", "at": at(300).isoformat(), "execution_limit": 50})
    value.state = "running"
    assert round_timing(progress(value), now=at(310)).running_ms == 130000


def test_task_wait_excludes_pauses_and_time_outside_its_budget():
    first = item_timing(round_(), sequence_number=0, created_at=at(1), started_at=at(150), completed_at=at(170), now=at(190))
    later = item_timing(round_(), sequence_number=7, created_at=at(1), started_at=at(150), completed_at=at(170), now=at(190))
    assert first["initial_queue_wait_ms"] == 80000
    assert later["initial_queue_wait_ms"] == 20000
    assert later["processing_wall_ms"] == 20000
    assert later["total_wall_ms"] == 169000
    assert later["timing_status"] == "complete"


def test_unclaimed_task_waits_without_fake_processing_time():
    result = item_timing(round_(), sequence_number=7, created_at=at(1), started_at=None, completed_at=None, now=at(190))
    assert result["initial_queue_wait_ms"] == 60000
    assert result["processing_wall_ms"] is None
    assert result["timing_status"] == "in_progress"


@pytest.mark.parametrize("invalid_at", ["bad date", at(-1).isoformat(), at(999).isoformat()])
def test_invalid_or_future_timestamps_are_unavailable_not_negative(invalid_at):
    value = round_(history=[{"from": "prepared", "to": "running", "at": invalid_at, "execution_limit": 10}])
    assert round_timing(progress(value), now=at(190)).estimate_status == "unavailable_history"


def test_cancelled_before_claim_has_no_processing_and_no_running_queue_after_end():
    result = item_timing(round_(), sequence_number=0, created_at=at(1), started_at=None, completed_at=at(50), now=at(190))
    assert result["initial_queue_wait_ms"] == 40000
    assert result["processing_wall_ms"] is None
    assert result["timing_status"] == "complete"
