#!/usr/bin/env python3
"""Build one Checkpoint D-2 generic-entity-extraction review artifact."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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

from soc_agent.core.runtime import inspect_alert_normalization  # noqa: E402
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (  # noqa: E402
    canonical_sha256,
    sha256_file,
    write_json_atomic,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.entity_extraction_review.v1"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_CHECKPOINT_D_ROOT = (
    ROOT / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
)
DEFAULT_ALERT_ID = 1965449


def build_entity_extraction_review(
    corpus: pd.DataFrame,
    *,
    alert_id: int,
    corpus_path: Path,
    corpus_file_sha256: str,
    normalization_review: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay public normalization inspection and compare it with confirmed D-1."""

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

    d1_normalization = normalization_review.get("normalization")
    if not isinstance(d1_normalization, Mapping):
        raise ValueError("D-1 artifact is missing normalization")
    d1_normalized_alert = d1_normalization.get("normalized_alert")
    if not isinstance(d1_normalized_alert, Mapping):
        raise ValueError("D-1 artifact is missing normalized_alert")
    d1_acceptance = normalization_review.get("acceptance")
    if not isinstance(d1_acceptance, Mapping):
        raise ValueError("D-1 artifact is missing acceptance")

    input_hash_before = canonical_sha256(alert_data)
    inspection = inspect_alert_normalization(alert_data)
    input_hash_after = canonical_sha256(alert_data)
    replayed_normalized_alert = inspection.alert.model_dump(
        mode="json",
        exclude_none=True,
    )
    entities = inspection.entities.model_dump(mode="json", exclude_none=True)
    extraction_report = inspection.extraction_report.model_dump(
        mode="json",
        exclude_none=True,
    )
    mentions = entities.get("mentions")
    if not isinstance(mentions, list):
        mentions = []

    kind_counts = Counter(
        str(item.get("kind")) for item in mentions if isinstance(item, Mapping)
    )
    role_counts = Counter(
        str(item.get("role"))
        for item in mentions
        if isinstance(item, Mapping) and item.get("role")
    )
    expected_entity_counts = dict(sorted(kind_counts.items()))
    d1_normalized_semantics = normalization_semantic_projection(d1_normalized_alert)
    replayed_normalized_semantics = normalization_semantic_projection(
        replayed_normalized_alert
    )
    normalized_alerts_match_exactly = canonical_sha256(
        replayed_normalized_alert
    ) == canonical_sha256(d1_normalized_alert)
    normalized_alerts_match_semantically = canonical_sha256(
        replayed_normalized_semantics
    ) == canonical_sha256(d1_normalized_semantics)
    runtime_variance_paths = (
        [] if normalized_alerts_match_exactly else ["event.received_at"]
    )
    checks = {
        "d1_acceptance_allows_continuation": d1_acceptance.get("status")
        in {"passed", "passed_with_parser_warnings"},
        "d1_alert_id_matches": str(
            _mapping_path(normalization_review, "input", "alert_id")
        )
        == str(alert_id),
        "replayed_normalized_alert_semantics_match_d1": (
            normalized_alerts_match_semantically
        ),
        "input_payload_unchanged": input_hash_before == input_hash_after,
        "normalized_raw_preserved_exactly": inspection.alert.raw == alert_data,
        "mention_count_matches_report": len(mentions)
        == extraction_report.get("mention_count"),
        "entity_counts_match_report": expected_entity_counts
        == extraction_report.get("entity_counts"),
        "mentions_are_deterministic": all(
            isinstance(item, Mapping) and item.get("source") == "deterministic"
            for item in mentions
        ),
        "mentions_have_evidence_paths": all(
            isinstance(item, Mapping) and bool(item.get("evidence_path"))
            for item in mentions
        ),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    warnings = extraction_report.get("warnings")
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    if failed_checks:
        status = "failed"
    elif warning_count:
        status = "passed_with_extraction_warnings"
    else:
        status = "passed"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "canonical_normalization_replay_for_chain_integrity",
                "generic_deterministic_entity_extraction",
                "extraction_report",
                "d1_normalized_hash_comparison",
            ],
            "not_performed": [
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
            "topic": _optional_string(row.get("topic")) or "unknown",
            "d1_schema_version": normalization_review.get("schema_version"),
            "d1_status": d1_acceptance.get("status"),
            "d1_normalized_alert_sha256": canonical_sha256(d1_normalized_alert),
            "replayed_normalized_alert_sha256": canonical_sha256(
                replayed_normalized_alert
            ),
            "d1_normalized_semantic_sha256": canonical_sha256(d1_normalized_semantics),
            "replayed_normalized_semantic_sha256": canonical_sha256(
                replayed_normalized_semantics
            ),
            "normalization_exact_match": normalized_alerts_match_exactly,
            "normalization_semantic_match": normalized_alerts_match_semantically,
            "allowed_runtime_variance_paths": runtime_variance_paths,
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "extraction_warning_count": warning_count,
        },
        "entity_extraction": {
            "mention_count": len(mentions),
            "kind_counts": expected_entity_counts,
            "role_counts": dict(sorted(role_counts.items())),
            "entities": entities,
            "extraction_report": extraction_report,
        },
    }


def _mapping_path(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def normalization_semantic_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove the sole accepted per-run normalization value for replay comparison."""

    comparable = json.loads(json.dumps(value, ensure_ascii=False))
    event = comparable.get("event")
    if isinstance(event, dict):
        event.pop("received_at", None)
    return comparable


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


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--alert-id", type=int, default=DEFAULT_ALERT_ID)
    parser.add_argument(
        "--normalization-review",
        type=Path,
        default=None,
        help="D-1 artifact; defaults to the selected alert under checkpoint-d.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    normalization_review_path = args.normalization_review or (
        DEFAULT_CHECKPOINT_D_ROOT
        / "step-d1-canonical-normalization"
        / f"{args.alert_id}.normalization.json"
    )
    output_dir = args.output_dir or (
        DEFAULT_CHECKPOINT_D_ROOT / "step-d2-generic-entity-extraction"
    )
    corpus = load_dataframe_pickle(args.corpus)
    normalization_review = json.loads(
        normalization_review_path.read_text(encoding="utf-8")
    )
    review = build_entity_extraction_review(
        corpus,
        alert_id=args.alert_id,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
        normalization_review=normalization_review,
    )
    output_path = output_dir / f"{args.alert_id}.entities.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "status": review["acceptance"]["status"],
                "failed_checks": review["acceptance"]["failed_checks"],
                "mention_count": review["entity_extraction"]["mention_count"],
                "kind_counts": review["entity_extraction"]["kind_counts"],
                "warnings": review["entity_extraction"]["extraction_report"].get(
                    "warnings",
                    [],
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
