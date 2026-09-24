"""Isolated report arithmetic and private output tests; no business database."""

import csv
import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soc_validation_report_render import build_report, write_report


def row(
    alert_id,
    *,
    batch="validation",
    tier="main",
    group="g1",
    status="completed",
    **summary,
):
    return {
        "alert_id": alert_id,
        "batch": batch,
        "validation_tier": tier,
        "group_id": group,
        "round_id": "round-fixed",
        "run_id": "run-" + alert_id,
        "status": status,
        "summary": {"memory_uses": [], **summary},
    }


def data(*rows, candidates=(), memories=(), learning=(), followups=()):
    return {
        "metadata": {"experiment_id": "exp", "round_id": "round-fixed"},
        "validation_rows": list(rows),
        "learning_rows": list(learning),
        "followup_rows": list(followups),
        "candidates": list(candidates),
        "memories": list(memories),
        "members": [],
        "warnings": [],
    }


def test_usage_is_exclusive_and_model_skip_is_a_separate_fact():
    directive = {
        "memory_id": "m1",
        "memory_version": 1,
        "directive_applied": True,
        "effect": "conflicted",
    }
    reference = {"memory_id": "m2", "memory_version": 1, "directive_applied": False}
    report = build_report(
        data(
            row(
                "a",
                memory_uses=[directive, reference],
                processing_path="model",
                base_model_evaluated=True,
            ),
            row("b", memory_uses=[reference]),
            row("c"),
            row("d", memory_uses=None),
            row(
                "e",
                memory_uses=[directive],
                processing_path="memory",
                base_model_evaluated=False,
            ),
        )
    )
    assert report["metrics"]["main"]["memory_usage"] == {
        "directive": 2,
        "reference": 1,
        "none": 1,
        "unknown": 1,
    }
    assert report["metrics"]["main"]["direct_model_skip"]["numerator"] == 1
    assert any(
        item["code"] == "memory_conflict" and item["alert_id"] == "a"
        for item in report["differences"]
    )


def test_failure_and_snapshot_drift_remain_in_total_but_not_quality():
    stable = row("a", recommended_handling="ignore", effective_verdict="false_positive")
    stable["label"] = {"scorable": True, "expected_handling": "transfer"}
    drift = row("b", recommended_handling="ignore")
    drift["snapshot_changed_during_run"] = True
    drift["label"] = {"scorable": True, "expected_handling": "ignore"}
    failed = row("c", status="failed")
    unknown_label = row("d", recommended_handling="ignore")
    metrics = build_report(data(stable, drift, failed, unknown_label))["metrics"][
        "main"
    ]
    assert metrics["selected"] == 4
    assert metrics["quality_eligible"] == 2
    assert metrics["historical_handling_agreement"] == {
        "numerator": 0,
        "denominator": 1,
        "rate": 0.0,
    }
    assert metrics["historical_label_unscored"] == 1
    assert metrics["snapshot_affected"] == 1
    assert metrics["status_counts"]["failed"] == 1


def test_unknown_tokens_not_zero_percentiles_keep_denominators_and_tiers():
    report = build_report(
        data(
            row(
                "a",
                measurements={
                    "total_tokens": 10,
                    "input_tokens": 7,
                    "output_tokens": 3,
                },
                total_duration_ms=100,
            ),
            row("b", measurements={"total_tokens": None}, total_duration_ms=None),
            row("c", measurements={"total_tokens": 0}, total_duration_ms=300),
            row(
                "d",
                tier="supplementary",
                measurements={"total_tokens": 500},
                total_duration_ms=999,
            ),
        )
    )
    metrics = report["metrics"]["main"]
    assert metrics["tokens"]["total_tokens"] == {
        "known_sum": 10,
        "known_count": 2,
        "unknown_count": 1,
    }
    assert metrics["duration_ms"]["known_count"] == 2
    assert metrics["duration_ms"]["p50"] == 200
    assert metrics["duration_ms"]["p95"] == 290
    assert report["metrics"]["supplementary"]["selected"] == 1


