"""Kafka consumer runner skeleton for SOC daemon ingestion.

The runner is client-neutral: real Kafka integrations implement
``KafkaConsumerPort`` and provide ``KafkaRecord`` objects from the mapper
module. This keeps broker IO separate from SOC business processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from soc_agent.contracts import SocDaemonProcessResult
from soc_agent.core import SocDaemonService, SocServiceError
from soc_agent.daemon.kafka_mapper import DEFAULT_ALERT_TOPICS, DEFAULT_APPROVAL_REQUEST_TOPICS, KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message


class KafkaConsumerPort(Protocol):
    """Client-neutral Kafka operations required by the SOC consumer runner."""

    def poll(self) -> KafkaRecord | None: ...

    def commit(self, record: KafkaRecord) -> None: ...

    def send_dead_letter(self, record: KafkaRecord, error: Exception) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class KafkaRunnerProcessResult:
    """Outcome of processing one Kafka record through the runner."""

    status: str
    record: KafkaRecord | None = None
    daemon_result: SocDaemonProcessResult | None = None
    error: str | None = None
    committed: bool = False
    dead_lettered: bool = False


class SocKafkaConsumerRunner:
    """Serial runner that maps Kafka records and calls ``SocDaemonService``."""

    def __init__(
        self,
        *,
        consumer: KafkaConsumerPort,
        daemon_service: SocDaemonService,
        alert_topics: frozenset[str] = DEFAULT_ALERT_TOPICS,
        approval_request_topics: frozenset[str] = DEFAULT_APPROVAL_REQUEST_TOPICS,
    ) -> None:
        self._consumer = consumer
        self._daemon_service = daemon_service
        self._alert_topics = alert_topics
        self._approval_request_topics = approval_request_topics

    def process_next(self) -> KafkaRunnerProcessResult:
        record = self._consumer.poll()
        if record is None:
            return KafkaRunnerProcessResult(status="idle")
        return self.process_record(record)

    def process_record(self, record: KafkaRecord) -> KafkaRunnerProcessResult:
        try:
            message = map_kafka_record_to_daemon_message(
                record,
                alert_topics=self._alert_topics,
                approval_request_topics=self._approval_request_topics,
            )
            daemon_result = self._daemon_service.process_message(message)
            self._consumer.commit(record)
            return KafkaRunnerProcessResult(
                status="processed",
                record=record,
                daemon_result=daemon_result,
                committed=True,
            )
        except (KafkaMapperError, SocServiceError) as exc:
            self._consumer.send_dead_letter(record, exc)
            self._consumer.commit(record)
            return KafkaRunnerProcessResult(
                status="dead_lettered",
                record=record,
                error=str(exc),
                committed=True,
                dead_lettered=True,
            )

    def close(self) -> None:
        self._consumer.close()
