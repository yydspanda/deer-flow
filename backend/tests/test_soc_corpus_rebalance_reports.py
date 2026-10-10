"""Rebalanced evaluation reports use held-out outcomes, never operational labels."""

import csv
import json

import pytest

from soc_agent.demo.corpus_experiment_reports import compare_reports, export_report


def report():
    def row(alert_id, tier, handling, uses):
        return {
            "alert_id": alert_id,
            "validation_tier": tier,
            "status": "completed",
            "summary": {"recommended_handling": handling, "decision_usable": True, "memory_uses": uses},
            "label": {"scorable": True, "expected_handling": "transfer", "reason": "must-not-export"},
        }

    return {
        "round": {"round_id": "ROUND-rebalanced", "experiment_id": "EXP-rebalanced", "purpose": "memory", "selection": {"batch": "validation", "scope": "all"}},
        "source_identity": {"sha256": "fixed"},
        "experiment": {"rebalance": {"parent_experiment_id": "EXP-parent", "plan_hash": "f" * 64}},
        "stage_scope": {"batch_total": 3, "stage_complete": True},
        "rows": [
            row("1", "main", "ignore", [{"memory_id": "M1", "directive_applied": True, "applicability_report": {"status": "applicable"}}]),
            row("2", "main", "transfer", [{"memory_id": "M2", "directive_applied": False, "applicability_report": {"status": "partial"}}]),
            row("3", "supplementary", "ignore", []),
        ],
    }


def test_rebalanced_export_separates_coverage_and_unknowns_without_historical_labels(tmp_path):
    destination = tmp_path / "stage"
    export_report(report(), destination)
    result = json.loads((destination / "report.json").read_text(encoding="utf-8"))
    assert result["stage_conclusion_allowed"] is True
    assert result["report_scope"] == "stage"
    assert set(result["metrics"]) == {"main", "supplementary"}
    main = result["metrics"]["main"]
    assert main["applicable_memory_coverage"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert main["memory_direct_reuse"]["numerator"] == 1
    assert main["known_experience"] == {"count": 1, "ignore": 1, "transfer": 0, "undetermined": 0}
    assert main["no_history_experience"] == {"count": 1, "ignore": 0, "transfer": 1, "undetermined": 0}
    assert result["metrics"]["supplementary"]["no_history_experience"]["ignore"] == 1
    content = (destination / "report.json").read_text(encoding="utf-8")
    assert "must-not-export" not in content
    assert "historical_handling_agreement" not in content
    assert "wrong_ignore_against_history" not in content
    assert all("label" not in row for row in result["rows"])
    with (destination / "alerts.csv").open(encoding="utf-8-sig") as stream:
        assert "expected_handling" not in csv.DictReader(stream).fieldnames
    rendered = (destination / "REPORT.md").read_text(encoding="utf-8")
    assert "阶段结论" in rendered and "无历史经验" in rendered
    assert "历史处置一致率" not in rendered and "错误忽略" not in rendered
    assert "不是准确率" in rendered


@pytest.mark.parametrize("defect", ["incomplete", "partial_round", "failed", "snapshot", "duplicate", "facts", "learning"])
def test_rebalanced_incomplete_or_ineligible_stage_exports_diagnostics_only(tmp_path, defect):
    value = report()
    if defect == "incomplete":
        value["stage_scope"]["stage_complete"] = False
    elif defect == "partial_round":
        value["stage_scope"]["batch_total"] = 4
    elif defect == "failed":
        value["rows"][0]["status"] = "failed"
    elif defect == "snapshot":
        value["rows"][0]["snapshot_changed_during_run"] = True
    elif defect == "duplicate":
        value["rows"][0]["alert_id"] = "2"
    elif defect == "facts":
        value["round"]["purpose"] = "facts"
    else:
        value["round"]["selection"] = {"batch": "learning", "scope": "reuse"}
    export_report(value, tmp_path / defect)
    result = json.loads((tmp_path / defect / "report.json").read_text(encoding="utf-8"))
    assert result["stage_conclusion_allowed"] is False
    assert result["report_scope"] == "round_diagnostic"
    assert "仅诊断" in (tmp_path / defect / "REPORT.md").read_text(encoding="utf-8")


def test_rebalanced_comparison_keeps_label_blind_metrics():
    value = report()
    result = compare_reports(value, {**value, "round": {**value["round"], "round_id": "ROUND-next"}})
    assert set(result["before_metrics"]) == {"main", "supplementary"}
    assert "historical_handling_agreement" not in json.dumps(result)
