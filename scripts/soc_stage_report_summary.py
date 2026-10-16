#!/usr/bin/env python3
"""Summarize one saved SOC report using only the Python standard library.

No Runtime imports, database access, network requests, or model calls.
The source report is never modified. Output contains aggregates, not alert details.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

SCHEMAS = {"soc.corpus_round_report.v1", "soc.corpus_merged_stage_report.v1"}
BOOL_OPTIONS = (
    "refresh_normalization",
    "tenant_policy_enabled",
    "tenant_policy_advisor_enabled",
    "tenant_policy_signal_providers_enabled",
)


def _percent(numerator, denominator):
    return round(100 * numerator / denominator, 4) if denominator else None


def _known_duration(value):
    return type(value) is int and value >= 0


def _one_status(items, key, value, allowed):
    found = [item.get("status") for item in items if item.get(key) == value]
    return found[0] if len(found) == 1 and found[0] in allowed else "unknown"


def _metrics(rows):
    included = [
        r
        for r in rows
        if r.get("status") == "completed"
        and r.get("snapshot_changed_during_run") is False
    ]
    summaries = [r.get("summary") or {} for r in included]
    used, unused, unknown = [], [], []
    direct, direct_unknown = 0, 0
    for summary in summaries:
        uses = summary.get("memory_uses")
        if not isinstance(uses, list) or any(not isinstance(u, dict) for u in uses):
            unknown.append(summary)
            direct_unknown += 1
        else:
            (used if uses else unused).append(summary)
            if any(u.get("directive_applied") is True for u in uses):
                direct += 1
            elif any(u.get("directive_applied") is not False for u in uses):
                direct_unknown += 1
    handling = Counter(s.get("recommended_handling") for s in summaries)
    durations = [
        s["total_duration_ms"]
        for s in summaries
        if _known_duration(s.get("total_duration_ms"))
    ]
    unused_transfer = sum(s.get("recommended_handling") == "transfer" for s in unused)
    return {
        "count": len(rows),
        "included_completed_stable": len(included),
        "excluded_incomplete_or_snapshot_changed": len(rows) - len(included),
        "handling": {
            "ignore": handling["ignore"],
            "transfer": handling["transfer"],
            "undetermined": len(included) - handling["ignore"] - handling["transfer"],
        },
        "ignore_percent": _percent(handling["ignore"], len(included)),
        "memory_used_including_reference": len(used),
        "ignored_with_memory": sum(
            s.get("recommended_handling") == "ignore" for s in used
        ),
        "memory_unused": len(unused),
        "memory_usage_unknown": len(unknown),
        "memory_unused_transfer": unused_transfer,
        "memory_unused_transfer_percent": _percent(unused_transfer, len(unused)),
        "memory_direct_reuse": direct,
        "memory_direct_reuse_unknown": direct_unknown,
        "decision_unusable_or_unknown": sum(
            s.get("decision_usable") is not True for s in summaries
        ),
        "final_run_duration": {
            "known_count": len(durations),
            "missing_count": len(included) - len(durations),
            "sum_ms": sum(durations),
            "mean_seconds": round(sum(durations) / len(durations) / 1000, 4)
            if durations
            else None,
        },
    }


def _round_summary(round_):
    options = round_.get("options") or {}
    mode = options.get("normalization_review_mode")
    snapshot = round_.get("memory_snapshot")
    return {
        "round_id": round_.get("round_id"),
        "experiment_id": round_.get("experiment_id"),
        "options": {
            "normalization_review_mode": mode
            if mode in {"off", "shadow", "apply"}
            else None,
            **{
                k: options.get(k) if type(options.get(k)) is bool else None
                for k in BOOL_OPTIONS
            },
        },
        "frozen_memory_count": len(snapshot) if isinstance(snapshot, list) else None,
    }


def summarize_report(report):
    if report.get("schema_version") not in SCHEMAS:
        raise ValueError("unsupported report schema; use the round export report.json")
    rows = report.get("rows")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("report rows must be a list of records")
    ids = [r.get("alert_id") for r in rows]
    if any(not isinstance(i, str) or not i.strip() for i in ids):
        raise ValueError("missing or invalid member identity")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate report members; refusing double counting")
    scope = report.get("stage_scope") or {}
    merged = report["schema_version"] == "soc.corpus_merged_stage_report.v1"
    rounds = (
        [s["round"] for s in report.get("source_reports", [])]
        if merged
        else [report["round"]]
    )
    status = dict(Counter(r.get("status") or "unknown" for r in rows))
    issues = []

    def require(condition, reason):
        if not condition:
            issues.append(reason)

    require(scope.get("stage_complete") is True, "stage_not_marked_complete")
    require(
        len(rows) == scope.get("batch_total") and bool(rows),
        "stage_member_count_mismatch",
    )
    require(
        len(rows) == scope.get("validation_total"), "not_full_validation_membership"
    )
    require(status == {"completed": len(rows)}, "unfinished_or_failed_members")
    require(
        all(r.get("snapshot_changed_during_run") is False for r in rows),
        "snapshot_changed_or_unrecorded",
    )
    require(
        all(r.get("validation_tier") in {"main", "supplementary"} for r in rows),
        "unknown_coarse_group",
    )
    if merged:
        require(bool(rounds), "missing_source_rounds")
        merge = report.get("merge") or {}
        checks = merge.get("checks") or {}
        require(
            checks.get("all_business_jobs_completed") is True
            and checks.get("no_snapshot_drift") is True,
            "merge_checks_not_passed",
        )
        require(merge.get("merged_count") == len(rows), "merge_member_count_mismatch")
    else:
        round_ = rounds[0]
        selection = round_.get("selection") or {}
        progress = report.get("progress") or {}
        require(
            report.get("stage_conclusion_allowed") is True
            and report.get("report_scope") == "stage",
            "report_is_not_a_stage_conclusion",
        )
        require(
            selection.get("batch") == "validation"
            and selection.get("scope") == "all"
            and round_.get("purpose") != "facts",
            "round_is_not_full_validation",
        )
        require(progress.get("selected_count") == len(rows), "selected_count_mismatch")
        require(
            progress.get("completed_count") == status.get("completed", 0),
            "completed_count_mismatch",
        )
        require(progress.get("counts") == status, "progress_status_mismatch")
        require(progress.get("active_count") == 0, "active_count_not_zero_or_unknown")
    metrics = {"all": _metrics(rows)}
    for tier in ("main", "supplementary"):
        metrics[tier] = _metrics([r for r in rows if r.get("validation_tier") == tier])
    included = [
        r.get("summary") or {}
        for r in rows
        if r.get("status") == "completed"
        and r.get("snapshot_changed_during_run") is False
    ]
    phase_states = {"success", "failed", "skipped", "running", "pending"}
    policy_states = {
        "observed",
        "no_match",
        "shadow_matched",
        "reinforced",
        "overridden",
        "conflicted",
        "applied",
        "unchanged",
        "disabled",
        "no_input",
        "failed",
        "blocked",
        "skipped",
    }
    paths = {"memory", "model_analysis", "tenant_policy"}
    return {
        "schema_version": "soc.stage_report_summary.v1",
        "source_rounds": [_round_summary(r) for r in rounds],
        "scope": {
            "learning_planned_count": scope.get("learning_total"),
            "validation_planned_count": scope.get("validation_total"),
            "report_member_count": len(rows),
            "job_status_counts": status,
            "stage_conclusion_ready": not issues,
            "issues": issues,
        },
        "group_labels": {
            "main": "粗分组：预估有适用历史经验",
            "supplementary": "粗分组：预估无历史经验",
        },
        "metrics": metrics,
        "final_step_counts": {
            "semantic_review": dict(
                Counter(
                    _one_status(
                        s.get("phase_timings") or [],
                        "phase",
                        "normalization_assist",
                        phase_states,
                    )
                    for s in included
                )
            ),
            "tenant_policy": dict(
                Counter(
                    _one_status(
                        s.get("decision_stages") or [],
                        "stage",
                        "tenant_policy",
                        policy_states,
                    )
                    for s in included
                )
            ),
            "processing_path": dict(
                Counter(
                    s.get("processing_path")
                    if s.get("processing_path") in paths
                    else "unknown"
                    for s in included
                )
            ),
        },
        "basis": [
            "指标仅统计completed且明确无快照漂移的最终结果；分母为included_completed_stable。",
            "实际使用经验含参考，按memory_uses计数；缺字段为未知，不算未使用。",
            "平均耗时按最终Run计量，不含被替换旧尝试和初始排队；缺失不补零。",
            "第一批数量是计划数，不能单凭此报告证明全部完成或审核。",
            "忽略/转交是系统建议；四项全开时不能把全部忽略归因于历史经验。",
        ],
    }


def read_summary(directory):
    path = directory / "report.json"
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    report = json.loads(payload)
    del payload
    verified = False
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("files", {}).get("report.json") != digest:
            raise ValueError("report checksum does not match manifest.json")
        if "round" in report and manifest.get("round_id") != report["round"].get(
            "round_id"
        ):
            raise ValueError("manifest round identity mismatch")
        verified = True
    result = summarize_report(report)
    result["source"] = {
        "report_sha256": digest,
        "report_schema": report["schema_version"],
        "report_manifest_verified": verified,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report_dir", type=Path, help="已导出且包含report.json的固定报告目录"
    )
    parser.add_argument(
        "--output", type=Path, help="另外保存精简JSON；拒绝覆盖已有文件"
    )
    args = parser.parse_args()
    try:
        result = read_summary(args.report_dir.expanduser())
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = args.output.expanduser()
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
        print(text, end="")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
