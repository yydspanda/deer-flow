import pytest

from soc_agent.demo.corpus_experiment_reports import compare_reports, export_report, result_breakdowns, summarize_results


def test_draft_export_separates_saved_usage_and_unknown_attempts(tmp_path):
    import csv
    import json

    from soc_agent.demo.corpus_experiment_reports import export_draft_report

    jobs = [
        {
            "job_id": "J1",
            "candidate_id": "=C1",
            "status": "completed",
            "version": 5,
            "attempt_count": 1,
            "provider_call_count": 2,
            "usage": {"total_tokens": 100},
            "created_at": "2026-09-18T00:00:00Z",
            "started_at": "2026-09-18T00:00:02Z",
            "completed_at": "2026-09-18T00:00:05Z",
            "authority": "draft_only",
        },
        {"job_id": "J2", "candidate_id": "C2", "status": "failed", "version": 4, "attempt_count": 2, "provider_call_count": None, "usage": None, "error_code": "generation_uncertain", "authority": "draft_only"},
    ]
    output = tmp_path / "drafting"
    export_draft_report("EXP1", jobs, output)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["scope"] == "experiment_background_drafting_only"
    assert report["metrics"]["known_total_tokens"] == 100
    assert report["metrics"]["unknown_usage_jobs"] == 1
    assert report["metrics"]["known_provider_calls"] == 2
    assert report["metrics"]["potentially_unmeasured_attempt_jobs"] == 1
    assert report["jobs"][0]["queue_wait_ms"] == 2000
    assert report["jobs"][0]["processing_wall_ms"] == 3000
    assert report["jobs"][1]["processing_wall_ms"] is None
    assert (output / "report.json").stat().st_mode & 0o777 == 0o600
    with (output / "draft-jobs.csv").open(encoding="utf-8-sig") as stream:
        exported = list(csv.DictReader(stream))
    assert exported[0]["candidate_id"] == "'=C1"
    assert exported[1]["total_tokens"] == ""
    with pytest.raises(FileExistsError):
        export_draft_report("EXP1", jobs, output)
    with pytest.raises(ValueError, match="未结束"):
        export_draft_report("EXP1", [{**jobs[0], "status": "analyzing"}], tmp_path / "active")


def test_breakdowns_preserve_many_to_many_memory_rule_relations_and_phase_timing():
    first, second = rows()[:2]
    first.update(rule_code="R1", group_id="G1")
    second.update(rule_code="R2", group_id="G2")
    first["summary"]["phase_timings"] = [{"phase": "analyze", "status": "success", "duration_ms": 200}]
    second["summary"]["phase_timings"] = [{"phase": "analyze", "status": "skipped", "duration_ms": 0}]
    second["summary"]["memory_uses"] = [{"memory_id": "M-1", "directive_applied": False}, {"memory_id": "M-2", "directive_applied": True}]
    report = result_breakdowns([first, second])
    assert len(report["rules"]) == 2
    memory = next(item for item in report["memories"] if item["memory_id"] == "M-1")
    assert memory["actual_rule_codes"] == ["R1", "R2"]
    assert memory["metrics"]["reuse"]["memory_direct_reuse"]["numerator"] == 1
    assert memory["metrics"]["reuse"]["memory_reference"]["numerator"] == 1
    assert report["phases"]["analyze"] == {"status_counts": {"success": 1, "skipped": 1}, "known_duration_count": 2, "total_ms": 200, "max_ms": 200}


def rows():
    return [
        {
            "alert_id": "1",
            "validation_tier": "main",
            "status": "completed",
            "summary": {"recommended_handling": "ignore", "measurements": {"total_tokens": 100, "usage_measurement_status": "reported"}, "memory_uses": [{"memory_id": "M-1", "directive_applied": True}]},
            "label": {"available": True, "expected_handling": "ignore", "scorable": True},
        },
        {
            "alert_id": "2",
            "validation_tier": "main",
            "status": "completed",
            "summary": {"recommended_handling": "ignore", "measurements": {"total_tokens": None, "usage_measurement_status": "unavailable"}},
            "label": {"available": True, "expected_handling": "transfer", "scorable": True},
        },
        {"alert_id": "3", "validation_tier": "supplementary", "status": "completed", "summary": {"recommended_handling": "transfer"}, "label": {"available": True, "expected_handling": "transfer", "scorable": True}},
        {"alert_id": "4", "validation_tier": "main", "status": "failed", "summary": {}, "label": {"available": False, "scorable": False}},
    ]


