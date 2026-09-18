from __future__ import annotations

import io
import json
import weakref

import pytest

from soc_agent.contracts import SocDaemonProcessResult
from soc_agent.core import SocDaemonService
from soc_agent.daemon.kafka_daemon import JsonLineKafkaDaemonMetricSink, KafkaDaemonStopSignal, SocKafkaDaemonRunner
from soc_agent.daemon.kafka_mapper import KafkaRecord
from soc_agent.daemon.kafka_runner import KafkaRunnerProcessResult, SocKafkaConsumerRunner


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


class ListMetricSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


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


def test_kafka_daemon_runner_emits_start_result_and_stop_metrics() -> None:
    consumer = IdleConsumer()
    sink = ListMetricSink()
    runner = SocKafkaDaemonRunner(
        runner=__import_runner(consumer),
        idle_sleep_seconds=0,
        metric_sink=sink,
    )

    result = runner.run(max_loops=1)

    assert result.stop_reason == "max_loops_reached"
    assert [event["event"] for event in sink.events] == ["start", "result", "stop"]
    assert all(event["schema_version"] == "soc.kafka_daemon_metric.v1" for event in sink.events)
    assert sink.events[1]["status"] == "idle"
    assert sink.events[1]["loop_count"] == 1
    assert sink.events[2]["stop_reason"] == "max_loops_reached"
    assert sink.events[2]["counters"]["idle"] == 1


def test_kafka_daemon_runner_emits_error_metric() -> None:
    consumer = FailingThenIdleConsumer(failures=1)
    sink = ListMetricSink()
    runner = SocKafkaDaemonRunner(
        runner=__import_runner(consumer),
        idle_sleep_seconds=0,
        error_backoff_seconds=0,
        max_consecutive_errors=1,
        metric_sink=sink,
    )

    result = runner.run()

    assert result.stop_reason == "max_consecutive_errors_reached"
    assert [event["event"] for event in sink.events] == ["start", "error", "stop"]
    assert sink.events[1]["error_type"] == "RuntimeError"
    assert sink.events[1]["consecutive_error_count"] == 1
    assert sink.events[2]["metrics"]["error_count"] == 1


def test_json_line_kafka_daemon_metric_sink_writes_one_json_object_per_line() -> None:
    stream = io.StringIO()
    sink = JsonLineKafkaDaemonMetricSink(stream)

    sink.emit({"schema_version": "soc.kafka_daemon_metric.v1", "event": "start"})
    sink.emit({"schema_version": "soc.kafka_daemon_metric.v1", "event": "stop"})

    lines = stream.getvalue().splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["start", "stop"]


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


@pytest.mark.parametrize("bounded", [False, True])
def test_kafka_daemon_history_does_not_determine_lifetime_counters(bounded: bool) -> None:
    stop_signal = KafkaDaemonStopSignal()
    records: list[weakref.ReferenceType[KafkaRecord]] = []

    class ResultRunner:
        count = 0
        closed = False

        def process_next(self) -> KafkaRunnerProcessResult:
            self.count += 1
            if self.count == 305:
                stop_signal.request_stop("test_complete")
            status = ("processed", "dead_lettered", "idle")[(self.count - 1) % 3]
            if status == "idle":
                return KafkaRunnerProcessResult(status=status)
            record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=self.count, value=b"large raw payload", headers=(("raw", b"header payload"),))
            records.append(weakref.ref(record))
            return KafkaRunnerProcessResult(
                status=status,
                record=record,
                committed=True,
                dead_lettered=status == "dead_lettered",
                daemon_result=SocDaemonProcessResult(message_id=str(self.count), kind="alert", status="processed", payload={"raw": "large service payload"}),
            )

        def close(self) -> None:
            self.closed = True

    worker = ResultRunner()
    sink = ListMetricSink()
    result = SocKafkaDaemonRunner(runner=worker, stop_signal=stop_signal, idle_sleep_seconds=0, metric_sink=sink).run(max_loops=305 if bounded else None)

    assert result.loop_count == 305
    assert result.processed_count == 102
    assert result.dead_lettered_count == 102
    assert result.idle_count == 101
    assert result.committed_count == 204
    assert result.error_count == 0
    assert sink.events[-1]["loop_count"] == 305
    assert sink.events[-1]["counters"] == {"processed": 102, "dead_lettered": 102, "idle": 101, "committed": 204}
    assert worker.closed is True
    if bounded:
        assert len(result.results) == 305
        assert result.results[0].record.value == b"large raw payload"
        assert result.results[0].daemon_result.payload == {"raw": "large service payload"}
        assert all(ref() is not None for ref in records)
    else:
        assert result.stop_reason == "test_complete"
        assert len(result.results) == 100
        assert result.results[-1].record.offset == 305
        assert all(item.record is None or (not item.record.value and not item.record.headers) for item in result.results)
        assert all(item.daemon_result is None or not item.daemon_result.payload for item in result.results)
        assert all(ref() is None for ref in records)


def __import_runner(consumer: IdleConsumer):
    return SocKafkaConsumerRunner(consumer=consumer, daemon_service=SocDaemonService())
