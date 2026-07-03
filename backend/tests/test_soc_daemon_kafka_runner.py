from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from soc_agent.contracts import SocDaemonMessage, SocDaemonProcessResult
from soc_agent.core import SocDaemonService, SocServiceError
from soc_agent.daemon.kafka_mapper import KafkaRecord
from soc_agent.daemon.kafka_runner import SocKafkaConsumerRunner


class FakeConsumer:
    def __init__(self, records: list[KafkaRecord] | None = None, *, dead_letter_error: Exception | None = None) -> None:
        self.records = deque(records or [])
        self.committed: list[KafkaRecord] = []
        self.dead_letters: list[tuple[KafkaRecord, Exception]] = []
        self.closed = False
        self.dead_letter_error = dead_letter_error

    def poll(self) -> KafkaRecord | None:
        if not self.records:
            return None
        return self.records.popleft()

    def commit(self, record: KafkaRecord) -> None:
        self.committed.append(record)

    def send_dead_letter(self, record: KafkaRecord, error: Exception) -> None:
        if self.dead_letter_error is not None:
            raise self.dead_letter_error
        self.dead_letters.append((record, error))

    def close(self) -> None:
        self.closed = True


class FakeDaemonService(SocDaemonService):
    def __init__(self, *, error: Exception | None = None) -> None:
        self.messages: list[SocDaemonMessage] = []
        self.error = error

    def process_message(self, message: SocDaemonMessage | dict[str, Any]) -> SocDaemonProcessResult:
        daemon_message = SocDaemonMessage.model_validate(message)
        self.messages.append(daemon_message)
        if self.error is not None:
            raise self.error
        return SocDaemonProcessResult(
            message_id=daemon_message.message_id,
            kind=daemon_message.kind,
            status="processed",
            run_id="RUN-FAKE-001" if daemon_message.kind == "alert" else None,
            approval_request_id="APR-FAKE-001" if daemon_message.kind == "approval_request" else None,
        )


def test_kafka_runner_processes_record_and_commits_after_service_success() -> None:
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=10, value='{"alert_id":"ALT-1"}')
    consumer = FakeConsumer([record])
    daemon_service = FakeDaemonService()

    result = SocKafkaConsumerRunner(consumer=consumer, daemon_service=daemon_service).process_next()

    assert result.status == "processed"
    assert result.committed is True
    assert result.daemon_result is not None
    assert result.daemon_result.run_id == "RUN-FAKE-001"
    assert consumer.committed == [record]
    assert consumer.dead_letters == []
    assert daemon_service.messages[0].kind == "alert"


def test_kafka_runner_uses_configured_topic_sets() -> None:
    record = KafkaRecord(topic="custom.alerts.v1", partition=0, offset=10, value='{"alert_id":"ALT-1"}')
    consumer = FakeConsumer([record])
    daemon_service = FakeDaemonService()

    result = SocKafkaConsumerRunner(
        consumer=consumer,
        daemon_service=daemon_service,
        alert_topics=frozenset({"custom.alerts.v1"}),
        approval_request_topics=frozenset({"custom.approvals.v1"}),
    ).process_next()

    assert result.status == "processed"
    assert consumer.committed == [record]
    assert consumer.dead_letters == []
    assert daemon_service.messages[0].kind == "alert"


def test_kafka_runner_returns_idle_when_no_record_is_available() -> None:
    consumer = FakeConsumer()

    result = SocKafkaConsumerRunner(consumer=consumer, daemon_service=FakeDaemonService()).process_next()

    assert result.status == "idle"
    assert result.record is None
    assert consumer.committed == []


def test_kafka_runner_run_aggregates_bounded_loop_results() -> None:
    first = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value='{"alert_id":"ALT-1"}')
    second = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=2, value='{"alert_id":"ALT-2"}')
    consumer = FakeConsumer([first, second])

    loop_result = SocKafkaConsumerRunner(consumer=consumer, daemon_service=FakeDaemonService()).run(max_records=3)

    assert [result.status for result in loop_result.results] == ["processed", "processed", "idle"]
    assert loop_result.processed_count == 2
    assert loop_result.dead_lettered_count == 0
    assert loop_result.idle_count == 1
    assert loop_result.committed_count == 2
    assert consumer.committed == [first, second]


def test_kafka_runner_run_rejects_invalid_max_records() -> None:
    with pytest.raises(ValueError, match="max_records"):
        SocKafkaConsumerRunner(consumer=FakeConsumer(), daemon_service=FakeDaemonService()).run(max_records=0)


def test_kafka_runner_sends_mapper_failure_to_dead_letter_then_commits() -> None:
    record = KafkaRecord(topic="unknown.topic", partition=0, offset=1, value="{}")
    consumer = FakeConsumer([record])

    result = SocKafkaConsumerRunner(consumer=consumer, daemon_service=FakeDaemonService()).process_next()

    assert result.status == "dead_lettered"
    assert result.dead_lettered is True
    assert result.committed is True
    assert "unsupported SOC Kafka topic" in (result.error or "")
    assert consumer.dead_letters[0][0] == record
    assert consumer.committed == [record]


def test_kafka_runner_sends_service_failure_to_dead_letter_then_commits() -> None:
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=2, value='{"alert_id":"ALT-2"}')
    consumer = FakeConsumer([record])

    result = SocKafkaConsumerRunner(
        consumer=consumer,
        daemon_service=FakeDaemonService(error=SocServiceError("db unavailable")),
    ).process_next()

    assert result.status == "dead_lettered"
    assert result.dead_lettered is True
    assert "db unavailable" in (result.error or "")
    assert consumer.dead_letters[0][0] == record
    assert consumer.committed == [record]


def test_kafka_runner_does_not_commit_when_dead_letter_write_fails() -> None:
    record = KafkaRecord(topic="unknown.topic", partition=0, offset=3, value="{}")
    consumer = FakeConsumer([record], dead_letter_error=RuntimeError("dead letter topic unavailable"))

    with pytest.raises(RuntimeError, match="dead letter topic unavailable"):
        SocKafkaConsumerRunner(consumer=consumer, daemon_service=FakeDaemonService()).process_next()

    assert consumer.committed == []
    assert consumer.dead_letters == []


def test_kafka_runner_closes_consumer_port() -> None:
    consumer = FakeConsumer()

    SocKafkaConsumerRunner(consumer=consumer, daemon_service=FakeDaemonService()).close()

    assert consumer.closed is True
