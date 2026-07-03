"""Disabled-by-default Kafka adapter boundary for SOC daemon ingestion."""

from __future__ import annotations

from soc_agent.daemon.kafka_config import KafkaConsumerSettings
from soc_agent.daemon.kafka_mapper import KafkaRecord


class KafkaAdapterNotConfiguredError(RuntimeError):
    """Raised when a real Kafka adapter is requested but not installed."""


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
