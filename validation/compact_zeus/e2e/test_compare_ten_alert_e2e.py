from __future__ import annotations

import json
from pathlib import Path

from validation.compact_zeus.e2e.compare_ten_alert_e2e import (
    compare_reports,
    load_report,
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
        role_verification = {
            "configured": current,
            "triggered": current and index < 3,
            "status": "confirmed" if current and index < 3 else "disabled",
            "claim_count": 4 if current and index < 3 else 0,
        }
        if current:
            final_conclusion["base_runtime_decision"] = {
                "verdict": base_verdict,
                "confidence": 0.7,
                "evidence_state": "partial",
                "needs_review": True,
                "automation_allowed": False,
                "suggested_action": "review",
                "policy_version": "soc.decision_policy.v4",
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
                "memory_stage": {
                    "stage": "memory",
                    "status": "reinforced" if index == 1 else "no_input",
                },
                "tenant_policy_stage": {
                    "stage": "tenant_policy",
                    "status": "applied" if index == 2 else "no_match",
                },
                "action_authorization_decisions": [],
                "action_execution_statuses": [],
            }
        cases.append(
            {
                "alert_id": alert_id,
                "lineage": {
                    "input_hash": f"hash-{index}",
                    "model_name": "deepseek-v4-flash",
                    "prompt_version": "soc-analysis-v13",
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
                "tenant_policy": {
                    "decision_source": (
                        "llm_policy_skill" if index == 2 else "no_match"
                    ),
                    "selected_rule_id": (
                        "llm-policy-skill-advice" if index == 2 else None
                    ),
                },
                "role_verification": role_verification,
                "model_calls": {
                    "runtime_total_duration_ms": 2000 if current else 1000,
                    "primary": {"usage": {"total_tokens": 100}},
                    "role_verifier": {
                        "present": current and index < 3,
                        "usage": {"total_tokens": 20} if index < 3 else {},
                    },
                    "tenant_policy_advisor": {
                        "present": current,
                        "provider_call_count": 1 if current else 0,
                        "usage": {"total_tokens": 30} if current else {},
                    },
                },
            }
        )
    return {
        "schema_version": (
            "soc.validation.e2e_ten_alert_report.v7"
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
            "tenant_policy_decision_count": 10 if current else 0,
            "automatic_authorization_without_memory_count": 0,
            "mocked_action_execution_count": 0,
            "real_external_action_call_count": 0,
            "runtime_total_duration_ms": 20000 if current else 10000,
            "primary_model_total_usage": {"total_tokens": 1000},
            "role_verifier_configured_case_count": 10 if current else 0,
            "role_verifier_triggered_case_count": 3 if current else 0,
            "role_verifier_logical_review_count": 3 if current else 0,
            "role_verifier_projected_candidate_claim_count": (30 if current else 0),
            "role_verifier_atomic_claim_count": 12 if current else 0,
            "role_verifier_call_count": 3 if current else 0,
            "role_verifier_provider_invocation_count": 5 if current else 0,
            "role_verifier_output_retry_case_count": 2 if current else 0,
            "role_verifier_usage_incomplete_case_count": 1 if current else 0,
            "role_verifier_status_counts": (
                {"confirmed": 3, "not_triggered": 7} if current else {"disabled": 10}
            ),
            "role_verifier_claim_status_counts": ({"supported": 12} if current else {}),
            "role_verifier_total_duration_ms": 3000 if current else 0,
            "role_verifier_total_usage": ({"total_tokens": 60} if current else {}),
            "tenant_policy_advisor_provider_invocation_count": 10 if current else 0,
            "tenant_policy_advisor_usage_incomplete_case_count": 0,
            "tenant_policy_advisor_total_usage": (
                {"total_tokens": 300} if current else {}
            ),
            "model_usage_measurement_status": "complete" if current else "unavailable",
            "model_usage_is_lower_bound": not current,
            "model_provider_invocation_count": 25 if current else 0,
            "model_usage_incomplete_case_count": 0,
            "measured_model_total_usage": ({"total_tokens": 1360} if current else {}),
            "cost_measurement_status": "not_measured",
            "quality_measurement_status": "structural_safety_only",
            "accuracy_measurement_status": "not_measured",
            "memory_database_candidate_count": 0,
            "memory_database_record_count": 0,
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
    assert summary["role_verifier_provider_invocation_count"] == 5
    assert summary["role_verifier_output_retry_case_count"] == 2
    assert summary["role_verifier_usage_incomplete_case_count"] == 1
    assert summary["base_verdict_changed_count"] == 1
    assert summary["effective_verdict_changed_from_base_count"] == 1
    assert summary["tenant_policy_decision_count"] == 10
    assert summary["automatic_authorization_without_memory_count"] == 0
    assert summary["mocked_action_execution_count"] == 0
    assert summary["real_external_action_call_count"] == 0
    assert summary["role_verifier_triggered_case_count"] == 3
    assert summary["role_verifier_logical_review_count"] == 3
    assert summary["role_verifier_projected_candidate_claim_count"] == 30
    assert summary["role_verifier_atomic_claim_count"] == 12
    assert summary["role_verifier_claim_status_counts"] == {"supported": 12}
    assert summary["tenant_policy_advisor_provider_invocation_count"] == 10
    assert summary["model_provider_invocation_count"] == 25
    assert summary["measured_model_total_usage"] == {"total_tokens": 1360}
    assert summary["cost_measurement_status"] == "not_measured"
    assert summary["accuracy_measurement_status"] == "not_measured"
    assert summary["runtime_duration_delta_ms"] == 10000
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


def test_load_report_accepts_current_v7_schema(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(current=True)), encoding="utf-8")

    assert load_report(report)["schema_version"] == (
        "soc.validation.e2e_ten_alert_report.v7"
    )
