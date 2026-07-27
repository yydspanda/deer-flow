from __future__ import annotations

import json

from validation.compact_zeus.build_pingan_ti_siem_field_audit import (
    build_ti_siem_field_audit,
)
from validation.compact_zeus.build_pingan_ti_siem_review_artifacts import (
    build_ti_siem_review_artifact,
)


def _row(alert_id: int, *, topic: str, raw_events: list[dict]) -> dict:
    return {
        "alert_id": alert_id,
        "alert_full_data": {
            "alert_data": {
                "alert": {
                    "alertId": str(alert_id),
                    "alertCode": f"PIE-{alert_id}",
                    "alertName": "validation fixture",
                    "riskLevel": "medium",
                    "createAt": "2026-04-01T00:00:00+08:00",
                    "hitLog": [
                        {
                            "topic": topic,
                            "topicName": topic,
                            "ruleCode": f"RULE-{alert_id}",
                            "ruleName": "validation rule",
                            "zeusRawLogs": raw_events,
                        }
                    ],
                },
                "relatedAlertList": [],
            }
        },
    }


def _threat_intel_row() -> dict:
    fields = {
        "timeStr": "2026-04-01T12:21:32+08:00",
        "direction": "out",
        "machine": "172.28.253.5",
        "external_ip": "30.198.71.231",
        "attacker": "30.198.71.231",
        "victim": "172.28.253.5",
        "net": {
            "src_ip": "172.28.253.5",
            "dest_ip": "30.198.71.231",
            "src_port": 6449,
            "dest_port": 80,
            "proto": "TCP",
        },
        "assets": {"ip": "172.16.0.0/12"},
        "threat": {
            "severity": 3,
            "type": "mining",
            "name": "CoinMiner",
            "tag": ["Mitre: T1496: Resource Hijacking"],
        },
    }
    return _row(
        1,
        topic="sec_guard_wb",
        raw_events=[{"message": "tdpv3-svc Threatbook[123]: " + json.dumps(fields, ensure_ascii=False)}],
    )


def _siem_row() -> dict:
    return _row(
        2,
        topic="T_GBD_zeus_data",
        raw_events=[
            {
                "subtype": "suspicious_email",
                "email_id": "email-001",
                "from": '["sender@example.test"]',
                "to": '["recipient@example.test"]',
                "cc": "[]",
                "subject": "Suspicious message",
                "url": "[]",
                "attachment": "{}",
                "User": "system",
                "llm_score": "80分",
                "llm_ans": '["source model"]',
            }
        ],
    )


def test_ti_siem_field_audit_locks_role_and_trust_boundaries() -> None:
    report = build_ti_siem_field_audit([_threat_intel_row(), _siem_row()])

    assert report["status"] == "passed"
    assert report["source_alert_counts"] == {"siem": 1, "threat_intel": 1}
    assert report["source_raw_event_counts"] == {"siem": 1, "threat_intel": 1}
    assert report["siem_subtype_alert_counts"] == {"suspicious_email": 1}
    assert report["parsed_message_count"] == 1
    assert report["network_observation_count"] == 1
    assert report["email_observation_count"] == 1
    assert report["raw_payload_mutation_count"] == 0
    assert report["threat_intel_asset_scope_leak_count"] == 0
    assert report["threat_intel_structured_role_claim_count"] == 0
    assert report["siem_directional_network_count"] == 0
    assert report["siem_pipeline_actor_leak_count"] == 0
    assert report["siem_unselected_fact_claim_count"] == 0
    assert all(report["checks"].values())


def test_ti_siem_review_artifact_contains_canonical_and_bounded_views() -> None:
    artifact = build_ti_siem_review_artifact(
        cohort="siem-suspicious-email",
        row=_siem_row(),
        review_focus="typed email projection",
    )

    assert artifact["schema_version"] == ("soc.validation.pingan_ti_siem_checkpoint_c.v1")
    assert artifact["canonical_alert_without_raw"]["entities"]["email"]["sender_addresses"] == ["sender@example.test"]
    assert artifact["extracted_entities"]["emails"] == [
        "sender@example.test",
        "recipient@example.test",
    ]
    primary = artifact["bounded_analysis_evidence"]["primary"]
    assert primary["layer"] == "raw_structured"
    assert primary["trust_level"] == "high"
    assert primary["sensitive_evidence_mode"] == "full"
