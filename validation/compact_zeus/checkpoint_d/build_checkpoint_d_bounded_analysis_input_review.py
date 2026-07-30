#!/usr/bin/env python3
"""Build one Checkpoint D-4 bounded-analysis-input review artifact."""

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

from soc_agent.contracts import SensitiveEvidenceMode  # noqa: E402
from soc_agent.core.runtime import inspect_alert_normalization  # noqa: E402
from soc_agent.pipeline.analysis_context import build_llm_analysis_request  # noqa: E402
from soc_agent.pipeline.fact_reconstructor import reconstruct_facts  # noqa: E402
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (  # noqa: E402
    canonical_sha256,
    sha256_file,
    write_json_atomic,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_entity_extraction_review import (  # noqa: E402
    normalization_semantic_projection,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.bounded_analysis_input_review.v1"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_CHECKPOINT_D_ROOT = (
    ROOT / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
)
DEFAULT_ALERT_ID = 1965449
_ALLOWED_D1_STATUSES = {"passed", "passed_with_parser_warnings"}
_ALLOWED_D2_STATUSES = {"passed", "passed_with_extraction_warnings"}
_ALLOWED_D3_STATUSES = {
    "passed",
    "passed_with_fact_warnings",
    "passed_with_fact_conflicts",
    "passed_with_fact_warnings_and_conflicts",
}


def build_bounded_analysis_input_review(
    corpus: pd.DataFrame,
    *,
    alert_id: int,
    corpus_path: Path,
    corpus_file_sha256: str,
    normalization_review: Mapping[str, Any],
    entity_review: Mapping[str, Any],
    fact_review: Mapping[str, Any],
    sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.FULL,
) -> dict[str, Any]:
    """Replay D1-D3 and build the production bounded analysis request only."""

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

    d1_acceptance = _required_mapping(normalization_review, "acceptance", "D-1")
    d1_normalization = _required_mapping(
        normalization_review,
        "normalization",
        "D-1",
    )
    d1_normalized_alert = _required_mapping(
        d1_normalization,
        "normalized_alert",
        "D-1",
    )
    d2_acceptance = _required_mapping(entity_review, "acceptance", "D-2")
    d2_extraction = _required_mapping(
        entity_review,
        "entity_extraction",
        "D-2",
    )
    d2_entities = _required_mapping(d2_extraction, "entities", "D-2")
    d3_acceptance = _required_mapping(fact_review, "acceptance", "D-3")
    d3_facts = _required_mapping(
        fact_review,
        "fact_reconstruction",
        "D-3",
    )

    input_hash_before = canonical_sha256(alert_data)
    inspection = inspect_alert_normalization(alert_data)
    fact_reconstruction = reconstruct_facts(inspection.alert)
    request = build_llm_analysis_request(
        inspection.alert,
        inspection.entities,
        fact_reconstruction,
        sensitive_evidence_mode=sensitive_evidence_mode,
    )
    input_hash_after = canonical_sha256(alert_data)

    replayed_normalized_alert = inspection.alert.model_dump(
        mode="json",
        exclude_none=True,
    )
    replayed_entities = inspection.entities.model_dump(mode="json", exclude_none=True)
    replayed_facts = fact_reconstruction.model_dump(mode="json", exclude_none=True)
    analysis_request = request.model_dump(mode="json", exclude_none=True)
    d1_semantics = normalization_semantic_projection(d1_normalized_alert)
    replayed_semantics = normalization_semantic_projection(replayed_normalized_alert)

    coverage = _required_mapping(
        analysis_request,
        "evidence_coverage",
        "LLMAnalysisRequest",
    )
    primary = analysis_request.get("primary_evidence")
    primary = primary if isinstance(primary, Mapping) else None
    supplementary = _mapping_list(analysis_request.get("supplementary_evidence"))
    bounded_items = [item for item in [primary, *supplementary] if item is not None]
    highlights = _mapping_list(analysis_request.get("evidence_highlights"))
    evidence_policy = _required_mapping(
        replayed_facts,
        "evidence_policy",
        "FactReconstructionResult",
    )

    expected_projected_paths = {
        str(path)
        for item in bounded_items
        for path in _string_list(item.get("projected_field_paths"))
    }
    expected_highlighted_paths = {
        str(path)
        for item in highlights
        for path in _string_list(item.get("evidence_paths"))
    }
    expected_projected_paths.update(expected_highlighted_paths)
    expected_sanitized_paths = {
        str(path)
        for item in bounded_items
        for path in _string_list(item.get("sanitized_field_paths"))
    }
    expected_compacted_paths = {
        str(omission.get("field_path"))
        for item in bounded_items
        for omission in _mapping_list(item.get("encoded_span_omissions"))
        if omission.get("field_path")
    }
    expected_truncated_paths = {
        str(item.get("source_path"))
        for item in bounded_items
        if item.get("truncated") is True and item.get("source_path")
    }
    coverage_projected_paths = set(_string_list(coverage.get("llm_projected_paths")))
    coverage_sanitized_paths = set(_string_list(coverage.get("llm_sanitized_paths")))
    coverage_compacted_paths = set(
        _string_list(coverage.get("llm_compacted_encoded_paths"))
    )
    coverage_truncated_paths = set(
        _string_list(coverage.get("llm_truncated_evidence_paths"))
    )
    omissions = _mapping_list(coverage.get("omissions"))
    omitted_paths = {
        str(item.get("field_path")) for item in omissions if item.get("field_path")
    }
    accounted_paths = (
        coverage_projected_paths | coverage_sanitized_paths | omitted_paths
    )
    parsed_paths = set(_string_list(coverage.get("parsed_field_paths")))
    decoded_paths = set(_string_list(coverage.get("decoded_field_paths")))
    repaired_paths = set(_string_list(coverage.get("repaired_field_paths")))
    selected_path = replayed_facts.get("selected_input_path")
    expected_evidence_paths = {
        str(path)
        for path in [
            evidence_policy.get("selected_input_path"),
            *_string_list(evidence_policy.get("supplementary_input_paths")),
        ]
        if path
    }
    actual_evidence_paths = {
        str(item.get("source_path"))
        for item in bounded_items
        if item.get("source_path")
    }
    requested_mode = sensitive_evidence_mode.value

    checks = {
        "d1_acceptance_allows_continuation": d1_acceptance.get("status")
        in _ALLOWED_D1_STATUSES,
        "d2_acceptance_allows_continuation": d2_acceptance.get("status")
        in _ALLOWED_D2_STATUSES,
        "d3_acceptance_allows_continuation": d3_acceptance.get("status")
        in _ALLOWED_D3_STATUSES,
        "d1_alert_id_matches": str(
            _mapping_path(normalization_review, "input", "alert_id")
        )
        == str(alert_id),
        "d2_alert_id_matches": str(_mapping_path(entity_review, "input", "alert_id"))
        == str(alert_id),
        "d3_alert_id_matches": str(_mapping_path(fact_review, "input", "alert_id"))
        == str(alert_id),
        "replayed_normalized_alert_semantics_match_d1": canonical_sha256(
            replayed_semantics
        )
        == canonical_sha256(d1_semantics),
        "replayed_entities_match_d2": canonical_sha256(replayed_entities)
        == canonical_sha256(d2_entities),
        "replayed_facts_match_d3": canonical_sha256(replayed_facts)
        == canonical_sha256(d3_facts),
        "input_payload_unchanged": input_hash_before == input_hash_after,
        "normalized_raw_preserved_exactly": inspection.alert.raw == alert_data,
        "analysis_request_excludes_raw_payload": "raw" not in analysis_request,
        "primary_evidence_matches_selected_input": primary is not None
        and primary.get("source_path") == selected_path
        and analysis_request.get("primary_evidence_path") == selected_path,
        "bounded_evidence_uses_requested_sensitive_mode": all(
            item.get("sensitive_evidence_mode") == requested_mode
            for item in [*bounded_items, *highlights]
        ),
        "skill_resolution_not_run": _mapping_path(
            analysis_request,
            "skill_context",
            "selected_skills",
        )
        == []
        and _mapping_path(
            analysis_request,
            "skill_context",
            "total_token_budget",
        )
        == 0,
        "coverage_projected_paths_match_bounded_evidence": coverage_projected_paths
        == expected_projected_paths,
        "coverage_includes_all_bounded_sanitized_paths": expected_sanitized_paths
        <= coverage_sanitized_paths,
        "coverage_compaction_matches_bounded_evidence": coverage_compacted_paths
        == expected_compacted_paths,
        "coverage_truncation_matches_bounded_evidence": coverage_truncated_paths
        == expected_truncated_paths,
        "coverage_accounts_for_all_parsed_paths": parsed_paths <= accounted_paths,
        "coverage_accounts_for_all_decoded_paths": decoded_paths <= accounted_paths,
        "coverage_accounts_for_all_repaired_paths": repaired_paths <= accounted_paths,
        "coverage_omissions_have_reasons": all(
            item.get("field_path") and item.get("reason") for item in omissions
        ),
        "raw_message_policy_does_not_project_structured_fallback": (
            evidence_policy.get("name") != "raw_message_first"
            or (
                actual_evidence_paths == expected_evidence_paths
                and not _string_list(coverage.get("structured_field_paths"))
                and all(item.get("layer") == "raw_message" for item in bounded_items)
            )
        ),
        "selected_message_has_schema_observation": any(
            item.get("source_path") == selected_path
            for item in _mapping_list(coverage.get("message_schemas"))
        ),
        "coverage_counts_match_lists": _coverage_counts_match(
            coverage,
            expected_highlighted_count=len(expected_highlighted_paths),
        ),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    coverage_warnings = _string_list(coverage.get("warnings"))
    high_value_gaps = _mapping_list(coverage.get("high_value_gaps"))
    if failed_checks:
        status = "failed"
    elif coverage_warnings or high_value_gaps or omissions:
        status = "passed_with_coverage_findings"
    else:
        status = "passed"

    omission_reason_counts = Counter(
        str(item.get("reason")) for item in omissions if item.get("reason") is not None
    )
    message_schema_status_counts = Counter(
        str(item.get("status"))
        for item in _mapping_list(coverage.get("message_schemas"))
        if item.get("status") is not None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "normalization_entity_and_fact_replay_for_chain_integrity",
                "bounded_analysis_input_building",
                "sensitive_evidence_projection",
                "encoded_context_compaction",
                "evidence_coverage_reporting",
            ],
            "not_performed": [
                "skill_resolution",
                "prompt_rendering",
                "analyzer_or_llm",
                "evidence_grounding",
                "decision_policy",
                "persistence",
            ],
        },
        "input": {
            "corpus_path": _relative_path(corpus_path),
            "corpus_sha256": corpus_file_sha256,
            "alert_id": alert_id,
            "topic": _optional_string(row.get("topic")) or "unknown",
            "sensitive_evidence_mode": requested_mode,
            "d1_schema_version": normalization_review.get("schema_version"),
            "d1_status": d1_acceptance.get("status"),
            "d2_schema_version": entity_review.get("schema_version"),
            "d2_status": d2_acceptance.get("status"),
            "d3_schema_version": fact_review.get("schema_version"),
            "d3_status": d3_acceptance.get("status"),
            "d1_normalized_semantic_sha256": canonical_sha256(d1_semantics),
            "replayed_normalized_semantic_sha256": canonical_sha256(replayed_semantics),
            "d2_entities_sha256": canonical_sha256(d2_entities),
            "replayed_entities_sha256": canonical_sha256(replayed_entities),
            "d3_facts_sha256": canonical_sha256(d3_facts),
            "replayed_facts_sha256": canonical_sha256(replayed_facts),
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "coverage_warning_count": len(coverage_warnings),
            "high_value_gap_count": len(high_value_gaps),
            "omission_count": len(omissions),
        },
        "analysis_input_summary": {
            "request_schema_version": analysis_request.get("schema_version"),
            "primary_evidence_path": analysis_request.get("primary_evidence_path"),
            "primary_layer": primary.get("layer") if primary else None,
            "primary_source_trust": primary.get("trust_level") if primary else None,
            "primary_original_length": (
                primary.get("original_length") if primary else None
            ),
            "primary_content_length": (
                len(str(primary.get("content", ""))) if primary else 0
            ),
            "primary_truncated": primary.get("truncated") if primary else None,
            "supplementary_evidence_count": len(supplementary),
            "highlight_count": len(highlights),
            "message_schema_status_counts": dict(
                sorted(message_schema_status_counts.items())
            ),
            "coverage_counts": coverage.get("counts", {}),
            "omission_reason_counts": dict(sorted(omission_reason_counts.items())),
            "coverage_warnings": coverage_warnings,
        },
        "llm_analysis_request": analysis_request,
    }


