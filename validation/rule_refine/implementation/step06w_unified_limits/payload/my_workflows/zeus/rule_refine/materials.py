"""组装规则突增分析材料；纯函数，不连接服务、不执行模型或规则修改。"""

import copy
import hashlib
import io
import json
import math
from datetime import datetime

from .limits import ALERT_BYTES, CANDIDATE_BYTES, JSON_MAX_DEPTH, TASK_ROWS
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
    """Canonical bounded JSON without a second, wrapper-sensitive node quota.

    Every value consumes at least its minimum JSON bytes during validation.
    The final incremental encoding checks actual UTF-8 bytes and escaping. Thus
    the supplied byte budget also bounds traversal work; a parent with a larger
    byte budget does not reject valid children solely for adding wrapper nodes.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("JSON 大小上限必须是正整数")
    budget = limit
    active = set()

    def visit(item, depth=0):
        nonlocal budget
        if depth > JSON_MAX_DEPTH:
            raise ValueError("JSON 材料层级过深")
        if isinstance(item, str):
            budget -= len(item) + 2
        elif item is None:
            budget -= 4
        elif type(item) is bool:
            budget -= 4 if item else 5
        elif type(item) is int:
            if item.bit_length() > 4096:
                raise ValueError("JSON 数值过大")
            budget -= len(str(item))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON 数值必须有限")
            budget -= len(str(item))
        elif isinstance(item, (dict, list)):
            budget -= 2 + max(0, len(item) - 1)
            if isinstance(item, dict):
                budget -= len(item)  # colons
        else:
            raise ValueError("材料必须使用 JSON 类型")
        if budget < 0:
            raise ValueError("材料超过本步大小上限")
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in active:
                raise ValueError("JSON 材料包含循环引用")
            active.add(identity)
            try:
                if isinstance(item, dict):
                    for key, child in item.items():
                        if not isinstance(key, str):
                            raise ValueError("JSON 字段名必须为字符串")
                        visit(key, depth + 1)
                        visit(child, depth + 1)
                else:
                    for child in item:
                        visit(child, depth + 1)
            finally:
                active.remove(identity)

    visit(value)
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with io.BytesIO() as output:
        for part in encoder.iterencode(value):
            encoded = part.encode("utf-8")
            if output.tell() + len(encoded) > limit:
                raise ValueError("材料超过本步大小上限")
            output.write(encoded)
        return output.getvalue()


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
    if not isinstance(task_rows, list) or len(task_rows) > TASK_ROWS:
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
    _encode(rule, CANDIDATE_BYTES)
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
            _encode(data, ALERT_BYTES)
            alert = data["alert"]
            if _alert_id(alert["alertId"]) != alert_id:
                raise ValueError("告警身份不一致")
            raw = _encode(alert, ALERT_BYTES)
            digest = hashlib.sha256(raw).hexdigest()
            hits = alert["hitLog"]
            if not isinstance(hits, list) or len(hits) > 1000:
                raise ValueError("命中日志格式或数量无效")
            matching = [h for h in hits if _matching_hit(h, scope)]
            logs = []
            available_logs, invalid_logs, limited_logs = 0, 0, 0
            for hit in matching:
                items = hit.get("zeusRawLogs")
                if not isinstance(items, list):
                    issue("invalid_raw_log_list_omitted", alert_id)
                    continue
                available_logs += len(items)
                for item in items:
                    if not isinstance(item, dict) or not item:
                        invalid_logs += 1
                    elif len(logs) >= 100:
                        limited_logs += 1
                    else:
                        logs.append(item)
            if not logs:
                raise ValueError("缺少可用对应原日志")
            if invalid_logs:
                issue("invalid_raw_logs_omitted", alert_id)
            if limited_logs:
                issue("raw_log_selection_limit", alert_id)
        except (ValueError, TypeError, KeyError, RecursionError):
            invalid.add(alert_id)
            issue("invalid_or_oversized_alert_material", alert_id)
            continue
        entry = groups.setdefault(
            alert_id, {"variants": {}, "tasks": set(), "responses": {}}
        )
        if digest in entry["variants"]:
            result["duplicate_task_rows"] += 1
        entry["variants"][digest] = {
            "logs": logs,
            "available_logs": available_logs,
            "invalid_logs": invalid_logs,
            "selection_omitted_logs": limited_logs,
        }
        entry["tasks"].add(task_id)
        response = row.get("agent_response")
        if row.get("agent_response_unavailable") is True:
            issue("stored_response_omitted", alert_id)
            response = None
        if response not in (None, {}, [], ""):
            try:
                encoded = _encode(response, ALERT_BYTES)
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
        digest, selected = next(iter(entry["variants"].items()))
        result["samples"].append(
            {
                "alert_id": alert_id,
                "task_ids": sorted(entry["tasks"]),
                "main_alert_sha256": digest,
                "raw_logs": copy.deepcopy(selected["logs"]),
                "raw_log_selection": {
                    "available": selected["available_logs"],
                    "included": len(selected["logs"]),
                    "invalid_omitted": selected["invalid_logs"],
                    "selection_omitted": selected["selection_omitted_logs"],
                },
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
