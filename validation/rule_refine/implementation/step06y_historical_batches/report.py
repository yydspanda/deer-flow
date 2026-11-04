"""Deterministic summaries of separate historical batch results; no generation."""

import json
from collections import Counter


VALID = {"passed", "manual_review"}


def identities(context):
    alerts, logs = set(), set()
    for sample in context["samples"]:
        alert = sample["alert_id"]
        alerts.add(alert)
        if len(sample["raw_logs"]) != len(sample["log_refs"]):
            raise ValueError("log_lineage_mismatch")
        for ref in sample["log_refs"]:
            logs.add(
                (
                    alert,
                    ref["source_row_position_zero_based"],
                    ref["hit_index"],
                    ref["raw_log_index"],
                )
            )
    return alerts, logs


def _coverage(contexts):
    alerts, logs = set(), set()
    for context in contexts:
        batch_alerts, batch_logs = identities(context)
        alerts.update(batch_alerts)
        logs.update(batch_logs)
    return {"unique_alerts": len(alerts), "unique_logs": len(logs)}


def summarize(plan, outcomes, *, model, limits, output_dir, stop_reason=None):
    """Union source identities, never add batch alert counts or filter matches."""
    contexts = plan["batches"]
    if len(contexts) != len(outcomes):
        raise ValueError("batch_outcome_count_mismatch")
    pairs = list(zip(contexts, outcomes))
    prompt_usage = [
        (outcome.get("usage") or {}).get("prompt_tokens")
        for outcome in outcomes
        if outcome.get("sdk_request_calls") != 0
    ]
    missing_prompt_usage = sum(type(value) is not int for value in prompt_usage)
    known_calls = sum(outcome.get("sdk_request_calls") or 0 for outcome in outcomes)
    unknown_calls = sum(
        outcome.get("sdk_request_calls") is None for outcome in outcomes
    )
    valid = [context for context, outcome in pairs if outcome["status"] in VALID]
    attempted = [
        context for context, outcome in pairs if outcome.get("sdk_request_calls") == 1
    ]
    received = [
        context
        for context, outcome in pairs
        if outcome.get("response_received") is True
    ]
    unknown = [
        context
        for context, outcome in pairs
        if outcome.get("sdk_request_calls") is None
    ]
    all_valid = bool(contexts) and len(valid) == len(contexts)
    if all_valid:
        status = "passed"
    elif any(row["status"] == "interrupted" for row in outcomes):
        status = "interrupted"
    elif any(row["status"] == "blocked" for row in outcomes):
        status = "blocked"
    elif any(row["status"] == "failed" for row in outcomes):
        status = "failed"
    else:
        status = "not_started"
    return {
        "schema_version": "sec_know.historical_rule_batches.v1",
        "check": "rule_refine_historical_batches",
        "status": status,
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "requested_model": model,
        "limits": limits,
        "source_coverage": plan["coverage"],
        "planned_coverage": _coverage(contexts),
        "request_attempted_coverage": _coverage(attempted),
        "response_received_coverage": _coverage(received),
        "valid_result_coverage": _coverage(valid),
        "unknown_request_coverage": _coverage(unknown),
        "all_planned_batches_valid": all_valid,
        "all_eligible_logs_planned": plan["coverage"].get(
            "all_eligible_logs_planned", False
        ),
        "batch_counts": {
            "planned": len(contexts),
            "execution_started": sum(
                row.get("execution_started", False) for row in outcomes
            ),
            "execution_finished": sum(
                row.get("execution_finished", False) for row in outcomes
            ),
            "valid_results": len(valid),
            "by_status": dict(Counter(row["status"] for row in outcomes)),
            "valid_analysis_statuses": dict(
                Counter(
                    row.get("analysis_status", "unknown")
                    for row in outcomes
                    if row["status"] in VALID
                )
            ),
        },
        "planned_sdk_requests": len(contexts),
        "sdk_request_calls": None if unknown_calls else known_calls,
        "known_sdk_request_calls": known_calls,
        "unknown_request_batches": unknown_calls,
        "model_call_basis": "sdk_create_invocations_not_upstream_attempts",
        "planned_input_bytes": sum(row["input_bytes"] for row in outcomes),
        "planned_estimated_input_tokens": sum(
            row.get("estimated_input_tokens", 0) for row in outcomes
        ),
        "reported_prompt_tokens": (
            None
            if missing_prompt_usage
            else sum(value for value in prompt_usage if type(value) is int)
        ),
        "prompt_usage_missing_batches": missing_prompt_usage,
        "input_size_basis": "serialized_messages_utf8_json_bytes",
        "stop_reason": stop_reason,
        "automatic_retries": False,
        "resume_supported": False,
        "new_invocation_repeats_all_planned_batches": True,
        "model_synthesis_calls": 0,
        "business_alerts_submitted": 0,
        "business_database_calls": 0,
        "zeus_callback_calls": 0,
        "live_counter_modified": False,
        "redis_usage": "existing_model_capacity_and_circuit_only",
        "automatic_apply": False,
        "formal_recommendation_submitted": False,
        "engine_compatibility": "not_verified",
        "application_code_updated": False,
        "output_dir": str(output_dir),
        "batches": outcomes,
    }