def test_report_separates_small_sample_exploration_and_unknown_usage():
    summary = summarize_results(rows())
    assert summary["reuse"]["completed"] == 2
    assert summary["reuse"]["historical_handling_agreement"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert summary["reuse"]["wrong_ignore_against_history"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert summary["explore"]["historical_handling_agreement"]["rate"] == 1.0
    assert summary["reuse"]["tokens"]["known_total"] == 100
    assert summary["reuse"]["tokens"]["unavailable_runs"] == 1
    assert summary["reuse"]["memory_direct_reuse"]["numerator"] == 1


def test_comparison_retains_failed_to_successful_run_lineage():
    before = {"round": {"round_id": "R1", "experiment_id": "EXP-1"}, "source_identity": {"sha256": "fixed"}, "rows": rows()}
    after = {**before, "round": {"round_id": "R2", "experiment_id": "EXP-1"}, "rows": [{**rows()[3], "status": "completed", "run_id": "RUN-retest", "summary": {"recommended_handling": "transfer"}}]}
    result = compare_reports(before, after)
    assert result["paired_alerts"] == 0  # No pair of valid conclusions to grade.
    assert result["recovered_failures"] == 1
    transition = next(row for row in result["item_transitions"] if row["alert_id"] == "4")
    assert transition["before_status"] == "failed"
    assert transition["after_status"] == "completed"
    assert transition["before_run_id"] is None
    assert transition["after_run_id"] == "RUN-retest"


def test_invalidated_or_unlabeled_runs_do_not_imply_ground_truth():
    result = rows()[0]
    result["snapshot_changed_during_run"] = True
    assert summarize_results([result])["reuse"]["historical_handling_agreement"]["denominator"] == 0
    result["snapshot_changed_during_run"] = False
    result["label"]["scorable"] = False
    assert summarize_results([result])["reuse"]["historical_handling_agreement"]["rate"] is None


def test_model_cost_includes_persisted_failure_and_success_attempts_without_double_counting():
    row = rows()[0]
    row["attempt_measurements"] = [
        {"run_id": "failed-1", "total_tokens": 200, "usage_measurement_status": "reported", "provider_call_count": 1},
        {"run_id": "success-2", "total_tokens": 100, "usage_measurement_status": "reported", "provider_call_count": 2},
    ]
    summary = summarize_results([row])["reuse"]
    assert summary["tokens"]["known_total"] == 300
    assert summary["provider_calls"] == 3
    assert summary["measured_run_attempts"] == 2
    assert summary["completed"] == 1


def test_private_export_is_atomic_formula_safe_and_comparison_is_paired(tmp_path):
    before = {"schema_version": "soc.corpus_round_report.v1", "round": {"round_id": "R1", "experiment_id": "EXP-1"}, "source_identity": {"sha256": "abc"}, "rows": rows()}
    before["rows"][0]["rule_code"] = "=1+1"
    before["rows"][0]["job_timing"] = {"initial_queue_wait_ms": 500, "processing_wall_ms": 1000, "total_wall_ms": 9000, "timing_status": "complete"}
    before["rows"][3].update(error_code="timeout", error_message="@retry", attempt_count=2)
    folder = tmp_path / "result"
    export_report(before, folder)
    assert (folder / "report.json").stat().st_mode & 0o777 == 0o600
    assert "'=1+1" in (folder / "alerts.csv").read_text(encoding="utf-8-sig")
    assert "500,1000,9000,complete" in (folder / "alerts.csv").read_text(encoding="utf-8-sig")
    assert (folder / "REPORT.md").is_file()
    assert "M-1" in (folder / "memory-usage.csv").read_text(encoding="utf-8-sig")
    assert "'=1+1" in (folder / "memory-usage.csv").read_text(encoding="utf-8-sig")
    assert "'@retry" in (folder / "failures.csv").read_text(encoding="utf-8-sig")
    import csv

    with (folder / "failures.csv").open(encoding="utf-8-sig") as stream:
        failures = list(csv.DictReader(stream))
    assert [row["alert_id"] for row in failures] == ["4"]
    assert failures[0]["attempt_count"] == "2"
    with pytest.raises(FileExistsError):
        export_report(before, folder)
    after = {**before, "round": {"round_id": "R2", "experiment_id": "EXP-1"}, "rows": [*rows()[:1], {**rows()[1], "summary": {"recommended_handling": "transfer"}}]}
    comparison = compare_reports(before, after)
    assert comparison["paired_alerts"] == 2
    assert comparison["changed_handling"] == 1
    with pytest.raises(ValueError, match="dataset"):
        compare_reports(before, {**after, "source_identity": {"sha256": "other"}})
