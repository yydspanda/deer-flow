"""Fixed-round report math and private exports, independent of today's latest Run."""

import csv
import hashlib
import json
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from soc_agent.contracts.processing_jobs import ProcessingJobStatus


def _interval_ms(start, end):
    if not start or not end:
        return None
    try:
        delta = datetime.fromisoformat(str(end).replace("Z", "+00:00")) - datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        return round(delta.total_seconds() * 1000) if delta.total_seconds() >= 0 else None
    except (ValueError, TypeError):
        return None


def export_draft_report(experiment_id: str, jobs: list[dict], output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"report already exists: {output_dir}")
    if any(not ProcessingJobStatus(job["status"]).is_terminal for job in jobs):
        raise ValueError("起草任务尚未结束，请等待后再导出固定报告")
    rows = []
    for job in jobs:
        tokens = (job.get("usage") or {}).get("total_tokens")
        rows.append(
            {
                **job,
                "total_tokens": tokens if type(tokens) is int and tokens >= 0 else None,
                "queue_wait_ms": _interval_ms(job.get("created_at"), job.get("started_at")),
                "processing_wall_ms": _interval_ms(job.get("started_at"), job.get("completed_at")),
                "total_wall_ms": _interval_ms(job.get("created_at"), job.get("completed_at")),
                "potentially_unmeasured_attempts": (job.get("attempt_count") or 0) > 1 or job.get("provider_call_count") is None,
            }
        )
    metrics = {
        "job_count": len(rows),
        "status_counts": dict(Counter(job["status"] for job in rows)),
        "known_total_tokens": sum(job["total_tokens"] or 0 for job in rows),
        "unknown_usage_jobs": sum(job["total_tokens"] is None for job in rows),
        "known_provider_calls": sum(job.get("provider_call_count") or 0 for job in rows),
        "potentially_unmeasured_attempt_jobs": sum(job["potentially_unmeasured_attempts"] for job in rows),
    }
    report = {
        "schema_version": "soc.corpus_draft_report.v1",
        "experiment_id": experiment_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "scope": "experiment_background_drafting_only",
        "measurement_scope": "saved_generation_only",
        "metrics": metrics,
        "jobs": rows,
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".draft-report-", dir=output_dir.parent) as temporary:
        directory = Path(temporary)
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        _write_csv(
            directory / "draft-jobs.csv",
            (
                "job_id",
                "candidate_id",
                "candidate_revision",
                "operator_verdict",
                "status",
                "version",
                "attempt_count",
                "model_name",
                "prompt_version",
                "prompt_hash",
                "response_hash",
                "provider_call_count",
                "total_tokens",
                "potentially_unmeasured_attempts",
                "queue_wait_ms",
                "processing_wall_ms",
                "total_wall_ms",
                "draft_version",
                "error_code",
                "error_message",
            ),
            rows,
        )
        (directory / "REPORT.md").write_text(
            f"# 经验后台起草报告\n\n实验：`{experiment_id}`\n\n"
            f"共 {metrics['job_count']} 个任务；已保存模型调用 {metrics['known_provider_calls']} 次，已知 Token {metrics['known_total_tokens']}。\n"
            f"{metrics['unknown_usage_jobs']} 个任务没有完整用量，{metrics['potentially_unmeasured_attempt_jobs']} 个任务存在未计量尝试的可能。\n\n"
            "仅统计本实验后台起草，不含告警研判，也不含网页同步起草。不能与告警数相加。\n"
            "计量只来自保存的生成结果；失败、恢复前未保存的调用可能未计入，未知不等于零。\n"
            "排队为创建到首次领取，处理墙钟为首次领取到结束（包含重试与恢复），不是纯模型延迟。缺少时间或用量保留为空。\n"
            "结果为工作草稿；生成完成不代表经验已审核或启用。逐任务与来源见 `draft-jobs.csv`，完整用量见 `report.json`。\n",
            encoding="utf-8",
        )
        manifest = {"schema_version": "soc.corpus_draft_report_manifest.v1", "experiment_id": experiment_id, "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        for path in directory.iterdir():
            path.chmod(0o600)
        if output_dir.exists():
            raise FileExistsError(f"output directory appeared during export: {output_dir}")
        directory.rename(output_dir)


def _ratio(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}


def summarize_results(rows: list[dict]) -> dict:
    return {key: _summarize([row for row in rows if ("explore" if row.get("validation_tier") == "supplementary" else "reuse") == key]) for key in ("reuse", "explore")}


def result_breakdowns(rows: list[dict]) -> dict:
    rules, memories, phases = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        key = row.get("detection_key") or row.get("rule_code") or row.get("group_id") or "unclassified"
        rules[key].append(row)
        uses = row.get("summary", {}).get("memory_uses", [])
        for memory_id in sorted({use["memory_id"] for use in uses}):
            # Count each associated alert once; other Memory uses cannot be
            # attributed to this Memory's direct/reference totals.
            memories[memory_id].append({**row, "summary": {**row.get("summary", {}), "memory_uses": [use for use in uses if use["memory_id"] == memory_id]}})
        for step in row.get("summary", {}).get("phase_timings", []):
            phases[step["phase"]].append(step)
    return {
        "rules": [
            {"detection_key": key, "rule_codes": sorted({row["rule_code"] for row in values if row.get("rule_code")}), "group_count": len({row.get("group_id") for row in values}), "metrics": summarize_results(values)}
            for key, values in sorted(rules.items())
        ],
        "memories": [
            {
                "memory_id": key,
                "actual_rule_codes": sorted({row["rule_code"] for row in values if row.get("rule_code")}),
                "metrics": summarize_results(values),
                "cost_attribution": "associated_runs_not_incremental",
                "causal_attribution_allowed": False,
            }
            for key, values in sorted(memories.items())
        ],
        "phases": {
            key: {
                "status_counts": dict(Counter(step["status"] for step in steps)),
                "known_duration_count": sum(step.get("duration_ms") is not None for step in steps),
                "total_ms": sum(step.get("duration_ms") or 0 for step in steps),
                "max_ms": max((step["duration_ms"] for step in steps if step.get("duration_ms") is not None), default=None),
            }
            for key, steps in sorted(phases.items())
        },
    }


def _summarize(rows):
    completed = [row for row in rows if row["status"] == "completed"]
    eligible = [row for row in completed if not row.get("snapshot_changed_during_run")]
    scorable = [row for row in eligible if row.get("label", {}).get("scorable") and row.get("summary", {}).get("recommended_handling") in {"ignore", "transfer"}]
    label_transfers = [row for row in scorable if row["label"]["expected_handling"] == "transfer"]
    measured = [measurement for row in rows for measurement in (row.get("attempt_measurements") or ([row.get("summary", {}).get("measurements", {})] if row["status"] == "completed" or row.get("run_id") else []))]
    known_tokens = [item["total_tokens"] for item in measured if isinstance(item.get("total_tokens"), int)]
    return {
        "selected": len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "completed": len(completed),
        "snapshot_affected": len(completed) - len(eligible),
        "historical_handling_agreement": _ratio(sum(row["summary"]["recommended_handling"] == row["label"]["expected_handling"] for row in scorable), len(scorable)),
        "wrong_ignore_against_history": _ratio(sum(row["summary"]["recommended_handling"] == "ignore" for row in label_transfers), len(label_transfers)),
        "transfer_detection_against_history": _ratio(sum(row["summary"]["recommended_handling"] == "transfer" for row in label_transfers), len(label_transfers)),
        "ignore_rate": _ratio(sum(row.get("summary", {}).get("recommended_handling") == "ignore" for row in eligible), len(eligible)),
        "memory_direct_reuse": _ratio(sum(any(use.get("directive_applied") for use in row.get("summary", {}).get("memory_uses", [])) for row in eligible), len(eligible)),
        "memory_reference": _ratio(sum(any(not use.get("directive_applied") for use in row.get("summary", {}).get("memory_uses", [])) for row in eligible), len(eligible)),
        "tokens": {
            "known_total": sum(known_tokens),
            "known_runs": len(known_tokens),
            "unavailable_runs": sum(item.get("total_tokens") is None and item.get("usage_measurement_status") != "not_applicable" for item in measured),
            "measurement_counts": dict(Counter(item.get("usage_measurement_status") or "unavailable" for item in measured)),
        },
        "provider_calls": sum(item.get("provider_call_count") or 0 for item in measured),
        "provider_calls_unknown_runs": sum(item.get("provider_call_count") is None for item in measured),
        "measured_run_attempts": len(measured),
        "summed_run_duration_ms": sum(row.get("summary", {}).get("total_duration_ms") or 0 for row in completed),
        "candidate_count": len({row["candidate_id"] for row in rows if row.get("candidate_id")}),
    }


def compare_reports(before: dict, after: dict) -> dict:
    if before.get("source_identity") != after.get("source_identity"):
        raise ValueError("cannot compare different dataset identities")
    if before["round"]["experiment_id"] != after["round"]["experiment_id"]:
        raise ValueError("comparison requires the same experiment")
    before_batch = (before["round"].get("selection") or {}).get("batch")
    after_batch = (after["round"].get("selection") or {}).get("batch")
    if before_batch not in {"learning", "validation"} or after_batch != before_batch:
        raise ValueError("只能比较同一批次的轮次报告；缺少批次信息时，请重新导出报告。")
    old_items = {row["alert_id"]: row for row in before["rows"]}
    new_items = {row["alert_id"]: row for row in after["rows"]}
    transitions = [
        {
            "alert_id": alert_id,
            "before_status": old_items[alert_id]["status"],
            "after_status": new_items[alert_id]["status"],
            "before_run_id": old_items[alert_id].get("run_id"),
            "after_run_id": new_items[alert_id].get("run_id"),
        }
        for alert_id in sorted(old_items.keys() & new_items.keys())
    ]
    left = {row["alert_id"]: row for row in before["rows"] if row["status"] == "completed" and not row.get("snapshot_changed_during_run")}
    right = {row["alert_id"]: row for row in after["rows"] if row["status"] == "completed" and not row.get("snapshot_changed_during_run")}
    paired = sorted(left.keys() & right.keys())
    changes = [
        {"alert_id": alert_id, "before_run_id": left[alert_id].get("run_id"), "after_run_id": right[alert_id].get("run_id"), "before": left[alert_id].get("summary", {}), "after": right[alert_id].get("summary", {})} for alert_id in paired
    ]
    return {
        "schema_version": "soc.corpus_round_comparison.v1",
        "before_round_id": before["round"]["round_id"],
        "after_round_id": after["round"]["round_id"],
        "paired_alerts": len(paired),
        "item_transitions": transitions,
        "recovered_failures": sum(row["before_status"] == "failed" and row["after_status"] == "completed" for row in transitions),
        "before_only": len(left.keys() - right.keys()),
        "after_only": len(right.keys() - left.keys()),
        "changed_handling": sum(row["before"].get("recommended_handling") != row["after"].get("recommended_handling") for row in changes),
        "before_metrics": summarize_results([left[key] for key in paired]),
        "after_metrics": summarize_results([right[key] for key in paired]),
        "causal_attribution_allowed": False,
        "interpretation": "同一批告警的前后运行对照，不自动把模型波动或其他配置变化归因于经验。",
        "rows": changes,
    }


def _cell(value):
    result = "" if value is None else str(value)
    return "'" + result if result.lstrip().startswith(("=", "+", "-", "@")) else result


def _write_csv(path, columns, rows):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in columns})