def report_text(plan, summary, analyses):
    rule = plan["batches"][0]["rule"]
    coverage = summary["source_coverage"]
    planned = summary["planned_coverage"]
    received = summary["response_received_coverage"]
    valid = summary["valid_result_coverage"]
    labels = {
        "proposal": "有条件建议",
        "insufficient_evidence": "证据不足",
        "no_change": "暂无修改建议",
        "manual_review": "需人工核对条件",
        "analysis_failed": "分析失败",
        "not_started": "未开始",
    }
    lines = [
        "# 单规则历史样本分批分析报告",
        "",
        f"规则：{rule['rule_code']} / {rule['rule_name']}",
        f"请求模型：{summary['requested_model']}；执行状态：{summary['status']}。",
        "本次按用户假定1M上下文的80%安排输入；token采用保守代理估算，"
        "并非内网容量实测或精确分词结果。",
        f"计划输入估算token合计：{summary['planned_estimated_input_tokens']}；"
        f"已调用请求的模型实际输入用量：{summary['reported_prompt_tokens']}"
        "（未知表示部分请求未返回usage；不是补零）。",
        "",
        f"扫描文件全部 {coverage['inspected_rows']} / {coverage['file_rows']} 行；"
        f"计划覆盖 {planned['unique_alerts']} 条独立告警、{planned['unique_logs']} 条完整日志，"
        f"分 {summary['batch_counts']['planned']} 批分析。",
        f"已收到模型回答的范围：{received['unique_alerts']} 条独立告警、{received['unique_logs']} 条日志；"
        f"回答和配置检查通过的范围：{valid['unique_alerts']} 条独立告警、{valid['unique_logs']} 条日志。",
        "同一告警的多条日志可能分到不同批次，上述告警数按原ID去重；计划覆盖不等于全部请求成功。",
        "",
        "来源为历史导出文件和CSV配置，未测量实时突增。以下按批保留模型问题判断、建议及前提；"
        "未额外调用模型综合，不合并不同批次的过滤条件。",
        "",
        "| 批次 | 独立告警 / 日志 | 执行检查 | 分析结论 | SDK请求 |",
        "|---|---:|---|---|---:|",
    ]
    for context, outcome in zip(plan["batches"], summary["batches"]):
        batch_cov = _coverage([context])
        analysis_status = outcome.get("analysis_status", "not_started")
        calls = outcome.get("sdk_request_calls")
        lines.append(
            f"| {outcome['batch_index']} | {batch_cov['unique_alerts']} / {batch_cov['unique_logs']} | "
            f"{outcome['status']} | {labels.get(analysis_status, analysis_status)} | "
            f"{calls if calls is not None else '未知'} |"
        )
    for outcome, analysis in zip(summary["batches"], analyses):
        index = outcome["batch_index"]
        lines += ["", f"## 第 {index} 批", ""]
        if outcome["status"] == "not_started":
            lines.append("本批未执行，没有模型结论。")
            continue
        if outcome["status"] not in VALID:
            lines.append(
                "本批执行或配置检查未通过；保存的回答仅供排查，不能作为可用修改建议。"
            )
        if analysis:
            lines += [
                "",
                "问题：" + analysis.get("issue", "未取得可用问题描述。"),
                "",
                "建议及前提：" + analysis.get("advice", "未取得可用建议。"),
            ]
            if analysis.get("filter") is not None:
                lines += [
                    "",
                    "本批条件：",
                    "",
                    "```json",
                    json.dumps(analysis["filter"], ensure_ascii=False, indent=2),
                    "```",
                ]
            counts = analysis.get("evaluation", {}).get("counts")
            if counts is not None:
                lines += [
                    "",
                    "本批原样日志试算：" + json.dumps(counts, ensure_ascii=False),
                ]
            if analysis.get("status") == "manual_review":
                lines += ["", "本地试算器未完成这些条件的匹配验证，请人工核对。"]
        else:
            lines.append("未取得可供报告引用的分析回答。")
        lines += [
            "",
            f"原始回答、引用样本、条件试算及请求来源见 [batch-{index:03d}](batch-{index:03d}/)。",
        ]
    lines += [
        "",
        "## 统计口径与后续处理",
        "",
        "不同批次可能分别给出建议、证据不足或无需修改；这些结论均按各自材料保留。"
        "日志匹配数不相加为规则效果，不代表误报率或线上降量率。业务正常性及ZEUS引擎兼容性仍需核对。",
        "未提交建议、未修改线上规则。详细源数据缺失、冲突及无效项见 coverage.json。",
        "本工具不自动重试，也不支持断点续跑；重新执行脚本会建立新目录并重新分析所有计划批次。",
    ]
    if summary.get("stop_reason"):
        lines += [
            "",
            "停止原因：" + summary["stop_reason"] + "；后续未开始批次保持未执行。",
        ]
    return "\n".join(lines) + "\n"