def _coverage_counts_match(
    coverage: Mapping[str, Any],
    *,
    expected_highlighted_count: int,
) -> bool:
    counts = coverage.get("counts")
    if not isinstance(counts, Mapping):
        return False
    expected = {
        "message_schema_count": len(_mapping_list(coverage.get("message_schemas"))),
        "structured_field_count": len(
            _string_list(coverage.get("structured_field_paths"))
        ),
        "parsed_field_count": len(_string_list(coverage.get("parsed_field_paths"))),
        "decoded_field_count": len(_string_list(coverage.get("decoded_field_paths"))),
        "repaired_field_count": len(_string_list(coverage.get("repaired_field_paths"))),
        "canonical_source_count": len(
            _string_list(coverage.get("canonical_source_paths"))
        ),
        "fact_source_count": len(_string_list(coverage.get("fact_source_paths"))),
        "scenario_source_count": len(
            _string_list(coverage.get("scenario_source_paths"))
        ),
        "llm_projected_count": len(_string_list(coverage.get("llm_projected_paths"))),
        "llm_highlighted_count": expected_highlighted_count,
        "llm_sanitized_count": len(_string_list(coverage.get("llm_sanitized_paths"))),
        "llm_compacted_encoded_count": len(
            _string_list(coverage.get("llm_compacted_encoded_paths"))
        ),
        "omission_count": len(_mapping_list(coverage.get("omissions"))),
        "high_value_gap_count": len(_mapping_list(coverage.get("high_value_gaps"))),
    }
    for key, value in expected.items():
        if counts.get(key) != value:
            return False
    return True


