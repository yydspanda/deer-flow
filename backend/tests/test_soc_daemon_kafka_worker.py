from __future__ import annotations

import json
from typing import Any

import pytest

from soc_agent.contracts import SocDaemonMessage, SocDaemonProcessResult
from soc_agent.core import SocDaemonService, SocServiceError
from soc_agent.daemon import KafkaWorkerError, KafkaWorkerResult, KafkaWorkerResultStatus, SocKafkaWorker
from soc_agent.daemon.kafka_mapper import KafkaRecord


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
            run_id="RUN-WORKER-001" if daemon_message.kind == "alert" else None,
        )


class FailedRuntimeDaemonService(SocDaemonService):
    def __init__(self, *, retryable: bool) -> None:
        self.retryable = retryable

    def process_message(self, message: SocDaemonMessage | dict[str, Any]) -> SocDaemonProcessResult:
        daemon_message = SocDaemonMessage.model_validate(message)
        return SocDaemonProcessResult(
            message_id=daemon_message.message_id,
            kind=daemon_message.kind,
            status="failed",
            run_id="RUN-FAILED-001",
            error="TimeoutError while invoking configured SOC analyzer",
            payload={
                "failure_kind": "analyzer_timeout" if self.retryable else "analyzer_output_invalid",
                "retryable": self.retryable,
            },
        )


def _record(*, topic: str = "soc.alerts.raw.v1", value: str | None = None) -> KafkaRecord:
    if value is None:
        value = json.dumps(
            {
                "schema_version": "soc.alert.raw.v1",
                "source": "edr",
                "alert_id": "ALT-1",
                "dedup_key": "edr:ALT-1",
                "occurred_at": "2026-07-18T10:00:00Z",
                "severity": "medium",
                "raw": {"alert_id": "ALT-1"},
                "entities_hint": {},
            }
        )
    return KafkaRecord(topic=topic, partition=0, offset=1, value=value)


def test_kafka_worker_returns_processed_result_without_kafka_side_effects() -> None:
    service = FakeDaemonService()
    record = _record()

    result = SocKafkaWorker(daemon_service=service).process_record(record)

    assert result.status == KafkaWorkerResultStatus.PROCESSED
    assert result.record == record
    assert result.daemon_result is not None
    assert result.daemon_result.run_id == "RUN-WORKER-001"
    assert result.error is None
    assert result.requires_dead_letter is False
    assert service.messages[0].kind == "alert"


def test_kafka_worker_returns_dead_letter_required_for_mapper_failure() -> None:
    service = FakeDaemonService()
    record = _record(topic="unsupported.topic")

    result = SocKafkaWorker(daemon_service=service).process_record(record)

    assert result.status == KafkaWorkerResultStatus.DEAD_LETTER_REQUIRED
    assert result.requires_dead_letter is True
    assert result.error is not None
    assert result.error.error_type == "KafkaMapperError"
    assert "unsupported SOC Kafka topic" in result.error.message
    assert service.messages == []


def test_kafka_worker_returns_dead_letter_required_for_service_failure() -> None:
    record = _record()

    result = SocKafkaWorker(daemon_service=FakeDaemonService(error=SocServiceError("db unavailable"))).process_record(record)

    assert result.status == KafkaWorkerResultStatus.DEAD_LETTER_REQUIRED
    assert result.error is not None
    assert result.error.error_type == "SocServiceError"
    assert result.error.message == "db unavailable"
    assert isinstance(result.error.as_exception(), SocServiceError)


def test_kafka_worker_preserves_runtime_retryability() -> None:
    retryable = SocKafkaWorker(daemon_service=FailedRuntimeDaemonService(retryable=True)).process_record(_record())
    invalid_output = SocKafkaWorker(daemon_service=FailedRuntimeDaemonService(retryable=False)).process_record(_record())

    assert retryable.status is KafkaWorkerResultStatus.RETRYABLE_ERROR
    assert retryable.should_retry is True
    assert retryable.error is not None
    assert retryable.error.error_type == "analyzer_timeout"
    assert invalid_output.status is KafkaWorkerResultStatus.DEAD_LETTER_REQUIRED
    assert invalid_output.requires_dead_letter is True


def test_kafka_worker_result_rejects_processed_without_daemon_result() -> None:
    with pytest.raises(ValueError, match="daemon_result"):
        KafkaWorkerResult(status=KafkaWorkerResultStatus.PROCESSED, record=_record())


def test_kafka_worker_result_rejects_error_status_without_error() -> None:
    with pytest.raises(ValueError, match="requires error"):
        KafkaWorkerResult(status=KafkaWorkerResultStatus.RETRYABLE_ERROR, record=_record())


def test_kafka_worker_result_exposes_controller_actions() -> None:
    record = _record()
    error = KafkaWorkerError(error_type="TimeoutError", message="LLM timeout", retryable=True)

    retryable = KafkaWorkerResult.retryable_error(record=record, error=error)
    fatal = KafkaWorkerResult.fatal_error(record=record, error=KafkaWorkerError(error_type="RuntimeError", message="bad config"))

    assert retryable.should_retry is True
    assert retryable.requires_dead_letter is False
    assert retryable.should_stop_controller is False
    assert fatal.should_stop_controller is True
    assert fatal.should_retry is False
