"""Historical analysis with installed checks and budgets; no formal recommendation."""

import copy
import hashlib
import json

PROMPT_VERSION = "rule-refine-historical-unified-v2"
SAMPLE_SENTENCE = "材料只是触发时引用的有限样本，省略项和不完整范围必须考虑，不能假设未展示的信息不存在。"
HISTORICAL_SENTENCE = "本次是手工触发的历史样本分析，没有实时突增信号、接收窗口或线上告警速率。样本数量仅代表本次文件选取数量。省略项和不完整范围必须考虑，不能假设未展示的信息不存在。"
INPUT_SIZE_BASIS = "serialized_messages_utf8_json_bytes"


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def build_request(context, analysis, *, limits, require_samples=True):
    if (
        not isinstance(context, dict)
        or context.get("material_source") != "historical_export"
        or context.get("trigger") != "manual_sample_review"
        or context.get("observed_surge") is not False
        or context.get("rate_window") is not None
        or not isinstance(context.get("samples"), list)
    ):
        raise ValueError("historical_context_required")
    if require_samples and not context["samples"]:
        raise ValueError("no_usable_samples")
    if analysis.SYSTEM.count(SAMPLE_SENTENCE) != 1:
        raise ValueError("reviewed_prompt_changed")
    system = analysis.SYSTEM.replace(SAMPLE_SENTENCE, HISTORICAL_SENTENCE)
    system += f"\n完整请求预算{limits.input_kib}KiB，回答UTF-8最多{limits.output_kib}KiB；简洁输出。"
    try:
        content = analysis._encode(context, limits.input_bytes).decode("utf-8")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        raw = analysis._encode(messages, limits.input_bytes)
    except ValueError as error:
        if str(error) == "材料超过本步大小上限":
            raise ValueError("historical_request_over_budget") from None
        raise
    return {
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(raw).hexdigest(),
        "messages": messages,
        "input_bytes": len(raw),
        "input_size_basis": INPUT_SIZE_BASIS,
        "max_input_kib": limits.input_kib,
        "max_output_kib": limits.output_kib,
        "max_input_bytes": limits.input_bytes,
        "max_output_bytes": limits.output_bytes,
    }


def _detail(error, stage):
    result = {"error_type": type(error).__name__, "category": stage}
    if isinstance(error, json.JSONDecodeError):
        result.update(line=error.lineno, column=error.colno, position=error.pos)
    return result


