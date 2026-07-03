from __future__ import annotations

import json

import pytest

from soc_agent.daemon.kafka_mapper import KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message


def test_kafka_mapper_maps_alert_topic_to_daemon_message() -> None:
    record = KafkaRecord(
        topic="soc.alerts.raw.v1",
        partition=2,
        offset=1024,
        key=b"alert-key-1",
        value=json.dumps({"alert_id": "ALT-1", "raw": {"src_ip": "1.1.1.1"}}).encode(),
    )

    message = map_kafka_record_to_daemon_message(record)

    assert message.kind == "alert"
    assert message.topic == "soc.alerts.raw.v1"
    assert message.partition == 2
    assert message.offset == 1024
    assert message.key == "alert-key-1"
    assert message.payload["alert_id"] == "ALT-1"


def test_kafka_mapper_maps_approval_request_topic_to_daemon_message() -> None:
    record = KafkaRecord(
        topic="soc.approvals.requests.v1",
        partition=0,
        offset=7,
        value={
            "approval_request_id": "APR-1",
            "permission_decision_id": "PERM-1",
            "route": "response.block_ip",
            "action": "response.block_ip",
            "risk_level": "high_risk",
            "reason": "requires approval",
            "requested_by": {"actor_id": "soc-daemon", "surface": "daemon"},
        },
    )

    message = map_kafka_record_to_daemon_message(record)

    assert message.kind == "approval_request"
    assert message.payload["approval_request_id"] == "APR-1"


def test_kafka_mapper_supports_custom_topic_sets() -> None:
    record = KafkaRecord(topic="custom.alerts", partition=0, offset=1, value='{"alert_id":"ALT-CUSTOM"}')

    message = map_kafka_record_to_daemon_message(
        record,
        alert_topics=frozenset({"custom.alerts"}),
        approval_request_topics=frozenset(),
    )

    assert message.kind == "alert"
    assert message.payload["alert_id"] == "ALT-CUSTOM"


def test_kafka_mapper_rejects_unknown_topic() -> None:
    record = KafkaRecord(topic="unknown.topic", partition=0, offset=1, value="{}")

    with pytest.raises(KafkaMapperError, match="unsupported"):
        map_kafka_record_to_daemon_message(record)


def test_kafka_mapper_rejects_invalid_json() -> None:
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{bad")

    with pytest.raises(KafkaMapperError, match="not valid JSON"):
        map_kafka_record_to_daemon_message(record)


def test_kafka_mapper_rejects_non_object_json() -> None:
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="[]")

    with pytest.raises(KafkaMapperError, match="must be an object"):
        map_kafka_record_to_daemon_message(record)


def test_kafka_mapper_rejects_non_utf8_key() -> None:
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, key=b"\xff", value="{}")

    with pytest.raises(KafkaMapperError, match="key is not valid UTF-8"):
        map_kafka_record_to_daemon_message(record)
