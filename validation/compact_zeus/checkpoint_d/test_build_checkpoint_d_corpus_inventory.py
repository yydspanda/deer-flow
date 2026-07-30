from __future__ import annotations

from pathlib import Path

import pandas as pd
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (
    build_inventory,
    canonical_sha256,
)


def _row(
    alert_id: int,
    *,
    topic: str,
    raw_logs: list[object],
) -> dict:
    alert_data = {
        "alert": {
            "alertId": str(alert_id),
            "hitLog": [
                {
                    "topic": topic,
                    "topicName": topic,
                    "zeusRawLogs": raw_logs,
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
        "topic": topic,
        "alert_full_data": full_data,
        "corpus_schema_version": "soc.validation.alert_corpus.v1",
        "sample_origin": "full_alert_sample",
        "legacy_demo_status": "not_provided",
        "canonical_payload_sha256": canonical_sha256(full_data),
    }


def test_inventory_separates_message_fallback_and_upstream_gap() -> None:
    corpus = pd.DataFrame(
        [
            _row(
                1,
                topic="ptp-nids",
                raw_logs=[{"message": '{"sip":"10.0.0.1"}'}],
            ),
            _row(2, topic="T_GBD_zeus_data", raw_logs=[{"subtype": "email"}]),
            _row(3, topic="leagsoft-edr", raw_logs=[]),
        ]
    )
    report = build_inventory(
        corpus,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        manifest={"output": {"sha256": "corpus-hash", "rows": 3}},
        expected_rows=3,
    )

    assert report["acceptance"] == {
        "status": "passed_with_known_input_gaps",
        "expected_rows": 3,
        "actual_rows": 3,
        "unique_alert_ids": 3,
        "duplicate_alert_ids": [],
        "blocking_row_count": 0,
        "known_input_gap_count": 1,
        "global_issue_count": 0,
    }
    assert report["summary"]["evidence_input_shape_counts"] == {
        "evidence_unavailable": 1,
        "raw_message_available": 1,
        "structured_fallback_candidate": 1,
    }
    assert report["known_input_gaps"] == [
        {
            "alert_id": 3,
            "topic": "leagsoft-edr",
            "issue_codes": ["evidence_unavailable"],
            "hit_log_count": 1,
            "raw_event_count": 0,
        }
    ]
    assert report["scope"]["not_performed"] == [
        "message_parsing",
        "normalization",
        "entity_or_fact_reconstruction",
        "llm_projection",
        "analyzer_or_decision_policy",
        "persistence",
    ]


def test_inventory_fails_unknown_topic_and_payload_hash_mismatch() -> None:
    row = _row(4, topic="new-vendor-topic", raw_logs=[{"message": "key=value"}])
    row["canonical_payload_sha256"] = "stale"
    report = build_inventory(
        pd.DataFrame([row]),
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        manifest={"output": {"sha256": "corpus-hash", "rows": 1}},
        expected_rows=1,
    )

    assert report["acceptance"]["status"] == "failed"
    assert report["acceptance"]["blocking_row_count"] == 1
    assert report["blocking_rows"] == [
        {
            "alert_id": 4,
            "topic": "new-vendor-topic",
            "issue_codes": [
                "canonical_payload_hash_mismatch",
                "unknown_topic",
            ],
        }
    ]
