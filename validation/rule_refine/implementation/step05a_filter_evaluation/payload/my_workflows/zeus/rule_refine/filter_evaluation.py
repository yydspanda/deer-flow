"""在提供的日志上试算条件组合；不代表 ZEUS 引擎语义或告警可忽略结论。"""

import hashlib

from .contracts import TOKEN, FilterDefinition
from .materials import _encode


def _postfix(expression):
    # FilterDefinition 已完整校验语法和规模；这里只转换优先级，不执行字符串代码。
    precedence = {"||": 1, "&&": 2}
    output, operators = [], []
    for token in TOKEN.findall(expression.strip()):
        if token == "(":
            operators.append(token)
        elif token == ")":
            while operators[-1] != "(":
                output.append(operators.pop())
            operators.pop()
        elif token in precedence:
            while operators and operators[-1] != "(" and precedence[operators[-1]] >= precedence[token]:
                output.append(operators.pop())
            operators.append(token)
        else:
            output.append(token)
    return output + list(reversed(operators))


def _condition(condition, log):
    name = condition.name
    if name not in log:
        return None, "missing_field"
    value = log[name]
    if value is None:
        return None, "null_value"
    if not isinstance(value, str):
        return None, "non_string_value"
    if condition.operator == "equal":
        return value == condition.value, None
    if condition.operator == "contain":
        return condition.value in value, None
    return condition.value not in value, None


def _combine(postfix, values):
    stack = []
    for token in postfix:
        if token not in ("&&", "||"):
            stack.append(values[token])
            continue
        right, left = stack.pop(), stack.pop()
        if token == "&&":
            result = False if left is False or right is False else None if left is None or right is None else True
        else:
            result = True if left is True or right is True else None if left is None or right is None else False
        stack.append(result)
    return stack[0]


def evaluate_filter(definition: dict, samples: list) -> dict:
    """最多 20 个告警、每个 100 条日志。字段名是顶层原样键，字符串区分大小写。

    调用方提供相同 topic 的日志；此函数不查询配置、不验证其版本或样本完整性。
    输出是逐日志匹配数，不是可忽略告警数、误报率或全窗口降噪率。
    """
    if not isinstance(definition, dict):
        raise ValueError("过滤条件必须为对象")
    _encode(definition, 64 * 1024)
    parsed = FilterDefinition.model_validate(definition)
    if not isinstance(samples, list) or len(samples) > 20:
        raise ValueError("每次最多提供 20 个告警样本")
    seen, empty = set(), []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("样本必须为对象")
        alert_id = sample.get("alert_id")
        if not isinstance(alert_id, str) or not 1 <= len(alert_id) <= 512 or alert_id != alert_id.strip():
            raise ValueError("告警 ID 必须为 1～512 字符且无首尾空白")
        if alert_id in seen:
            raise ValueError("告警 ID 不能重复")
        seen.add(alert_id)
        logs = sample.get("raw_logs")
        if not isinstance(logs, list) or len(logs) > 100 or any(not isinstance(log, dict) for log in logs):
            raise ValueError("每个样本需要最多 100 条日志对象的列表")
        if not logs:
            empty.append(alert_id)
    raw = _encode({"filter": parsed.model_dump(mode="json"), "samples": samples}, 2 * 1024 * 1024)
    postfix = _postfix(parsed.expression)
    aliases = sorted(set(postfix) - {"&&", "||"})
    conditions = parsed.__pydantic_extra__
    counts = {"matched": 0, "not_matched": 0, "unknown": 0}
    results = []
    for sample in samples:
        for index, log in enumerate(sample["raw_logs"]):
            values, unknown = {}, {}
            for alias in aliases:
                values[alias], reason = _condition(conditions[alias], log)
                if reason:
                    unknown[alias] = reason
            value = _combine(postfix, values)
            status = "unknown" if value is None else "matched" if value else "not_matched"
            counts[status] += 1
            results.append(
                {"alert_id": sample["alert_id"], "log_index": index, "status": status, "unknown_conditions": unknown}
            )
    return {
        "schema_version": "sec_know.rule_filter_evaluation.v1",
        "status": "evaluated" if results else "insufficient_evidence",
        "scope": "provided_logs_only",
        "source_completeness": "not_asserted",
        "engine_compatibility": "not_verified",
        "configuration_version_check": "not_performed",
        "field_catalog_check": "not_performed",
        "automatic_apply": False,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_alert_count": len(samples),
        "input_log_count": len(results),
        "counts": counts,
        "empty_alert_ids": empty,
        "results": results,
    }
