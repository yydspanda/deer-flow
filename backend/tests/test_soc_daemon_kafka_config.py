from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from soc_agent.daemon.kafka_adapter import (
    ConfluentKafkaConsumerPort,
    KafkaAdapterError,
    KafkaAdapterNotConfiguredError,
    NullKafkaConsumerPort,
    build_kafka_consumer_port,
)
from soc_agent.daemon.kafka_config import KafkaConsumerSettings, KafkaSecurityProtocol
from soc_agent.daemon.kafka_mapper import KafkaRecord


def test_kafka_consumer_settings_defaults_disabled_local_broker() -> None:
    settings = KafkaConsumerSettings()

    assert settings.enabled is False
    assert settings.bootstrap_servers == ["localhost:9092"]
    assert settings.alert_topics == ["soc.alerts.raw.v1"]
    assert settings.approval_request_topics == ["soc.approvals.requests.v1"]
    assert settings.dead_letter_topic == "soc.alerts.dead_letter.v1"
    assert settings.security_protocol is KafkaSecurityProtocol.PLAINTEXT


def test_kafka_consumer_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOC_KAFKA_ENABLED", "true")
    monkeypatch.setenv("SOC_KAFKA_BOOTSTRAP_SERVERS", "kafka-1:9092,kafka-2:9092")
    monkeypatch.setenv("SOC_KAFKA_ALERT_TOPICS", "alerts.a,alerts.b")
    monkeypatch.setenv("SOC_KAFKA_APPROVAL_REQUEST_TOPICS", "approvals.a")
    monkeypatch.setenv("SOC_KAFKA_GROUP_ID", "soc-group")
    monkeypatch.setenv("SOC_KAFKA_CLIENT_ID", "soc-client")
    monkeypatch.setenv("SOC_KAFKA_DEAD_LETTER_TOPIC", "soc.dlq")
    monkeypatch.setenv("SOC_KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("SOC_KAFKA_SASL_MECHANISM", "PLAIN")
    monkeypatch.setenv("SOC_KAFKA_SASL_USERNAME", "soc-user")
    monkeypatch.setenv("SOC_KAFKA_SASL_PASSWORD_ENV", "SOC_KAFKA_PASSWORD")
    monkeypatch.setenv("SOC_KAFKA_PASSWORD", "secret")
    monkeypatch.setenv("SOC_KAFKA_SSL_CA_LOCATION", "/tmp/ca.pem")
    monkeypatch.setenv("SOC_KAFKA_POLL_TIMEOUT_MS", "250")
    monkeypatch.setenv("SOC_KAFKA_MAX_POLL_RECORDS", "10")

    settings = KafkaConsumerSettings.from_env()

    assert settings.enabled is True
    assert settings.bootstrap_servers == ["kafka-1:9092", "kafka-2:9092"]
    assert settings.alert_topics == ["alerts.a", "alerts.b"]
    assert settings.approval_request_topics == ["approvals.a"]
    assert settings.group_id == "soc-group"
    assert settings.client_id == "soc-client"
    assert settings.dead_letter_topic == "soc.dlq"
    assert settings.security_protocol is KafkaSecurityProtocol.SASL_SSL
    assert settings.sasl_mechanism == "PLAIN"
    assert settings.sasl_username == "soc-user"
    assert settings.sasl_password() == "secret"
    assert settings.ssl_ca_location == "/tmp/ca.pem"
    assert settings.poll_timeout_ms == 250
    assert settings.max_poll_records == 10


def test_kafka_consumer_settings_rejects_empty_topic_list() -> None:
    with pytest.raises(ValidationError):
        KafkaConsumerSettings(alert_topics=[])


def test_null_kafka_consumer_port_is_idle_when_disabled() -> None:
    port = NullKafkaConsumerPort(KafkaConsumerSettings(enabled=False))

    assert port.poll() is None
    port.commit(KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{}"))
    port.send_dead_letter(KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{}"), RuntimeError("bad"))
    port.close()

    assert port.closed is True


def test_null_kafka_consumer_port_fails_fast_when_enabled() -> None:
    port = NullKafkaConsumerPort(KafkaConsumerSettings(enabled=True))

    with pytest.raises(KafkaAdapterNotConfiguredError, match="enabled"):
        port.poll()
    with pytest.raises(KafkaAdapterNotConfiguredError, match="dead-letter"):
        port.send_dead_letter(KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{}"), RuntimeError("bad"))


def test_build_kafka_consumer_port_returns_null_when_disabled() -> None:
    port = build_kafka_consumer_port(KafkaConsumerSettings(enabled=False))

    assert isinstance(port, NullKafkaConsumerPort)


def test_confluent_kafka_consumer_port_maps_message_and_commits_offset() -> None:
    consumer = FakeConfluentConsumer(
        [
            FakeConfluentMessage(
                topic="soc.alerts.raw.v1",
                partition=2,
                offset=41,
                key=b"alert-key",
                value=b'{"alert_id":"ALT-1"}',
                headers=[("trace", b"trace-1")],
            )
        ]
    )
    producer = FakeConfluentProducer()
    port = ConfluentKafkaConsumerPort(
        KafkaConsumerSettings(enabled=True, alert_topics=["soc.alerts.raw.v1"], approval_request_topics=["soc.approvals.requests.v1"]),
        consumer=consumer,
        producer=producer,
        topic_partition_cls=FakeTopicPartition,
    )

    record = port.poll()

    assert record == KafkaRecord(
        topic="soc.alerts.raw.v1",
        partition=2,
        offset=41,
        key=b"alert-key",
        value=b'{"alert_id":"ALT-1"}',
        headers=(("trace", b"trace-1"),),
    )
    assert consumer.subscribed_topics == ["soc.alerts.raw.v1", "soc.approvals.requests.v1"]

    port.commit(record)

    assert consumer.committed_offsets == [FakeTopicPartition("soc.alerts.raw.v1", 2, 42)]


def test_confluent_kafka_consumer_port_sends_dead_letter_payload() -> None:
    consumer = FakeConfluentConsumer()
    producer = FakeConfluentProducer()
    port = ConfluentKafkaConsumerPort(
        KafkaConsumerSettings(enabled=True, dead_letter_topic="soc.dead.v1"),
        consumer=consumer,
        producer=producer,
        topic_partition_cls=FakeTopicPartition,
    )

    record = KafkaRecord(
        topic="unknown.topic",
        partition=0,
        offset=7,
        key=b"k1",
        value=b"{bad-json",
        headers=(("source", b"unit"),),
    )
    port.send_dead_letter(record, ValueError("bad input"))

    assert len(producer.produced) == 1
    produced = producer.produced[0]
    assert produced["topic"] == "soc.dead.v1"
    assert produced["key"] == b"k1"
    payload = json.loads(produced["value"].decode("utf-8"))
    assert payload["schema_version"] == "soc.kafka_dead_letter.v1"
    assert payload["topic"] == "unknown.topic"
    assert payload["partition"] == 0
    assert payload["offset"] == 7
    assert payload["key"] == "k1"
    assert payload["headers"] == [{"key": "source", "value": "unit"}]
    assert payload["value"] == "{bad-json"
    assert payload["error_type"] == "ValueError"
    assert payload["error_message"] == "bad input"
    assert producer.flush_calls == [1.0]


def test_confluent_kafka_consumer_port_raises_on_consumer_error() -> None:
    port = ConfluentKafkaConsumerPort(
        KafkaConsumerSettings(enabled=True),
        consumer=FakeConfluentConsumer([FakeConfluentMessage(error="broker unavailable")]),
        producer=FakeConfluentProducer(),
        topic_partition_cls=FakeTopicPartition,
    )

    with pytest.raises(KafkaAdapterError, match="broker unavailable"):
        port.poll()


def test_confluent_kafka_consumer_port_raises_on_empty_value() -> None:
    port = ConfluentKafkaConsumerPort(
        KafkaConsumerSettings(enabled=True),
        consumer=FakeConfluentConsumer([FakeConfluentMessage(value=None)]),
        producer=FakeConfluentProducer(),
        topic_partition_cls=FakeTopicPartition,
    )

    with pytest.raises(KafkaAdapterError, match="value is empty"):
        port.poll()


def test_confluent_kafka_consumer_port_raises_when_dead_letter_flush_fails() -> None:
    port = ConfluentKafkaConsumerPort(
        KafkaConsumerSettings(enabled=True),
        consumer=FakeConfluentConsumer(),
        producer=FakeConfluentProducer(flush_remaining=1),
        topic_partition_cls=FakeTopicPartition,
    )

    with pytest.raises(KafkaAdapterError, match="failed to flush"):
        port.send_dead_letter(KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{}"), RuntimeError("bad"))


class FakeConfluentMessage:
    def __init__(
        self,
        *,
        topic: str = "soc.alerts.raw.v1",
        partition: int = 0,
        offset: int = 0,
        key: bytes | str | None = None,
        value: bytes | str | None = b"{}",
        headers: list[tuple[str, bytes | str | None]] | None = None,
        error: object | None = None,
    ) -> None:
        self._topic = topic
        self._partition = partition
        self._offset = offset
        self._key = key
        self._value = value
        self._headers = headers
        self._error = error

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def key(self) -> bytes | str | None:
        return self._key

    def value(self) -> bytes | str | None:
        return self._value

    def headers(self) -> list[tuple[str, bytes | str | None]] | None:
        return self._headers

    def error(self) -> object | None:
        return self._error


class FakeConfluentConsumer:
    def __init__(self, messages: list[FakeConfluentMessage] | None = None) -> None:
        self.messages = list(messages or [])
        self.subscribed_topics: list[str] = []
        self.committed_offsets: list[FakeTopicPartition] = []
        self.closed = False

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics = topics

    def poll(self, timeout: float) -> FakeConfluentMessage | None:
        if not self.messages:
            return None
        return self.messages.pop(0)

    def commit(self, *, offsets: list[FakeTopicPartition], asynchronous: bool) -> None:
        assert asynchronous is False
        self.committed_offsets.extend(offsets)

    def close(self) -> None:
        self.closed = True


class FakeConfluentProducer:
    def __init__(self, *, flush_remaining: int = 0) -> None:
        self.flush_remaining = flush_remaining
        self.produced: list[dict[str, Any]] = []
        self.flush_calls: list[float] = []

    def produce(self, topic: str, *, key: bytes | str | None, value: bytes) -> None:
        self.produced.append({"topic": topic, "key": key, "value": value})

    def flush(self, timeout: float) -> int:
        self.flush_calls.append(timeout)
        return self.flush_remaining


class FakeTopicPartition:
    def __init__(self, topic: str, partition: int, offset: int) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FakeTopicPartition) and self.topic == other.topic and self.partition == other.partition and self.offset == other.offset

    def __repr__(self) -> str:
        return f"FakeTopicPartition(topic={self.topic!r}, partition={self.partition!r}, offset={self.offset!r})"
