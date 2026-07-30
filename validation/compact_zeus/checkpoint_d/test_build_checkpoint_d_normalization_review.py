from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (
    canonical_sha256,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_normalization_review import (
    build_normalization_review,
)


def _corpus_row(alert_id: int) -> dict:
    alert_data = {
        "alert": {
            "alertId": str(alert_id),
            "riskLevel": "high",
            "hitLog": [
                {
                    "topic": "sec_guard_apt",
                    "topicName": "APT",
                    "ruleCode": "APT-001",
                    "zeusRawLogs": [
                        {
                            "source_ip": "processed-outer-value",
                            "message": json.dumps(
                                {
                                    "sip": "10.0.0.1",
                                    "dip": "10.0.0.2",
                                    "sport": "12345",
                                    "dport": "443",
                                    "proto": "tcp",
                                    "attack_type": "test-attempt",
                                }
                            ),
                        }
                    ],
                }
            ],
        }
    }
    full_data = {
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": str(alert_id),
        "alert_data": alert_data,
    }
    return {
        "alert_id": alert_id,
        "topic": "sec_guard_apt",
        "alert_full_data": full_data,
        "sample_origin": "full_alert_sample",
        "legacy_demo_status": "not_provided",
        "source_refs": [f"source.pkl#alert_id={alert_id}"],
        "canonical_payload_sha256": canonical_sha256(full_data),
    }


def test_normalization_review_proves_message_first_and_raw_immutability() -> None:
    corpus = pd.DataFrame([_corpus_row(1)])

    review = build_normalization_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
    )

    assert review["acceptance"]["status"] == "passed"
    assert review["acceptance"]["failed_checks"] == []
    assert all(review["acceptance"]["checks"].values())
    assert review["acceptance"]["parser_warning_count"] == 0
    assert review["acceptance"]["accepted_repair_count"] == 0
    assert review["acceptance"]["rejected_repair_count"] == 0
    assert review["normalization"]["adapter"] == "pingan_legacy_alert_platform"
    assert review["normalization"]["source_type"] == "ndr"
    assert review["normalization"]["evidence_input_policy"]["name"] == (
        "raw_message_first"
    )
    assert review["normalization"]["parsed_message_summary"] == [
        {
            "source_path": "alert.hitLog[0].zeusRawLogs[0].message",
            "parser_name": "pingan_json_object",
            "parser_version": "v1",
            "message_hash": review["normalization"]["parsed_message_summary"][0][
                "message_hash"
            ],
            "original_length": 119,
            "parsed_field_count": 6,
            "decoded_top_level_field_count": 0,
            "repaired_top_level_field_count": 0,
            "warning_count": 0,
        }
    ]
    normalized = review["normalization"]["normalized_alert"]
    assert normalized["entities"]["network"]["source_ip"] == "10.0.0.1"
    assert normalized["raw"] == corpus.iloc[0]["alert_full_data"]["alert_data"]
    assert review["scope"]["not_performed"] == [
        "generic_entity_extraction",
        "fact_reconstruction",
        "analysis_input_building",
        "skill_resolution",
        "analyzer_or_llm",
        "decision_policy",
        "persistence",
    ]
