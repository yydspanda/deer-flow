from __future__ import annotations

import pytest

from soc_agent.core import SocDaemonService
from soc_agent.daemon.kafka_daemon import KafkaDaemonStopSignal, SocKafkaDaemonRunner
from soc_agent.daemon.kafka_mapper import KafkaRecord
from soc_agent.daemon.kafka_runner import SocKafkaConsumerRunner


class IdleConsumer:
    def __init__(self) -> None:
        self.closed = False

    def poll(self) -> KafkaRecord | None:
        return None

    def commit(self, record: KafkaRecord) -> None:
        raise AssertionError("idle test consumer should not commit")

    def send_dead_letter(self, record: KafkaRecord, error: Exception) -> None:
        raise AssertionError("idle test consumer should not dead-letter")

    def close(self) -> None:
        self.closed = True


class FailingThenIdleConsumer(IdleConsumer):
    def __init__(self, *, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def poll(self) -> KafkaRecord | None:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("broker temporarily unavailable")
        return None


def test_kafka_daemon_runner_runs_until_max_loops_and_closes_consumer() -> None:
    consumer = IdleConsumer()
    runner = SocKafkaDaemonRunner(
        runner=__import_runner(consumer),
        idle_sleep_seconds=0,
    )

    result = runner.run(max_loops=2)

    assert result.stop_reason == "max_loops_reached"
    assert result.loop_count == 2
    assert result.idle_count == 2
    assert result.processed_count == 0
    assert result.error_count == 0
    assert result.started_at <= result.stopped_at
    assert consumer.closed is True


def test_kafka_daemon_runner_closes_consumer_when_already_stopped() -> None:
    consumer = IdleConsumer()
    stop_signal = KafkaDaemonStopSignal()
    stop_signal.request_stop("test_stop")
    runner = SocKafkaDaemonRunner(
        runner=__import_runner(consumer),
        stop_signal=stop_signal,
        idle_sleep_seconds=0,
    )

    result = runner.run()

    assert result.stop_reason == "test_stop"
    assert result.results == []
    assert consumer.closed is True


def test_kafka_daemon_runner_can_stop_after_idle_sleep() -> None:
    consumer = IdleConsumer()
    stop_signal = KafkaDaemonStopSignal()

    def sleeper(_seconds: float) -> None:
        stop_signal.request_stop("test_idle_stop")

    runner = SocKafkaDaemonRunner(
        runner=__import_runner(consumer),
        stop_signal=stop_signal,
        idle_sleep_seconds=0.01,
        sleeper=sleeper,
    )

    result = runner.run()

    assert result.stop_reason == "test_idle_stop"
    assert [item.status for item in result.results] == ["idle"]
    assert consumer.closed is True


def test_kafka_daemon_runner_backs_off_after_error_and_continues() -> None:
    consumer = FailingThenIdleConsumer(failures=1)
    sleep_calls: list[float] = []
    runner = SocKafkaDaemonRunner(
        runner=__import_runner(consumer),
        idle_sleep_seconds=0,
        error_backoff_seconds=0.25,
        sleeper=sleep_calls.append,
    )

    result = runner.run(max_loops=2)

    assert result.stop_reason == "max_loops_reached"
    assert result.loop_count == 2
    assert result.error_count == 1
    assert result.consecutive_error_count == 0
    assert result.idle_count == 1
    assert result.last_error_type == "RuntimeError"
    assert result.last_error_message == "broker temporarily unavailable"
    assert result.last_error_at is not None
    assert sleep_calls == [0.25]
    assert consumer.closed is True


def test_kafka_daemon_runner_stops_after_max_consecutive_errors() -> None:
    consumer = FailingThenIdleConsumer(failures=3)
    runner = SocKafkaDaemonRunner(
        runner=__import_runner(consumer),
        idle_sleep_seconds=0,
        error_backoff_seconds=0,
        max_consecutive_errors=2,
    )

    result = runner.run()

    assert result.stop_reason == "max_consecutive_errors_reached"
    assert result.loop_count == 2
    assert result.error_count == 2
    assert result.consecutive_error_count == 2
    assert result.results == []
    assert consumer.closed is True


def test_kafka_daemon_runner_rejects_invalid_settings() -> None:
    with pytest.raises(ValueError, match="idle_sleep_seconds"):
        SocKafkaDaemonRunner(runner=__import_runner(IdleConsumer()), idle_sleep_seconds=-1)
    with pytest.raises(ValueError, match="error_backoff_seconds"):
        SocKafkaDaemonRunner(runner=__import_runner(IdleConsumer()), error_backoff_seconds=-1)
    with pytest.raises(ValueError, match="max_consecutive_errors"):
        SocKafkaDaemonRunner(runner=__import_runner(IdleConsumer()), max_consecutive_errors=0)

    with pytest.raises(ValueError, match="max_loops"):
        SocKafkaDaemonRunner(runner=__import_runner(IdleConsumer()), idle_sleep_seconds=0).run(max_loops=0)


def __import_runner(consumer: IdleConsumer):
    return SocKafkaConsumerRunner(consumer=consumer, daemon_service=SocDaemonService())
