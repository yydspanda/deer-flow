from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.compact_zeus.audits.build_pingan_nids_field_audit import (  # noqa: E402
    build_nids_field_audit,
)
from validation.compact_zeus.reviews.build_pingan_nids_review_artifacts import (  # noqa: E402
    build_nids_review_artifact,
)


def _row(*messages: dict) -> dict:
    return {
        "alert_id": 1,
        "alert_full_data": {
            "alert_data": {
                "alert": {
                    "alertId": "NIDS-AUDIT-001",
                    "alertCode": "PIE-NIDS-AUDIT-001",
                    "riskLevel": "high",
                    "createAt": "2026-07-14T10:00:00+08:00",
                    "hitLog": [
                        {
                            "topic": "ptp-nids",
                            "topicName": "NIDS",
                            "ruleCode": "NIDS-RULE-001",
                            "zeusRawLogs": [
                                {
                                    "message": json.dumps(
                                        message,
                                        ensure_ascii=False,
                                    )
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


def _message(*, source_port: int, path: str) -> dict:
    return {
        "sip": "198.51.100.10",
        "sport": str(source_port),
        "dip": "10.20.30.40",
        "dport": "8080",
        "proto": "TCP",
        "app_proto": "http",
        "direction": "to_server",
        "query": "10.20.30.40",
        "request_header_str": "{}",
        "packet": (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/" * 5
        ),
        "files": [
            {
                "filename": path,
                "gaps": False,
                "size": 42,
                "state": "CLOSED",
                "stored": False,
                "tx_id": 7,
            }
        ],
        "alert": {
            "action": "allowed",
            "attack_res": "1",
            "category": "代码执行",
            "signature": "Web RCE",
            "source": {"zone": "外网"},
            "target": {"zone": "内网"},
        },
        "http": {
            "http_method": "POST",
            "hostname": "app.example.internal",
            "url": path,
            "status": 200,
            "http_user_agent": "AuditFixture/1.0",
        },
    }


def test_nids_field_audit_tracks_canonical_observation_and_query_lanes() -> None:
    report = build_nids_field_audit(
        [
            _row(
                _message(source_port=43123, path="/first"),
                _message(source_port=43124, path="/second"),
            )
        ]
    )

    assert report["sample_count"] == 1
    assert report["parsed_message_count"] == 2
    assert report["five_tuple_complete"] == {
        "alerts": 1,
        "coverage_ratio": 1.0,
    }
    assert report["observation_coverage"]["network"]["observations"] == 2
    assert report["observation_coverage"]["http"]["observations"] == 2
    assert report["field_group_alert_counts"]["dns"]["alerts"] == 0
    assert report["field_group_alert_counts"]["query_context"]["alerts"] == 1
    assert report["field_group_alert_counts"]["file"]["alerts"] == 1
    assert report["canonical_target_coverage"]["http.method"]["alerts"] == 1
    assert report["canonical_target_coverage"]["network.direction"]["alerts"] == 1
    assert report["high_value_gap_counts"] == {}

    fields = {item["path"]: item for item in report["fields"]}
    assert fields["sport"]["lanes"]["canonical_provenance"]["messages"] == 2
    assert fields["http.url"]["lanes"]["canonical_provenance"]["messages"] == 2
    assert fields["query"]["lanes"]["canonical_provenance"]["messages"] == 0
    assert fields["query"]["lanes"]["llm"]["messages"] == 2
    assert report["encoded_context_policy"] == {
        "implementation": "backend/soc_agent/pipeline/encoded_context.py",
        "validation_entrypoint": "validation/compact_zeus/shared/compact_encoded_llm_context.py",
        "scope": "LLM projection only",
        "decoding": False,
        "raw_payload_preserved": True,
    }
    assert report["encoded_context_field_shapes"] == {
        "packet": {"encoded_span_compacted": 2},
        "request_header_str": {"empty_json_object": 2},
    }
    assert report["llm_encoded_compaction"] == {
        "alerts": 1,
        "spans": 2,
        "kinds": {"base64_like": 2},
    }


def test_nids_review_artifact_exposes_source_field_semantics() -> None:
    artifact = build_nids_review_artifact(
        cohort="fixture",
        row=_row(_message(source_port=43123, path="/first")),
        review_focus="verify semantics",
        phase="after_adapter_mapping",
    )

    semantics = {
        item["semantic_type"]: item for item in artifact["source_field_semantics"]
    }
    assert semantics["sensor_enforcement_action"]["participates_in_reasoning"] is True
    assert semantics["sensor_transaction_file_metadata"]["meaning"] == (
        "transaction_file_metadata_is_not_proof_of_endpoint_file_write"
    )
