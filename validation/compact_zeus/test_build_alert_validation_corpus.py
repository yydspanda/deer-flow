from __future__ import annotations

import json
import pickle
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest
from validation.compact_zeus.build_alert_validation_corpus import (
    CORPUS_SCHEMA_VERSION,
    build_corpus,
    deep_difference_paths,
    validate_corpus,
    validate_with_soc_normalizer,
    write_pickle_atomic,
)
from validation.compact_zeus.restricted_dataframe_pickle import load_dataframe_pickle

SOURCE_COLUMNS = [
    "alert_id",
    "alert_full_data",
    "agent_response",
    "risk_level",
    "topic",
    "topic_name",
    "related_status_dict",
    "status",
    "ignore_reason",
    "predict_label",
    "action_label",
    "status_label",
    "ground_label",
    "execute_type",
    "primary_type",
    "secondary_type",
    "tertiary_type",
]


def _payload(alert_id: int, *, source_ip: str) -> dict:
    return {
        "alert": {
            "alertId": str(alert_id),
            "executeType": "0",
            "riskLevel": "medium",
            "status": "pending",
            "primaryType": "intrusion",
            "secondaryType": "technical",
            "tertiaryType": "network",
            "hitLog": [
                {
                    "topic": "sec_guard_apt",
                    "topicName": "APT",
                    "zeusRawLogs": [{"source_ip": source_ip}],
                }
            ],
        },
        "relatedAlertList": [],
    }


def _row(alert_id: int, payload: dict) -> dict:
    return {
        "alert_id": alert_id,
        "alert_full_data": {
            "app_code": "zeus",
            "flow_id": "alert_agent",
            "alert_id": str(alert_id),
            "alert_data": payload,
        },
        "agent_response": json.dumps({"alert_id": str(alert_id), "analysis_result": {}}),
        "risk_level": "medium",
        "topic": "sec_guard_apt",
        "topic_name": "APT",
        "related_status_dict": Counter(),
        "status": "pending",
        "ignore_reason": None,
        "predict_label": None,
        "action_label": None,
        "status_label": None,
        "ground_label": None,
        "execute_type": "0",
        "primary_type": "intrusion",
        "secondary_type": "technical",
        "tertiary_type": "network",
    }


def _source_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=SOURCE_COLUMNS, dtype=object)
    frame["alert_id"] = frame["alert_id"].astype("int64")
    return frame


def test_build_corpus_deduplicates_and_preserves_conflict_variant(
    tmp_path: Path,
) -> None:
    exact_payload = _payload(1, source_ip="10.0.0.1")
    canonical_conflict = _payload(2, source_ip="10.0.0.2")
    legacy_conflict = _payload(2, source_ip="10.0.0.200")
    appended_payload = _payload(3, source_ip="10.0.0.3")
    source = _source_frame(
        [
            _row(1, exact_payload),
            _row(2, canonical_conflict),
        ]
    )
    source_before = source.copy(deep=True)

    corpus, report = build_corpus(
        source,
        source_ref="source.pkl",
        demos=[
            (tmp_path / "apt-1.json", exact_payload),
            (tmp_path / "apt-2.json", legacy_conflict),
            (tmp_path / "apt-3.json", appended_payload),
        ],
    )

    assert len(corpus) == 3
    assert corpus["alert_id"].tolist() == [1, 2, 3]
    assert source.equals(source_before)
    assert report["legacy_demo_status_counts"] == {
        "appended": 1,
        "conflict_pkl_authoritative": 1,
        "exact_match": 1,
    }
    by_id = corpus.set_index("alert_id")
    assert by_id.loc[1, "legacy_demo_status"] == "exact_match"
    assert by_id.loc[2, "legacy_demo_status"] == "conflict_pkl_authoritative"
    assert by_id.loc[2, "alert_full_data"]["alert_data"] == canonical_conflict
    variants = by_id.loc[2, "legacy_demo_variants"]
    assert len(variants) == 1
    assert variants[0]["alert_data"] == legacy_conflict
    assert variants[0]["difference_paths"] == ["$.alert.hitLog[0].zeusRawLogs[0].source_ip"]
    assert by_id.loc[3, "sample_origin"] == "legacy_demo"
    assert pd.isna(by_id.loc[3, "agent_response"])
    assert by_id.loc[3, "corpus_schema_version"] == CORPUS_SCHEMA_VERSION

    quality = validate_corpus(corpus)
    assert quality["status"] == "passed"
    assert quality["agent_response_counts"] == {"missing": 1, "valid": 2}


def test_corpus_round_trips_through_restricted_unpickler(tmp_path: Path) -> None:
    payload = _payload(10, source_ip="10.0.0.10")
    source = _source_frame([_row(10, payload)])
    corpus, _ = build_corpus(
        source,
        source_ref="source.pkl",
        demos=[(tmp_path / "apt-10.json", payload)],
    )
    output = tmp_path / "corpus.pkl"

    write_pickle_atomic(corpus, output)

    assert load_dataframe_pickle(output).equals(corpus)


def test_normalizer_validation_requires_bounded_structured_fallback() -> None:
    payload = _payload(11, source_ip="10.0.0.11")
    report = validate_with_soc_normalizer(_source_frame([_row(11, payload)]))

    assert report["status"] == "passed"
    assert report["structured_fallback_analysis_count"] == 1
    assert report["structured_fallback_projected_fields"] == 1
    assert report["policy_contract_violations"] == []


def test_normalizer_validation_reports_empty_structured_fallback_as_upstream_gap() -> None:
    payload = _payload(12, source_ip="10.0.0.12")
    payload["alert"]["hitLog"][0]["zeusRawLogs"] = []
    report = validate_with_soc_normalizer(_source_frame([_row(12, payload)]))

    assert report["status"] == "passed"
    assert report["structured_fallback_analysis_count"] == 1
    assert report["structured_fallback_unavailable_count"] == 1
    assert report["structured_fallback_projected_fields"] == 0
    assert report["policy_contract_violations"] == []


def test_restricted_unpickler_rejects_unapproved_global(tmp_path: Path) -> None:
    output = tmp_path / "not-a-dataframe.pkl"
    output.write_bytes(pickle.dumps(Path("/tmp/blocked")))

    with pytest.raises(pickle.UnpicklingError, match="blocked pickle global"):
        load_dataframe_pickle(output)


def test_deep_difference_paths_reports_presence_length_and_value() -> None:
    assert deep_difference_paths(
        {"a": [1, 2], "b": "left"},
        {"a": [1], "c": "right"},
    ) == [
        "$.a.length",
        "$.b",
        "$.c",
    ]
