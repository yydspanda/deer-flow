from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (
    canonical_sha256,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_entity_extraction_review import (
    build_entity_extraction_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_normalization_review import (
    build_normalization_review,
)


def _corpus() -> pd.DataFrame:
    alert_data = {
        "alert": {
            "alertId": "1",
            "riskLevel": "high",
            "hitLog": [
                {
                    "topic": "sec_guard_apt",
                    "topicName": "APT",
                    "ruleCode": "APT-001",
                    "ruleName": "Test alert",
                    "zeusRawLogs": [
                        {
                            "message": json.dumps(
                                {
                                    "sip": "10.0.0.1",
                                    "dip": "10.0.0.2",
                                    "sport": "12345",
                                    "dport": "443",
                                    "proto": "tcp",
                                    "host": "example.test",
                                    "asset_group": "Example Business Unit",
                                    "attack_type": "test-attempt",
                                }
                            )
                        }
                    ],
                }
            ],
        }
    }
    full_data = {
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": "1",
        "alert_data": alert_data,
    }
    return pd.DataFrame(
        [
            {
                "alert_id": 1,
                "topic": "sec_guard_apt",
                "alert_full_data": full_data,
                "sample_origin": "full_alert_sample",
                "legacy_demo_status": "not_provided",
                "source_refs": ["source.pkl#alert_id=1"],
                "canonical_payload_sha256": canonical_sha256(full_data),
            }
        ]
    )


def test_entity_extraction_review_matches_d1_and_emits_deterministic_mentions() -> None:
    corpus = _corpus()
    d1_review = build_normalization_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
    )

    review = build_entity_extraction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
    )

    assert review["acceptance"]["status"] == "passed_with_extraction_warnings"
    assert review["acceptance"]["failed_checks"] == []
    assert all(review["acceptance"]["checks"].values())
    extraction = review["entity_extraction"]
    assert extraction["kind_counts"] == {
        "asset": 1,
        "domain": 2,
        "ip": 2,
        "rule": 1,
        "rule_code": 1,
        "rule_name": 1,
    }
    assert extraction["entities"]["hosts"] == []
    assert extraction["entities"]["assets"] == ["Example Business Unit"]
    assert extraction["extraction_report"]["warnings"] == [
        "no process entity extracted"
    ]
    assert all(
        mention["source"] == "deterministic"
        and mention["confidence"] == 1.0
        and mention["evidence_path"]
        for mention in extraction["entities"]["mentions"]
    )
    assert review["scope"]["not_performed"] == [
        "fact_reconstruction",
        "analysis_input_building",
        "skill_resolution",
        "analyzer_or_llm",
        "decision_policy",
        "persistence",
    ]
