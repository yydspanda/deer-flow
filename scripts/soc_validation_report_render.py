"""Pure fixed-snapshot report arithmetic and private, spreadsheet-safe exports.

Only consumes the scalar/allowlisted collector contract. No Runtime imports,
database access, matching, inference, network requests or mutations are performed.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = "soc.validation_effect_report.v1"
USAGE_LABELS = {
    "directive": "经验指令参与",
    "reference": "仅作研判参考",
    "none": "未使用经验",
    "unknown": "无法确认",
}
TIER_LABELS = {
    "main": "验证经验复用",
    "supplementary": "其他测试告警",
    "unknown": "范围待核实",
    "all": "第二批整体",
}
ROW_FIELDS = (
    "batch",
    "validation_tier",
    "alert_id",
    "group_id",
    "rule_code",
    "rule_name",
    "source_type",
    "category",
    "detection_key",
    "round_id",
    "run_id",
    "job_id",
    "status",
    "attempt_count",
    "error_code",
    "as_of_status",
)
SUMMARY_FIELDS = (
    "base_verdict",
    "effective_verdict",
    "recommended_handling",
    "processing_path",
    "base_model_evaluated",
    "decision_usable",
    "model_name",
    "prompt_version",
    "total_duration_ms",
)
MEASUREMENT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "provider_call_count",
    "usage_measurement_status",
)
OPTION_LABELS = {
    "normalization_review_mode": "语义核对模式",
    "refresh_normalization": "刷新规范化",
    "tenant_policy_enabled": "租户策略",
    "tenant_policy_advisor_enabled": "策略辅助",
    "tenant_policy_signal_providers_enabled": "策略信号提供方",
}
DIFFERENCE_LABELS = {
    "historical_transfer_now_ignore": "历史应转交，本次建议忽略；核对风险判断",
    "directive_ignore": "经验指令参与且建议忽略；核对适用边界",
    "memory_conflict": "存在经验指令冲突记录；核对最终处理",
    "reviewed_memory_verdict_differs": "最终结论与有历史证据的所用经验审核结论不同；核对适用边界",
    "snapshot_source_memory_unused": "快照包含同组来源经验但本次未使用；同组不保证适用",
    "rejected_candidate_clear_result": "第一批存在放弃候选，本次给出明确判断；放弃原因待人工补充",
    "foreign_group_memory": "使用了已证实来自其他组的经验；核对适用范围",
    "group_verdict_divergence": "同组内或两批之间存在不同判断；差异可能合理",
    "semantic_review_failed": "语义核对存在失败记录；核对降级影响",
    "semantic_review_partial": "语义核对记录为部分完成；核对未完成内容，不直接判错",
    "execution_failed": "告警运行失败；未计入研判质量",
    "snapshot_drift": "运行期间快照变化；未计入研判质量",
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _number(value):
    return (
        value
        if type(value) in (int, float) and math.isfinite(value) and value >= 0
        else None
    )


def _count(values):
    return dict(
        sorted(
            Counter(
                str(value) if value is not None else "unknown" for value in values
            ).items()
        )
    )


def _ratio(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _memory_key(memory):
    return f"{memory.get('memory_id') or 'unknown'}@{memory.get('memory_version') if memory.get('memory_version') is not None else 'unknown'}"


def _usage(uses):
    if not isinstance(uses, list):
        return "unknown"
    if any(
        isinstance(use, dict) and use.get("directive_applied") is True for use in uses
    ):
        return "directive"
    if any(
        not isinstance(use, dict) or type(use.get("directive_applied")) is not bool
        for use in uses
    ):
        return "unknown"
    return "reference" if uses else "none"


def _normalize_row(row):
    summary = _dict(row.get("summary"))
    uses = summary.get("memory_uses")
    filtered_uses = (
        [
            {
                key: use.get(key)
                for key in (
                    "memory_id",
                    "memory_version",
                    "directive_applied",
                    "effect",
                    "applicability_status",
                    "reason_codes",
                    "reason_codes_truncated",
                )
            }
            for use in uses
            if isinstance(use, dict)
        ]
        if isinstance(uses, list)
        else None
    )
    measurements = _dict(summary.get("measurements"))
    label = _dict(row.get("label"))
    semantic = _dict(row.get("semantic_review"))
    return {
        **{key: row.get(key) for key in ROW_FIELDS},
        **{key: summary.get(key) for key in SUMMARY_FIELDS},
        **{
            key: _number(measurements.get(key))
            if key != "usage_measurement_status"
            else measurements.get(key)
            for key in MEASUREMENT_FIELDS
        },
        "options": _dict(row.get("options")),
        "snapshot_changed_during_run": row.get("snapshot_changed_during_run") is True,
        "quality_eligible": row.get("status") == "completed"
        and row.get("snapshot_changed_during_run") is not True
        and row.get("as_of_status") != "unknown",
        "memory_usage": _usage(uses),
        "memory_usage_label": USAGE_LABELS[_usage(uses)],
        "memory_uses": filtered_uses,
        "memory_ids": sorted({_memory_key(use) for use in filtered_uses or []}),
        "direct_model_skip": summary.get("processing_path") == "memory"
        and summary.get("base_model_evaluated") is False,
        "historical_label_scorable": label.get("scorable") is True
        and label.get("expected_handling") in {"ignore", "transfer"},
        "historical_expected_handling": label.get("expected_handling")
        if label.get("scorable") is True
        else None,
        "historical_label_reason": label.get("reason"),
        "historical_label_source": label.get("source"),
        "historical_label_method": label.get("method"),
        "historical_label_temporal_status": label.get("temporal_status"),
        "semantic_review_mode": semantic.get("mode"),
        "semantic_review_status": semantic.get("status"),
        "semantic_review_change_count": _number(semantic.get("change_count")),
        "semantic_review_observation_change_count": _number(
            semantic.get("observation_change_count")
        ),
        "semantic_review_issue_count": _number(semantic.get("issue_count")),
        "phase_timings": [
            {key: phase.get(key) for key in ("phase", "status", "duration_ms")}
            for phase in _list(summary.get("phase_timings"))
            if isinstance(phase, dict)
        ],
    }


def _percentile(values, fraction):
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return round(
        values[lower] + (values[upper] - values[lower]) * (position - lower), 3
    )


def _summarize(rows):
    eligible = [row for row in rows if row["quality_eligible"]]
    completed = [row for row in rows if row["status"] == "completed"]
    scored = [
        row
        for row in eligible
        if row["historical_label_scorable"]
        and row["recommended_handling"] in {"ignore", "transfer"}
    ]
    transfers = [
        row for row in scored if row["historical_expected_handling"] == "transfer"
    ]
    duration = sorted(
        value
        for row in completed
        if (value := _number(row["total_duration_ms"])) is not None
    )
    usage_counts = {
        key: sum(row["memory_usage"] == key for row in eligible) for key in USAGE_LABELS
    }
    return {
        "selected": len(rows),
        "status_counts": _count(row["status"] for row in rows),
        "completed": len(completed),
        "quality_eligible": len(eligible),
        "snapshot_affected": len(completed) - len(eligible),
        "memory_usage": usage_counts,
        "memory_used_all_selected": _ratio(
            usage_counts["directive"] + usage_counts["reference"], len(rows)
        ),
        "memory_used_quality_eligible": _ratio(
            usage_counts["directive"] + usage_counts["reference"], len(eligible)
        ),
        "direct_model_skip": _ratio(
            sum(row["direct_model_skip"] for row in eligible), len(eligible)
        ),
        "historical_handling_agreement": _ratio(
            sum(
                row["recommended_handling"] == row["historical_expected_handling"]
                for row in scored
            ),
            len(scored),
        ),
        "historical_transfer_now_ignore": _ratio(
            sum(row["recommended_handling"] == "ignore" for row in transfers),
            len(transfers),
        ),
        "historical_label_unscored": len(eligible) - len(scored),
        "base_verdict_counts": _count(row["base_verdict"] for row in eligible),
        "effective_verdict_counts": _count(
            row["effective_verdict"] for row in eligible
        ),
        "handling_counts": _count(row["recommended_handling"] for row in eligible),
        "semantic_review_status_counts": _count(
            row["semantic_review_status"] for row in rows
        ),
        "semantic_review_mode_counts": _count(
            row["semantic_review_mode"] for row in rows
        ),
        "tokens": {
            key: _measurement(rows, key)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        },
        "provider_calls": _measurement(rows, "provider_call_count"),
        "usage_measurement_status_counts": _count(
            row["usage_measurement_status"] for row in rows
        ),
        "duration_ms": {
            "known_count": len(duration),
            "unknown_count": len(completed) - len(duration),
            "scope": "completed_saved_runs",
            "sum": sum(duration) if duration else None,
            "p50": _percentile(duration, 0.5),
            "p95": _percentile(duration, 0.95),
            "method": "linear_interpolation",
        },
    }


def _measurement(rows, key):
    values = [value for row in rows if (value := _number(row.get(key))) is not None]
    return {
        "known_sum": sum(values) if values else None,
        "known_count": len(values),
        "unknown_count": len(rows) - len(values),
    }


def _candidate(candidate):
    return {
        **{
            key: candidate.get(key)
            for key in (
                "candidate_id",
                "current_status",
                "as_of_status",
                "as_of_evidence",
                "created_at",
                "reviewed_at",
                "current_reviewed_verdict",
                "as_of_reviewed_verdict",
                "unresolved_observation_count",
                "reason_truncated",
            )
        },
        **{
            key: sorted(set(_list(candidate.get(key))))
            for key in (
                "group_ids",
                "source_alert_ids",
                "source_run_ids",
                "memory_ids",
                "snapshot_memory_ids",
            )
        },
        "reason_recorded": bool(candidate.get("reason")),
        "reason_evidence": "provided" if candidate.get("reason") else "not_extracted",
        "reason": candidate.get("reason") or "本次未提取具体原因；待人工核实补充",
    }


def _option_distribution(rows):
    counts = Counter(
        json.dumps(
            {key: row["options"].get(key) for key in OPTION_LABELS}, sort_keys=True
        )
        for row in rows
    )
    return [
        {"options": json.loads(options), "alert_count": count}
        for options, count in sorted(counts.items())
    ]


def build_report(data: dict, groups_per_batch: int = 20) -> dict:
    """Build deterministic facts and review leads, without inferring correctness."""
    if type(groups_per_batch) is not int or not 1 <= groups_per_batch <= 1000:
        raise ValueError("groups_per_batch must be between 1 and 1000")
    learning = [_normalize_row(row) for row in data.get("learning_rows", [])]
    validation = [_normalize_row(row) for row in data.get("validation_rows", [])]
    for rows in (learning, validation):
        identities = [row["alert_id"] for row in rows]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate primary alert identity")
        rows.sort(key=lambda row: (str(row["group_id"]), str(row["alert_id"])))
    candidates = sorted(
        (_candidate(item) for item in data.get("candidates", [])),
        key=lambda item: item["candidate_id"],
    )
    memories = _memory_rows(data.get("memories", []), validation)
    memory_map = {_memory_key(item): item for item in memories}
    learning_by_group, validation_by_group, candidates_by_group, supply_by_group = (
        defaultdict(list) for _ in range(4)
    )
    for row in learning:
        learning_by_group[row["group_id"]].append(row)
    for row in validation:
        validation_by_group[row["group_id"]].append(row)
    for candidate in candidates:
        for group_id in candidate["group_ids"]:
            candidates_by_group[group_id].append(candidate)
    for memory in memories:
        if memory["snapshot_included"]:
            for group_id in memory["source_group_ids"]:
                supply_by_group[group_id].append(memory)
    group_ids = {
        row.get("group_id")
        for row in [*data.get("members", []), *learning, *validation]
    }
    group_ids.update(
        group for candidate in candidates for group in candidate["group_ids"]
    )
    groups = []
    differences = []
    for group_id in sorted(group_ids, key=str):
        first = learning_by_group[group_id]
        second = validation_by_group[group_id]
        group_candidates = candidates_by_group[group_id]
        supply = supply_by_group[group_id]
        rejected_as_of = any(
            item["as_of_status"] == "rejected" for item in group_candidates
        )
        divergences = {
            row["effective_verdict"]
            for row in [*first, *second]
            if row["quality_eligible"] and row["effective_verdict"] is not None
        }
        group_differences = []
        for row in second:
            group_differences.extend(
                _differences(
                    row, supply, memory_map, rejected_as_of, len(divergences) > 1
                )
            )
        differences.extend(group_differences)
        groups.append(
            {
                "group_id": group_id,
                "rule_codes": sorted(
                    {row["rule_code"] for row in [*first, *second] if row["rule_code"]}
                ),
                "rule_names": sorted(
                    {row["rule_name"] for row in [*first, *second] if row["rule_name"]}
                ),
                "source_types": sorted(
                    {
                        row["source_type"]
                        for row in [*first, *second]
                        if row["source_type"]
                    }
                ),
                "categories": sorted(
                    {row["category"] for row in [*first, *second] if row["category"]}
                ),
                "validation_tiers": sorted({_tier(row) for row in second}),
                "learning_alert_ids": [row["alert_id"] for row in first],
                "validation_alert_ids": [row["alert_id"] for row in second],
                "learning_count": len(first),
                "validation_count": len(second),
                "learning_status_counts": _count(
                    row["as_of_status"] or row["status"] for row in first
                ),
                "learning_base_verdict_counts": _count(
                    row["base_verdict"] for row in first if row["quality_eligible"]
                ),
                "learning_effective_verdict_counts": _count(
                    row["effective_verdict"] for row in first if row["quality_eligible"]
                ),
                "validation_status_counts": _count(row["status"] for row in second),
                "validation_effective_verdict_counts": _count(
                    row["effective_verdict"]
                    for row in second
                    if row["quality_eligible"]
                ),
                "validation_handling_counts": _count(
                    row["recommended_handling"]
                    for row in second
                    if row["quality_eligible"]
                ),
                "candidate_ids": [item["candidate_id"] for item in group_candidates],
                "candidate_current_status_counts": _count(
                    item["current_status"] for item in group_candidates
                ),
                "candidate_as_of_status_counts": _count(
                    item["as_of_status"] for item in group_candidates
                ),
                "candidate_current_reviewed_verdict_counts": _count(
                    item["current_reviewed_verdict"] for item in group_candidates
                ),
                "candidate_as_of_reviewed_verdict_counts": _count(
                    item["as_of_reviewed_verdict"] for item in group_candidates
                ),
                "snapshot_memory_ids": sorted({_memory_key(item) for item in supply}),
                "actual_memory_ids": sorted(
                    {memory for row in second for memory in row["memory_ids"]}
                ),
                "validation_usage_counts": _count(
                    row["memory_usage"] for row in second if row["quality_eligible"]
                ),
                "difference_codes": sorted(
                    {item["code"] for item in group_differences}
                ),
                "difference_count": len(group_differences),
                "priority": min(
                    (item["priority"] for item in group_differences), default=4
                ),
                "snapshot_supply_usage": _ratio(
                    sum(
                        row["memory_usage"] in {"directive", "reference"}
                        for row in second
                        if row["quality_eligible"]
                    ),
                    len(second),
                )
                if supply
                else None,
            }
        )
    differences.sort(
        key=lambda item: (
            item["priority"],
            str(item["group_id"]),
            str(item["alert_id"]),
            item["code"],
        )
    )
    metrics = {
        tier: _summarize([row for row in validation if _tier(row) == tier])
        for tier in ("main", "supplementary", "unknown")
    }
    metrics["all"] = _summarize(validation)
    for tier, metric in metrics.items():
        group_set = {
            group["group_id"] for group in groups if group["snapshot_memory_ids"]
        }
        available = [
            row
            for row in validation
            if row["group_id"] in group_set and (tier == "all" or _tier(row) == tier)
        ]
        metric["same_source_snapshot_available"] = len(available)
        metric["usage_when_same_source_snapshot_available"] = _ratio(
            sum(
                row["quality_eligible"]
                and row["memory_usage"] in {"directive", "reference"}
                for row in available
            ),
            len(available),
        )
    return {
        "metadata": {
            **data.get("metadata", {}),
            "schema_version": SCHEMA,
            "groups_per_review_batch": groups_per_batch,
            "review_selection_seed": "soc-validation-review-v1",
            "causal_attribution_allowed": False,
            "cost_estimated": False,
            "measurement_scope": "selected_saved_runs_only",
        },
        "warnings": list(data.get("warnings", [])),
        "metrics": metrics,
        "learning_options_distribution": _option_distribution(learning),
        "alerts": [*learning, *validation],
        "groups": groups,
        "memories": memories,
        "candidates": candidates,
        "differences": differences,
        "review": _review_rows(groups, differences, groups_per_batch),
        "followups": [_normalize_row(row) for row in data.get("followup_rows", [])],
    }


def _tier(row):
    return (
        row.get("validation_tier")
        if row.get("validation_tier") in {"main", "supplementary"}
        else "unknown"
    )


def _memory_rows(memories, validation):
    records = {_memory_key(item): item for item in memories}
    use_rows = defaultdict(list)
    for row in validation:
        for key in row["memory_ids"]:
            use_rows[key].append(row)
        for use in row["memory_uses"] or []:
            records.setdefault(
                _memory_key(use),
                {
                    "memory_id": use["memory_id"],
                    "memory_version": use["memory_version"],
                },
            )
    result = []
    for key, item in sorted(records.items()):
        used = use_rows[key]
        eligible = [row for row in used if row["quality_eligible"]]
        classifications = [
            _usage([use for use in row["memory_uses"] or [] if _memory_key(use) == key])
            for row in eligible
        ]
        result.append(
            {
                **{
                    field: item.get(field)
                    for field in (
                        "memory_id",
                        "memory_version",
                        "source_candidate_id",
                        "current_version",
                        "current_status",
                        "as_of_status",
                        "reviewed_verdict",
                        "historical_verdict_known",
                    )
                },
                "source_group_ids": sorted(set(_list(item.get("source_group_ids")))),
                "source_groups_complete": item.get("source_groups_complete") is True,
                "snapshot_included": item.get("snapshot_included") is True,
                "validation_alert_count": len(used),
                "quality_eligible_alert_count": len(eligible),
                "directive_alert_count": classifications.count("directive"),
                "reference_only_alert_count": classifications.count("reference"),
                "usage_unknown_alert_count": classifications.count("unknown"),
                "alert_ids": [row["alert_id"] for row in used],
                "actual_group_ids": sorted({row["group_id"] for row in used}, key=str),
                "actual_tiers": sorted({_tier(row) for row in used}),
                "cost_attribution": "associated_runs_not_incremental",
                "causal_attribution_allowed": False,
            }
        )
    return result


def _differences(row, supply, memories, rejected_as_of, divergent):
    codes = []
    if row["status"] == "failed":
        codes.append((2, "execution_failed"))
    if row["snapshot_changed_during_run"]:
        codes.append((2, "snapshot_drift"))
    if row["semantic_review_status"] == "failed" or any(
        phase["phase"]
        in {"semantic_review", "normalization_assist", "apply_normalization"}
        and phase["status"] == "failed"
        for phase in row["phase_timings"]
    ):
        codes.append((2, "semantic_review_failed"))
    if row["semantic_review_status"] == "partial":
        codes.append((2, "semantic_review_partial"))
    if row["quality_eligible"]:
        if (
            row["historical_label_scorable"]
            and row["historical_expected_handling"] == "transfer"
            and row["recommended_handling"] == "ignore"
        ):
            codes.append((1, "historical_transfer_now_ignore"))
        if (
            row["memory_usage"] == "directive"
            and row["recommended_handling"] == "ignore"
        ):
            codes.append((1, "directive_ignore"))
        if any(use.get("effect") == "conflicted" for use in row["memory_uses"] or []):
            codes.append((1, "memory_conflict"))
        if supply and row["memory_usage"] == "none":
            codes.append((2, "snapshot_source_memory_unused"))
        if rejected_as_of and row["effective_verdict"] in {
            "true_positive",
            "false_positive",
        }:
            codes.append((2, "rejected_candidate_clear_result"))
        if divergent:
            codes.append((3, "group_verdict_divergence"))
        used_records = [memories[key] for key in row["memory_ids"] if key in memories]
        origins = [
            item
            for item in used_records
            if item["source_group_ids"] and item["source_groups_complete"]
        ]
        if any(row["group_id"] not in item["source_group_ids"] for item in origins):
            codes.append((2, "foreign_group_memory"))
        directive_keys = {
            _memory_key(use)
            for use in row["memory_uses"] or []
            if use.get("directive_applied") is True
        }
        known_verdicts = {
            item["reviewed_verdict"]
            for item in used_records
            if _memory_key(item) in directive_keys
            and item["historical_verdict_known"] is True
            and item["reviewed_verdict"] in {"true_positive", "false_positive"}
        }
        if row["effective_verdict"] in {"true_positive", "false_positive"} and any(
            verdict != row["effective_verdict"] for verdict in known_verdicts
        ):
            codes.append((1, "reviewed_memory_verdict_differs"))
    return [
        {
            "priority": priority,
            "code": code,
            "description": DIFFERENCE_LABELS[code],
            **{
                key: row.get(key)
                for key in (
                    "alert_id",
                    "group_id",
                    "round_id",
                    "run_id",
                    "validation_tier",
                    "effective_verdict",
                    "recommended_handling",
                    "memory_ids",
                    "historical_expected_handling",
                )
            },
            "classification": "待核查线索，不代表错误",
        }
        for priority, code in codes
    ]


def _review_rows(groups, differences, batch_size):
    flagged_alerts = defaultdict(list)
    for item in differences:
        if item["alert_id"] not in flagged_alerts[item["group_id"]]:
            flagged_alerts[item["group_id"]].append(item["alert_id"])
    selected = [
        (group, "risk_directed", ",".join(group["validation_tiers"]))
        for group in sorted(
            groups, key=lambda item: (item["priority"], str(item["group_id"]))
        )
        if group["validation_count"] and group["difference_codes"]
    ]
    chosen_random = set()
    for tier in ("main", "supplementary", "unknown"):
        candidates = [
            group
            for group in groups
            if tier in group["validation_tiers"]
            and not group["difference_codes"]
            and group["group_id"] not in chosen_random
        ]
        candidates.sort(
            key=lambda group: hashlib.sha256(
                f"soc-validation-review-v1|{tier}|{group['group_id']}".encode()
            ).hexdigest()
        )
        for group in candidates[:batch_size]:
            selected.append((group, "random_unflagged", tier))
            chosen_random.add(group["group_id"])
    return [
        {
            "review_batch": index // batch_size + 1,
            "selection_method": method,
            "sampling_stratum": stratum,
            "group_id": group["group_id"],
            "rule_names": group["rule_names"],
            "source_types": group["source_types"],
            "validation_count": group["validation_count"],
            "representative_alert_ids": list(
                dict.fromkeys(
                    [*flagged_alerts[group["group_id"]], *group["validation_alert_ids"]]
                )
            )[:3],
            "candidate_ids": group["candidate_ids"],
            "memory_ids": group["actual_memory_ids"],
            "difference_codes": group["difference_codes"],
            "review_judgement": "",
            "reason": "",
            "suggested_action": "",
            "reviewer": "",
            "reviewed_at": "",
        }
        for index, (group, method, stratum) in enumerate(selected)
    ]


ALERT_COLUMNS = [
    ("batch", "批次"),
    ("validation_tier", "第二批范围"),
    ("alert_id", "告警ID"),
    ("group_id", "同类组"),
    ("rule_code", "规则"),
    ("rule_name", "规则名称"),
    ("source_type", "来源"),
    ("category", "告警类别"),
    ("round_id", "固定轮次"),
    ("run_id", "运行ID"),
    ("status", "任务状态"),
    ("as_of_status", "第二批开始时首批任务状态"),
    ("attempt_count", "尝试次数"),
    ("error_code", "错误类型"),
    ("quality_eligible", "计入研判质量"),
    ("snapshot_changed_during_run", "运行中快照变化"),
    ("base_verdict", "原研判结论"),
    ("effective_verdict", "最终结论"),
    ("recommended_handling", "建议处置"),
    ("historical_expected_handling", "历史应处置"),
    ("historical_label_scorable", "历史标签可比较"),
    ("memory_usage_label", "经验使用方式"),
    ("memory_ids", "使用经验及版本"),
    ("direct_model_skip", "Memory路径跳过主模型"),
    ("input_tokens", "输入Token"),
    ("output_tokens", "输出Token"),
    ("total_tokens", "总Token"),
    ("usage_measurement_status", "计量状态"),
    ("provider_call_count", "已保存调用次数"),
    ("total_duration_ms", "耗时毫秒"),
    ("semantic_review_mode", "语义核对模式"),
    ("semantic_review_status", "语义核对状态"),
    ("semantic_review_change_count", "语义核对修改数"),
    ("semantic_review_observation_change_count", "语义核对观察修改数"),
    ("semantic_review_issue_count", "语义核对问题数"),
    ("options", "保存配置"),
]
CSV_COLUMNS = {
    "alerts": ALERT_COLUMNS,
    "followups": ALERT_COLUMNS,
    "groups": [
        ("group_id", "同类组"),
        ("rule_codes", "规则"),
        ("rule_names", "规则名称"),
        ("source_types", "来源"),
        ("categories", "告警类别"),
        ("validation_tiers", "第二批范围"),
        ("learning_count", "第一批告警数"),
        ("validation_count", "第二批告警数"),
        ("learning_status_counts", "第一批任务状态分布"),
        ("learning_base_verdict_counts", "第一批原研判分布"),
        ("learning_effective_verdict_counts", "第一批最终结论分布"),
        ("candidate_ids", "关联候选"),
        ("candidate_current_status_counts", "当前审核状态分布"),
        ("candidate_as_of_status_counts", "第二批开始时审核状态分布"),
        ("candidate_current_reviewed_verdict_counts", "当前审核结论分布"),
        ("candidate_as_of_reviewed_verdict_counts", "第二批开始时审核结论分布"),
        ("snapshot_memory_ids", "快照中同组来源经验"),
        ("actual_memory_ids", "第二批实际使用经验"),
        ("validation_status_counts", "第二批任务状态分布"),
        ("validation_effective_verdict_counts", "第二批结论分布"),
        ("validation_handling_counts", "第二批处置分布"),
        ("validation_usage_counts", "第二批经验使用分布"),
        ("difference_codes", "待抽查差异"),
        ("difference_count", "差异线索数"),
        ("snapshot_supply_usage", "同组经验供给范围使用数及分母"),
    ],
    "memories": [
        ("memory_id", "经验ID"),
        ("memory_version", "记录版本"),
        ("source_candidate_id", "来源候选"),
        ("source_group_ids", "来源同类组"),
        ("source_groups_complete", "来源同类组已完整核实"),
        ("snapshot_included", "纳入本次快照"),
        ("current_version", "当前版本"),
        ("current_status", "当前状态"),
        ("as_of_status", "开始时状态"),
        ("validation_alert_count", "关联第二批告警数"),
        ("quality_eligible_alert_count", "可评价告警数"),
        ("directive_alert_count", "指令参与告警数"),
        ("reference_only_alert_count", "仅参考告警数"),
        ("usage_unknown_alert_count", "使用方式未知告警数"),
        ("reviewed_verdict", "有历史证据的审核结论"),
        ("historical_verdict_known", "审核结论历史可证"),
        ("actual_group_ids", "实际使用组"),
        ("actual_tiers", "实际使用范围"),
        ("alert_ids", "关联告警"),
    ],
    "differences": [
        ("priority", "抽查优先级"),
        ("code", "差异代码"),
        ("description", "差异说明"),
        ("alert_id", "告警ID"),
        ("group_id", "同类组"),
        ("validation_tier", "第二批范围"),
        ("round_id", "固定轮次"),
        ("run_id", "运行ID"),
        ("effective_verdict", "最终结论"),
        ("recommended_handling", "建议处置"),
        ("historical_expected_handling", "历史应处置"),
        ("memory_ids", "使用经验"),
        ("classification", "说明"),
    ],
    "review": [
        ("review_batch", "抽查批次"),
        ("selection_method", "抽样方式"),
        ("sampling_stratum", "抽样分层"),
        ("group_id", "同类组"),
        ("rule_names", "规则名称"),
        ("source_types", "来源"),
        ("validation_count", "第二批告警数"),
        ("representative_alert_ids", "起步查看告警"),
        ("candidate_ids", "关联候选"),
        ("memory_ids", "实际使用经验"),
        ("difference_codes", "差异线索"),
        ("review_judgement", "抽查判断"),
        ("reason", "原因说明"),
        ("suggested_action", "后续处理建议"),
        ("reviewer", "审核人"),
        ("reviewed_at", "审核时间"),
    ],
    "candidates": [
        ("candidate_id", "候选ID"),
        ("group_ids", "关联同类组"),
        ("source_alert_ids", "来源告警"),
        ("source_run_ids", "来源运行"),
        ("current_status", "当前审核状态"),
        ("as_of_status", "第二批开始时状态"),
        ("as_of_evidence", "开始时状态证据"),
        ("created_at", "创建时间"),
        ("reviewed_at", "审核时间"),
        ("memory_ids", "形成经验"),
        ("snapshot_memory_ids", "本次快照中的来源经验"),
        ("reason_recorded", "本次提供具体原因"),
        ("reason", "放弃原因"),
        ("reason_truncated", "原因仅提取前1000字符"),
        ("current_reviewed_verdict", "当前审核结论"),
        ("as_of_reviewed_verdict", "第二批开始时审核结论"),
        ("unresolved_observation_count", "尚未关联来源观察数"),
    ],
}
EXCLUDED_COLUMNS = [
    ("alert_id", "告警ID"),
    ("validation_tier", "第二批范围"),
    ("round_id", "固定轮次"),
    ("job_id", "任务ID"),
    ("run_id", "运行ID"),
    ("status", "任务状态"),
    ("semantic_review_status", "语义核对状态"),
    ("error_code", "错误类型"),
    ("exclusion_reason", "排除原因"),
]


def _cell(value):
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = "" if value is None else str(value)
    text = "".join(
        character if character >= " " or character in "\t\r\n" else " "
        for character in text
    )
    if len(text) > 31_900:
        text = (
            text[:31_800]
            + "…【列表过长，完整关联见 alerts.csv / report.json；此单元格仅为预览】"
        )
    return (
        "'" + text
        if text.lstrip().startswith(("=", "+", "-", "@"))
        or text.startswith(("\t", "\r", "\n"))
        else text
    )


def _md(value):
    text = (
        html.escape(str(value if value is not None else "未知"), quote=True)
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return re.sub(r"([\\\[\]()!*_])", r"\\\1", text)


def _display_ratio(metric):
    return f"{metric['numerator']} / {metric['denominator']}" + (
        f"（{metric['rate']:.1%}）" if metric["rate"] is not None else "（无法计算）"
    )


def _evaluation_scope(report):
    scope = _dict(report["metadata"].get("evaluation_scope"))
    return scope or None


def _markdown(report):
    metadata = report["metadata"]
    scope = _evaluation_scope(report)
    scope_note = []
    if scope is not None:
        tier_counts = _dict(scope.get("excluded_by_tier"))
        exclude_jobs = (
            scope.get(
                "exclude_failed_jobs",
                scope.get("policy") == "exclude_failed_validation_jobs",
            )
            is True
        )
        exclude_semantic = scope.get("exclude_semantic_review_failed") is True
        reason_counts = _dict(scope.get("excluded_by_reason"))
        selected_conditions = []
        reason_lines = []
        if exclude_jobs:
            selected_conditions.append("整条任务运行失败")
            reason_lines.append(
                f"整条任务运行失败 {_md(reason_counts.get('job_failed', scope.get('excluded_validation_count') if not exclude_semantic else None))} 条"
            )
        if exclude_semantic:
            selected_conditions.append("语义核对失败")
            reason_lines.append(
                f"语义核对失败 {_md(reason_counts.get('semantic_review_failed'))} 条"
            )
        if exclude_jobs and exclude_semantic:
            reason_lines.append(
                f"同时符合两项 {_md(scope.get('excluded_overlap_count'))} 条，交集不重复计入排除总数"
            )
        exclusion_text = (
            "排除整条任务运行失败"
            if exclude_jobs and not exclude_semantic
            else "按所选失败条件去重排除"
        )
        scope_note = [
            "## 本次效果评估范围",
            "",
            f"原第二批范围 {_md(scope.get('original_validation_count'))} 条，{exclusion_text} {_md(scope.get('excluded_validation_count'))} 条，纳入效果评估 {_md(scope.get('included_validation_count'))} 条。",
            "这是按所选失败条件剔除后的效果报告，不是全批成功率；原始报告和历史记录不受影响。",
            "所选排除条件："
            + "、".join(selected_conditions)
            + "。分项计数："
            + "；".join(reason_lines)
            + "。",
            "排除数按原第二批范围："
            + "；".join(
                f"{TIER_LABELS[tier]} {_md(tier_counts.get(tier))} 条"
                for tier in ("main", "supplementary", "unknown")
            )
            + "。具体任务见 excluded_alerts.csv。",
            "语义核对仅按明确记录的 failed 状态排除；partial、unknown 和缺失状态不视为失败。第一批及后续运行历史不按此规则过滤。"
            if exclude_semantic
            else "仅排除整条任务状态为 failed 的第二批主轮次记录；成功任务中的语义核对失败或部分完成仍保留。第一批及后续运行历史不按此规则过滤。",
            "下文经验覆盖、使用、研判对照与抽查分母均指纳入效果评估范围，不代表未剔除的整批。",
            "",
        ]
    lines = [
        "# 第二批效果与分组抽查报告",
        "",
        f"实验：{_md(metadata.get('experiment_id'))}；固定第二批轮次：{_md(metadata.get('round_id'))}。",
        "",
        "本报告仅汇总已保存事实，不重跑告警，不重算经验匹配，不判断人工审核尚未确认的正确性。后续单条运行另列 followups.csv，不混入主报告。",
        "",
        *scope_note,
        "## 本次范围与保存配置",
        "",
        f"第二批轮次创建时间：{_md(metadata.get('round_created_at'))}；读取开始：{_md(metadata.get('read_started_at'))}；读取结束：{_md(metadata.get('read_finished_at'))}。",
        f"数据范围标识：{_md(metadata.get('scope_sha256'))}；数据库版本：{_md(json.dumps(metadata.get('source_schema_revision'), ensure_ascii=False))}。",
        f"固定语料索引 SHA256：{_md(_dict(_dict(metadata.get('source_identity')).get('index')).get('sha256'))}。索引缺失或不匹配时，相关历史标签保留未知。",
        "",
        "| 第二批保存配置 | 值 |",
        "|---|---|",
        *[
            f"| {label} | {_md(_dict(metadata.get('options')).get(key))} |"
            for key, label in OPTION_LABELS.items()
        ],
        "",
        "第一批基线按告警保存配置分组如下，未知值不补默认；不同配置如实并列，不宣称两批配置都一致：",
        "",
        "| 第一批保存配置 | 告警数 |",
        "|---|---:|",
        *[
            f"| {_md(json.dumps(item['options'], ensure_ascii=False, sort_keys=True))} | {item['alert_count']} |"
            for item in report["learning_options_distribution"]
        ],
        "",
        "## 第二批效果评估总览" if scope is not None else "## 第二批总览",
        "",
        "| 范围 | 纳入效果评估 | 可评价 | 经验使用/纳入范围 | 历史处置一致/可比较 | 耗时 P50/P95（毫秒；有效条数） |"
        if scope is not None
        else "| 范围 | 纳入告警 | 完成 | 可评价 | 运行失败 | 经验使用/全范围 | 历史处置一致/可比较 | 耗时 P50/P95（毫秒；有效条数） |",
        "|---|---:|---:|---|---|---|"
        if scope is not None
        else "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for tier in ("main", "supplementary", "unknown", "all"):
        metric = report["metrics"][tier]
        if not metric["selected"] and tier != "all":
            continue
        duration = metric["duration_ms"]
        if scope is not None:
            lines.append(
                f"| {TIER_LABELS[tier]} | {metric['selected']} | {metric['quality_eligible']} | {_display_ratio(metric['memory_used_all_selected'])} | {_display_ratio(metric['historical_handling_agreement'])} | {_md(duration['p50'])} / {_md(duration['p95'])}；{duration['known_count']} 条 |"
            )
        else:
            lines.append(
                f"| {TIER_LABELS[tier]} | {metric['selected']} | {metric['completed']} | {metric['quality_eligible']} | {metric['status_counts'].get('failed', 0)} | {_display_ratio(metric['memory_used_all_selected'])} | {_display_ratio(metric['historical_handling_agreement'])} | {_md(duration['p50'])} / {_md(duration['p95'])}；{duration['known_count']} 条 |"
            )
    lines.extend(
        [
            "",
            "## 经验使用与已保存消耗",
            "",
            "| 范围 | 指令参与 | 仅参考 | 未使用 | 无法确认 | 跳过主模型/可评价 | 已知总 Token / 未知条数 |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for tier in ("main", "supplementary", "unknown", "all"):
        metric = report["metrics"][tier]
        if not metric["selected"] and tier != "all":
            continue
        usage = metric["memory_usage"]
        tokens = metric["tokens"]["total_tokens"]
        lines.append(
            f"| {TIER_LABELS[tier]} | {usage['directive']} | {usage['reference']} | {usage['none']} | {usage['unknown']} | {_display_ratio(metric['direct_model_skip'])} | {_md(tokens['known_sum'])} / {tokens['unknown_count']} 条 |"
        )
    lines.extend(["", "## 语义核对及经验准备情况", ""])
    for tier in ("main", "supplementary", "unknown"):
        metric = report["metrics"][tier]
        if metric["selected"]:
            semantic = json.dumps(
                metric["semantic_review_status_counts"],
                ensure_ascii=False,
                sort_keys=True,
            )
            lines.append(
                f"- {TIER_LABELS[tier]}：语义核对状态 {_md(semantic)}；快照存在同组来源经验的范围 {metric['same_source_snapshot_available']} 条，其中实际使用经验 {_display_ratio(metric['usage_when_same_source_snapshot_available'])}。"
            )
    current = _count(item["current_status"] for item in report["candidates"])
    historical = _count(item["as_of_status"] for item in report["candidates"])
    lines.extend(
        [
            f"- 关联候选去重共 {len(report['candidates'])} 条；当前审核状态 {_md(json.dumps(current, ensure_ascii=False))}；第二批开始时状态 {_md(json.dumps(historical, ensure_ascii=False))}。候选可关联多个组。",
            "- 语义核对修改条目数见 alerts.csv，需与模式和状态一起看；shadow 的修改建议不代表已应用，更不代表修改正确。",
        ]
    )
    lines.extend(
        [
            "",
            "## 统计口径",
            "",
            "- 经验使用互斥分类：经验指令参与、仅作研判参考、未使用经验、无法确认。指令参与可能包含冲突，不等于正确自动处置。",
            "- 质量及经验使用评价只含已完成且未标记运行中快照变化的记录；纳入范围仅按上文选定的失败条件排除，未选择的条件不额外剔除。快照异常样本仍留在清单中，但不计入研判质量。"
            if scope is not None
            else "- 质量及经验使用评价只含已完成且未标记运行中快照变化的记录；全范围分母仍保留失败、未完成和快照异常样本。",
            "- 历史处置一致性只使用明确可比较的历史标签；数据库没有标签时保持未知，不把模型判断当真值。",
            "- 同组来源经验在快照中可用，不证明适用条件必然匹配；当前审核状态与第二批开始时状态分别展示。",
            "- 候选数不等于告警数；同组可同时有批准、放弃和待审核候选。具体放弃原因按已提取记录展示；未提供或被截取时需人工核实补充，不推断历史没有记录，也不猜测业务场景。",
            "- Token 和调用数只统计选定运行已保存的计量，缺失为空，不代表零；未保存的失败调用可能未计入。没有计算费用。",
            "- P50/P95 使用已完成且耗时已知的运行，采用线性插值；耗时相加不等于整批墙钟时间。",
            "- 第一批与第二批不是同一告警，有经验与无经验组难度也可能不同；比例差不能作为经验带来提升的因果证据。",
            "",
            "## 分批抽查",
            "",
            f"来源对照保留 {len(report['groups'])} 个同类组，其中 {sum(group['validation_count'] > 0 for group in report['groups'])} 组含纳入范围的第二批告警；共 {len(report['differences'])} 条差异线索，review.csv 每 {metadata['groups_per_review_batch']} 组为操作批次。"
            if scope is not None
            else f"共 {len(report['groups'])} 个同类组，{len(report['differences'])} 条差异线索；review.csv 每 {metadata['groups_per_review_batch']} 组为操作批次。",
            "",
            "risk_directed 为定向检查全部有差异的组；random_unflagged 为按第二批范围分层、固定种子选择的无差异组，每层最多一个批次。抽查批次大小不表示统计置信度。",
            "每组先看起步告警，并结合 differences.csv 查看具体异常告警；必要时扩查。两种抽样结果分别解读，不将定向样本的错误比例外推全体。",
            "",
            "填写 review.csv 的抽查判断（合理差异／需要修正／证据不足／暂未确认）、原因说明、后续处理建议、审核人和审核时间。所有反馈初始为空；未抽查不默认正确。填写建议不会修改系统或触发重跑。",
            "",
            "## 文件说明",
            "",
            "- alerts.csv：第一批基线与固定第二批告警摘要。groups.csv：一组一行的两批分布及审核情况。",
            "- candidates.csv：候选的多对多来源、当前和开始时审核状态。memories.csv：快照供给与实际使用分别列出，按告警去重。",
            "- differences.csv：全部差异线索，不代表判错。review.csv：可填写抽查表。followups.csv：独立后续运行。",
            "- report.json：完整结构化摘要与分母。不同经验覆盖可重叠，不能将各经验关联告警数相加作为总体覆盖数。",
            *(
                [
                    "- excluded_alerts.csv：本次按所选失败条件排除的告警、任务和语义核对状态及排除理由。groups.csv 和 memories.csv 保留原来源关系，第二批计数仅指纳入范围；计数为零不说明原整批从未使用该经验。仅含已排除告警的组不会进入抽查表。"
                ]
                if scope is not None
                else []
            ),
            "",
            "CSV 超长单元格带有明确预览提示，完整关系保留在 report.json；可在 alerts.csv 按告警和经验标识筛选。",
            "",
            "输出含内部告警和经验标识，按内网数据管理要求保管；报告不含原始告警正文或模型推理正文。",
            "",
        ]
    )
    if report["warnings"]:
        lines.extend(
            [
                "## 数据限制",
                "",
                *[f"- {_md(warning)}" for warning in report["warnings"]],
                "",
            ]
        )
    return "\n".join(lines)


def _private_file(path, *, encoding="utf-8", newline=None):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    return os.fdopen(descriptor, "w", encoding=encoding, newline=newline)


def write_report(report: dict, output_dir: Path) -> list[Path]:
    """Write an existing empty staging directory. The CLI owns atomic publish."""
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("output must be an existing real staging directory")
    if any(output_dir.iterdir()):
        raise FileExistsError("report staging directory is not empty")
    output_dir.chmod(0o700)
    paths = []
    for name, body in (
        ("REPORT.md", _markdown(report)),
        (
            "report.json",
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        ),
    ):
        path = output_dir / name
        with _private_file(path) as stream:
            stream.write(body)
        paths.append(path)
    scope = _evaluation_scope(report)
    csv_files = list(CSV_COLUMNS.items())
    if scope is not None:
        csv_files.append(("excluded_alerts", EXCLUDED_COLUMNS))
    for name, columns in csv_files:
        path = output_dir / f"{name}.csv"
        rows = (
            _list(scope.get("excluded_alerts"))
            if name == "excluded_alerts"
            else report[name]
        )
        with _private_file(path, encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "纳入效果评估告警数"
                    if scope is not None and key == "validation_count"
                    else "纳入范围关联告警数"
                    if scope is not None and key == "validation_alert_count"
                    else label
                    for key, label in columns
                ]
            )
            writer.writerows(
                [_cell(row.get(key)) for key, _ in columns] for row in rows
            )
        paths.append(path)
    return paths
