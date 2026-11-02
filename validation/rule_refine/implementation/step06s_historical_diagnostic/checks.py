"""Explain the unchanged historical-review checks without printing input values."""

import json

from pydantic import ValidationError

FIELDS = set(
    "status issue recommendation certainty sample_alert_ids filter topic expression name value operator".split()
)
REASONS = {
    "invalid_response_size",
    "unknown_sample_reference",
    "topic_mismatch",
    "condition_not_supported",
    "cited_sample_not_matched",
    "回答含重复JSON字段",
    "回答必须使用标准JSON数值",
    "样本引用不能重复",
    "新增排除建议需要条件和样本",
    "非建议状态不能携带过滤条件",
    "表达式含不支持的字符",
    "表达式过长",
    "括号位置不正确或嵌套过深",
    "括号不匹配或缺少条件",
    "运算符前缺少条件",
    "缺少运算符或引用了未定义条件",
    "表达式不完整",
    "包含/不包含条件的值不能为空",
    "需要1至32个条件，别名使用大写字母开头的字母、数字或下划线",
}


def safe_error(error):
    result = {"error_type": type(error).__name__}
    if isinstance(error, json.JSONDecodeError):
        result.update(
            category="json", line=error.lineno, column=error.colno, position=error.pos
        )
    elif isinstance(error, ValidationError):
        rows = error.errors(include_url=False, include_input=False)
        result.update(category="schema", error_count=len(rows), errors=[])
        for row in rows[:12]:
            location = [
                part
                if type(part) is int or isinstance(part, str) and part in FIELDS
                else "<other_field>"
                for part in row.get("loc", ())[:8]
            ]
            item = {"type": row["type"], "loc": location}
            reason = str(row.get("ctx", {}).get("error", ""))
            if reason in REASONS:
                item["reason"] = reason
            # Only numeric schema bounds, never the offending value or free text.
            for key in ("min_length", "max_length"):
                value = row.get("ctx", {}).get(key)
                if type(value) is int:
                    item[key] = value
            result["errors"].append(item)
    else:
        result["category"] = "output_check"
        if str(error) in REASONS:
            result["reason"] = str(error)
    return result


def diagnose(context, content, *, analysis, evaluator, review):
    """Same checks as step6R, plus a stage label and sanitized diagnostics.

    The unchanged original collector is also replayed from this saved string;
    disagreement is a diagnostic failure, never a reason to accept an answer.
    """
    stage = "response_size"
    result = {"format_valid": False}
    try:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 16384:
            raise ValueError("invalid_response_size")
        stage = "json_parse"
        value = json.loads(
            analysis._json_document(content),
            object_pairs_hook=analysis._unique_object,
            parse_constant=analysis._invalid_constant,
        )
        stage = "draft_schema"
        draft = analysis.AnalysisDraft.model_validate(value)
        stage = "sample_references"
        available = {sample["alert_id"]: sample for sample in context["samples"]}
        if not set(draft.sample_alert_ids).issubset(available):
            raise ValueError("unknown_sample_reference")
        if draft.status == "proposal":
            stage = "topic"
            if draft.filter.topic != context["rule"]["topic"]:
                raise ValueError("topic_mismatch")
            stage = "individual_conditions"
            logs = [
                log
                for ref in draft.sample_alert_ids
                for log in available[ref]["raw_logs"]
            ]
            if not all(
                analysis._supported(condition, logs)
                for condition in draft.filter.__pydantic_extra__.values()
            ):
                raise ValueError("condition_not_supported")
            stage = "combined_filter"
            evaluated = evaluator(
                draft.filter.model_dump(mode="json"), context["samples"]
            )
            matched = {
                row["alert_id"]
                for row in evaluated["results"]
                if row["status"] == "matched"
            }
            if not set(draft.sample_alert_ids).issubset(matched):
                raise ValueError("cited_sample_not_matched")
        result.update(
            format_valid=True,
            analysis_status=draft.status,
            validation_stage="complete",
            validation_error=None,
        )
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        result.update(
            analysis_status="analysis_failed",
            validation_stage=stage,
            validation_error=safe_error(error),
        )
    original = review.analyze(
        context,
        lambda messages: content,
        analysis=analysis,
        evaluator=evaluator,
        save=lambda name, raw: None,
        response_source="saved_model_response",
    )
    result["original_validator_agrees"] = (
        result["format_valid"] == original["format_valid"]
        and result["analysis_status"] == original["status"]
    )
    if not result["original_validator_agrees"]:
        raise ValueError("diagnostic_validator_disagreement")
    return result
