"""Manual historical review: no burst signal, rate window, or delivery record."""

import copy
import hashlib
import json

PROMPT_VERSION = "rule-refine-historical-review-v1"
SAMPLE_SENTENCE = "材料只是触发时引用的有限样本，省略项和不完整范围必须考虑，不能假设未展示的信息不存在。"
HISTORICAL_SENTENCE = "本次是手工触发的历史样本分析，没有实时突增信号、接收窗口或线上告警速率。样本数量仅代表本次文件选取数量。省略项和不完整范围必须考虑，不能假设未展示的信息不存在。"


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def build_request(context, analysis):
    if (
        context.get("material_source") != "historical_export"
        or context.get("observed_surge") is not False
        or context.get("rate_window") is not None
    ):
        raise ValueError("historical_context_required")
    if not context.get("samples"):
        raise ValueError("no_usable_samples")
    if analysis.SYSTEM.count(SAMPLE_SENTENCE) != 1:
        raise ValueError("reviewed_prompt_changed")
    system = analysis.SYSTEM.replace(SAMPLE_SENTENCE, HISTORICAL_SENTENCE)
    raw = encoded(context)
    if len(raw) > 60 * 1024:
        raise ValueError("context_over_budget")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": raw.decode("utf-8")},
    ]
    return {
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(encoded(messages)).hexdigest(),
        "messages": messages,
    }


def analyze(context, generate, *, analysis, evaluator, save, response_source="model"):
    """Compatible generation callback for the isolated existing transport adapter."""
    request = build_request(context, analysis)
    result = {
        "schema_version": "sec_know.historical_rule_review.v1",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": request["prompt_sha256"],
        "response_source": response_source,
        "status": "analysis_failed",
        "generation_calls": 1,
        "automatic_apply": False,
        "formal_recommendation_submitted": False,
        "engine_compatibility": "not_verified",
        "format_valid": False,
    }
    try:
        content = generate(copy.deepcopy(request["messages"]))
    except Exception as error:
        return {
            **result,
            "error_code": "generation_failed",
            "error_type": type(error).__name__,
        }
    try:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 16384:
            raise ValueError("invalid_response_size")
        save("assistant-content.txt", content.encode("utf-8"))
        result["response_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        value = json.loads(
            analysis._json_document(content),
            object_pairs_hook=analysis._unique_object,
            parse_constant=analysis._invalid_constant,
        )
        draft = analysis.AnalysisDraft.model_validate(value)
        available = {sample["alert_id"]: sample for sample in context["samples"]}
        if not set(draft.sample_alert_ids).issubset(available):
            raise ValueError("unknown_sample_reference")
        if draft.status == "proposal":
            if draft.filter.topic != context["rule"]["topic"]:
                raise ValueError("topic_mismatch")
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
            result["evaluation"] = evaluated
        result.update(
            format_valid=True,
            status=draft.status,
            issue=draft.issue,
            advice=draft.recommendation,
            certainty=draft.certainty,
            cited_sample_count=len(draft.sample_alert_ids),
            filter=draft.filter.model_dump(mode="json") if draft.filter else None,
            sample_alert_ids=draft.sample_alert_ids,
        )
        return result
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        # Full assistant content stays in private artifacts. Never print Pydantic inputs.
        return {
            **result,
            "error_code": "invalid_model_output",
            "error_type": type(error).__name__,
        }


def report_text(context, result, summary):
    lines = [
        "# 单规则历史样本建议验证",
        "",
        "来源为历史导出文件和CSV配置；手工触发分析，没有实时突增信号。仅供运营复核，未修改或提交线上规则。",
        "",
        f"规则：{context['rule']['rule_code']} / {context['rule']['rule_name']}",
        f"分析状态：{summary['analysis_status']}；检查状态：{summary['status']}",
        f"模型可见告警数：{len(context['samples'])}；SDK请求次数：{summary.get('sdk_request_calls')}",
        "",
        "## 问题",
        "",
        result.get("issue", "本次未取得通过校验的分析。"),
        "",
        "## 修改建议及前提",
        "",
        result.get("advice", "请先查看summary.json中的错误类别。"),
        "",
        result.get("certainty", ""),
        "",
        "## 条件建议",
        "",
        "```json",
        json.dumps(result.get("filter"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## 样本试算",
        "",
        "```json",
        json.dumps(
            result.get("evaluation", {}).get("counts", {}), ensure_ascii=False, indent=2
        ),
        "```",
        "",
        "以上为模型看见的历史日志匹配情况，不是误报率、线上降量率或全部50条告警的效果。缺失和省略材料见coverage.json；业务属性及Zeus引擎兼容性仍需核对。",
        "",
        "历史原研判回答未作为正常业务标签提供给本次模型。模型可能返回证据不足、暂无建议或需修改已有条件；这些有效回答不视为调用失败。",
    ]
    if summary["status"] != "passed":
        lines += ["", "本次配置或输出检查未通过，请勿将上述内容作为可用修改建议。"]
    return "\n".join(lines) + "\n"
