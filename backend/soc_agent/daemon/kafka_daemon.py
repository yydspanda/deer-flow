"""Long-running Kafka daemon controller for SOC ingestion."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

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

    @property
    def loop_count(self) -> int:
        return len(self.results)

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
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if idle_sleep_seconds < 0:
            raise ValueError("idle_sleep_seconds must be >= 0")
        self._runner = runner
        self._stop_signal = stop_signal or KafkaDaemonStopSignal()
        self._idle_sleep_seconds = idle_sleep_seconds
        self._sleeper = sleeper

    def run(self, *, max_loops: int | None = None) -> KafkaDaemonRunResult:
        """Run until a stop signal is requested or an optional loop cap is reached."""

        if max_loops is not None and max_loops < 1:
            raise ValueError("max_loops must be >= 1")

        results: list[KafkaRunnerProcessResult] = []
        stop_reason = self._stop_signal.reason or "stop_requested"
        try:
            while not self._stop_signal.requested:
                result = self._runner.process_next()
                results.append(result)

                if max_loops is not None and len(results) >= max_loops:
                    stop_reason = "max_loops_reached"
                    break

                if result.status == "idle" and self._idle_sleep_seconds > 0 and not self._stop_signal.requested:
                    self._sleeper(self._idle_sleep_seconds)
            else:
                stop_reason = self._stop_signal.reason or "stop_requested"
        finally:
            self._runner.close()

        return KafkaDaemonRunResult(results=results, stop_reason=stop_reason)