def test_mixed_candidate_states_and_snapshot_supply_are_separate_from_use():
    candidates = [
        {
            "candidate_id": "c1",
            "group_ids": ["g1", "g2"],
            "current_status": "approved",
            "as_of_status": "unknown",
        },
        {
            "candidate_id": "c2",
            "group_ids": ["g1"],
            "current_status": "rejected",
            "as_of_status": "rejected",
            "reason": None,
        },
    ]
    memories = [
        {
            "memory_id": "m1",
            "memory_version": 2,
            "source_group_ids": ["g1"],
            "snapshot_included": True,
        }
    ]
    report = build_report(
        data(
            row("a", effective_verdict="false_positive"),
            row("b", group="g2"),
            candidates=candidates,
            memories=memories,
        )
    )
    first = next(item for item in report["groups"] if item["group_id"] == "g1")
    assert first["candidate_current_status_counts"] == {"approved": 1, "rejected": 1}
    assert first["candidate_as_of_status_counts"] == {"unknown": 1, "rejected": 1}
    assert first["snapshot_memory_ids"] == ["m1@2"]
    assert first["actual_memory_ids"] == []
    assert report["candidates"][1]["reason_recorded"] is False
    assert any(
        item["code"] == "snapshot_source_memory_unused"
        for item in report["differences"]
    )
    assert any(
        item["code"] == "rejected_candidate_clear_result"
        for item in report["differences"]
    )


def test_memory_alert_counts_deduplicate_and_foreign_origin_needs_provenance():
    uses = [{"memory_id": "m1", "memory_version": 1, "directive_applied": False}] * 2
    report = build_report(
        data(
            row("a", memory_uses=uses),
            memories=[
                {
                    "memory_id": "m1",
                    "memory_version": 1,
                    "source_group_ids": ["g2"],
                    "source_groups_complete": True,
                }
            ],
        )
    )
    assert report["memories"][0]["validation_alert_count"] == 1
    assert any(item["code"] == "foreign_group_memory" for item in report["differences"])
    unknown = build_report(data(row("a", memory_uses=uses)))
    assert not any(
        item["code"] == "foreign_group_memory" for item in unknown["differences"]
    )
    incomplete = build_report(
        data(
            row("a", memory_uses=uses),
            memories=[
                {
                    "memory_id": "m1",
                    "memory_version": 1,
                    "source_group_ids": ["g2"],
                    "source_groups_complete": False,
                }
            ],
        )
    )
    assert not any(
        item["code"] == "foreign_group_memory" for item in incomplete["differences"]
    )


def test_followups_are_separate_and_review_is_deterministic_stratified():
    rows = [
        row(str(i), group=f"g{i}", tier="main" if i < 5 else "supplementary")
        for i in range(10)
    ]
    report = build_report(
        data(*rows, followups=[row("a", status="failed")]), groups_per_batch=2
    )
    again = build_report(data(*reversed(rows)), groups_per_batch=2)
    assert report["metrics"]["all"]["selected"] == 10
    assert len(report["followups"]) == 1
    assert report["review"] == again["review"]
    assert {item["sampling_stratum"] for item in report["review"]} == {
        "main",
        "supplementary",
    }
    assert all(
        item["selection_method"] == "random_unflagged" for item in report["review"]
    )
    assert all(item["review_judgement"] == "" for item in report["review"])


