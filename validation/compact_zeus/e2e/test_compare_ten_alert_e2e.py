from __future__ import annotations

import json
from pathlib import Path

from validation.compact_zeus.e2e.compare_ten_alert_e2e import (
    compare_reports,
    main,
)


def _report(*, current: bool) -> dict:
    cases = []
    for index in range(10):
        alert_id = f"alert-{index}"
        base_verdict = "true_positive" if current and index == 0 else "suspicious"
        effective_verdict = "true_positive" if current and index == 1 else base_verdict
        final_conclusion = {
            "verdict": base_verdict,
            "confidence": 0.7,
            "evidence_state": "partial",
            "needs_review": True,
            "automation_allowed": False,
        }
        automation = {}
        if current:
            final_conclusion["base_runtime_decision"] = {
                "verdict": base_verdict,
                "confidence": 0.7,
                "evidence_state": "partial",
                "needs_review": True,
                "automation_allowed": False,
                "suggested_action": "review",
                "policy_version": "soc.decision_policy.v3",
            }
            final_conclusion["effective_decision"] = {
                "verdict": effective_verdict,
                "confidence": 0.7,
                "evidence_state": "partial",
                "needs_review": index != 1,
                "suggested_action": "review",
                "policy_version": "soc.effective_decision_policy.v1",
            }
            automation = {
                "decision_transition_count": 1,
                "effective_decision_changed": index == 1,
                "selected_rule_id": (
                    "simulate-reviewed-network-source-block" if index == 1 else None
                ),
                "action_authorization_decisions": (
                    ["authorized"] if index == 1 else []
                ),
                "action_execution_statuses": (["succeeded"] if index == 1 else []),
            }
        cases.append(
            {
                "alert_id": alert_id,
                "lineage": {
                    "input_hash": f"hash-{index}",
                    "model_name": "deepseek-v4-flash",
                    "prompt_version": "soc-analysis-v12",
                },
                "source": {"source_type": "ndr"},
                "grounding": {
                    "grounded_count": 10 + (1 if current else 0),
                    "ungrounded_count": 1 if not current else 0,
                },
                "quality_status": (
                    "no_findings" if current and index == 0 else "review_required"
                ),
                "final_conclusion": final_conclusion,
                "automation": automation,
            }
        )
    return {
        "schema_version": (
            "soc.validation.e2e_ten_alert_report.v2"
            if current
            else "soc.validation.e2e_ten_alert_report.v1"
        ),
        "generated_at": "2026-08-11T00:00:00+00:00",
        "cohort_id": "test-cohort",
        "acceptance_status": "passed",
        "quality_status": "review_required",
        "input": {"source_sha256": "source-hash"},
        "summary": {
            "decision_transition_count": 10 if current else 0,
            "memory_contributor_count": 0,
            "automatic_authorization_without_memory_count": 1 if current else 0,
            "mocked_action_execution_count": 1 if current else 0,
            "real_external_action_call_count": 0,
        },
        "cases": cases,
    }


def test_compare_reports_separates_live_base_drift_from_effective_transition() -> None:
    comparison = compare_reports(
        _report(current=False),
        _report(current=True),
        baseline_path="old/summary.json",
        current_path="new/summary.json",
    )

    summary = comparison["summary"]
    assert summary["case_count"] == 10
    assert summary["same_input_hash_count"] == 10
    assert summary["base_verdict_changed_count"] == 1
    assert summary["effective_verdict_changed_from_base_count"] == 1
    assert summary["automatic_authorization_without_memory_count"] == 1
    assert summary["mocked_action_execution_count"] == 1
    assert summary["real_external_action_call_count"] == 0
    assert comparison["cases"][0]["interpretation"] == (
        "live_model_resampling_or_runtime_change"
    )


def test_compare_cli_writes_private_json_and_markdown(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    output = tmp_path / "comparison"
    baseline.write_text(json.dumps(_report(current=False)), encoding="utf-8")
    current.write_text(json.dumps(_report(current=True)), encoding="utf-8")

    exit_code = main(
        [
            str(baseline),
            str(current),
            "--output-dir",
            str(output),
        ]
    )

    assert exit_code == 0
    assert (output / "comparison.json").is_file()
    assert (output / "COMPARISON.md").is_file()
    assert (output / "comparison.json").stat().st_mode & 0o777 == 0o600
    assert "Automatic authorizations without Memory" in (
        output / "COMPARISON.md"
    ).read_text(encoding="utf-8")
