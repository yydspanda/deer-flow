"""Synthetic saved reports only; summary generation must remain offline."""

import copy
import hashlib
import json
import shutil
import subprocess
import sys

import pytest

from scripts import soc_stage_report_summary as tool


def fixture_report():
    def row(number, tier, handling, uses, duration):
        return {
            "alert_id": str(number),
            "validation_tier": tier,
            "status": "completed",
            "snapshot_changed_during_run": False,
            "summary": {
                "recommended_handling": handling,
                "memory_uses": uses,
                "total_duration_ms": duration,
                "decision_usable": True,
                "processing_path": "model_analysis",
                "phase_timings": [
                    {"phase": "normalization_assist", "status": "success"}
                ],
                "decision_stages": [{"stage": "tenant_policy", "status": "disabled"}],
                "secret": "NEVER_EXPORT",
            },
            "attempt_measurements": [{"total_duration_ms": 900000}],
        }

    reference = [
        {
            "directive_applied": False,
            "applicability_report": {"status": "not_applicable"},
        }
    ]
    return {
        "schema_version": "soc.corpus_round_report.v1",
        "report_scope": "stage",
        "stage_conclusion_allowed": True,
        "stage_scope": {
            "batch_total": 4,
            "validation_total": 4,
            "learning_total": 8,
            "stage_complete": True,
        },
        "round": {
            "round_id": "ROUND-TEST",
            "experiment_id": "EXP-TEST",
            "purpose": "validation",
            "selection": {"batch": "validation", "scope": "all"},
            "options": {
                "normalization_review_mode": "apply",
                "tenant_policy_enabled": True,
            },
            "memory_snapshot": [{"memory_id": "DO_NOT_EXPORT"}],
            "config_snapshot": {"secret": "NEVER_EXPORT"},
        },
        "progress": {
            "selected_count": 4,
            "completed_count": 4,
            "active_count": 0,
            "counts": {"completed": 4},
        },
        "rows": [
            row(1, "main", "ignore", [{"directive_applied": True}], 1000),
            row(2, "supplementary", "ignore", reference, 3000),
            row(3, "supplementary", "transfer", [], 5000),
            row(4, "supplementary", "ignore", [], 7000),
        ],
    }


def test_original_conclusion_metrics_count_reference_and_only_final_run_duration():
    report = fixture_report()
    before = copy.deepcopy(report)
    result = tool.summarize_report(report)
    assert result["scope"]["stage_conclusion_ready"] is True
    total = result["metrics"]["all"]
    group = result["metrics"]["supplementary"]
    assert total["memory_used_including_reference"] == 2
    assert total["memory_direct_reuse"] == 1
    assert total["final_run_duration"]["mean_seconds"] == 4
    assert group["ignored_with_memory"] == 1
    assert group["memory_unused"] == 2
    assert group["memory_unused_transfer_percent"] == 50
    assert group["ignore_percent"] == pytest.approx(66.6667, abs=0.001)
    assert report == before
    assert "NEVER_EXPORT" not in json.dumps(result)
    assert "DO_NOT_EXPORT" not in json.dumps(result)


def test_incomplete_report_produces_diagnostic_without_claiming_full_stage():
    report = fixture_report()
    report["rows"][0]["status"] = "failed"
    report["rows"][1]["snapshot_changed_during_run"] = True
    report["progress"]["active_count"] = 1
    result = tool.summarize_report(report)
    assert result["scope"]["stage_conclusion_ready"] is False
    assert result["scope"]["job_status_counts"] == {"failed": 1, "completed": 3}
    assert result["metrics"]["all"]["included_completed_stable"] == 2
    assert result["metrics"]["main"]["ignore_percent"] is None


def test_missing_memory_and_duration_are_unknown_not_zero_or_unused():
    report = fixture_report()
    del report["rows"][2]["summary"]["memory_uses"]
    report["rows"][2]["summary"]["total_duration_ms"] = None
    result = tool.summarize_report(report)["metrics"]["all"]
    assert result["memory_usage_unknown"] == 1
    assert result["memory_unused"] == 1
    assert result["memory_unused_transfer"] == 0
    assert result["final_run_duration"]["missing_count"] == 1
    assert result["final_run_duration"]["mean_seconds"] == pytest.approx(
        11 / 3, abs=0.001
    )


def test_duplicate_members_and_unknown_schema_are_rejected():
    report = fixture_report()
    report["rows"].append(copy.deepcopy(report["rows"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        tool.summarize_report(report)
    report["schema_version"] = "unknown"
    with pytest.raises(ValueError, match="schema"):
        tool.summarize_report(report)


def test_policy_statuses_and_unrecorded_snapshot_remain_explicit():
    report = fixture_report()
    statuses = ["overridden", "reinforced", "shadow_matched", "no_match"]
    for row, status in zip(report["rows"], statuses, strict=True):
        row["summary"]["decision_stages"][0]["status"] = status
    assert tool.summarize_report(report)["final_step_counts"][
        "tenant_policy"
    ] == dict.fromkeys(statuses, 1)
    del report["rows"][0]["snapshot_changed_during_run"]
    result = tool.summarize_report(report)
    assert result["scope"]["stage_conclusion_ready"] is False
    assert result["metrics"]["all"]["included_completed_stable"] == 3
    assert "overridden" not in result["final_step_counts"]["tenant_policy"]


def test_merged_report_preserves_distinct_settings_and_final_results():
    report = fixture_report()
    base = report.pop("round")
    retest = copy.deepcopy(base)
    retest["round_id"] = "ROUND-RETEST"
    retest["options"]["refresh_normalization"] = True
    report.update(
        schema_version="soc.corpus_merged_stage_report.v1",
        source_reports=[{"round": base}, {"round": retest}],
        merge={
            "merged_count": 4,
            "replacement_count": 1,
            "checks": {"all_business_jobs_completed": True, "no_snapshot_drift": True},
        },
    )
    result = tool.summarize_report(report)
    assert len(result["source_rounds"]) == 2
    assert result["source_rounds"][1]["options"]["refresh_normalization"] is True
    assert result["scope"]["stage_conclusion_ready"] is True
    assert result["metrics"]["all"]["count"] == 4


def test_standalone_cli_checks_manifest_and_does_not_change_source(tmp_path):
    folder = tmp_path / "report"
    folder.mkdir()
    payload = json.dumps(fixture_report()).encode()
    (folder / "report.json").write_bytes(payload)
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "round_id": "ROUND-TEST",
                "files": {"report.json": hashlib.sha256(payload).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "summary.py"
    shutil.copyfile(tool.__file__, script)
    output = tmp_path / "summary.json"
    command = [sys.executable, "-I", str(script), str(folder), "--output", str(output)]
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result == json.loads(output.read_text(encoding="utf-8"))
    assert result["source"]["report_manifest_verified"] is True
    assert (folder / "report.json").read_bytes() == payload
    # Existing outputs cannot be overwritten, even when they are the source report.
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode != 0
    (folder / "report.json").write_bytes(payload + b" ")
    run = subprocess.run(command[:-2], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode != 0 and "checksum" in run.stderr