def test_outputs_are_private_no_overwrite_excel_safe_and_do_not_expose_raw_fields(
    tmp_path,
):
    report = build_report(
        data(row("=1+1", group="<script>|bad", effective_verdict="false_positive"))
    )
    report["metadata"]["experiment_id"] = (
        "<script>alert(1)</script>\n|bad ![tracking](https://example.invalid/image)"
    )
    paths = write_report(report, tmp_path)
    assert {path.name for path in paths} == {
        "REPORT.md",
        "report.json",
        "alerts.csv",
        "groups.csv",
        "memories.csv",
        "differences.csv",
        "review.csv",
        "followups.csv",
        "candidates.csv",
    }
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert (tmp_path / "alerts.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    with (tmp_path / "alerts.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["告警ID"] == "'=1+1"
    assert "<script>" not in (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "![tracking](" not in (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert (
        json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["metadata"][
            "schema_version"
        ]
        == "soc.validation_effect_report.v1"
    )
    with pytest.raises(FileExistsError):
        write_report(report, tmp_path)


def test_invalid_batch_size_and_duplicate_primary_identity_are_rejected():
    with pytest.raises(ValueError):
        build_report(data(), groups_per_batch=0)
    with pytest.raises(ValueError):
        build_report(data(row("a"), row("a")))


def test_unknown_first_batch_as_of_is_not_a_quality_baseline():
    historical = row("old", batch="learning", effective_verdict="true_positive")
    historical["as_of_status"] = "unknown"
    report = build_report(
        data(row("new", effective_verdict="false_positive"), learning=[historical])
    )
    assert report["alerts"][0]["quality_eligible"] is False
    assert report["groups"][0]["learning_status_counts"] == {"unknown": 1}
    assert "group_verdict_divergence" not in report["groups"][0]["difference_codes"]


def test_mixed_learning_settings_are_preserved_in_report_and_missing_is_unknown(
    tmp_path,
):
    first = row("old1", batch="learning")
    first["options"] = {"normalization_review_mode": "apply"}
    second = row("old2", batch="learning")
    second["options"] = {"normalization_review_mode": "off"}
    report = build_report(data(row("new"), learning=[first, second]))
    assert {
        item["options"]["normalization_review_mode"]
        for item in report["learning_options_distribution"]
    } == {"apply", "off"}
    assert all(
        item["options"]["tenant_policy_enabled"] is None
        for item in report["learning_options_distribution"]
    )
    report["metadata"]["options"] = {"normalization_review_mode": "apply"}
    write_report(report, tmp_path)
    text = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "| 语义核对模式 | apply |" in text
    assert "| 租户策略 | 未知 |" in text
    assert "数据范围标识" in text


def test_semantic_changes_are_not_applied_claims_and_real_failed_phase_is_flagged():
    shadow = row("a")
    shadow["semantic_review"] = {
        "mode": "shadow",
        "status": "shadow",
        "change_count": 3,
        "observation_change_count": 2,
        "issue_count": 1,
    }
    failure = row(
        "b",
        phase_timings=[
            {"phase": "normalization_assist", "status": "failed", "duration_ms": 50}
        ],
    )
    partial = row("c")
    partial["semantic_review"] = {"mode": "apply", "status": "partial"}
    report = build_report(data(shadow, failure, partial))
    assert report["alerts"][0]["semantic_review_change_count"] == 3
    assert "semantic_review_applied_correction_count" not in report["alerts"][0]
    assert any(
        item["alert_id"] == "b" and item["code"] == "semantic_review_failed"
        for item in report["differences"]
    )
    assert any(
        item["alert_id"] == "c" and item["code"] == "semantic_review_partial"
        for item in report["differences"]
    )


def test_review_representatives_prioritize_actual_risk_not_first_alert():
    ordinary = [row(str(i)) for i in range(5)]
    risky = row(
        "last",
        effective_verdict="false_positive",
        recommended_handling="ignore",
        memory_uses=[
            {"memory_id": "m1", "memory_version": 1, "directive_applied": True}
        ],
    )
    risky["rule_name"] = "可读名称"
    report = build_report(data(*ordinary, risky))
    assert report["review"][0]["representative_alert_ids"][0] == "last"
    assert report["review"][0]["rule_names"] == ["可读名称"]


def test_current_rejected_does_not_invent_historical_rejection_and_known_directive_wins():
    candidate = {
        "candidate_id": "c",
        "group_ids": ["g1"],
        "current_status": "rejected",
        "as_of_status": "unknown",
    }
    alert = row(
        "a",
        effective_verdict="false_positive",
        memory_uses=[
            {"memory_id": "m", "memory_version": 1, "directive_applied": True},
            {"memory_id": "n", "memory_version": 1},
        ],
    )
    report = build_report(data(alert, candidates=[candidate]))
    assert report["alerts"][0]["memory_usage"] == "directive"
    assert not any(
        item["code"] == "rejected_candidate_clear_result"
        for item in report["differences"]
    )
    unknown = next(item for item in report["memories"] if item["memory_id"] == "n")
    assert unknown["reference_only_alert_count"] == 0
    assert unknown["usage_unknown_alert_count"] == 1


def test_reviewed_verdict_difference_requires_version_specific_historical_proof():
    alert = row(
        "a",
        effective_verdict="false_positive",
        memory_uses=[
            {"memory_id": "m", "memory_version": 2, "directive_applied": True}
        ],
    )
    memory = {
        "memory_id": "m",
        "memory_version": 2,
        "reviewed_verdict": "true_positive",
        "historical_verdict_known": True,
    }
    report = build_report(data(alert, memories=[memory]))
    assert any(
        item["code"] == "reviewed_memory_verdict_differs"
        for item in report["differences"]
    )
    memory["historical_verdict_known"] = False
    unknown = build_report(data(alert, memories=[memory]))
    assert not any(
        item["code"] == "reviewed_memory_verdict_differs"
        for item in unknown["differences"]
    )


def test_csv_large_cells_are_marked_as_previews_json_is_complete(tmp_path):
    alert = row(
        "a",
        memory_uses=[
            {"memory_id": "m", "memory_version": 1, "directive_applied": False}
        ],
    )
    report = build_report(data(alert))
    full_ids = [f"ALERT-{i:08}" for i in range(5000)]
    report["memories"][0]["alert_ids"] = full_ids
    write_report(report, tmp_path)
    with (tmp_path / "memories.csv").open(encoding="utf-8-sig", newline="") as stream:
        cell = next(csv.DictReader(stream))["关联告警"]
    assert len(cell) < 32_000
    assert "列表过长" in cell
    assert (
        json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["memories"][
            0
        ]["alert_ids"]
        == full_ids
    )


@pytest.mark.parametrize(
    "value", ["\t=SUM(A1)", "  +cmd", "\n@formula", "-formula", "=1+1"]
)
def test_formula_prefixes_are_escaped_in_every_output_column(tmp_path, value):
    report = build_report(data(row("a", group=value)))
    write_report(report, tmp_path)
    with (tmp_path / "groups.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert next(csv.DictReader(stream))["同类组"].startswith("'")


def excluded_scope(*alerts, included=1):
    return {
        "policy": "exclude_failed_validation_jobs",
        "original_validation_count": len(alerts) + included,
        "included_validation_count": included,
        "excluded_validation_count": len(alerts),
        "excluded_by_tier": {"main": len(alerts), "supplementary": 0, "unknown": 0},
        "excluded_alerts": [
            {
                "alert_id": alert,
                "validation_tier": "main",
                "round_id": "round-fixed",
                "job_id": "job-excluded",
                "run_id": "run-excluded",
                "status": "failed",
                "error_code": "provider_error",
                "exclusion_reason": "整条任务运行失败",
            }
            for alert in alerts
        ],
    }


def test_filtered_effect_report_labels_scope_and_exports_exclusions_privately(tmp_path):
    values = data(row("kept", recommended_handling="ignore"))
    values["metadata"]["evaluation_scope"] = excluded_scope("=excluded")
    report = build_report(values)
    paths = write_report(report, tmp_path)
    markdown = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "原第二批范围 2 条" in markdown
    assert "排除整条任务运行失败 1 条" in markdown
    assert "纳入效果评估 1 条" in markdown
    assert "不是全批成功率" in markdown
    assert "| 完成 |" not in markdown
    assert "| 运行失败 |" not in markdown
    assert "经验使用/纳入范围" in markdown
    assert "全范围分母仍保留失败" not in markdown
    excluded = tmp_path / "excluded_alerts.csv"
    assert excluded in paths
    assert excluded.read_bytes().startswith(b"\xef\xbb\xbf")
    assert stat.S_IMODE(excluded.stat().st_mode) == 0o600
    with excluded.open(encoding="utf-8-sig", newline="") as stream:
        record = next(csv.DictReader(stream))
    assert record["告警ID"] == "'=excluded"
    assert record["排除原因"] == "整条任务运行失败"
    assert report["metadata"]["evaluation_scope"]["excluded_by_tier"]["main"] == 1


def test_filtered_scope_keeps_semantic_failures_and_other_batch_history():
    completed = row("kept", group="g1")
    completed["semantic_review"] = {"status": "failed", "mode": "apply"}
    values = data(
        completed,
        learning=[row("old", batch="learning", status="failed")],
        followups=[row("later", status="failed")],
    )
    values["members"] = [
        {"alert_id": "removed", "batch": "validation", "group_id": "excluded-only"}
    ]
    values["metadata"]["evaluation_scope"] = excluded_scope("removed")
    report = build_report(values)
    assert report["metrics"]["all"]["selected"] == 1
    assert report["metrics"]["all"]["semantic_review_status_counts"] == {"failed": 1}
    assert any(
        item["code"] == "semantic_review_failed" for item in report["differences"]
    )
    assert {item["alert_id"] for item in report["alerts"]} == {"old", "kept"}
    assert report["followups"][0]["alert_id"] == "later"
    excluded_group = next(
        item for item in report["groups"] if item["group_id"] == "excluded-only"
    )
    assert excluded_group["validation_count"] == 0
    assert all(item["group_id"] != "excluded-only" for item in report["review"])


def test_filtered_report_with_zero_exclusions_still_records_explicit_policy(tmp_path):
    values = data(row("kept"))
    values["metadata"]["evaluation_scope"] = excluded_scope()
    report = build_report(values)
    write_report(report, tmp_path)
    with (tmp_path / "excluded_alerts.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        assert list(csv.DictReader(stream)) == []
    assert "排除整条任务运行失败 0 条" in (tmp_path / "REPORT.md").read_text(
        encoding="utf-8"
    )


def test_all_failed_effect_scope_has_no_computable_success_or_usage_rate(tmp_path):
    values = data()
    values["metadata"]["evaluation_scope"] = excluded_scope("only-failure", included=0)
    report = build_report(values)
    assert report["metrics"]["all"]["selected"] == 0
    assert report["metrics"]["all"]["memory_used_all_selected"]["rate"] is None
    assert report["metrics"]["all"]["historical_handling_agreement"]["rate"] is None
    assert report["review"] == []
    write_report(report, tmp_path)
    markdown = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "纳入效果评估 0 条" in markdown
    assert "无法计算" in markdown
    assert "100.0%" not in markdown


def test_combined_failure_exclusions_are_counted_once_and_partial_unknown_remain(
    tmp_path,
):
    partial = row("partial")
    partial["semantic_review"] = {"mode": "apply", "status": "partial"}
    unknown = row("unknown")
    values = data(partial, unknown)
    scope = excluded_scope("job-only", "semantic-only", "both", included=2)
    scope.update(
        policy="exclude_failed_validation_stages",
        exclude_failed_jobs=True,
        exclude_semantic_review_failed=True,
        excluded_by_reason={"job_failed": 2, "semantic_review_failed": 2},
        excluded_overlap_count=1,
    )
    scope["excluded_alerts"][0]["semantic_review_status"] = None
    scope["excluded_alerts"][1].update(
        status="completed",
        semantic_review_status="failed",
        exclusion_reason="语义核对失败",
    )
    scope["excluded_alerts"][2].update(
        semantic_review_status="failed",
        exclusion_reason="整条任务运行失败；语义核对失败",
    )
    values["metadata"]["evaluation_scope"] = scope
    report = build_report(values)
    assert report["metrics"]["all"]["selected"] == 2
    assert report["metrics"]["all"]["semantic_review_status_counts"] == {
        "partial": 1,
        "unknown": 1,
    }
    write_report(report, tmp_path)
    markdown = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "原第二批范围 5 条" in markdown
    assert "去重排除 3 条" in markdown
    assert "整条任务运行失败 2 条" in markdown
    assert "语义核对失败 2 条" in markdown
    assert "同时符合两项 1 条" in markdown
    assert "partial" in markdown
    assert "unknown" in markdown
    assert "成功任务中的语义核对失败或部分完成仍保留" not in markdown
    with (tmp_path / "excluded_alerts.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert rows[1]["语义核对状态"] == "failed"
    assert rows[2]["排除原因"] == "整条任务运行失败；语义核对失败"


def test_semantic_only_exclusion_does_not_claim_all_job_failures_were_removed(tmp_path):
    values = data(row("failed-job-kept", status="failed"), row("kept"))
    scope = excluded_scope("semantic-only", included=2)
    scope.update(
        policy="exclude_failed_validation_stages",
        exclude_failed_jobs=False,
        exclude_semantic_review_failed=True,
        excluded_by_reason={"job_failed": 0, "semantic_review_failed": 1},
        excluded_overlap_count=0,
    )
    scope["excluded_alerts"][0].update(
        status="completed",
        semantic_review_status="failed",
        exclusion_reason="语义核对失败",
    )
    values["metadata"]["evaluation_scope"] = scope
    report = build_report(values)
    assert report["metrics"]["all"]["status_counts"]["failed"] == 1
    write_report(report, tmp_path)
    markdown = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "所选排除条件：语义核对失败" in markdown
    assert "纳入范围已经排除整条失败任务" not in markdown
    assert "成功任务中的语义核对失败或部分完成仍保留" not in markdown
