#!/usr/bin/env python3
"""Build one Checkpoint D-1 canonical-normalization review artifact."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.normalizers import normalize_alert_payload  # noqa: E402
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (  # noqa: E402
    EXPECTED_SOURCE_TYPE_BY_TOPIC,
    canonical_sha256,
    sha256_file,
    write_json_atomic,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.normalization_review.v1"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
    / "step-d1-canonical-normalization"
)
DEFAULT_ALERT_ID = 1965449


def build_normalization_review(
    corpus: pd.DataFrame,
    *,
    alert_id: int,
    corpus_path: Path,
    corpus_file_sha256: str,
) -> dict[str, Any]:
    """Normalize one canonical corpus row and prove the raw payload is unchanged."""

    matches = corpus.loc[corpus["alert_id"].astype(str) == str(alert_id)]
    if len(matches) != 1:
        raise ValueError(
            f"alert_id={alert_id}: expected exactly one corpus row, found {len(matches)}"
        )
    row = matches.iloc[0]
    full_data = row.get("alert_full_data")
    if not isinstance(full_data, Mapping):
        raise TypeError(f"alert_id={alert_id}: alert_full_data must be an object")
    alert_data = full_data.get("alert_data")
    if not isinstance(alert_data, Mapping):
        raise TypeError(f"alert_id={alert_id}: alert_data must be an object")

    topic = _topic(row, alert_data)
    expected_source_type = EXPECTED_SOURCE_TYPE_BY_TOPIC.get(topic, "other")
    input_hash_before = canonical_sha256(alert_data)
    normalized = normalize_alert_payload(alert_data)
    input_hash_after = canonical_sha256(alert_data)
    normalized_json = normalized.model_dump(mode="json", exclude_none=True)
    normalized_raw_hash = canonical_sha256(normalized.raw)

    extensions = normalized.extensions
    parsed_messages = extensions.get("parsed_raw_messages")
    if not isinstance(parsed_messages, list):
        parsed_messages = []
    evidence_policy = extensions.get("evidence_input_policy")
    if not isinstance(evidence_policy, Mapping):
        evidence_policy = {}
    provenance = extensions.get("canonical_field_provenance")
    if not isinstance(provenance, list):
        provenance = []

    first_parsed_path = None
    if parsed_messages and isinstance(parsed_messages[0], Mapping):
        first_parsed_path = parsed_messages[0].get("source_path")

    parser_warning_count = 0
    accepted_repair_count = 0
    rejected_repair_count = 0
    for parsed_message in parsed_messages:
        if not isinstance(parsed_message, Mapping):
            continue
        warnings = parsed_message.get("warnings")
        if isinstance(warnings, list):
            parser_warning_count += len(warnings)
        repair_observations = parsed_message.get("repair_observations")
        if not isinstance(repair_observations, list):
            continue
        for observation in repair_observations:
            if not isinstance(observation, Mapping):
                continue
            if observation.get("status") == "accepted":
                accepted_repair_count += 1
            elif observation.get("status") == "rejected":
                rejected_repair_count += 1

    checks = {
        "corpus_payload_hash_matches": canonical_sha256(full_data)
        == row.get("canonical_payload_sha256"),
        "input_payload_unchanged": input_hash_before == input_hash_after,
        "normalized_raw_preserved_exactly": normalized.raw == alert_data,
        "normalized_raw_hash_matches_input": normalized_raw_hash == input_hash_before,
        "alert_id_matches": str(normalized.alert_id) == str(alert_id),
        "source_type_matches_expected": normalized.source.source_type.value
        == expected_source_type,
        "pingan_adapter_selected": normalized.source.integration_name
        == "pingan_legacy_alert_platform",
        "schema_version_is_canonical": normalized.schema_version == "soc.alert.v1",
        "parsed_message_available": bool(parsed_messages),
        "raw_message_first_selected": evidence_policy.get("name")
        == "raw_message_first",
        "selected_layer_is_raw_message": evidence_policy.get("selected_layer")
        == "raw_message",
        "selected_path_matches_first_parsed_message": evidence_policy.get(
            "selected_input_path"
        )
        == first_parsed_path,
        "processed_sibling_fields_excluded_from_reasoning": evidence_policy.get(
            "ignore_processed_fields_for_reasoning"
        )
        is True,
        "canonical_provenance_present": bool(provenance),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    if failed_checks:
        acceptance_status = "failed"
    elif parser_warning_count:
        acceptance_status = "passed_with_parser_warnings"
    else:
        acceptance_status = "passed"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "canonical_corpus_row_selection",
                "pingan_message_parsing",
                "canonical_alert_normalization",
                "raw_payload_immutability_check",
                "evidence_policy_and_provenance_check",
            ],
            "not_performed": [
                "generic_entity_extraction",
                "fact_reconstruction",
                "analysis_input_building",
                "skill_resolution",
                "analyzer_or_llm",
                "decision_policy",
                "persistence",
            ],
        },
        "input": {
            "corpus_path": _relative_path(corpus_path),
            "corpus_sha256": corpus_file_sha256,
            "alert_id": alert_id,
            "topic": topic,
            "expected_source_type": expected_source_type,
            "sample_origin": _optional_string(row.get("sample_origin")) or "unknown",
            "legacy_demo_status": _optional_string(row.get("legacy_demo_status"))
            or "unknown",
            "source_refs": _json_list(row.get("source_refs")),
            "canonical_corpus_payload_sha256": canonical_sha256(full_data),
            "canonical_alert_data_sha256": input_hash_before,
        },
        "acceptance": {
            "status": acceptance_status,
            "failed_checks": failed_checks,
            "checks": checks,
            "parser_warning_count": parser_warning_count,
            "accepted_repair_count": accepted_repair_count,
            "rejected_repair_count": rejected_repair_count,
        },
        "normalization": {
            "adapter": normalized.source.integration_name,
            "source_type": normalized.source.source_type.value,
            "evidence_input_policy": dict(evidence_policy),
            "parsed_message_count": len(parsed_messages),
            "parsed_message_summary": [
                _parsed_message_summary(item)
                for item in parsed_messages
                if isinstance(item, Mapping)
            ],
            "canonical_provenance_count": len(provenance),
            "normalized_alert": normalized_json,
        },
    }


def _parsed_message_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    fields = item.get("fields")
    decoded_fields = item.get("decoded_fields")
    repaired_fields = item.get("repaired_fields")
    warnings = item.get("warnings")
    return {
        "source_path": item.get("source_path"),
        "parser_name": item.get("parser_name"),
        "parser_version": item.get("parser_version"),
        "message_hash": item.get("message_hash"),
        "original_length": item.get("original_length"),
        "parsed_field_count": len(fields) if isinstance(fields, Mapping) else 0,
        "decoded_top_level_field_count": (
            len(decoded_fields) if isinstance(decoded_fields, Mapping) else 0
        ),
        "repaired_top_level_field_count": (
            len(repaired_fields) if isinstance(repaired_fields, Mapping) else 0
        ),
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
    }


def _topic(row: pd.Series, alert_data: Mapping[str, Any]) -> str:
    row_topic = _optional_string(row.get("topic"))
    if row_topic:
        return row_topic
    alert = alert_data.get("alert")
    if isinstance(alert, Mapping):
        hit_logs = alert.get("hitLog")
        if isinstance(hit_logs, list):
            for hit_log in hit_logs:
                if isinstance(hit_log, Mapping):
                    topic = _optional_string(hit_log.get("topic"))
                    if topic:
                        return topic
    return "unknown"


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


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--alert-id", type=int, default=DEFAULT_ALERT_ID)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus = load_dataframe_pickle(args.corpus)
    review = build_normalization_review(
        corpus,
        alert_id=args.alert_id,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
    )
    output_path = args.output_dir / f"{args.alert_id}.normalization.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "status": review["acceptance"]["status"],
                "failed_checks": review["acceptance"]["failed_checks"],
                "adapter": review["normalization"]["adapter"],
                "source_type": review["normalization"]["source_type"],
                "parsed_message_count": review["normalization"]["parsed_message_count"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
