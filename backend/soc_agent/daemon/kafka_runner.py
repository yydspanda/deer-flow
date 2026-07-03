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


@dataclass(frozen=True)
class KafkaRunnerLoopResult:
    """Aggregated outcome of running the Kafka runner for a bounded loop."""

    results: list[KafkaRunnerProcessResult]

    @property
    def processed_count(self) -> int:
        return sum(1 for result in self.results if result.status == "processed")

    @property
    def dead_lettered_count(self) -> int:
        return sum(1 for result in self.results if result.dead_lettered)

    @property
    def idle_count(self) -> int:
        return sum(1 for result in self.results if result.status == "idle")

    @property
    def committed_count(self) -> int:
        return sum(1 for result in self.results if result.committed)


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

    def run(self, *, max_records: int, stop_on_idle: bool = True) -> KafkaRunnerLoopResult:
        """Run a bounded consumer loop.

        This is intentionally finite. A future daemon supervisor can call this
        repeatedly or wrap it with shutdown/backoff/readiness logic without
        changing per-record processing semantics.
        """

        if max_records < 1:
            raise ValueError("max_records must be >= 1")

        results: list[KafkaRunnerProcessResult] = []
        for _ in range(max_records):
            result = self.process_next()
            results.append(result)
            if stop_on_idle and result.status == "idle":
                break
        return KafkaRunnerLoopResult(results=results)

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
