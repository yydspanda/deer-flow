"""组装规则突增分析材料；纯函数，不连接服务、不执行模型或规则修改。"""

import copy
import hashlib
import json
from datetime import datetime

from .rule_config_source import (
    AmbiguousRuleError,
    RuleConfigSource,
    RuleNotFoundError,
    validate_rule_match,
)


def _text(value):
    if isinstance(value, str):
        value = value.strip()
        if 1 <= len(value) <= 512 and value != "待定":
            return value
    raise ValueError("材料身份字段必须是 1～512 字符的有效字符串")


def _alert_id(value):
    return _text(str(value) if type(value) is int and value >= 0 else value)


def _encode(value, limit):
    # 编码前限制遍历规模，避免超大字符串或深层对象先耗尽资源。
    budget, nodes = limit, 10000

    def visit(item, depth=0):
        nonlocal budget, nodes
        nodes -= 1
        if depth > 32 or nodes < 0:
            raise ValueError("JSON 材料层级或节点过多")
        if isinstance(item, str):
            budget -= len(item)
        elif item is None or type(item) in (bool, float, int):
            if type(item) is int and item.bit_length() > 4096:
                raise ValueError("JSON 数值过大")
            budget -= len(str(item))
        elif isinstance(item, (dict, list)):
            budget -= 2 * len(item) + 2
        else:
            raise ValueError("材料必须使用 JSON 类型")
        if budget < 0:
            raise ValueError("材料超过本步大小上限")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("JSON 字段名必须为字符串")
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value)
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("材料超过本步大小上限")
    return raw


def _matching_hit(hit, scope):
    try:
        return (
            _text(hit.get("topic")) == scope["topic"]
            and _text(hit.get("ruleCode")) == scope["rule_code"]
        )
    except (AttributeError, ValueError):
        return False


