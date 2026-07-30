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
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_fact_reconstruction_review import (
    build_fact_reconstruction_review,
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
                    "ruleName": "Weak-password attempt",
                    "zeusRawLogs": [
                        {
                            "message": json.dumps(
                                {
                                    "sip": "10.0.0.1",
                                    "dip": "10.0.0.2",
                                    "sport": "12345",
                                    "dport": "443",
                                    "proto": "tcp",
                                    "attack_type": "弱口令",
                                    "attack_sip": "10.0.0.1",
                                    "alarm_sip": "10.0.0.2",
                                    "rule_desc": "Sensor description, not the platform rule name",
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


def test_fact_reconstruction_review_chains_d1_d2_and_stops_before_analysis() -> None:
    corpus = _corpus()
    d1_review = build_normalization_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
    )
    d2_review = build_entity_extraction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
    )

    review = build_fact_reconstruction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
        entity_review=d2_review,
    )

    assert review["acceptance"]["status"] == "passed"
    assert review["acceptance"]["failed_checks"] == []
    assert all(review["acceptance"]["checks"].values())
    summary = review["fact_summary"]
    assert summary["selected_layer"] == "raw_message"
    assert summary["selected_source_trust"] == "high"
    assert summary["participating_field_trust_count"] == 1
    assert summary["field_reasoning_status_counts"] == {
        "excluded_duplicate_projection": 2,
        "excluded_unselected_fallback": 1,
        "selected_evidence": 1,
    }
    assert summary["role_claim_counts"] == {
        "attacker": 1,
        "destination": 1,
        "impacted_asset": 1,
        "source": 1,
        "victim": 1,
    }
    assert summary["role_resolution_status_counts"] == {
        "observed": 2,
        "tentative": 3,
    }
    assert summary["unresolved_roles"] == []
    assert summary["scenario_types"] == ["web_attack"]
    assert summary["conflict_types"] == []
    provenance_paths = {
        item["canonical_path"]
        for item in review["fact_reconstruction"]["canonical_field_provenance"]
    }
    assert "detection.rule_name" not in provenance_paths
    assert review["scope"]["not_performed"] == [
        "analysis_input_building",
        "skill_resolution",
        "analyzer_or_llm",
        "evidence_grounding",
        "decision_policy",
        "persistence",
    ]
