from __future__ import annotations

from validation.compact_zeus.audits.build_pingan_ndr_hids_field_audit import (
    build_ndr_hids_field_audit,
)
from validation.compact_zeus.reviews.build_pingan_ndr_hids_review_artifacts import (
    build_ndr_hids_review_artifact,
)


def _row(alert_id: int, *, topic: str, messages: list[str]) -> dict:
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
                            "zeusRawLogs": [
                                {
                                    "message": message,
                                    "sip": "198.51.100.200",
                                    "dip": "203.0.113.200",
                                    "internal_ip": "192.0.2.200",
                                    "attack_type": "processed-only taxonomy",
                                }
                                for message in messages
                            ],
                        }
                    ],
                },
                "relatedAlertList": [],
            }
        },
    }


def _ndr_row() -> dict:
    return _row(
        1,
        topic="sec_guard_apt",
        messages=[
            "skyeye|!{"
            '"sip":"30.1.1.10","dip":"30.2.2.20",'
            '"sport":"45678","dport":"443","proto":"TCP",'
            '"attack_type":"代码执行","rule_name":"fixture rule",'
            '"host":"app.example.test","uri":"/upload",'
            '"x_forwarded_for":"203.0.113.7",'
            '"file_name":"payload.jsp",'
            '"file_md5":"0123456789abcdef0123456789abcdef",'
            '"rule_labels":"{\\"kind\\":\\"web\\"}",'
            '"payload":{"req_body":"{\\"command\\":\\"id\\"}"},'
            '"_origin":{"vuln_harm":"fixture impact",'
            '"sip":"30.1.1.10","dip":"30.2.2.20"},'
            '"ioc":"19023-发现反弹SHELL行为（Linux）"}'
        ],
    )


def _hids_row() -> dict:
    return _row(
        2,
        topic="security_qthids",
        messages=[
            'qtAlert event_type="bounce_shell" internal_ip="30.3.3.30" '
            'external_ip="1.1.1.1" host_name="host-30" agent_id="agent-30" '
            'pname="bash" pid="100" cmd="bash -i" uname="app" '
            'dst_ip="198.51.100.9" port="4444"',
            'qtAlert event_type="honey_file" internal_ip="30.3.3.30" '
            'host_name="host-30" process_chain="java(10)->touch(11)" '
            'file_path="/srv/decoy.txt" '
            'md5="0123456789abcdef0123456789abcdef"',
        ],
    )


def test_ndr_hids_field_audit_locks_message_first_semantics() -> None:
    report = build_ndr_hids_field_audit([_ndr_row(), _hids_row()])

    assert report["status"] == "passed"
    assert report["schema_version"] == "soc.validation.pingan_ndr_hids_field_audit.v3"
    assert report["source_alert_counts"] == {"hids": 1, "ndr": 1}
    assert report["parsed_message_counts"] == {"hids": 2, "ndr": 1}
    assert report["ndr_observation_coverage"] == {
        "network_expected_messages": 1,
        "network_mapped_messages": 1,
        "http_observations": 1,
        "file_expected_messages": 1,
        "file_mapped_messages": 1,
        "vendor_descriptor_messages": 1,
        "threat_indicator_leaks": 0,
    }
    assert report["hids_observation_coverage"] == {
        "process_expected_messages": 2,
        "process_mapped_messages": 2,
        "file_expected_messages": 1,
        "file_mapped_messages": 1,
        "network_observations": 1,
        "invalid_network_observations": 0,
        "canonical_directional_network_alerts": 0,
        "default_external_ip_leaks": 0,
    }
    assert report["parsed_message_fallback_violations"] == []
    assert report["high_value_instance_gaps"] == []
    assert report["field_instance_lane_counts"]["non_empty_not_typed_or_semantic"] == 0
    assert report["checks"]["all_nonempty_fields_typed_or_semantic"]
    assert report["checks"]["parsed_message_analysis_excludes_structured_fallback"]
    assert all(report["checks"].values())


def test_ndr_hids_review_artifact_contains_canonical_and_bounded_views() -> None:
    artifact = build_ndr_hids_review_artifact(
        cohort="ndr-fixture",
        row=_ndr_row(),
        review_focus="message-first field projection",
    )

    assert artifact["schema_version"] == (
        "soc.validation.pingan_ndr_hids_checkpoint_c.v1"
    )
    canonical = artifact["canonical_alert_without_raw"]
    assert canonical["entities"]["network"]["source_ip"] == "30.1.1.10"
    assert canonical["entities"]["file"]["observations"][0]["relation"] == (
        "observed_artifact"
    )
    assert canonical["entities"]["threat"]["iocs"] == []
    semantics = {item["semantic_type"] for item in artifact["source_field_semantics"]}
    assert "vendor_detection_descriptor" in semantics
    assert artifact["evidence_coverage"]["high_value_gaps"] == []
    assert artifact["bounded_analysis_evidence"]["primary"]["trust_level"] == ("high")
