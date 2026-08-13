#!/usr/bin/env python3
"""Compare two fixed-cohort E2E reports without hiding LLM drift."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMPARISON_SCHEMA_VERSION = "soc.validation.e2e_ten_alert_comparison.v2"
SUPPORTED_REPORT_SCHEMAS = {
    "soc.validation.e2e_ten_alert_report.v1",
    "soc.validation.e2e_ten_alert_report.v2",
    "soc.validation.e2e_ten_alert_report.v3",
    "soc.validation.e2e_ten_alert_report.v4",
    "soc.validation.e2e_ten_alert_report.v5",
    "soc.validation.e2e_ten_alert_report.v6",
    "soc.validation.e2e_ten_alert_report.v7",
    "soc.validation.e2e_ten_alert_report.v8",
    "soc.validation.e2e_alert_cohort_report.v1",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"E2E report must be an object: {path}")
    if payload.get("schema_version") not in SUPPORTED_REPORT_SCHEMAS:
        raise ValueError(f"unsupported E2E report schema: {path}")
    return payload


def compare_reports(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    baseline_path: str,
    current_path: str,
) -> dict[str, Any]:
    if baseline.get("cohort_id") != current.get("cohort_id"):
        raise ValueError("E2E reports use different cohorts")
    baseline_input = _mapping(baseline.get("input"))
    current_input = _mapping(current.get("input"))
    if baseline_input.get("source_sha256") != current_input.get("source_sha256"):
        raise ValueError("E2E reports use different source corpus fingerprints")

    baseline_cases = _case_index(baseline)
    current_cases = _case_index(current)
    if set(baseline_cases) != set(current_cases):
        raise ValueError("E2E reports do not contain the same alert IDs")

    case_diffs = [
        _compare_case(baseline_cases[alert_id], current_cases[alert_id])
        for alert_id in baseline_cases
    ]
    current_summary = _mapping(current.get("summary"))
    baseline_summary = _mapping(baseline.get("summary"))
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "cohort_id": current.get("cohort_id"),
        "baseline": {
            "path": baseline_path,
            "schema_version": baseline.get("schema_version"),
            "generated_at": baseline.get("generated_at"),
            "acceptance_status": baseline.get("acceptance_status"),
            "quality_status": baseline.get("quality_status"),
        },
        "current": {
            "path": current_path,
            "schema_version": current.get("schema_version"),
            "generated_at": current.get("generated_at"),
            "acceptance_status": current.get("acceptance_status"),
            "quality_status": current.get("quality_status"),
        },
        "comparison_boundary": {
            "same_cohort": True,
            "same_source_corpus": True,
            "base_model_output_can_vary_between_live_calls": True,
            "base_verdict_delta_is_not_attributed_to_memory_or_automation": True,
            "effective_decision_delta_requires_append_only_transition_lineage": True,
            "tenant_policy_is_independent_from_runtime_detection_truth": True,
            "mocked_execution_is_not_real_provider_evidence": True,
        },
        "summary": {
            "case_count": len(case_diffs),
            "same_input_hash_count": sum(
                item["same_input_hash"] for item in case_diffs
            ),
            "base_verdict_changed_count": sum(
                item["base_verdict_changed"] for item in case_diffs
            ),
            "effective_verdict_changed_from_base_count": sum(
                item["effective_verdict_changed_from_base"] for item in case_diffs
            ),
            "grounded_evidence_delta": sum(
                item["grounding"]["grounded_delta"] for item in case_diffs
            ),
            "ungrounded_evidence_delta": sum(
                item["grounding"]["ungrounded_delta"] for item in case_diffs
            ),
            "quality_status_changed_count": sum(
                item["quality_status_changed"] for item in case_diffs
            ),
            "analysis_output_quality_changed_count": sum(
                item["analysis_output_quality_changed"] for item in case_diffs
            ),
            "analysis_output_quality_status_counts": current_summary.get(
                "analysis_output_quality_status_counts",
                {},
            ),
            "analysis_output_degraded_section_counts": current_summary.get(
                "analysis_output_degraded_section_counts",
                {},
            ),
            "decision_transition_count": current_summary.get(
                "decision_transition_count",
                0,
            ),
            "memory_contributor_count": current_summary.get(
                "memory_contributor_count",
                0,
            ),
            "tenant_policy_decision_count": current_summary.get(
                "tenant_policy_decision_count",
                0,
            ),
            "automatic_authorization_without_memory_count": (
                current_summary.get(
                    "automatic_authorization_without_memory_count",
                    0,
                )
            ),
            "mocked_action_execution_count": current_summary.get(
                "mocked_action_execution_count",
                0,
            ),
            "real_external_action_call_count": current_summary.get(
                "real_external_action_call_count",
                0,
            ),
            "role_verifier_configured_case_count": current_summary.get(
                "role_verifier_configured_case_count",
                0,
            ),
            "role_verifier_triggered_case_count": current_summary.get(
                "role_verifier_triggered_case_count",
                0,
            ),
            "role_verifier_logical_review_count": current_summary.get(
                "role_verifier_logical_review_count",
                0,
            ),
            "role_verifier_projected_candidate_claim_count": current_summary.get(
                "role_verifier_projected_candidate_claim_count",
                0,
            ),
            "role_verifier_atomic_claim_count": current_summary.get(
                "role_verifier_atomic_claim_count",
                0,
            ),
            "role_verifier_call_count": current_summary.get(
                "role_verifier_call_count",
                0,
            ),
            "role_verifier_provider_invocation_count": current_summary.get(
                "role_verifier_provider_invocation_count",
                0,
            ),
            "role_verifier_output_retry_case_count": current_summary.get(
                "role_verifier_output_retry_case_count",
                0,
            ),
            "role_verifier_usage_incomplete_case_count": current_summary.get(
                "role_verifier_usage_incomplete_case_count",
                0,
            ),
            "role_verifier_status_counts": current_summary.get(
                "role_verifier_status_counts",
                {},
            ),
            "role_verifier_claim_status_counts": current_summary.get(
                "role_verifier_claim_status_counts",
                {},
            ),
            "role_verifier_total_duration_ms": current_summary.get(
                "role_verifier_total_duration_ms",
                0,
            ),
            "role_verifier_total_usage": current_summary.get(
                "role_verifier_total_usage",
                {},
            ),
            "tenant_policy_advisor_provider_invocation_count": (
                current_summary.get(
                    "tenant_policy_advisor_provider_invocation_count",
                    0,
                )
            ),
            "tenant_policy_advisor_usage_incomplete_case_count": (
                current_summary.get(
                    "tenant_policy_advisor_usage_incomplete_case_count",
                    0,
                )
            ),
            "tenant_policy_advisor_total_usage": current_summary.get(
                "tenant_policy_advisor_total_usage",
                {},
            ),
            "model_usage_measurement_status": current_summary.get(
                "model_usage_measurement_status",
                "unavailable",
            ),
            "model_usage_is_lower_bound": current_summary.get(
                "model_usage_is_lower_bound",
                True,
            ),
            "model_provider_invocation_count": current_summary.get(
                "model_provider_invocation_count",
                0,
            ),
            "model_usage_incomplete_case_count": current_summary.get(
                "model_usage_incomplete_case_count",
                0,
            ),
            "measured_model_total_usage": current_summary.get(
                "measured_model_total_usage",
                {},
            ),
            "cost_measurement_status": current_summary.get(
                "cost_measurement_status",
                "not_measured",
            ),
            "quality_measurement_status": current_summary.get(
                "quality_measurement_status",
                "unavailable",
            ),
            "accuracy_measurement_status": current_summary.get(
                "accuracy_measurement_status",
                "not_measured",
            ),
            "memory_database_candidate_count": current_summary.get(
                "memory_database_candidate_count",
                0,
            ),
            "memory_database_record_count": current_summary.get(
                "memory_database_record_count",
                0,
            ),
            "baseline_runtime_total_duration_ms": baseline_summary.get(
                "runtime_total_duration_ms",
                0,
            ),
            "current_runtime_total_duration_ms": current_summary.get(
                "runtime_total_duration_ms",
                0,
            ),
            "runtime_duration_delta_ms": int(
                current_summary.get("runtime_total_duration_ms") or 0
            )
            - int(baseline_summary.get("runtime_total_duration_ms") or 0),
            "baseline_primary_model_total_usage": baseline_summary.get(
                "primary_model_total_usage",
                {},
            ),
            "current_primary_model_total_usage": current_summary.get(
                "primary_model_total_usage",
                {},
            ),
            "review_requirement_changed_count": sum(
                item["review_requirement_changed"] for item in case_diffs
            ),
        },
        "cases": case_diffs,
    }


def _compare_case(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_lineage = _mapping(baseline.get("lineage"))
    current_lineage = _mapping(current.get("lineage"))
    baseline_base = _base_decision(baseline)
    current_base = _base_decision(current)
    current_effective = _effective_decision(current)
    baseline_grounding = _mapping(baseline.get("grounding"))
    current_grounding = _mapping(current.get("grounding"))
    automation = _mapping(current.get("automation"))
    tenant_policy = _mapping(current.get("tenant_policy"))
    baseline_role_verification = _mapping(baseline.get("role_verification"))
    current_role_verification = _mapping(current.get("role_verification"))
    baseline_output_quality = _mapping(baseline.get("analysis_output_quality"))
    current_output_quality = _mapping(current.get("analysis_output_quality"))
    base_verdict_changed = baseline_base.get("verdict") != current_base.get("verdict")
    effective_changed = current_base.get("verdict") != current_effective.get(
        "verdict"
    ) or current_base.get("needs_review") != current_effective.get("needs_review")
    return {
        "alert_id": current.get("alert_id"),
        "source_type": _mapping(current.get("source")).get("source_type"),
        "same_input_hash": (
            baseline_lineage.get("input_hash") == current_lineage.get("input_hash")
        ),
        "same_model_name": (
            baseline_lineage.get("model_name") == current_lineage.get("model_name")
        ),
        "same_prompt_version": (
            baseline_lineage.get("prompt_version")
            == current_lineage.get("prompt_version")
        ),
        "baseline_base_decision": baseline_base,
        "current_base_decision": current_base,
        "current_effective_decision": current_effective,
        "base_verdict_changed": base_verdict_changed,
        "effective_verdict_changed_from_base": effective_changed,
        "review_requirement_changed": (
            baseline_base.get("needs_review") != current_base.get("needs_review")
        ),
        "baseline_role_verification": baseline_role_verification,
        "current_role_verification": current_role_verification,
        "baseline_analysis_output_quality": baseline_output_quality,
        "current_analysis_output_quality": current_output_quality,
        "analysis_output_quality_changed": (
            baseline_output_quality.get("status")
            != current_output_quality.get("status")
            or baseline_output_quality.get("degraded_sections")
            != current_output_quality.get("degraded_sections")
        ),
        "baseline_model_calls": _mapping(baseline.get("model_calls")),
        "current_model_calls": _mapping(current.get("model_calls")),
        "grounding": {
            "baseline_grounded": int(baseline_grounding.get("grounded_count") or 0),
            "current_grounded": int(current_grounding.get("grounded_count") or 0),
            "grounded_delta": int(current_grounding.get("grounded_count") or 0)
            - int(baseline_grounding.get("grounded_count") or 0),
            "baseline_ungrounded": int(baseline_grounding.get("ungrounded_count") or 0),
            "current_ungrounded": int(current_grounding.get("ungrounded_count") or 0),
            "ungrounded_delta": int(current_grounding.get("ungrounded_count") or 0)
            - int(baseline_grounding.get("ungrounded_count") or 0),
        },
        "baseline_quality_status": baseline.get("quality_status"),
        "current_quality_status": current.get("quality_status"),
        "quality_status_changed": (
            baseline.get("quality_status") != current.get("quality_status")
        ),
        "automation": automation,
        "memory_stage": _mapping(automation.get("memory_stage")),
        "tenant_policy_stage": _mapping(automation.get("tenant_policy_stage")),
        "tenant_policy": tenant_policy,
        "interpretation": (
            "live_model_resampling_or_runtime_change"
            if base_verdict_changed
            else "base_verdict_stable"
        ),
    }


def _base_decision(case: Mapping[str, Any]) -> dict[str, Any]:
    conclusion = _mapping(case.get("final_conclusion"))
    decision = _mapping(conclusion.get("base_runtime_decision"))
    if decision:
        return decision
    return {
        "verdict": conclusion.get("verdict"),
        "confidence": conclusion.get("confidence"),
        "evidence_state": conclusion.get("evidence_state"),
        "needs_review": conclusion.get("needs_review"),
        "automation_allowed": conclusion.get("automation_allowed"),
    }


def _effective_decision(case: Mapping[str, Any]) -> dict[str, Any]:
    conclusion = _mapping(case.get("final_conclusion"))
    return _mapping(conclusion.get("effective_decision")) or _base_decision(case)


def _case_index(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in report.get("cases") or []:
        if not isinstance(item, Mapping):
            raise ValueError("E2E report case entries must be objects")
        alert_id = str(item.get("alert_id") or "").strip()
        if not alert_id or alert_id in result:
            raise ValueError("E2E report has missing or duplicate alert IDs")
        result[alert_id] = dict(item)
    if len(result) != 10:
        raise ValueError("E2E comparison requires exactly 10 cases")
    return result


def render_markdown(comparison: Mapping[str, Any]) -> str:
    summary = _mapping(comparison.get("summary"))
    lines = [
        "# SOC Ten-Alert Before/After Comparison",
        "",
        f"- Cohort: `{comparison.get('cohort_id')}`",
        f"- Same-input cases: `{summary.get('same_input_hash_count')}/{summary.get('case_count')}`",
        f"- Base verdict changes: `{summary.get('base_verdict_changed_count')}`; these are not automatically attributed to Memory or automation",
        f"- Effective verdict/review changes with transition lineage: `{summary.get('effective_verdict_changed_from_base_count')}`",
        f"- PingAn tenant policy decisions: `{summary.get('tenant_policy_decision_count')}`",
        f"- Automatic authorizations without Memory: `{summary.get('automatic_authorization_without_memory_count')}`",
        f"- Mocked action executions: `{summary.get('mocked_action_execution_count')}`; real external calls: `{summary.get('real_external_action_call_count')}`",
        (
            "- Role verifier: "
            f"triggered `{summary.get('role_verifier_triggered_case_count')}` / "
            f"case calls `{summary.get('role_verifier_call_count')}` / "
            f"provider invocations `{summary.get('role_verifier_provider_invocation_count')}` / "
            f"retry cases `{summary.get('role_verifier_output_retry_case_count')}` / "
            f"usage-incomplete cases `{summary.get('role_verifier_usage_incomplete_case_count')}`; "
            f"statuses `{summary.get('role_verifier_status_counts')}`; "
            f"claim statuses `{summary.get('role_verifier_claim_status_counts')}`"
        ),
        (
            f"- Primary analysis output: statuses `{summary.get('analysis_output_quality_status_counts')}`; degraded sections `{summary.get('analysis_output_degraded_section_counts')}`"
        ),
        f"- Review requirement changes: `{summary.get('review_requirement_changed_count')}`",
        (
            f"- Runtime duration: `{summary.get('baseline_runtime_total_duration_ms')} ms -> {summary.get('current_runtime_total_duration_ms')} ms` (delta `{summary.get('runtime_duration_delta_ms')} ms`)"
        ),
        f"- Verifier usage: `{summary.get('role_verifier_total_usage')}`",
        (
            f"- Measured model usage: `{summary.get('measured_model_total_usage')}` (status={summary.get('model_usage_measurement_status')}, lower_bound={summary.get('model_usage_is_lower_bound')})"
        ),
        f"- Cost: `{summary.get('cost_measurement_status')}`",
        "",
        "| Alert | Source | Old base | Verifier | New base | Memory stage | PingAn Policy | New effective | Grounded old -> new | Rejected old -> new | Action auth/execution |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in comparison.get("cases") or []:
        if not isinstance(item, Mapping):
            continue
        old_base = _mapping(item.get("baseline_base_decision"))
        new_base = _mapping(item.get("current_base_decision"))
        effective = _mapping(item.get("current_effective_decision"))
        grounding = _mapping(item.get("grounding"))
        automation = _mapping(item.get("automation"))
        memory_stage = _mapping(item.get("memory_stage"))
        tenant_stage = _mapping(item.get("tenant_policy_stage"))
        tenant_policy = _mapping(item.get("tenant_policy"))
        role_verification = _mapping(item.get("current_role_verification"))
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.get("alert_id")),
                    _md(item.get("source_type")),
                    _md(old_base.get("verdict")),
                    _md(
                        f"{role_verification.get('status') or 'disabled'}"
                        + (
                            f" ({role_verification.get('claim_count')} claims)"
                            if role_verification.get("triggered")
                            else ""
                        )
                    ),
                    _md(new_base.get("verdict")),
                    _md(memory_stage.get("status") or "none"),
                    _md(
                        f"{tenant_stage.get('status') or 'none'} / {tenant_policy.get('decision_source') or 'none'} / {tenant_policy.get('selected_rule_id') or 'no-match'}"
                    ),
                    _md(effective.get("verdict")),
                    _md(
                        f"{grounding.get('baseline_grounded')} -> {grounding.get('current_grounded')}"
                    ),
                    _md(
                        f"{grounding.get('baseline_ungrounded')} -> {grounding.get('current_ungrounded')}"
                    ),
                    _md(
                        "auth="
                        + (
                            ", ".join(
                                str(value)
                                for value in automation.get(
                                    "action_authorization_decisions"
                                )
                                or []
                            )
                            or "none"
                        )
                        + "; exec="
                        + (
                            ", ".join(
                                str(value)
                                for value in automation.get("action_execution_statuses")
                                or []
                            )
                            or "none"
                        )
                    ),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "A live LLM may produce a different base verdict or evidence selection on the same input. "
            "The baseline and verifier-enabled runs each perform their own primary call, so a base "
            "decision delta is not by itself evidence that the verifier caused the change. "
            "Only `current_effective_decision` changes backed by `SocDecisionTransitionRecord` are "
            "attributed to reviewed Memory or policy processing. Every execution in this comparison "
            "must remain `mocked=true`; it is not real provider acceptance evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def write_comparison(
    output_dir: Path,
    comparison: Mapping[str, Any],
) -> tuple[Path, Path]:
    root = output_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    json_path = root / "comparison.json"
    markdown_path = root / "COMPARISON.md"
    _write_private(
        json_path,
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
    )
    _write_private(markdown_path, render_markdown(comparison))
    return json_path, markdown_path


def _write_private(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline_path = args.baseline.expanduser().resolve()
        current_path = args.current.expanduser().resolve()
        comparison = compare_reports(
            load_report(baseline_path),
            load_report(current_path),
            baseline_path=str(baseline_path),
            current_path=str(current_path),
        )
        json_path, markdown_path = write_comparison(
            args.output_dir,
            comparison,
        )
        print(
            json.dumps(
                {
                    "comparison": str(json_path),
                    "markdown": str(markdown_path),
                    **_mapping(comparison.get("summary")),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
