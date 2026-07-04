"""Worker-side result contract for future SOC Kafka worker pools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from soc_agent.contracts import SocDaemonProcessResult
from soc_agent.core import SocDaemonService, SocServiceError
from soc_agent.daemon.kafka_mapper import DEFAULT_ALERT_TOPICS, DEFAULT_APPROVAL_REQUEST_TOPICS, KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message


class KafkaWorkerResultStatus(StrEnum):
    """Worker outcome categories consumed by a future poller/controller."""

    PROCESSED = "processed"
    DEAD_LETTER_REQUIRED = "dead_letter_required"
    RETRYABLE_ERROR = "retryable_error"
    FATAL_ERROR = "fatal_error"


@dataclass(frozen=True)
class KafkaWorkerError:
    """Structured worker error payload.

    ``exception`` is kept only for the in-process controller to publish a
    precise dead-letter error. It is not intended for durable serialization.
    """

    error_type: str
    message: str
    retryable: bool = False
    exception: Exception | None = field(default=None, compare=False, repr=False)

    @classmethod
    def from_exception(cls, exception: Exception, *, retryable: bool = False) -> KafkaWorkerError:
        return cls(
            error_type=type(exception).__name__,
            message=str(exception),
            retryable=retryable,
            exception=exception,
        )

    def as_exception(self) -> Exception:
        return self.exception or RuntimeError(f"{self.error_type}: {self.message}")


@dataclass(frozen=True)
class KafkaWorkerResult:
    """Result returned by a worker after processing one Kafka record.

    The worker result intentionally has no commit or dead-letter fields. Those
    operations belong to the Kafka poller/controller, not the worker.
    """

    status: KafkaWorkerResultStatus
    record: KafkaRecord
    daemon_result: SocDaemonProcessResult | None = None
    error: KafkaWorkerError | None = None

    def __post_init__(self) -> None:
        if self.status == KafkaWorkerResultStatus.PROCESSED:
            if self.daemon_result is None:
                raise ValueError("processed worker result requires daemon_result")
            if self.error is not None:
                raise ValueError("processed worker result cannot include error")
            return
        if self.daemon_result is not None:
            raise ValueError("error worker result cannot include daemon_result")
        if self.error is None:
            raise ValueError(f"{self.status.value} worker result requires error")

    @classmethod
    def processed(cls, *, record: KafkaRecord, daemon_result: SocDaemonProcessResult) -> KafkaWorkerResult:
        return cls(
            status=KafkaWorkerResultStatus.PROCESSED,
            record=record,
            daemon_result=daemon_result,
        )

    @classmethod
    def dead_letter_required(cls, *, record: KafkaRecord, error: KafkaWorkerError) -> KafkaWorkerResult:
        return cls(
            status=KafkaWorkerResultStatus.DEAD_LETTER_REQUIRED,
            record=record,
            error=error,
        )

    @classmethod
    def retryable_error(cls, *, record: KafkaRecord, error: KafkaWorkerError) -> KafkaWorkerResult:
        return cls(
            status=KafkaWorkerResultStatus.RETRYABLE_ERROR,
            record=record,
            error=error,
        )

    @classmethod
    def fatal_error(cls, *, record: KafkaRecord, error: KafkaWorkerError) -> KafkaWorkerResult:
        return cls(
            status=KafkaWorkerResultStatus.FATAL_ERROR,
            record=record,
            error=error,
        )

    @property
    def requires_dead_letter(self) -> bool:
        return self.status == KafkaWorkerResultStatus.DEAD_LETTER_REQUIRED

    @property
    def should_retry(self) -> bool:
        return self.status == KafkaWorkerResultStatus.RETRYABLE_ERROR

    @property
    def should_stop_controller(self) -> bool:
        return self.status == KafkaWorkerResultStatus.FATAL_ERROR


class SocKafkaWorker:
    """Worker that maps a record and invokes ``SocDaemonService``.

    The worker does not poll, commit offsets, write dead-letter records, or
    decide partition advancement. A future worker pool controller will consume
    ``KafkaWorkerResult`` and perform those Kafka operations centrally.
    """

    def __init__(
        self,
        *,
        daemon_service: SocDaemonService,
        alert_topics: frozenset[str] = DEFAULT_ALERT_TOPICS,
        approval_request_topics: frozenset[str] = DEFAULT_APPROVAL_REQUEST_TOPICS,
    ) -> None:
        self._daemon_service = daemon_service
        self._alert_topics = alert_topics
        self._approval_request_topics = approval_request_topics

    def process_record(self, record: KafkaRecord) -> KafkaWorkerResult:
        try:
            message = map_kafka_record_to_daemon_message(
                record,
                alert_topics=self._alert_topics,
                approval_request_topics=self._approval_request_topics,
            )
            daemon_result = self._daemon_service.process_message(message)
            return KafkaWorkerResult.processed(record=record, daemon_result=daemon_result)
        except (KafkaMapperError, SocServiceError) as exc:
            return KafkaWorkerResult.dead_letter_required(
                record=record,
                error=KafkaWorkerError.from_exception(exc, retryable=False),
            )
