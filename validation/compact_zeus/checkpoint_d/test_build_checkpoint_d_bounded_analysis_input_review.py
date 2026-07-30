from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_bounded_analysis_input_review import (
    SensitiveEvidenceMode,
    build_bounded_analysis_input_review,
)
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
                                    "req_header": (
                                        "POST /login HTTP/1.1\r\n"
                                        "Authorization: Bearer secret-token\r\n\r\n"
                                    ),
                                    "req_body": json.dumps(
                                        {
                                            "username": "analyst",
                                            "password": "secret-password",
                                        }
                                    ),
                                    "rsp_body": json.dumps(
                                        {
                                            "token": "secret-response-token",
                                            "status": "ok",
                                        }
                                    ),
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


def test_bounded_analysis_input_review_chains_d1_d3_without_running_llm() -> None:
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
    d3_review = build_fact_reconstruction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
        entity_review=d2_review,
    )

    review = build_bounded_analysis_input_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
        entity_review=d2_review,
        fact_review=d3_review,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )

    assert review["acceptance"]["status"] == "passed"
    assert review["acceptance"]["failed_checks"] == []
    assert all(review["acceptance"]["checks"].values())
    request = review["llm_analysis_request"]
    primary = request["primary_evidence"]
    assert primary["layer"] == "raw_message"
    assert primary["trust_level"] == "high"
    assert primary["sensitive_evidence_mode"] == "full"
    assert "secret-password" in primary["content"]
    assert "secret-token" in primary["content"]
    assert request["skill_context"]["selected_skills"] == []
    coverage = request["evidence_coverage"]
    assert coverage["counts"]["parsed_field_count"] > 0
    assert coverage["counts"]["decoded_field_count"] > 0
    assert coverage["counts"]["llm_sanitized_count"] == 0
    assert review["scope"]["not_performed"] == [
        "skill_resolution",
        "prompt_rendering",
        "analyzer_or_llm",
        "evidence_grounding",
        "decision_policy",
        "persistence",
    ]
