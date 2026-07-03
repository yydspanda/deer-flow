"""Kafka adapter boundaries for SOC daemon ingestion."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from soc_agent.daemon.kafka_config import KafkaConsumerSettings
from soc_agent.daemon.kafka_mapper import KafkaRecord


class KafkaAdapterError(RuntimeError):
    """Raised when a Kafka adapter operation fails."""


class KafkaAdapterNotConfiguredError(RuntimeError):
    """Raised when a real Kafka adapter is requested but not installed."""


def build_kafka_consumer_port(settings: KafkaConsumerSettings):
    """Build the configured Kafka consumer port.

    Disabled settings intentionally return the null port so local development
    and CI do not require a broker. Enabled settings construct the real
    confluent-kafka adapter and fail fast if the optional dependency is missing.
    """

    if not settings.enabled:
        return NullKafkaConsumerPort(settings)
    return ConfluentKafkaConsumerPort(settings)


class NullKafkaConsumerPort:
    """KafkaConsumerPort implementation used before a real broker client exists."""

    def __init__(self, settings: KafkaConsumerSettings | None = None) -> None:
        self.settings = settings or KafkaConsumerSettings()
        self.closed = False

    def poll(self) -> KafkaRecord | None:
        if self.settings.enabled:
            raise KafkaAdapterNotConfiguredError("Kafka consumer is enabled but no broker client adapter is configured")
        return None

    def commit(self, record: KafkaRecord) -> None:
        return None

    def send_dead_letter(self, record: KafkaRecord, error: Exception) -> None:
        if self.settings.enabled:
            raise KafkaAdapterNotConfiguredError("dead-letter publishing requires a real Kafka adapter")
        return None

    def close(self) -> None:
        self.closed = True


class ConfluentKafkaConsumerPort:
    """KafkaConsumerPort implementation backed by ``confluent-kafka``."""

    def __init__(
        self,
        settings: KafkaConsumerSettings,
        *,
        consumer: Any | None = None,
        producer: Any | None = None,
        topic_partition_cls: type[Any] | None = None,
    ) -> None:
        self.settings = settings
        if consumer is None or producer is None or topic_partition_cls is None:
            try:
                from confluent_kafka import Consumer, Producer, TopicPartition
            except ImportError as exc:
                raise KafkaAdapterNotConfiguredError("install the kafka extra to enable SOC Kafka: backend[kafka]") from exc

            consumer = consumer or Consumer(_consumer_config(settings))
            producer = producer or Producer(_producer_config(settings))
            topic_partition_cls = topic_partition_cls or TopicPartition

        self._consumer = consumer
        self._producer = producer
        self._topic_partition_cls = topic_partition_cls
        self._consumer.subscribe(_input_topics(settings))

    def poll(self) -> KafkaRecord | None:
        message = self._consumer.poll(self.settings.poll_timeout_ms / 1000)
        if message is None:
            return None
        error = message.error()
        if error is not None:
            raise KafkaAdapterError(f"Kafka consumer error: {error}")
        value = message.value()
        if value is None:
            raise KafkaAdapterError("Kafka message value is empty")
        return KafkaRecord(
            topic=message.topic(),
            partition=message.partition(),
            offset=message.offset(),
            key=message.key(),
            value=value,
            headers=tuple(message.headers() or ()),
        )

    def commit(self, record: KafkaRecord) -> None:
        offset = self._topic_partition_cls(record.topic, record.partition, record.offset + 1)
        self._consumer.commit(offsets=[offset], asynchronous=False)

    def send_dead_letter(self, record: KafkaRecord, error: Exception) -> None:
        value = json.dumps(_dead_letter_payload(record, error), ensure_ascii=False).encode("utf-8")
        self._producer.produce(self.settings.dead_letter_topic, key=record.key, value=value)
        remaining = self._producer.flush(max(1.0, self.settings.poll_timeout_ms / 1000))
        if remaining:
            raise KafkaAdapterError(f"failed to flush {remaining} dead-letter Kafka message(s)")

    def close(self) -> None:
        self._consumer.close()


def _consumer_config(settings: KafkaConsumerSettings) -> dict[str, Any]:
    config = _base_config(settings)
    config.update(
        {
            "group.id": settings.group_id,
            "client.id": settings.client_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    return config


def _producer_config(settings: KafkaConsumerSettings) -> dict[str, Any]:
    config = _base_config(settings)
    config["client.id"] = f"{settings.client_id}-dead-letter"
    return config


def _base_config(settings: KafkaConsumerSettings) -> dict[str, Any]:
    config: dict[str, Any] = {
        "bootstrap.servers": ",".join(settings.bootstrap_servers),
        "security.protocol": settings.security_protocol.value,
    }
    if settings.sasl_mechanism:
        config["sasl.mechanisms"] = settings.sasl_mechanism
    if settings.sasl_username:
        config["sasl.username"] = settings.sasl_username
    if settings.sasl_password_env:
        password = settings.sasl_password()
        if password is None:
            raise KafkaAdapterNotConfiguredError(f"Kafka SASL password env var is not set: {settings.sasl_password_env}")
        config["sasl.password"] = password
    if settings.ssl_ca_location:
        config["ssl.ca.location"] = settings.ssl_ca_location
    return config


def _input_topics(settings: KafkaConsumerSettings) -> list[str]:
    return [*settings.alert_topics, *settings.approval_request_topics]


def _dead_letter_payload(record: KafkaRecord, error: Exception) -> dict[str, Any]:
    return {
        "schema_version": "soc.kafka_dead_letter.v1",
        "failed_at": datetime.now(UTC).isoformat(),
        "topic": record.topic,
        "partition": record.partition,
        "offset": record.offset,
        "key": _safe_bytes_or_text(record.key),
        "headers": [{"key": key, "value": _safe_bytes_or_text(value)} for key, value in record.headers],
        "value": _safe_record_value(record.value),
        "error_type": type(error).__name__,
        "error_message": str(error),
    }


def _safe_record_value(value: bytes | str | dict[str, Any]) -> str | dict[str, Any]:
    if isinstance(value, dict):
        return value
    return _safe_bytes_or_text(value) or ""


def _safe_bytes_or_text(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
