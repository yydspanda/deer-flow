#!/usr/bin/env python3
"""Build deterministic Checkpoint D-9 Decision Policy review artifacts."""

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
    AnalysisEvidenceGroundingReport,
    AnalysisResult,
    DecisionEvidenceState,
    DecisionReviewReason,
    LLMAnalysisRequest,
)
from soc_agent.core.decision_policy import SocDecisionPolicy  # noqa: E402

SCHEMA_VERSION = "soc.validation.checkpoint_d.decision_policy_review.v1"
DEFAULT_CHECKPOINT_D_ROOT = (
    ROOT / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
)
DEFAULT_ALERT_ID = 1965449
_ALLOWED_D7_STATUSES = {"passed"}
_ALLOWED_D8_STATUSES = {"passed"}


def build_decision_policy_review(
    skill_context_review: Mapping[str, Any],
    analyzer_output_review: Mapping[str, Any],
    evidence_grounding_review: Mapping[str, Any],
    *,
    alert_id: int,
) -> dict[str, Any]:
    """Apply the production Decision Policy to persisted D5/D7/D8 boundaries."""

    d5_request_payload = _required_mapping(
        skill_context_review,
        "llm_analysis_request",
        "D-5",
    )
    d7_acceptance = _required_mapping(analyzer_output_review, "acceptance", "D-7")
    d7_input = _required_mapping(analyzer_output_review, "input", "D-7")
    d7_analyzer = _required_mapping(analyzer_output_review, "analyzer", "D-7")
    d7_analysis_payload = _required_mapping(
        analyzer_output_review,
        "analysis_result",
        "D-7",
    )
    d8_acceptance = _required_mapping(
        evidence_grounding_review,
        "acceptance",
        "D-8",
    )
    d8_quality = _required_mapping(
        evidence_grounding_review,
        "quality_gate",
        "D-8",
    )
    d8_input = _required_mapping(evidence_grounding_review, "input", "D-8")
    d8_grounding_payload = _required_mapping(
        evidence_grounding_review,
        "grounding_report",
        "D-8",
    )

    request = LLMAnalysisRequest.model_validate(d5_request_payload)
    analysis = AnalysisResult.model_validate(d7_analysis_payload)
    grounding = AnalysisEvidenceGroundingReport.model_validate(d8_grounding_payload)

    request_hash_before = canonical_sha256(
        request.model_dump(mode="json", exclude_none=True)
    )
    analysis_hash_before = canonical_sha256(
        analysis.model_dump(mode="json", exclude_none=True)
    )
    grounding_hash_before = canonical_sha256(
        grounding.model_dump(mode="json", exclude_none=True)
    )

    decision = SocDecisionPolicy().decide(
        analysis,
        request=request,
        grounding=grounding,
        analyzer_step_name=str(d7_analyzer.get("step_name") or ""),
    )

    request_hash_after = canonical_sha256(
        request.model_dump(mode="json", exclude_none=True)
    )
    analysis_hash_after = canonical_sha256(
        analysis.model_dump(mode="json", exclude_none=True)
    )
    grounding_hash_after = canonical_sha256(
        grounding.model_dump(mode="json", exclude_none=True)
    )

    d8_is_blocked = d8_quality.get("status") == "blocked"
    guarded_evidence_states = {
        DecisionEvidenceState.DEGRADED,
        DecisionEvidenceState.CONFLICTED,
    }
    checks = {
        "d7_acceptance_allows_continuation": (
            d7_acceptance.get("status") in _ALLOWED_D7_STATUSES
        ),
        "d8_acceptance_allows_continuation": (
            d8_acceptance.get("status") in _ALLOWED_D8_STATUSES
            and d8_quality.get("decision_policy_may_consume_report") is True
        ),
        "d5_alert_id_matches": (
            str(_mapping_path(skill_context_review, "input", "alert_id"))
            == str(alert_id)
        ),
        "d7_alert_id_matches": (
            str(_mapping_path(analyzer_output_review, "input", "alert_id"))
            == str(alert_id)
        ),
        "d8_alert_id_matches": (
            str(_mapping_path(evidence_grounding_review, "input", "alert_id"))
            == str(alert_id)
        ),
        "d7_links_exact_d5_request": (
            d7_input.get("d5_request_sha256") == request_hash_before
        ),
        "d8_links_exact_d5_request": (
            d8_input.get("d5_request_sha256") == request_hash_before
        ),
        "d8_links_exact_d7_analysis": (
            d8_input.get("d7_analysis_result_sha256") == analysis_hash_before
        ),
        "d7_analysis_hash_matches": (
            analyzer_output_review.get("analysis_result_sha256") == analysis_hash_before
        ),
        "decision_policy_did_not_mutate_inputs": (
            request_hash_before == request_hash_after
            and analysis_hash_before == analysis_hash_after
            and grounding_hash_before == grounding_hash_after
        ),
        "decision_preserves_analyzer_verdict": decision.verdict is analysis.verdict,
        "blocked_grounding_forces_human_review": (
            not d8_is_blocked or decision.needs_review
        ),
        "ungrounded_evidence_is_fail_closed": (
            not grounding.ungrounded_count
            or (
                decision.evidence_state in guarded_evidence_states
                and DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE
                in decision.review_reasons
            )
        ),
        "uncalibrated_confidence_requires_review": (
            not decision.confidence_is_calibrated
            and DecisionReviewReason.CONFIDENCE_NOT_CALIBRATED
            in decision.review_reasons
            and decision.needs_review
        ),
        "automation_remains_disabled": decision.automation_allowed is False,
        "decision_policy_version_recorded": bool(decision.policy_version),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    execution_status = "failed" if failed_checks else "passed"

    if execution_status == "failed":
        decision_gate_status = "failed"
    elif d8_is_blocked:
        decision_gate_status = "guarded_review_required"
    elif decision.needs_review:
        decision_gate_status = "review_required"
    else:
        decision_gate_status = "ready"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "d5_d7_d8_lineage_validation",
                "production_decision_policy",
                "evidence_state_guard_validation",
                "human_review_reason_validation",
                "automation_guard_validation",
            ],
            "not_performed": [
                "llm_call",
                "evidence_regrounding_or_repair",
                "tenant_disposition_policy",
                "correlation_or_memory_retrieval",
                "tool_or_mcp_invocation",
                "persistence",
                "review_queue_or_action",
            ],
        },
        "input": {
            "alert_id": alert_id,
            "topic": _mapping_path(skill_context_review, "input", "topic"),
            "d5_request_sha256": request_hash_before,
            "d7_analysis_result_sha256": analysis_hash_before,
            "d8_grounding_report_sha256": grounding_hash_before,
            "d7_analyzer_step_name": d7_analyzer.get("step_name"),
            "d7_model_name": d7_analyzer.get("model_name"),
        },
        "acceptance": {
            "status": execution_status,
            "failed_checks": failed_checks,
            "checks": checks,
        },
        "grounding_summary": {
            "quality_status": d8_quality.get("status"),
            "total_count": grounding.total_count,
            "grounded_count": grounding.grounded_count,
            "ungrounded_count": grounding.ungrounded_count,
            "description_leakage_count": grounding.description_leakage_count,
            "blocking_reasons": d8_quality.get("blocking_reasons", []),
        },
        "decision_gate": {
            "status": decision_gate_status,
            "detection_verdict_preserved": decision.verdict is analysis.verdict,
            "human_review_required": decision.needs_review,
            "automation_allowed": decision.automation_allowed,
            "tenant_disposition_evaluated": False,
        },
        "decision": decision.model_dump(mode="json", exclude_none=True),
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
    parser.add_argument("--evidence-grounding-review", type=Path, default=None)
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
    evidence_grounding_review_path = args.evidence_grounding_review or (
        DEFAULT_CHECKPOINT_D_ROOT
        / "step-d8-evidence-grounding"
        / f"{args.alert_id}.grounding.json"
    )
    output_dir = args.output_dir or (
        DEFAULT_CHECKPOINT_D_ROOT / "step-d9-decision-policy"
    )
    skill_context_review = json.loads(
        skill_context_review_path.read_text(encoding="utf-8")
    )
    analyzer_output_review = json.loads(
        analyzer_output_review_path.read_text(encoding="utf-8")
    )
    evidence_grounding_review = json.loads(
        evidence_grounding_review_path.read_text(encoding="utf-8")
    )
    review = build_decision_policy_review(
        skill_context_review,
        analyzer_output_review,
        evidence_grounding_review,
        alert_id=args.alert_id,
    )
    output_path = output_dir / f"{args.alert_id}.decision.json"
    write_json_atomic(review, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "alert_id": args.alert_id,
                "execution_status": review["acceptance"]["status"],
                "decision_gate_status": review["decision_gate"]["status"],
                "verdict": review["decision"]["verdict"],
                "evidence_state": review["decision"]["evidence_state"],
                "needs_review": review["decision"]["needs_review"],
                "review_reasons": review["decision"]["review_reasons"],
                "automation_allowed": review["decision"]["automation_allowed"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if review["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
