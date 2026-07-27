from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.compact_zeus.build_pingan_edr_field_audit import (  # noqa: E402
    build_edr_field_audit,
)
from validation.compact_zeus.build_pingan_edr_review_artifacts import (  # noqa: E402
    build_edr_review_artifact,
)


def _row() -> dict:
    fields = {
        "agent_id": "AGENT-XC-AUDIT",
        "alert_describe": "EDR nested detail audit",
        "host_name": "XC-AUDIT-HOST",
        "iplist": "10.20.30.40",
        "details0": {
            "attck_id": "TA0003,T1053.005",
            "command": "cmd.exe /c whoami",
            "process_mame": "cmd.exe",
            "process_md5": "a" * 32,
            "process_path": "C:\\Windows\\System32\\cmd.exe",
            "process_pid": "1200",
            "process_sha256": "b" * 64,
            "process_user": "SYSTEM",
            "rule_name": "Child process",
            "action_detail": {
                "child_name": "powershell.exe",
                "child_path": "C:\\Windows\\System32\\powershell.exe",
                "child_pid": "1201",
            },
        },
        "details1": {
            "process_mame": "reg.exe",
            "process_md5": "21",
            "process_sha256": "21",
            "action_detail": {
                "file_name": "payload.dll",
                "file_path": "C:\\Temp\\payload.dll",
                "registry_key": "HKCU\\Software\\Example",
                "task_name": "Updater",
            },
        },
    }
    return {
        "alert_id": 1,
        "alert_full_data": {
            "alert_data": {
                "alert": {
                    "alertId": "EDR-AUDIT-001",
                    "alertCode": "PIE-EDR-AUDIT-001",
                    "riskLevel": "high",
                    "createAt": "2026-07-14T10:00:00+08:00",
                    "hitLog": [
                        {
                            "topic": "edr-core-xc",
                            "topicName": "EDR",
                            "ruleCode": "EDR-RULE-001",
                            "zeusRawLogs": [
                                {
                                    "message": json.dumps(
                                        fields,
                                        ensure_ascii=False,
                                    )
                                }
                            ],
                        }
                    ],
                },
                "relatedAlertList": [],
            }
        },
    }


def test_edr_field_audit_tracks_nested_observations_and_invalid_hash_boundary() -> None:
    report = build_edr_field_audit([_row()])

    assert report["sample_count"] == 1
    assert report["parsed_message_count"] == 1
    assert report["nested_detail_coverage"]["records"] == 2
    assert report["nested_detail_coverage"]["valid_hash_counts"] == {
        "process_md5": 1,
        "process_sha256": 1,
    }
    assert report["nested_detail_coverage"]["invalid_hash_counts"] == {
        "process_md5": 1,
        "process_sha256": 1,
    }
    assert report["observation_coverage"] == {
        "process": {"alerts": 1, "observations": 2, "nodes": 3},
        "file": {"alerts": 1, "observations": 1},
        "directional_network": {"alerts": 0, "observations": 0},
    }
    assert report["canonical_target_coverage"]["host.ip_addresses"]["alerts"] == 1
    assert report["canonical_target_coverage"]["classification.tactic"]["alerts"] == 1
    assert (
        report["canonical_target_coverage"]["classification.technique"]["alerts"] == 1
    )
    assert report["high_value_gap_counts"] == {}
    assert report["raw_payload_mutation_count"] == 0

    fields = {item["path"]: item for item in report["fields"]}
    assert (
        fields["details1.process_mame"]["lanes"]["canonical_provenance"]["messages"]
        == 1
    )
    assert (
        fields["details1.process_md5"]["lanes"]["canonical_provenance"]["messages"] == 0
    )
    assert fields["details1.process_md5"]["lanes"]["llm"]["messages"] == 0


def test_edr_review_artifact_exposes_typed_action_and_hash_semantics() -> None:
    artifact = build_edr_review_artifact(
        cohort="fixture",
        row=_row(),
        review_focus="verify EDR nested evidence",
        phase="after_adapter_mapping",
    )

    semantics = {item["semantic_type"] for item in artifact["source_field_semantics"]}
    assert {
        "endpoint_child_process_observation",
        "endpoint_file_action_target",
        "endpoint_registry_action_context",
        "endpoint_scheduled_task_context",
        "invalid_process_hash",
        "vendor_mitre_classification",
    } <= semantics
    canonical = artifact["canonical_alert_without_raw"]
    assert canonical["entities"]["process"]["observations"][1]["nodes"][0] == {
        "process_name": "reg.exe"
    }
    assert canonical["entities"]["file"]["observations"][0]["relation"] == (
        "endpoint_action_target"
    )
