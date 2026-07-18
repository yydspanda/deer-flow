from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.daemon.kafka_mapper import KafkaMapperError, KafkaRecord, map_kafka_record_to_daemon_message

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _alert_envelope(
    alert_id: str = "ALT-1",
    *,
    source: str = "edr",
    raw: dict | None = None,
    schema_version: str = "soc.alert.raw.v1",
) -> dict:
    return {
        "schema_version": schema_version,
        "source": source,
        "alert_id": alert_id,
        "dedup_key": f"{source}:{alert_id}",
        "occurred_at": "2026-07-18T10:00:00Z",
        "severity": "medium",
        "raw": raw or {"alert_id": alert_id},
        "entities_hint": {},
        "source_event_id": f"EVT-{alert_id}",
    }


def test_kafka_mapper_maps_alert_topic_to_daemon_message() -> None:
    record = KafkaRecord(
        topic="soc.alerts.raw.v1",
        partition=2,
        offset=1024,
        key=b"alert-key-1",
        value=json.dumps(_alert_envelope(raw={"alert_id": "ALT-1", "src_ip": "1.1.1.1"})).encode(),
    )

    message = map_kafka_record_to_daemon_message(record)

    assert message.kind == "alert"
    assert message.topic == "soc.alerts.raw.v1"
    assert message.partition == 2
    assert message.offset == 1024
    assert message.key == "alert-key-1"
    assert message.payload["alert_id"] == "ALT-1"
    assert message.payload["src_ip"] == "1.1.1.1"
    assert message.payload["_soc_ingress"]["schema_version"] == "soc.alert.raw.v1"
    assert message.payload["_soc_ingress"]["source_event_id"] == "EVT-ALT-1"


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
    record = KafkaRecord(
        topic="custom.alerts",
        partition=0,
        offset=1,
        value=json.dumps(_alert_envelope("ALT-CUSTOM")),
    )

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
    record = KafkaRecord(
        topic="soc.alerts.raw.v1",
        partition=0,
        offset=1,
        key=b"\xff",
        value=_alert_envelope(),
    )

    with pytest.raises(KafkaMapperError, match="key is not valid UTF-8"):
        map_kafka_record_to_daemon_message(record)


@pytest.mark.parametrize(
    ("source", "sample_name"),
    [
        ("ndr", "pingan_legacy_apt.json"),
        ("edr", "pingan_legacy_edr.json"),
        ("hids", "pingan_legacy_hids.json"),
    ],
)
def test_kafka_mapper_accepts_representative_apt_edr_hids_envelopes(
    source: str,
    sample_name: str,
) -> None:
    raw = json.loads((SAMPLES / sample_name).read_text(encoding="utf-8"))
    record = KafkaRecord(
        topic="soc.alerts.raw.v1",
        partition=1,
        offset=9,
        value=_alert_envelope(f"ALT-{source.upper()}", source=source, raw=raw),
    )

    message = map_kafka_record_to_daemon_message(record)

    assert message.kind == "alert"
    assert message.payload["_soc_ingress"]["source"] == source
    assert message.payload["_soc_ingress"]["alert_id"] == f"ALT-{source.upper()}"
    assert message.payload["alert"] == raw["alert"]
    assert message.payload["alert"]["hitLog"][0]["zeusRawLogs"] == raw["alert"]["hitLog"][0]["zeusRawLogs"]


def test_kafka_mapper_rejects_bad_alert_envelope_version_without_raw_leak() -> None:
    envelope = _alert_envelope(schema_version="soc.alert.raw.v2")
    envelope["raw"] = {"authorization": "Bearer must-not-leak"}
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value=envelope)

    with pytest.raises(KafkaMapperError, match="schema_version") as exc_info:
        map_kafka_record_to_daemon_message(record)

    assert "must-not-leak" not in str(exc_info.value)


def test_kafka_mapper_rejects_incomplete_alert_envelope() -> None:
    record = KafkaRecord(
        topic="soc.alerts.raw.v1",
        partition=0,
        offset=1,
        value={"schema_version": "soc.alert.raw.v1", "raw": {}},
    )

    with pytest.raises(KafkaMapperError, match="source: Field required"):
        map_kafka_record_to_daemon_message(record)


def test_kafka_mapper_rejects_oversized_raw_payload() -> None:
    envelope = _alert_envelope(raw={"message": "x" * 900_001})
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value=envelope)

    with pytest.raises(KafkaMapperError, match="raw exceeds 900000"):
        map_kafka_record_to_daemon_message(record)


def test_kafka_mapper_rejects_reserved_ingress_metadata_collision() -> None:
    envelope = _alert_envelope(raw={"_soc_ingress": {"spoofed": True}})
    record = KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value=envelope)

    with pytest.raises(KafkaMapperError, match="reserved key _soc_ingress"):
        map_kafka_record_to_daemon_message(record)