def build_rule_materials(
    signal: dict, rules: RuleConfigSource, task_rows: list, *, source_kind: str
) -> dict:
    """消费 v1 信号和已解码任务字典；最多 100 行、20 个告警引用。

    source_kind 只能由调用方选择；人工源仅可配 demo，正式任务源仅可配 live/replay。
    只组装触发时引用的样本，不声称覆盖整个统计窗口；任务时间不作为接收时间。
    """
    if (
        not isinstance(signal, dict)
        or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
    ):
        raise ValueError("需要 v1 突增信号")
    mode = signal.get("mode")
    if (source_kind, mode) not in {
        ("synthetic_fixture", "demo"),
        ("workflow_task_store", "live"),
        ("workflow_task_store", "replay"),
    }:
        raise ValueError("材料来源和信号模式不匹配")
    scope = {
        key: _text(signal.get(key))
        for key in (
            "signal_id",
            "namespace",
            "source_app",
            "company_code",
            "topic",
            "rule_code",
        )
    }
    times = [
        datetime.fromisoformat(signal[key]) for key in ("window_start", "window_end")
    ]
    if any(at.utcoffset() is None for at in times) or times[0] >= times[1]:
        raise ValueError("信号窗口必须包含时区且起点早于终点")
    time_basis = signal.get("time_basis", "unknown")
    expected_basis = "redis_observed_at" if mode == "live" else "provided_received_at"
    if time_basis not in ("unknown", expected_basis):
        raise ValueError("信号时间依据与模式不一致")
    refs = signal.get("sample_alert_ids")
    if not isinstance(refs, list) or len(refs) > 20:
        raise ValueError("信号样本引用必须是最多 20 项的列表")
    refs = list(dict.fromkeys(_text(value) for value in refs))
    count = signal.get("current_count")
    if type(count) is not int or count < max(1, len(refs)):
        raise ValueError("信号计数无效")
    if not isinstance(task_rows, list) or len(task_rows) > 100:
        raise ValueError("每次最多提供 100 条任务记录")
    result = {
        "schema_version": "sec_know.rule_materials.v1",
        "status": "insufficient_evidence",
        "mode": mode,
        "material_source": source_kind,
        "signal_scope": scope,
        "window": {
            "start": times[0].isoformat(),
            "end": times[1].isoformat(),
            "count": count,
            "time_basis": time_basis,
        },
        "sample_basis": "signal_sample_alert_ids_only",
        "requested_sample_count": len(refs),
        "rule_version_basis": "configuration_fetched_for_materials_not_frozen_at_trigger",
        "rule": None,
        "samples": [],
        "issues": [],
        "duplicate_task_rows": 0,
        "stored_response_role": "unverified_reference_not_human_label",
    }

    def issue(code, alert_id=None):
        item = {"code": code}
        if alert_id is not None:
            item["alert_id"] = alert_id
        if item not in result["issues"]:
            result["issues"].append(item)

    try:
        # 与入口相同：先唯一匹配，再核对公司；不使用资产的企业字段。
        rule = rules.get_rule(scope["rule_code"], topic=scope["topic"])
    except (RuleNotFoundError, AmbiguousRuleError) as error:
        issue(
            "rule_not_found"
            if isinstance(error, RuleNotFoundError)
            else "rule_ambiguous"
        )
        return result
    validate_rule_match(
        rule,
        rule_code=scope["rule_code"],
        topic=scope["topic"],
        company_code=scope["company_code"],
    )
    _encode(rule, 512 * 1024)
    result["rule"] = copy.deepcopy(rule)
    groups = {}
    invalid = set()
    wanted = set(refs)
    for row in task_rows:
        if (
            not isinstance(row, dict)
            or row.get("app_code") != scope["source_app"]
            or row.get("flow_id") != "alert_agent"
        ):
            continue
        try:
            alert_id = _alert_id(row.get("alert_id"))
        except ValueError:
            continue
        if alert_id not in wanted:
            continue
        try:
            task_id = _text(row.get("id"))
            data = row["alert_data"]
            _encode(data, 1024 * 1024)
            alert = data["alert"]
            if _alert_id(alert["alertId"]) != alert_id:
                raise ValueError("告警身份不一致")
            raw = _encode(alert, 1024 * 1024)
            digest = hashlib.sha256(raw).hexdigest()
            hits = alert["hitLog"]
            if not isinstance(hits, list) or len(hits) > 1000:
                raise ValueError("命中日志格式或数量无效")
            matching = [h for h in hits if _matching_hit(h, scope)]
            logs = []
            for hit in matching:
                items = hit.get("zeusRawLogs")
                if not isinstance(items, list) or any(
                    not isinstance(item, dict) or not item for item in items
                ):
                    raise ValueError("原日志需要非空对象列表")
                logs.extend(items)
            if not 1 <= len(logs) <= 100:
                raise ValueError("缺少对应原日志或日志过多")
            _encode(logs, 1024 * 1024)
        except (ValueError, TypeError, KeyError, RecursionError):
            invalid.add(alert_id)
            issue("invalid_or_oversized_alert_material", alert_id)
            continue
        entry = groups.setdefault(
            alert_id, {"variants": {}, "tasks": set(), "responses": {}}
        )
        if digest in entry["variants"]:
            result["duplicate_task_rows"] += 1
        entry["variants"][digest] = logs
        entry["tasks"].add(task_id)
        response = row.get("agent_response")
        if row.get("agent_response_unavailable") is True:
            issue("stored_response_omitted", alert_id)
            response = None
        if response not in (None, {}, [], ""):
            try:
                encoded = _encode(response, 16 * 1024)
                entry["responses"][encoded] = response
            except (ValueError, TypeError, RecursionError):
                issue("stored_response_omitted", alert_id)
    for alert_id in refs:
        entry = groups.get(alert_id)
        if entry is None:
            issue("sample_unavailable", alert_id)
            continue
        if alert_id in invalid or len(entry["variants"]) != 1:
            issue("sample_conflicting_or_invalid", alert_id)
            continue
        responses = list(entry["responses"].values())
        if len(responses) > 1:
            issue("stored_response_conflict", alert_id)
        digest, logs = next(iter(entry["variants"].items()))
        result["samples"].append(
            {
                "alert_id": alert_id,
                "task_ids": sorted(entry["tasks"]),
                "main_alert_sha256": digest,
                "raw_logs": copy.deepcopy(logs),
                "stored_response": copy.deepcopy(responses[0])
                if len(responses) == 1
                else None,
                "stored_response_status": "conflict"
                if len(responses) > 1
                else "present"
                if responses
                else "missing",
            }
        )
    if result["samples"]:
        result["status"] = "partial" if result["issues"] else "ready"
    return result