def _required_mapping(
    value: Mapping[str, Any],
    key: str,
    artifact_name: str,
) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"{artifact_name} artifact is missing {key}")
    return item


def _mapping_path(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


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
    parser.add_argument("--normalization-review", type=Path, default=None)
    parser.add_argument("--entity-review", type=Path, default=None)
    parser.add_argument("--fact-review", type=Path, default=None)
    parser.add_argument(
        "--sensitive-evidence-mode",
        choices=[item.value for item in SensitiveEvidenceMode],
        default=SensitiveEvidenceMode.FULL.value,
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
    entity_review_path = args.entity_review or (
        DEFAULT_CHECKPOINT_D_ROOT
        / "step-d2-generic-entity-extraction"
        / f"{args.alert_id}.entities.json"
    )
    fact_review_path = args.fact_review or (
        DEFAULT_CHECKPOINT_D_ROOT
        / "step-d3-fact-reconstruction"
        / f"{args.alert_id}.facts.json"
    )
    output_dir = args.output_dir or (
        DEFAULT_CHECKPOINT_D_ROOT / "step-d4-bounded-analysis-input"
    )
    corpus = load_dataframe_pickle(args.corpus)
    normalization_review = json.loads(
        normalization_review_path.read_text(encoding="utf-8")
    )
    entity_review = json.loads(entity_review_path.read_text(encoding="utf-8"))
    fact_review = json.loads(fact_review_path.read_text(encoding="utf-8"))
    review = build_bounded_analysis_input_review(
        corpus,
        alert_id=args.alert_id,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
        normalization_review=normalization_review,
        entity_review=entity_review,
        fact_review=fact_review,
        sensitive_evidence_mode=SensitiveEvidenceMode(args.sensitive_evidence_mode),
    )
    output_path = output_dir / f"{args.alert_id}.analysis-input.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "status": review["acceptance"]["status"],
                "failed_checks": review["acceptance"]["failed_checks"],
                **review["analysis_input_summary"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