def export_report(report: dict, output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"report already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    report = {**report, "metrics": summarize_results(report["rows"]), "breakdowns": result_breakdowns(report["rows"])}
    with tempfile.TemporaryDirectory(prefix=".corpus-report-", dir=output_dir.parent) as temporary:
        directory = Path(temporary)
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        columns = (
            "alert_id",
            "rule_code",
            "group_id",
            "validation_tier",
            "status",
            "run_id",
            "candidate_id",
            "base_verdict",
            "effective_verdict",
            "recommended_handling",
            "expected_handling",
            "scorable",
            "memory_ids",
            "total_tokens",
            "usage_measurement_status",
            "total_duration_ms",
            "initial_queue_wait_ms",
            "processing_wall_ms",
            "total_wall_ms",
            "timing_status",
            "error_code",
        )
        with (directory / "alerts.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in report["rows"]:
                summary, label = row.get("summary", {}), row.get("label", {})
                values = {**row, **summary, **label, **summary.get("measurements", {}), **row.get("job_timing", {}), "memory_ids": ",".join(use["memory_id"] for use in summary.get("memory_uses", []))}
                writer.writerow({key: _cell(values.get(key)) for key in columns})
        _write_csv(
            directory / "memory-usage.csv",
            ("alert_id", "run_id", "rule_code", "group_id", "validation_tier", "memory_id", "memory_version", "effect", "directive_applied", "recommended_handling", "snapshot_changed_during_run"),
            ({**row, **use, "recommended_handling": row.get("summary", {}).get("recommended_handling")} for row in report["rows"] for use in row.get("summary", {}).get("memory_uses", [])),
        )
        _write_csv(
            directory / "failures.csv",
            ("alert_id", "run_id", "job_id", "rule_code", "group_id", "validation_tier", "status", "attempt_count", "error_code", "error_message"),
            (row for row in report["rows"] if row["status"] == "failed"),
        )
        (directory / "REPORT.md").write_text(render_report(report), encoding="utf-8")
        manifest = {"schema_version": "soc.corpus_report_manifest.v1", "round_id": report["round"]["round_id"], "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        for path in directory.iterdir():
            path.chmod(0o600)
        if output_dir.exists():
            raise FileExistsError(f"output directory appeared during export: {output_dir}")
        directory.rename(output_dir)


def render_report(report: dict) -> str:
    lines = ["# 两批经验验证报告", "", f"轮次：`{report['round']['round_id']}`", "", "结果按本轮固定 Run 保存，不使用告警后来重跑的结果。历史忽略/转交标签用于处置对照，不等于安全真值。", ""]
    timing = report.get("progress", {}).get("timing") or {}
    if timing.get("running_ms") is not None:
        lines += [
            f"允许调度期间累计 {timing['running_ms'] / 1000:.1f} 秒，操作暂停累计 {timing['paused_ms'] / 1000:.1f} 秒。",
            "这是批次状态的墙钟计时，包含调度开启时的等待和服务离线时间，不是模型耗时。暂停只停止领取新任务，已领取任务可继续完成。",
            "",
        ]
    batch = (report["round"].get("selection") or {}).get("batch")
    sections = (
        (("reuse", "第一批：沉淀经验", "用于积累同类样本并提炼待审核经验，生成候选不代表经验已确认。"),)
        if batch == "learning"
        else (
            ("reuse", "验证经验复用", "第一批中有同类样本，用来验证审核后的经验对后续告警是否有用，不保证每条都命中经验。"),
            ("explore", "其他告警测试", "同类只有1～5条，或事件时间无法确认；单独查看研判结果，不混入经验复用效果统计。"),
        )
    )
    for key, title, description in sections:
        metrics = report["metrics"][key]
        lines += [f"## {title}", "", description, "", f"选择 {metrics['selected']} 条，完成 {metrics['completed']} 条，受运行中快照变更影响 {metrics['snapshot_affected']} 条。", "", "| 指标 | 分子 / 分母 | 比例 |", "|---|---:|---:|"]
        for field, label in (
            ("historical_handling_agreement", "历史处置一致率"),
            ("wrong_ignore_against_history", "错误忽略（历史转交作参照）"),
            ("transfer_detection_against_history", "转交检出（历史转交作参照）"),
            ("ignore_rate", "忽略比例"),
            ("memory_direct_reuse", "直接复用经验"),
            ("memory_reference", "经验供模型参考"),
        ):
            value = metrics[field]
            percent = f"{value['rate']:.1%}" if value["rate"] is not None else "暂无可比较样本"
            lines.append(f"| {label} | {value['numerator']} / {value['denominator']} | {percent} |")
        tokens = metrics["tokens"]
        lines += [
            "",
            f"{metrics['measured_run_attempts']} 次持久化运行（含失败重试），模型调用已记录 {metrics['provider_calls']} 次；"
            f"Token 已知合计 {tokens['known_total']}，{tokens['unavailable_runs']} 次未获用量。用量来源详见 JSON，不把缺失当作零。",
            f"运行耗时累计 {metrics['summed_run_duration_ms']} ms，这是各条耗时之和，不是并发批次的墙钟时间。",
            "",
        ]
    lines += [
        "## 复核入口",
        "",
        "逐条结果、规则编码、候选、经验编号、前后结论及用量见 `alerts.csv`；固定配置和经验快照见 `report.json`。",
        "`memory-usage.csv` 每个实际使用关系一行，`failures.csv` 单列失败与尝试次数；CSV 可直接用表格软件打开。",
        "`alerts.csv` 的 initial_queue_wait_ms 只计首次领取前、本任务已纳入额度且允许调度的等待；processing_wall_ms 从首次领取到结束，含重试/恢复；"
        "total_wall_ms 从任务创建到结束，包含未纳入额度及暂停时间。三者不能简单相加，也不能替代逐阶段模型耗时；缺少时间记录保留为空。",
        "`report.json → breakdowns` 按检测规则、实际使用的经验和运行阶段汇总。一条经验可关联多个规则；相似经验参与研判不代表单独决定结果，关联运行的消耗不能相加当作经验额外成本。",
        "失败、未运行、快照变更、无有效标签的样本不会被计为判断正确；其他告警测试单独统计。",
        "",
    ]
    return "\n".join(lines)
