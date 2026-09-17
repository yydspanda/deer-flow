"""Read-only timings from persisted states; never a model cost or health signal."""

from datetime import UTC, datetime
from math import ceil

from soc_agent.contracts.corpus_experiments import CorpusRound, CorpusRoundProgress, CorpusRoundTiming
from soc_agent.contracts.processing_jobs import ProcessingJobStatus


def _utc(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    # SQLite returns naive datetimes for columns written in UTC.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _intervals(round_: CorpusRound, now: datetime):
    cursor, state, limit = _utc(round_.created_at), "prepared", 0
    result = []
    for event in round_.state_history:
        end = _utc(event["at"])
        if end < cursor or end > now or event["from"] != state:
            raise ValueError("incomplete or non-monotonic round history")
        result.append((cursor, end, state, limit))
        cursor, state, limit = end, event["to"], int(event["execution_limit"])
    if state != round_.state or cursor > now:
        raise ValueError("round state has no matching timestamp")
    # Completed clocks freeze; a later explicit resume starts another interval.
    result.append((cursor, cursor if state == "completed" else now, state, limit))
    return result


def _ms(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() * 1000))


def round_timing(progress: CorpusRoundProgress, *, now: datetime | None = None) -> CorpusRoundTiming:
    terminal_states = {state.value for state in ProcessingJobStatus if state.is_terminal}
    terminal = sum(value for key, value in progress.counts.items() if key in terminal_states)
    try:
        intervals = _intervals(progress.round, _utc(now or datetime.now(UTC)))
    except (KeyError, TypeError, ValueError, AttributeError):
        return CorpusRoundTiming(terminal_count=terminal, estimate_status="unavailable_history")
    running = sum(_ms(start, end) for start, end, state, _ in intervals if state == "running")
    paused = sum(_ms(start, end) for start, end, state, _ in intervals if state == "paused")
    remaining = max(0, progress.admitted_count - terminal)
    status = "finished" if not remaining and progress.admitted_count else "not_running" if progress.round.state != "running" else "insufficient_samples"
    rate = terminal * 60000 / running if terminal and running else None
    estimate = 0 if status == "finished" else None
    if status == "insufficient_samples" and terminal >= 5 and rate:
        status, estimate = "estimated", ceil(remaining * 60 / rate)
    return CorpusRoundTiming(
        elapsed_ms=_ms(intervals[0][0], intervals[-1][1]),
        running_ms=running,
        paused_ms=paused,
        terminal_count=terminal,
        processed_per_minute=round(rate, 3) if rate else None,
        estimated_remaining_seconds=estimate,
        estimate_status=status,
    )


def item_timing(round_: CorpusRound, *, sequence_number: int, created_at: datetime, started_at: datetime | None, completed_at: datetime | None, now: datetime | None = None) -> dict:
    observed = _utc(now or datetime.now(UTC))
    created = _utc(created_at)
    started = _utc(started_at) if started_at else None
    finished = _utc(completed_at) if completed_at else None
    end = finished or observed
    result = {"timing_status": "complete" if finished else "in_progress", "initial_queue_wait_ms": None, "processing_wall_ms": None, "total_wall_ms": None}
    if created > end or end > observed or (started and not created <= started <= end):
        return {**result, "timing_status": "unavailable"}
    result["total_wall_ms"] = _ms(created, end)
    result["processing_wall_ms"] = _ms(started, end) if started else None
    try:
        intervals = _intervals(round_, observed)
    except (KeyError, TypeError, ValueError, AttributeError):
        return {**result, "timing_status": "unavailable_history"}
    # Waiting only counts while this item was admitted and new claims were enabled.
    # First-claim-to-end wall time includes retries/recovery, not just LLM execution.
    until = started or end
    result["initial_queue_wait_ms"] = sum(_ms(max(created, start), min(until, stop)) for start, stop, state, limit in intervals if state == "running" and sequence_number < limit and start < until and stop > created)
    return result