def analyze(
    context,
    generate,
    *,
    analysis,
    evaluator,
    save,
    limits,
    response_source="model",
    expected_prompt_sha256=None,
):
    """One generation followed by the same local condition checks as installed 6W."""
    if response_source not in {"model", "synthetic_reply", "saved_model_response"}:
        raise ValueError("response_source_required")
    request = build_request(context, analysis, limits=limits)
    result = {
        "schema_version": "sec_know.historical_rule_review.v2",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": request["prompt_sha256"],
        "input_bytes": request["input_bytes"],
        "max_input_kib": limits.input_kib,
        "max_output_kib": limits.output_kib,
        "input_size_basis": INPUT_SIZE_BASIS,
        "response_source": response_source,
        "status": "analysis_failed",
        "generation_calls": 0,
        "automatic_apply": False,
        "formal_recommendation_submitted": False,
        "engine_compatibility": "not_verified",
        "validation_scope": "provided_historical_logs_only",
        "format_valid": False,
        "assistant_content_saved": False,
        "validation_error": None,
    }
    if (
        expected_prompt_sha256 is not None
        and expected_prompt_sha256 != request["prompt_sha256"]
    ):
        return {**result, "error_code": "prompt_changed", "validation_stage": "input"}
    result["generation_calls"] = 1
    try:
        content = generate(copy.deepcopy(request["messages"]))
    except Exception as error:
        return {
            **result,
            "error_code": "generation_failed",
            "error_type": type(error).__name__,
            "validation_stage": "transport",
            "validation_error": _detail(error, "transport"),
        }
    stage = "response_budget"
    try:
        if not isinstance(content, str):
            raise analysis.OutputCheckError("invalid_response_type")
        raw = content.encode("utf-8")
        if len(raw) > limits.output_bytes:
            raise analysis.OutputCheckError("response_over_budget")
        result["response_sha256"] = hashlib.sha256(raw).hexdigest()
        stage = "response_persistence"
        save("assistant-content.txt", raw)
        result["assistant_content_saved"] = True
        stage = "json"
        value = json.loads(
            analysis._json_document(content),
            object_pairs_hook=analysis._unique_object,
            parse_constant=analysis._invalid_constant,
        )
        stage = "output_contract"
        draft = analysis.AnalysisDraft.model_validate(value)
        available = {sample["alert_id"]: sample for sample in context["samples"]}
        stage = "evidence"
        if not set(draft.sample_alert_ids).issubset(available):
            raise analysis.OutputCheckError("uncited_sample")
        descriptions = {
            "status": draft.status,
            "issue": draft.issue,
            "advice": draft.recommendation,
            "certainty": draft.certainty,
            "cited_sample_count": len(draft.sample_alert_ids),
            "sample_alert_ids": list(draft.sample_alert_ids),
            "filter": None,
        }
        if draft.status != "proposal":
            return {
                **result,
                **descriptions,
                "format_valid": True,
                "validation_stage": "complete",
            }
        if draft.filter.topic != context["rule"]["topic"]:
            raise analysis.OutputCheckError("wrong_topic")
        logs = [
            log for ref in draft.sample_alert_ids for log in available[ref]["raw_logs"]
        ]
        stage = "individual_conditions"
        manual = analysis.inspect_condition_support(draft.filter, logs)
        if manual:
            return {
                **result,
                **descriptions,
                "format_valid": True,
                "status": "manual_review",
                "filter": draft.filter.model_dump(mode="json"),
                "review_reason": "local_evaluation_not_supported",
                "unsupported_features": manual,
                "validation_stage": "local_capability",
                "local_evaluation_completed": False,
            }
        used = set(analysis._used_aliases(draft.filter)) | {"topic", "expression"}
        executable = analysis.FilterDefinition.model_validate(
            {
                key: value
                for key, value in draft.filter.model_dump(mode="json").items()
                if key in used
            }
        )
        stage = "combination_evaluation"
        evaluated = evaluator(executable.model_dump(mode="json"), context["samples"])
        matched = {
            row["alert_id"]
            for row in evaluated["results"]
            if row["status"] == "matched"
        }
        if not set(draft.sample_alert_ids).issubset(matched):
            raise analysis.OutputCheckError("cited_sample_not_matched")
        return {
            **result,
            **descriptions,
            "format_valid": True,
            "filter": executable.model_dump(mode="json"),
            "evaluation": evaluated,
            "validation_stage": "complete",
            "local_evaluation_completed": True,
        }
    except analysis.OutputCheckError as error:
        return {
            **result,
            "error_code": str(error),
            "validation_stage": stage,
            "validation_error": {**_detail(error, stage), "reason": str(error)},
        }
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        OSError,
    ) as error:
        code = {
            "json": "invalid_json",
            "output_contract": "invalid_output_contract",
            "response_budget": "invalid_response_encoding",
            "response_persistence": "response_save_failed",
            "combination_evaluation": "evaluation_failed",
        }.get(stage, "invalid_model_output")
        return {
            **result,
            "error_code": code,
            "error_type": type(error).__name__,
            "validation_stage": stage,
            "validation_error": _detail(error, stage),
        }


def report_text(context, result, summary):
    manual = result.get("status") == "manual_review"
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
        result.get("advice", "请查看summary.json中的具体失败环节与原因。"),
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
    ]
    if manual:
        lines += [
            "建议已保存，但本地试算器不支持其中的比较方式，尚未完成匹配验证；请人工核对条件与ZEUS语义。",
            "",
            "```json",
            json.dumps(
                result.get("unsupported_features", []), ensure_ascii=False, indent=2
            ),
            "```",
        ]
    else:
        lines += [
            "```json",
            json.dumps(
                result.get("evaluation", {}).get("counts", {}),
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    lines += [
        "",
        "以上为模型看见的历史日志匹配情况，不是误报率、线上降量率或全部历史告警的效果。缺失和省略材料见coverage.json；业务属性及ZEUS引擎兼容性仍需核对。",
        "",
        "历史原研判回答未作为正常业务标签提供给本次模型。模型可能返回证据不足、暂无建议或需修改已有条件；这些有效回答不视为调用失败。",
    ]
    if summary["status"] not in {"passed", "manual_review"}:
        lines += ["", "本次配置或输出检查未通过，请勿将上述内容作为可用修改建议。"]
    return "\n".join(lines) + "\n"
