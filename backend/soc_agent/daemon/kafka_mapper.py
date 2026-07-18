"""Map decoded Kafka records into SOC daemon message contracts.

This module intentionally avoids importing a Kafka client. Real consumers
should adapt their record object into ``KafkaRecord`` before calling the mapper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import SocAlertRawEnvelope, SocDaemonMessage

DEFAULT_ALERT_TOPICS = frozenset({"soc.alerts.raw.v1"})
DEFAULT_APPROVAL_REQUEST_TOPICS = frozenset({"soc.approvals.requests.v1"})


class KafkaMapperError(ValueError):
    """Raised when a Kafka record cannot be mapped into a daemon message."""


@dataclass(frozen=True)
class KafkaRecord:
    """Kafka-client-neutral record shape used by the SOC daemon mapper."""

    topic: str
    partition: int
    offset: int
    value: bytes | str | dict[str, Any]
    key: bytes | str | None = None
    headers: tuple[tuple[str, bytes | str | None], ...] = field(default_factory=tuple)


def map_kafka_record_to_daemon_message(
    record: KafkaRecord,
    *,
    alert_topics: frozenset[str] = DEFAULT_ALERT_TOPICS,
    approval_request_topics: frozenset[str] = DEFAULT_APPROVAL_REQUEST_TOPICS,
) -> SocDaemonMessage:
    """Map one Kafka record into the versioned daemon message contract."""

    kind = _kind_for_topic(record.topic, alert_topics=alert_topics, approval_request_topics=approval_request_topics)
    payload = _object_payload(record.value)
    if kind == "alert":
        try:
            payload = SocAlertRawEnvelope.model_validate(payload).to_analysis_payload()
        except ValidationError as exc:
            raise KafkaMapperError(_validation_error_message(exc)) from exc
    return SocDaemonMessage(
        kind=kind,
        payload=payload,
        topic=record.topic,
        partition=record.partition,
        offset=record.offset,
        key=_decode_optional_text(record.key),
    )


def _kind_for_topic(
    topic: str,
    *,
    alert_topics: frozenset[str],
    approval_request_topics: frozenset[str],
) -> str:
    if topic in alert_topics:
        return "alert"
    if topic in approval_request_topics:
        return "approval_request"
    raise KafkaMapperError(f"unsupported SOC Kafka topic: {topic}")


def _object_payload(value: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        text = value.decode("utf-8") if isinstance(value, bytes) else value
    except UnicodeDecodeError as exc:
        raise KafkaMapperError("Kafka record value is not valid UTF-8") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KafkaMapperError(f"Kafka record value is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise KafkaMapperError("Kafka record value JSON must be an object")
    return payload


def _decode_optional_text(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KafkaMapperError("Kafka record key is not valid UTF-8") from exc


def _validation_error_message(exc: ValidationError) -> str:
    details = []
    for error in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "envelope"
        details.append(f"{location}: {error['msg']}")
    return "Kafka alert envelope validation failed: " + "; ".join(details)
