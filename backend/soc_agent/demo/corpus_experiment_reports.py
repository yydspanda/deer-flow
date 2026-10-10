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


def summarize_rebalance_results(rows: list[dict]) -> dict:
    """Applicability is a saved Runtime fact; navigation groups are not coverage."""
    result = {}
    for tier in ("main", "supplementary"):
        selected = [row for row in rows if (row.get("validation_tier") or "main") == tier]
        eligible = [row for row in selected if row["status"] == "completed" and not row.get("snapshot_changed_during_run")]
        known = [row for row in eligible if any(use.get("directive_applied") or use.get("applicability_report", {}).get("status") == "applicable" for use in row.get("summary", {}).get("memory_uses", []))]
        known_ids = {id(row) for row in known}

        def handling_counts(values):
            counts = Counter(row.get("summary", {}).get("recommended_handling") for row in values)
            return {"count": len(values), "ignore": counts["ignore"], "transfer": counts["transfer"], "undetermined": len(values) - counts["ignore"] - counts["transfer"]}

        result[tier] = {
            **_summarize(selected, include_history=False),
            "completed_coverage": _ratio(len(eligible), len(selected)),
            "applicable_memory_coverage": _ratio(len(known), len(eligible)),
            "transfer_rate": _ratio(sum(row.get("summary", {}).get("recommended_handling") == "transfer" for row in eligible), len(eligible)),
            "known_experience": handling_counts(known),
            "no_history_experience": handling_counts([row for row in eligible if id(row) not in known_ids]),
            "decision_unusable": sum(row.get("summary", {}).get("decision_usable") is not True for row in eligible),
        }
    return result


