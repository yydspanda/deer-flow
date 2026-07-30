#!/usr/bin/env python3
"""Build Checkpoint D-0 inventory without invoking an adapter or Runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.corpus_inventory.v1"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_MANIFEST_PATH = DEFAULT_CORPUS_PATH.with_suffix(".manifest.json")
DEFAULT_OUTPUT_PATH = (
    ROOT
    / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
    / "step-d0-corpus-inventory/corpus-inventory.json"
)

EXPECTED_SOURCE_TYPE_BY_TOPIC = {
    "T_GBD_zeus_data": "siem",
    "edr-core-xc": "edr",
    "leagsoft-edr": "edr",
    "ptp-nids": "nids",
    "sec_guard_apt": "ndr",
    "sec_guard_apt_detail": "ndr",
    "sec_guard_wb": "threat_intel",
    "security_qthids": "hids",
}

BLOCKING_ISSUE_CODES = frozenset(
    {
        "alert_full_data_not_object",
        "wrapper_keys_missing",
        "wrapper_alert_id_mismatch",
        "alert_data_not_object",
        "alert_object_missing",
        "payload_alert_id_mismatch",
        "hit_log_not_list",
        "topic_missing",
        "topic_metadata_mismatch",
        "unknown_topic",
        "zeus_raw_logs_not_list",
        "raw_log_not_object",
        "canonical_payload_hash_mismatch",
        "corpus_schema_version_mismatch",
    }
)
KNOWN_GAP_ISSUE_CODES = frozenset({"evidence_unavailable"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    serialized = json.dumps(
        _json_compatible(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_inventory(
    corpus: pd.DataFrame,
    *,
    corpus_path: Path,
    corpus_file_sha256: str,
    manifest: Mapping[str, Any],
    expected_rows: int,
) -> dict[str, Any]:
    """Inspect only corpus wrappers and raw evidence availability."""

    rows: list[dict[str, Any]] = []
    topic_counts: Counter[str] = Counter()
    source_type_counts: Counter[str] = Counter()
    evidence_shape_counts: Counter[str] = Counter()
    message_value_type_counts: Counter[str] = Counter()
    issue_code_counts: Counter[str] = Counter()

    alert_ids = [_alert_id(value) for value in corpus.get("alert_id", [])]
    duplicate_alert_ids = sorted(
        alert_id for alert_id, count in Counter(alert_ids).items() if count > 1
    )

    for _, source_row in corpus.iterrows():
        row = _inspect_row(source_row)
        rows.append(row)
        topic_counts[row["topic"]] += 1
        source_type_counts[row["expected_source_type"]] += 1
        evidence_shape_counts[row["evidence_input_shape"]] += 1
        message_value_type_counts.update(row["message_value_type_counts"])
        issue_code_counts.update(row["issue_codes"])

    actual_rows = len(corpus)
    unique_alert_ids = len(set(alert_ids))
    manifest_output = manifest.get("output")
    if not isinstance(manifest_output, Mapping):
        manifest_output = {}

    global_issues: list[dict[str, Any]] = []
    if actual_rows != expected_rows:
        global_issues.append(
            {
                "code": "unexpected_row_count",
                "expected": expected_rows,
                "actual": actual_rows,
            }
        )
    if unique_alert_ids != actual_rows or duplicate_alert_ids:
        global_issues.append(
            {
                "code": "duplicate_alert_ids",
                "alert_ids": duplicate_alert_ids,
            }
        )

    manifest_sha256 = manifest_output.get("sha256")
    if manifest_sha256 != corpus_file_sha256:
        global_issues.append(
            {
                "code": "corpus_manifest_hash_mismatch",
                "expected": manifest_sha256,
                "actual": corpus_file_sha256,
            }
        )
    if manifest_output.get("rows") != actual_rows:
        global_issues.append(
            {
                "code": "corpus_manifest_row_count_mismatch",
                "expected": manifest_output.get("rows"),
                "actual": actual_rows,
            }
        )

    blocking_rows = [
        {
            "alert_id": row["alert_id"],
            "topic": row["topic"],
            "issue_codes": [
                code for code in row["issue_codes"] if code in BLOCKING_ISSUE_CODES
            ],
        }
        for row in rows
        if any(code in BLOCKING_ISSUE_CODES for code in row["issue_codes"])
    ]
    known_input_gaps = [
        {
            "alert_id": row["alert_id"],
            "topic": row["topic"],
            "issue_codes": [
                code for code in row["issue_codes"] if code in KNOWN_GAP_ISSUE_CODES
            ],
            "hit_log_count": row["hit_log_count"],
            "raw_event_count": row["raw_event_count"],
        }
        for row in rows
        if any(code in KNOWN_GAP_ISSUE_CODES for code in row["issue_codes"])
    ]

    if global_issues or blocking_rows:
        status = "failed"
    elif known_input_gaps:
        status = "passed_with_known_input_gaps"
    else:
        status = "passed"

    by_topic = _summarize_by(rows, "topic")
    by_source_type = _summarize_by(rows, "expected_source_type")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "restricted_corpus_load",
                "lineage_and_hash_check",
                "wrapper_and_alert_id_check",
                "topic_and_expected_source_family_inventory",
                "hit_log_raw_event_and_message_availability_inventory",
            ],
            "not_performed": [
                "message_parsing",
                "normalization",
                "entity_or_fact_reconstruction",
                "llm_projection",
                "analyzer_or_decision_policy",
                "persistence",
            ],
        },
        "input": {
            "corpus_path": _relative_path(corpus_path),
            "corpus_sha256": corpus_file_sha256,
            "manifest_schema_version": manifest.get("schema_version"),
            "manifest_sha256_matches": manifest_sha256 == corpus_file_sha256,
        },
        "acceptance": {
            "status": status,
            "expected_rows": expected_rows,
            "actual_rows": actual_rows,
            "unique_alert_ids": unique_alert_ids,
            "duplicate_alert_ids": duplicate_alert_ids,
            "blocking_row_count": len(blocking_rows),
            "known_input_gap_count": len(known_input_gaps),
            "global_issue_count": len(global_issues),
        },
        "summary": {
            "topic_counts": dict(sorted(topic_counts.items())),
            "expected_source_type_counts": dict(sorted(source_type_counts.items())),
            "evidence_input_shape_counts": dict(sorted(evidence_shape_counts.items())),
            "total_hit_logs": sum(row["hit_log_count"] for row in rows),
            "total_raw_log_items": sum(row["raw_log_item_count"] for row in rows),
            "total_raw_events": sum(row["raw_event_count"] for row in rows),
            "total_message_fields": sum(row["message_field_count"] for row in rows),
            "total_non_empty_messages": sum(
                row["non_empty_message_count"] for row in rows
            ),
            "message_value_type_counts": dict(
                sorted(message_value_type_counts.items())
            ),
            "issue_code_counts": dict(sorted(issue_code_counts.items())),
        },
        "by_topic": by_topic,
        "by_expected_source_type": by_source_type,
        "global_issues": global_issues,
        "blocking_rows": blocking_rows,
        "known_input_gaps": known_input_gaps,
        "rows": rows,
    }


def _inspect_row(row: pd.Series) -> dict[str, Any]:
    alert_id = _alert_id(row.get("alert_id"))
    issue_codes: list[str] = []
    full_data = row.get("alert_full_data")
    alert_data: Mapping[str, Any] = {}
    alert: Mapping[str, Any] = {}
    hit_logs: list[Any] = []

    if not isinstance(full_data, Mapping):
        issue_codes.append("alert_full_data_not_object")
    else:
        missing_wrapper_keys = sorted(
            {"app_code", "flow_id", "alert_id", "alert_data"}.difference(full_data)
        )
        if missing_wrapper_keys:
            issue_codes.append("wrapper_keys_missing")
        if str(full_data.get("alert_id")) != str(alert_id):
            issue_codes.append("wrapper_alert_id_mismatch")
        candidate_alert_data = full_data.get("alert_data")
        if not isinstance(candidate_alert_data, Mapping):
            issue_codes.append("alert_data_not_object")
        else:
            alert_data = candidate_alert_data
            candidate_alert = alert_data.get("alert")
            if not isinstance(candidate_alert, Mapping):
                issue_codes.append("alert_object_missing")
            else:
                alert = candidate_alert
                if str(alert.get("alertId")) != str(alert_id):
                    issue_codes.append("payload_alert_id_mismatch")
                candidate_hit_logs = alert.get("hitLog")
                if not isinstance(candidate_hit_logs, list):
                    issue_codes.append("hit_log_not_list")
                else:
                    hit_logs = candidate_hit_logs

    row_topic = _optional_string(row.get("topic"))
    hit_log_topics = sorted(
        {
            topic
            for item in hit_logs
            if isinstance(item, Mapping)
            if (topic := _optional_string(item.get("topic"))) is not None
        }
    )
    topic = row_topic or (hit_log_topics[0] if len(hit_log_topics) == 1 else "unknown")
    if topic == "unknown":
        issue_codes.append("topic_missing")
    if row_topic is not None and hit_log_topics and row_topic not in hit_log_topics:
        issue_codes.append("topic_metadata_mismatch")
    expected_source_type = EXPECTED_SOURCE_TYPE_BY_TOPIC.get(topic, "other")
    if expected_source_type == "other":
        issue_codes.append("unknown_topic")

    raw_log_item_count = 0
    raw_events: list[Mapping[str, Any]] = []
    message_field_count = 0
    non_empty_message_count = 0
    message_types: Counter[str] = Counter()
    for hit_log in hit_logs:
        if not isinstance(hit_log, Mapping):
            issue_codes.append("hit_log_not_object")
            continue
        raw_logs = hit_log.get("zeusRawLogs")
        if not isinstance(raw_logs, list):
            issue_codes.append("zeus_raw_logs_not_list")
            continue
        raw_log_item_count += len(raw_logs)
        for raw_log in raw_logs:
            if not isinstance(raw_log, Mapping):
                issue_codes.append("raw_log_not_object")
                continue
            raw_events.append(raw_log)
            if "message" not in raw_log:
                continue
            message_field_count += 1
            message = raw_log.get("message")
            message_type = type(message).__name__
            message_types[message_type] += 1
            if isinstance(message, str) and message.strip():
                non_empty_message_count += 1
            elif message is not None and not isinstance(message, str):
                issue_codes.append("message_not_string")

    if non_empty_message_count:
        evidence_input_shape = "raw_message_available"
    elif raw_events:
        evidence_input_shape = "structured_fallback_candidate"
    else:
        evidence_input_shape = "evidence_unavailable"
        issue_codes.append("evidence_unavailable")

    expected_payload_hash = _optional_string(row.get("canonical_payload_sha256"))
    actual_payload_hash = (
        canonical_sha256(full_data) if isinstance(full_data, Mapping) else None
    )
    if expected_payload_hash != actual_payload_hash:
        issue_codes.append("canonical_payload_hash_mismatch")
    if row.get("corpus_schema_version") != "soc.validation.alert_corpus.v1":
        issue_codes.append("corpus_schema_version_mismatch")

    return {
        "alert_id": alert_id,
        "topic": topic,
        "hit_log_topics": hit_log_topics,
        "expected_source_type": expected_source_type,
        "sample_origin": _optional_string(row.get("sample_origin")) or "unknown",
        "legacy_demo_status": _optional_string(row.get("legacy_demo_status"))
        or "unknown",
        "canonical_payload_sha256": actual_payload_hash,
        "hit_log_count": len(hit_logs),
        "raw_log_item_count": raw_log_item_count,
        "raw_event_count": len(raw_events),
        "message_field_count": message_field_count,
        "non_empty_message_count": non_empty_message_count,
        "message_value_type_counts": dict(sorted(message_types.items())),
        "evidence_input_shape": evidence_input_shape,
        "issue_codes": sorted(set(issue_codes)),
    }


def _summarize_by(
    rows: list[dict[str, Any]],
    key: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(row)
    return {
        name: {
            "alerts": len(items),
            "evidence_input_shape_counts": dict(
                sorted(Counter(item["evidence_input_shape"] for item in items).items())
            ),
            "raw_events": sum(item["raw_event_count"] for item in items),
            "non_empty_messages": sum(
                item["non_empty_message_count"] for item in items
            ),
            "alerts_with_issues": sum(bool(item["issue_codes"]) for item in items),
        }
        for name, items in sorted(grouped.items())
    }


def _alert_id(value: Any) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_compatible(child) for child in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_compatible(value.item())
        except (TypeError, ValueError):
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def write_json_atomic(value: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--expected-rows", type=int, default=212)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus = load_dataframe_pickle(args.corpus)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    inventory = build_inventory(
        corpus,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
        manifest=manifest,
        expected_rows=args.expected_rows,
    )
    write_json_atomic(inventory, args.output)
    print(
        json.dumps(
            {
                "output": _relative_path(args.output),
                **inventory["acceptance"],
                "evidence_input_shape_counts": inventory["summary"][
                    "evidence_input_shape_counts"
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if inventory["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
