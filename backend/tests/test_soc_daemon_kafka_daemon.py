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


def test_kafka_daemon_runner_rejects_invalid_settings() -> None:
    with pytest.raises(ValueError, match="idle_sleep_seconds"):
        SocKafkaDaemonRunner(runner=__import_runner(IdleConsumer()), idle_sleep_seconds=-1)

    with pytest.raises(ValueError, match="max_loops"):
        SocKafkaDaemonRunner(runner=__import_runner(IdleConsumer()), idle_sleep_seconds=0).run(max_loops=0)


def __import_runner(consumer: IdleConsumer):
    return SocKafkaConsumerRunner(consumer=consumer, daemon_service=SocDaemonService())
