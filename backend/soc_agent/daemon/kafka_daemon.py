"""Long-running Kafka daemon controller for SOC ingestion."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from soc_agent.daemon.kafka_runner import KafkaRunnerLoopResult, KafkaRunnerProcessResult, SocKafkaConsumerRunner


class KafkaDaemonStopSignal:
    """Shared stop flag for graceful daemon shutdown."""

    def __init__(self) -> None:
        self._requested = False
        self._reason: str | None = None

    @property
    def requested(self) -> bool:
        return self._requested

    @property
    def reason(self) -> str | None:
        return self._reason

    def request_stop(self, reason: str = "stop_requested") -> None:
        if self._requested:
            return
        self._requested = True
        self._reason = reason


@dataclass(frozen=True)
class KafkaDaemonRunResult:
    """Aggregated result of a daemon run loop."""

    results: list[KafkaRunnerProcessResult]
    stop_reason: str
    started_at: datetime
    stopped_at: datetime
    error_count: int = 0
    consecutive_error_count: int = 0
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_type: str | None = None
    last_error_message: str | None = None

    @property
    def loop_count(self) -> int:
        return len(self.results) + self.error_count

    @property
    def processed_count(self) -> int:
        return KafkaRunnerLoopResult(results=self.results).processed_count

    @property
    def dead_lettered_count(self) -> int:
        return KafkaRunnerLoopResult(results=self.results).dead_lettered_count

    @property
    def idle_count(self) -> int:
        return KafkaRunnerLoopResult(results=self.results).idle_count

    @property
    def committed_count(self) -> int:
        return KafkaRunnerLoopResult(results=self.results).committed_count


class SocKafkaDaemonRunner:
    """Graceful long-running wrapper around ``SocKafkaConsumerRunner``."""

    def __init__(
        self,
        *,
        runner: SocKafkaConsumerRunner,
        stop_signal: KafkaDaemonStopSignal | None = None,
        idle_sleep_seconds: float = 1.0,
        error_backoff_seconds: float = 1.0,
        max_consecutive_errors: int | None = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if idle_sleep_seconds < 0:
            raise ValueError("idle_sleep_seconds must be >= 0")
        if error_backoff_seconds < 0:
            raise ValueError("error_backoff_seconds must be >= 0")
        if max_consecutive_errors is not None and max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be >= 1")
        self._runner = runner
        self._stop_signal = stop_signal or KafkaDaemonStopSignal()
        self._idle_sleep_seconds = idle_sleep_seconds
        self._error_backoff_seconds = error_backoff_seconds
        self._max_consecutive_errors = max_consecutive_errors
        self._sleeper = sleeper

    def run(self, *, max_loops: int | None = None) -> KafkaDaemonRunResult:
        """Run until a stop signal is requested or an optional loop cap is reached."""

        if max_loops is not None and max_loops < 1:
            raise ValueError("max_loops must be >= 1")

        results: list[KafkaRunnerProcessResult] = []
        started_at = _utc_now()
        stopped_at = started_at
        error_count = 0
        consecutive_error_count = 0
        last_success_at: datetime | None = None
        last_error_at: datetime | None = None
        last_error_type: str | None = None
        last_error_message: str | None = None
        stop_reason = self._stop_signal.reason or "stop_requested"
        try:
            while not self._stop_signal.requested:
                try:
                    result = self._runner.process_next()
                except Exception as exc:  # noqa: BLE001 - daemon boundary backs off and reports adapter/runtime failures
                    error_count += 1
                    consecutive_error_count += 1
                    last_error_at = _utc_now()
                    last_error_type = type(exc).__name__
                    last_error_message = str(exc)

                    if _max_loops_reached(max_loops=max_loops, loop_count=len(results) + error_count):
                        stop_reason = "max_loops_reached"
                        break
                    if self._max_consecutive_errors is not None and consecutive_error_count >= self._max_consecutive_errors:
                        stop_reason = "max_consecutive_errors_reached"
                        break
                    if self._error_backoff_seconds > 0 and not self._stop_signal.requested:
                        self._sleeper(self._error_backoff_seconds)
                    continue

                results.append(result)
                consecutive_error_count = 0
                if result.status != "idle":
                    last_success_at = _utc_now()

                if _max_loops_reached(max_loops=max_loops, loop_count=len(results) + error_count):
                    stop_reason = "max_loops_reached"
                    break

                if result.status == "idle" and self._idle_sleep_seconds > 0 and not self._stop_signal.requested:
                    self._sleeper(self._idle_sleep_seconds)
            else:
                stop_reason = self._stop_signal.reason or "stop_requested"
        finally:
            stopped_at = _utc_now()
            self._runner.close()

        return KafkaDaemonRunResult(
            results=results,
            stop_reason=stop_reason,
            started_at=started_at,
            stopped_at=stopped_at,
            error_count=error_count,
            consecutive_error_count=consecutive_error_count,
            last_success_at=last_success_at,
            last_error_at=last_error_at,
            last_error_type=last_error_type,
            last_error_message=last_error_message,
        )


def _max_loops_reached(*, max_loops: int | None, loop_count: int) -> bool:
    return max_loops is not None and loop_count >= max_loops


def _utc_now() -> datetime:
    return datetime.now(UTC)
