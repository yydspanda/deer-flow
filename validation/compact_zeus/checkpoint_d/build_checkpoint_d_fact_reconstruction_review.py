#!/usr/bin/env python3
"""Build one Checkpoint D-3 fact-reconstruction review artifact."""

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

SCHEMA_VERSION = "soc.validation.checkpoint_d.fact_reconstruction_review.v2"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_CHECKPOINT_D_ROOT = (
    ROOT / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
)
DEFAULT_ALERT_ID = 1965449
_ALLOWED_PREVIOUS_STATUSES = {
    "passed",
    "passed_with_parser_warnings",
    "passed_with_extraction_warnings",
}
_EXPECTED_ROLES = {"source", "destination", "attacker", "victim", "impacted_asset"}


def build_fact_reconstruction_review(
    corpus: pd.DataFrame,
    *,
    alert_id: int,
    corpus_path: Path,
    corpus_file_sha256: str,
    normalization_review: Mapping[str, Any],
    entity_review: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay the production pre-analysis chain through fact reconstruction."""

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

    input_hash_before = canonical_sha256(alert_data)
    inspection = inspect_alert_normalization(alert_data)
    fact_reconstruction = reconstruct_facts(inspection.alert)
    input_hash_after = canonical_sha256(alert_data)

    replayed_normalized_alert = inspection.alert.model_dump(
        mode="json",
        exclude_none=True,
    )
    replayed_entities = inspection.entities.model_dump(
        mode="json",
        exclude_none=True,
    )
    facts = fact_reconstruction.model_dump(mode="json", exclude_none=True)
    d1_semantics = normalization_semantic_projection(d1_normalized_alert)
    replayed_semantics = normalization_semantic_projection(replayed_normalized_alert)

    field_trusts = _mapping_list(facts.get("field_trusts"))
    role_claims = _mapping_list(facts.get("role_claims"))
    role_resolutions = _mapping_list(facts.get("role_resolutions"))
    scenario_hypotheses = _mapping_list(facts.get("scenario_hypotheses"))
    conflict_reports = _mapping_list(facts.get("conflict_reports"))
    canonical_provenance = _mapping_list(facts.get("canonical_field_provenance"))
    warnings = _string_list(facts.get("warnings"))
    evidence_policy = facts.get("evidence_policy")
    if not isinstance(evidence_policy, Mapping):
        evidence_policy = {}

    resolution_roles = {
        str(item.get("role")) for item in role_resolutions if item.get("role")
    }
    claim_ids = [str(item.get("claim_id")) for item in role_claims]
    selected_input_path = facts.get("selected_input_path")
    selected_input_trusts = [
        item for item in field_trusts if item.get("field_path") == selected_input_path
    ]
    fallback_input_path = evidence_policy.get("fallback_input_path")
    inactive_fallback_trusts = [
        item
        for item in field_trusts
        if fallback_input_path
        and fallback_input_path != selected_input_path
        and item.get("field_path") == fallback_input_path
    ]
    duplicate_projection_trusts = [
        item
        for item in field_trusts
        if item.get("reasoning_status") == "excluded_duplicate_projection"
    ]
    provenance_trust_by_path = {
        str(item.get("canonical_path")): item.get("trust_level")
        for item in canonical_provenance
        if item.get("canonical_path")
    }
    checks = {
        "d1_acceptance_allows_continuation": d1_acceptance.get("status")
        in _ALLOWED_PREVIOUS_STATUSES,
        "d2_acceptance_allows_continuation": d2_acceptance.get("status")
        in _ALLOWED_PREVIOUS_STATUSES,
        "d1_alert_id_matches": str(
            _mapping_path(normalization_review, "input", "alert_id")
        )
        == str(alert_id),
        "d2_alert_id_matches": str(_mapping_path(entity_review, "input", "alert_id"))
        == str(alert_id),
        "replayed_normalized_alert_semantics_match_d1": canonical_sha256(
            replayed_semantics
        )
        == canonical_sha256(d1_semantics),
        "replayed_entities_match_d2": canonical_sha256(replayed_entities)
        == canonical_sha256(d2_entities),
        "input_payload_unchanged": input_hash_before == input_hash_after,
        "normalized_raw_preserved_exactly": inspection.alert.raw == alert_data,
        "selected_input_is_available": facts.get("selected_input_available") is True,
        "selected_input_matches_evidence_policy": selected_input_path
        == evidence_policy.get("selected_input_path"),
        "selected_input_is_selected_evidence": any(
            item.get("participates") is True
            and item.get("reasoning_status") == "selected_evidence"
            for item in selected_input_trusts
        ),
        "unselected_fallback_is_audit_only": (
            not fallback_input_path
            or fallback_input_path == selected_input_path
            or (
                len(inactive_fallback_trusts) == 1
                and inactive_fallback_trusts[0].get("participates") is False
                and inactive_fallback_trusts[0].get("source_trust") == "unknown"
                and inactive_fallback_trusts[0].get("reasoning_status")
                == "excluded_unselected_fallback"
            )
        ),
        "duplicate_projections_preserve_source_trust": all(
            item.get("participates") is False
            and item.get("source_trust")
            == provenance_trust_by_path.get(str(item.get("field_path")))
            for item in duplicate_projection_trusts
        ),
        "all_role_claim_ids_are_unique": len(claim_ids) == len(set(claim_ids)),
        "all_role_resolutions_are_present": resolution_roles == _EXPECTED_ROLES,
        "role_resolutions_block_automation": all(
            item.get("automation_allowed") is False for item in role_resolutions
        ),
        "conflicts_block_automation": all(
            item.get("blocks_automation") is True for item in conflict_reports
        ),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    if failed_checks:
        status = "failed"
    elif warnings and conflict_reports:
        status = "passed_with_fact_warnings_and_conflicts"
    elif warnings:
        status = "passed_with_fact_warnings"
    elif conflict_reports:
        status = "passed_with_fact_conflicts"
    else:
        status = "passed"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "normalization_and_entity_replay_for_chain_integrity",
                "evidence_policy_resolution",
                "field_trust_projection",
                "role_claim_and_resolution",
                "scenario_hypothesis_detection",
                "conflict_reporting",
                "canonical_provenance_merge",
            ],
            "not_performed": [
                "analysis_input_building",
                "skill_resolution",
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
            "d1_schema_version": normalization_review.get("schema_version"),
            "d1_status": d1_acceptance.get("status"),
            "d2_schema_version": entity_review.get("schema_version"),
            "d2_status": d2_acceptance.get("status"),
            "d1_normalized_semantic_sha256": canonical_sha256(d1_semantics),
            "replayed_normalized_semantic_sha256": canonical_sha256(replayed_semantics),
            "d2_entities_sha256": canonical_sha256(d2_entities),
            "replayed_entities_sha256": canonical_sha256(replayed_entities),
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "fact_warning_count": len(warnings),
            "conflict_count": len(conflict_reports),
        },
        "fact_summary": {
            "selected_input_path": selected_input_path,
            "selected_layer": evidence_policy.get("selected_layer"),
            "selected_source_trust": evidence_policy.get("trust_level"),
            "field_trust_count": len(field_trusts),
            "participating_field_trust_count": sum(
                item.get("participates") is True for item in field_trusts
            ),
            "field_reasoning_status_counts": _counter(
                field_trusts,
                "reasoning_status",
            ),
            "role_claim_count": len(role_claims),
            "role_claim_type_counts": _counter(role_claims, "claim_type"),
            "role_claim_counts": _counter(role_claims, "role"),
            "role_resolution_status_counts": _counter(
                role_resolutions,
                "status",
            ),
            "unresolved_roles": sorted(
                str(item.get("role"))
                for item in role_resolutions
                if item.get("status") == "unresolved"
            ),
            "scenario_types": sorted(
                str(item.get("scenario_type"))
                for item in scenario_hypotheses
                if item.get("scenario_type")
            ),
            "conflict_types": sorted(
                str(item.get("conflict_type"))
                for item in conflict_reports
                if item.get("conflict_type")
            ),
            "canonical_provenance_count": len(canonical_provenance),
        },
        "fact_reconstruction": facts,
    }


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


def _counter(values: list[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(str(item.get(key)) for item in values if item.get(key) is not None)
    return dict(sorted(counts.items()))


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
    output_dir = args.output_dir or (
        DEFAULT_CHECKPOINT_D_ROOT / "step-d3-fact-reconstruction"
    )
    corpus = load_dataframe_pickle(args.corpus)
    normalization_review = json.loads(
        normalization_review_path.read_text(encoding="utf-8")
    )
    entity_review = json.loads(entity_review_path.read_text(encoding="utf-8"))
    review = build_fact_reconstruction_review(
        corpus,
        alert_id=args.alert_id,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
        normalization_review=normalization_review,
        entity_review=entity_review,
    )
    output_path = output_dir / f"{args.alert_id}.facts.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "status": review["acceptance"]["status"],
                "failed_checks": review["acceptance"]["failed_checks"],
                **review["fact_summary"],
                "warnings": review["fact_reconstruction"].get("warnings", []),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