def result_breakdowns(rows: list[dict], *, rebalanced: bool = False) -> dict:
    summarize = summarize_rebalance_results if rebalanced else summarize_results
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
            {"detection_key": key, "rule_codes": sorted({row["rule_code"] for row in values if row.get("rule_code")}), "group_count": len({row.get("group_id") for row in values}), "metrics": summarize(values)}
            for key, values in sorted(rules.items())
        ],
        "memories": [
            {
                "memory_id": key,
                "actual_rule_codes": sorted({row["rule_code"] for row in values if row.get("rule_code")}),
                "metrics": summarize(values),
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


def _summarize(rows, *, include_history=True):
    completed = [row for row in rows if row["status"] == "completed"]
    eligible = [row for row in completed if not row.get("snapshot_changed_during_run")]
    scorable = [row for row in eligible if row.get("label", {}).get("scorable") and row.get("summary", {}).get("recommended_handling") in {"ignore", "transfer"}] if include_history else []
    label_transfers = [row for row in scorable if row["label"]["expected_handling"] == "transfer"]
    measured = [measurement for row in rows for measurement in (row.get("attempt_measurements") or ([row.get("summary", {}).get("measurements", {})] if row["status"] == "completed" or row.get("run_id") else []))]
    known_tokens = [item["total_tokens"] for item in measured if isinstance(item.get("total_tokens"), int)]
    return {
        "selected": len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "completed": len(completed),
        "snapshot_affected": len(completed) - len(eligible),
        **(
            {
                "historical_handling_agreement": _ratio(sum(row["summary"]["recommended_handling"] == row["label"]["expected_handling"] for row in scorable), len(scorable)),
                "wrong_ignore_against_history": _ratio(sum(row["summary"]["recommended_handling"] == "ignore" for row in label_transfers), len(label_transfers)),
                "transfer_detection_against_history": _ratio(sum(row["summary"]["recommended_handling"] == "transfer" for row in label_transfers), len(label_transfers)),
            }
            if include_history
            else {}
        ),
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
    summarize = summarize_rebalance_results if _uses_applicability_metrics(before) or _uses_applicability_metrics(after) else summarize_results
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
        "before_metrics": summarize([left[key] for key in paired]),
        "after_metrics": summarize([right[key] for key in paired]),
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


def _is_rebalanced(report: dict) -> bool:
    return bool(report.get("experiment", {}).get("rebalance"))


def _uses_applicability_metrics(report: dict) -> bool:
    return _is_rebalanced(report) or report.get("report_mode") == "memory_applicability"


def _complete_validation_stage(report: dict) -> bool:
    rows, scope, round_ = report["rows"], report.get("stage_scope", {}), report["round"]
    selection = round_.get("selection") or {}
    return (
        scope.get("stage_complete") is True
        and scope.get("batch_total", 0) > 0
        and len(rows) == len({row["alert_id"] for row in rows}) == scope["batch_total"]
        and all(row["status"] == "completed" and not row.get("snapshot_changed_during_run") for row in rows)
        and selection.get("batch") == "validation"
        and selection.get("scope") == "all"
        and round_.get("purpose") != "facts"
    )


def export_report(report: dict, output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"report already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    applicability = _uses_applicability_metrics(report)
    if applicability:
        report = {**report, "rows": [{key: value for key, value in row.items() if key != "label"} for row in report["rows"]]}
        complete = _complete_validation_stage(report)
        report.update(report_scope="stage" if complete else "round_diagnostic", stage_conclusion_allowed=complete)
    summarize = summarize_rebalance_results if applicability else summarize_results
    report = {**report, "metrics": summarize(report["rows"]), "breakdowns": result_breakdowns(report["rows"], rebalanced=applicability)}
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
        if applicability:
            columns = tuple(column for column in columns if column not in {"expected_handling", "scorable"})
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


def _render_applicability_report(report: dict) -> str:
    complete = report.get("stage_conclusion_allowed") is True
    rebalanced = _is_rebalanced(report)
    lines = [
        "# 经验适用与分流：验证阶段结论" if complete else "# 经验适用与分流：仅诊断",
        "",
        f"实验：`{report['round']['experiment_id']}`；轮次：`{report['round']['round_id']}`。",
        "",
        (
            "按真实核心行为重新分配学习与验证样本，保留既有数据库和审核经验；本轮固定结果单独统计，不与旧批次拼接。"
            if rebalanced
            else "在原主验证范围内，按既有静态分组和事件时间将较早的 80%（向上取整，至少保留一条验证）分配给第一批，补充验证保持不变。"
            "静态分组只用于固定抽样；运行时仍按语义核对后的真实核心行为积累经验，不保证每种细行为都已有足够学习样本。本轮结果不与旧批次拼接。"
        ),
        "",
    ]
    if not complete:
        lines += [
            "当前导出未满足完整验证阶段条件，仅用于诊断，不形成阶段结论。请等待覆盖全部第二批的轮次完成，且运行期间经验快照有效后再导出。",
            "",
        ]
        for tier, label in (("main", "主验证"), ("supplementary", "补充验证")):
            metrics = report["metrics"][tier]
            lines.append(f"{label}：本轮选择 {metrics['selected']} 条，完成 {metrics['completed']} 条，快照受影响 {metrics['snapshot_affected']} 条。")
        lines += ["", "状态、固定 Run、失败及计量详见 `report.json`、`alerts.csv`、`failures.csv`。", ""]
        return "\n".join(lines)

    lines += [
        f"第二批共 {report['stage_scope']['batch_total']} 条，已完整完成。主验证与补充验证分别统计。",
        f"本阶段第一批 {report['stage_scope'].get('learning_total', '未记录')} 条，第二批 {report['stage_scope'].get('validation_total', report['stage_scope']['batch_total'])} 条。",
        "覆盖和忽略比例不是准确率；系统建议不代表已经执行处置，也不证明所有忽略判断均正确。",
        "",
    ]
    if rebalanced:
        lines += [
            f"继承已审核经验 {report['experiment']['rebalance'].get('inherited_memory_count', '未记录')} 条，复用既有独立学习运行 {report['experiment']['rebalance'].get('imported_learning_run_count', '未记录')} 条。",
            "",
        ]
    for tier, label in (("main", "主验证"), ("supplementary", "补充验证")):
        metrics = report["metrics"][tier]
        lines += [f"## {label}", "", f"样本 {metrics['selected']} 条，完成 {metrics['completed']} 条，结论不可用或可用性未知 {metrics['decision_unusable']} 条。", "", "| 指标 | 分子 / 分母 | 比例 |", "|---|---:|---:|"]
        for key, title in (
            ("applicable_memory_coverage", "适用经验覆盖"),
            ("memory_direct_reuse", "直接复用经验"),
            ("memory_reference", "经验供模型参考"),
            ("ignore_rate", "建议忽略"),
            ("transfer_rate", "建议转交"),
        ):
            ratio = metrics[key]
            rate = f"{ratio['rate']:.1%}" if ratio["rate"] is not None else "无样本"
            lines.append(f"| {title} | {ratio['numerator']} / {ratio['denominator']} | {rate} |")
        lines += ["", "| 经验范围 | 样本数 | 建议忽略 | 建议转交 | 未确定处置 |", "|---|---:|---:|---:|---:|"]
        for key, title in (("known_experience", "有适用经验"), ("no_history_experience", "无历史经验")):
            counts = metrics[key]
            lines.append(f"| {title} | {counts['count']} | {counts['ignore']} | {counts['transfer']} | {counts['undetermined']} |")
        lines += [
            "",
            f"已记录模型调用 {metrics['provider_calls']} 次，{metrics['provider_calls_unknown_runs']} 次运行调用数未知；已知 Token {metrics['tokens']['known_total']}，{metrics['tokens']['unavailable_runs']} 次运行用量缺失。",
            "",
        ]
    lines += [
        "适用经验以本轮保存的 applicability=applicable 或已直接复用记录为准；有同类学习样本不等于已命中适用经验。",
        "“无历史经验”在此表示本次未命中适用经验；可能仍有部分相似经验供参考。直接复用与参考记录可能同时出现，二者不能相加作为覆盖率。",
        "主验证保留了同一抽样组的学习样本；这不保证运行时细行为相同或命中经验。补充验证的来源、样本不足等原因见 membership_reason。两个范围独立统计，样本分配可按冻结计划追溯。",
        "逐条固定结果、经验使用关系和配置快照见 `alerts.csv`、`memory-usage.csv` 与 `report.json`。此报告不使用原始忽略/转交标签评分。",
        "",
    ]
    return "\n".join(lines)


def render_report(report: dict) -> str:
    if _uses_applicability_metrics(report):
        return _render_applicability_report(report)
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
