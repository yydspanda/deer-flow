#!/usr/bin/env python3
"""Build deterministic Checkpoint D-8 evidence-grounding review artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (  # noqa: E402
    canonical_sha256,
    write_json_atomic,
)

from soc_agent.contracts import (  # noqa: E402
    AnalysisEvidenceGroundingStatus,
    AnalysisResult,
    LLMAnalysisRequest,
)
from soc_agent.pipeline.evidence_grounding import (  # noqa: E402
    ground_analysis_evidence,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.evidence_grounding_review.v1"
DEFAULT_CHECKPOINT_D_ROOT = (
    ROOT / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
)
DEFAULT_ALERT_ID = 1965449
_ALLOWED_D7_STATUSES = {"passed"}


def build_evidence_grounding_review(
    skill_context_review: Mapping[str, Any],
    analyzer_output_review: Mapping[str, Any],
    *,
    alert_id: int,
) -> dict[str, Any]:
    """Ground one D7 result without running Decision or another model call."""

    d5_request_payload = _required_mapping(
        skill_context_review,
        "llm_analysis_request",
        "D-5",
    )
    d7_acceptance = _required_mapping(analyzer_output_review, "acceptance", "D-7")
    d7_input = _required_mapping(analyzer_output_review, "input", "D-7")
    d7_analysis_payload = _required_mapping(
        analyzer_output_review,
        "analysis_result",
        "D-7",
    )

    request = LLMAnalysisRequest.model_validate(d5_request_payload)
    analysis = AnalysisResult.model_validate(d7_analysis_payload)
    request_hash = canonical_sha256(request.model_dump(mode="json", exclude_none=True))
    analysis_hash_before = canonical_sha256(
        analysis.model_dump(mode="json", exclude_none=True)
    )
    grounding = ground_analysis_evidence(analysis, request)
    analysis_hash_after = canonical_sha256(
        analysis.model_dump(mode="json", exclude_none=True)
    )

    checks = {
        "d7_acceptance_allows_continuation": (
            d7_acceptance.get("status") in _ALLOWED_D7_STATUSES
        ),
        "d5_alert_id_matches": (
            str(_mapping_path(skill_context_review, "input", "alert_id"))
            == str(alert_id)
        ),
        "d7_alert_id_matches": (
            str(_mapping_path(analyzer_output_review, "input", "alert_id"))
            == str(alert_id)
        ),
        "d7_links_exact_d5_request": (
            d7_input.get("d5_request_sha256") == request_hash
        ),
        "d7_analysis_hash_matches": (
            analyzer_output_review.get("analysis_result_sha256") == analysis_hash_before
        ),
        "grounding_did_not_mutate_analysis": (
            analysis_hash_before == analysis_hash_after
        ),
        "grounding_covers_every_evidence_item": (
            grounding.total_count == len(analysis.evidence)
            and len(grounding.items) == len(analysis.evidence)
        ),
        "grounding_counts_are_consistent": (
            grounding.grounded_count + grounding.ungrounded_count
            == grounding.total_count
        ),
        "grounding_covers_every_reasoning_item": (
            grounding.reasoning_total_count == len(analysis.reasoning)
            and len(grounding.reasoning_items) == len(analysis.reasoning)
        ),
        "reasoning_counts_are_consistent": (
            grounding.reasoning_grounded_count + grounding.reasoning_ungrounded_count
            == grounding.reasoning_total_count
        ),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    execution_status = "failed" if failed_checks else "passed"

    blocking_reasons = []
    if grounding.ungrounded_count:
        blocking_reasons.append("ungrounded_analysis_evidence")
    if grounding.reasoning_ungrounded_count:
        blocking_reasons.append("ungrounded_analysis_reasoning")
    if any("outcome-success claim" in warning for warning in grounding.warnings):
        blocking_reasons.append("unproven_outcome_claim")

    grounding_by_ref = {item.evidence_ref: item for item in grounding.items}
    reasoning_by_id = {item.reasoning_id: item for item in grounding.reasoning_items}
    scenario_support = []
    for scenario in analysis.scenario_assessments:
        referenced_items = [grounding_by_ref[ref] for ref in scenario.evidence_refs]
        referenced_reasoning = [reasoning_by_id[ref] for ref in scenario.reasoning_refs]
        rejected_refs = [
            item.evidence_ref
            for item in referenced_items
            if item.status is not AnalysisEvidenceGroundingStatus.GROUNDED
        ]
        rejected_reasoning_refs = [
            item.reasoning_id
            for item in referenced_reasoning
            if item.status is not AnalysisEvidenceGroundingStatus.GROUNDED
        ]
        scenario_support.append(
            {
                "scenario_name": scenario.scenario_name,
                "scenario_key": scenario.scenario_key,
                "is_primary": scenario.is_primary,
                "activity_stage": scenario.activity_stage.value,
                "evidence_refs": scenario.evidence_refs,
                "reasoning_refs": scenario.reasoning_refs,
                "grounded_evidence_refs": [
                    item.evidence_ref
                    for item in referenced_items
                    if item.status is AnalysisEvidenceGroundingStatus.GROUNDED
                ],
                "rejected_evidence_refs": rejected_refs,
                "grounded_reasoning_refs": [
                    item.reasoning_id
                    for item in referenced_reasoning
                    if item.status is AnalysisEvidenceGroundingStatus.GROUNDED
                ],
                "rejected_reasoning_refs": rejected_reasoning_refs,
                "all_references_grounded": not rejected_refs
                and not rejected_reasoning_refs,
            }
        )

    evidence_review = []
    for evidence_index, evidence in enumerate(analysis.evidence):
        grounding_item = grounding_by_ref[evidence.evidence_ref]
        evidence_review.append(
            {
                "evidence_index": evidence_index,
                "evidence": evidence.model_dump(mode="json"),
                "grounding": grounding_item.model_dump(mode="json"),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "d5_and_d7_lineage_validation",
                "production_evidence_grounding",
                "exact_evidence_catalog_reference_validation",
                "reasoning_reference_integrity_validation",
                "scenario_fact_and_reasoning_support_projection",
            ],
            "not_performed": [
                "llm_call",
                "decision_policy",
                "correlation_or_memory_retrieval",
                "tool_or_mcp_invocation",
                "persistence",
                "review_queue_or_action",
            ],
        },
        "input": {
            "alert_id": alert_id,
            "topic": _mapping_path(skill_context_review, "input", "topic"),
            "d5_request_sha256": request_hash,
            "d7_analysis_result_sha256": analysis_hash_before,
            "d7_model_name": _mapping_path(
                analyzer_output_review,
                "analyzer",
                "model_name",
            ),
            "d7_prompt_version": _mapping_path(
                analyzer_output_review,
                "analyzer",
                "prompt_version",
            ),
            "d7_parser_version": _mapping_path(
                analyzer_output_review,
                "analyzer",
                "parser_version",
            ),
        },
        "acceptance": {
            "status": execution_status,
            "failed_checks": failed_checks,
            "checks": checks,
        },
        "quality_gate": {
            "status": "blocked" if blocking_reasons else "ready",
            "fully_grounded": not blocking_reasons,
            "decision_policy_may_consume_report": execution_status == "passed",
            "requires_degraded_human_review": bool(blocking_reasons),
            "automation_allowed": False,
            "blocking_reasons": blocking_reasons,
        },
        "grounding_report": grounding.model_dump(mode="json"),
        "scenario_support_review": scenario_support,
        "evidence_review": evidence_review,
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


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert-id", type=int, default=DEFAULT_ALERT_ID)
    parser.add_argument("--skill-context-review", type=Path, default=None)
    parser.add_argument("--analyzer-output-review", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    skill_context_review_path = args.skill_context_review or (
        DEFAULT_CHECKPOINT_D_ROOT
        / "step-d5-skill-context"
        / f"{args.alert_id}.skill-context.json"
    )
    analyzer_output_review_path = args.analyzer_output_review or (
        DEFAULT_CHECKPOINT_D_ROOT
        / "step-d7-analyzer-output"
        / f"{args.alert_id}.analyzer-output.json"
    )
    output_dir = args.output_dir or (
        DEFAULT_CHECKPOINT_D_ROOT / "step-d8-evidence-grounding"
    )
    skill_context_review = json.loads(
        skill_context_review_path.read_text(encoding="utf-8")
    )
    analyzer_output_review = json.loads(
        analyzer_output_review_path.read_text(encoding="utf-8")
    )
    review = build_evidence_grounding_review(
        skill_context_review,
        analyzer_output_review,
        alert_id=args.alert_id,
    )
    output_path = output_dir / f"{args.alert_id}.grounding.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "execution_status": review["acceptance"]["status"],
                "quality_status": review["quality_gate"]["status"],
                "grounded_count": review["grounding_report"]["grounded_count"],
                "ungrounded_count": review["grounding_report"]["ungrounded_count"],
                "description_leakage_count": review["grounding_report"][
                    "description_leakage_count"
                ],
                "blocking_reasons": review["quality_gate"]["blocking_reasons"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
