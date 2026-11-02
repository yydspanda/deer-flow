"""关联建议、模型可见材料和重新查询的配置；只生成供运营审阅的校验结果。"""

import copy

from .analysis import PROMPT_VERSION, build_analysis_request
from .contracts import Evidence, RuleRecommendation
from .filter_evaluation import evaluate_filter
from .materials import _encode
from .rule_config_source import validate_rule_match

NONPROPOSAL = {"no_change", "insufficient_evidence", "needs_existing_condition_change"}


def _coverage(materials, context):
    requested = materials["requested_sample_count"]
    samples = materials["samples"]
    if type(requested) is not int or not len(samples) <= requested <= min(20, context["window"]["count"]):
        raise ValueError("请求样本数量无效")
    projected = {sample["alert_id"]: len(sample["raw_logs"]) for sample in context["samples"]}
    rows = []
    for sample in samples:
        identity, logs = sample["alert_id"], sample["raw_logs"]
        if not 1 <= len(identity) <= 512 or identity != identity.strip() or len(logs) > 100:
            raise ValueError("输入样本超出本步范围")
        if any(not isinstance(log, dict) for log in logs):
            raise ValueError("日志必须为对象")
        shown = projected.get(identity, 0)
        rows.append(
            {"alert_id": identity, "input_logs": len(logs), "projected_logs": shown, "omitted_logs": len(logs) - shown}
        )
    return {
        "requested_alert_count": requested,
        "input_alert_count": len(samples),
        "projected_alert_count": len(projected),
        "unavailable_alert_count": requested - len(samples),
        "input_log_count": sum(row["input_logs"] for row in rows),
        "projected_log_count": sum(row["projected_logs"] for row in rows),
        "omitted_log_count": sum(row["omitted_logs"] for row in rows),
        "per_alert": rows,
    }


def _config_bytes(rule):
    if not isinstance(rule["configuration"], dict):
        raise ValueError("规则配置必须为对象")
    view = {
        key: rule[key]
        for key in ("company_code", "topic", "rule_code", "rule_name", "rule_config_version", "configuration")
    }
    view["source_kind"] = rule["source"]["kind"]
    return _encode(view, 512 * 1024)


def check_proposal(materials: dict, analysis_result: dict, load_rules) -> dict:
    """load_rules() 必须重新读取配置；不得传入复用旧快照的工厂。

    本函数不生成建议或调用模型。输入分析结果来自本项目生产者；摘要不是数字签名。
    reviewable 仅表示可交运营审阅，不代表业务正常、已审批或引擎兼容。
    """
    outcome = {
        "schema_version": "sec_know.rule_proposal_check.v1",
        "status": "blocked",
        "reason": "invalid_analysis",
        "recommendation": None,
        "scope": "model_projected_logs_only",
        "engine_compatibility": "not_verified",
        "field_catalog_check": "not_performed",
        "automatic_apply": False,
    }

    def stop(reason, **details):
        return {**outcome, "reason": reason, **details}

    try:
        _encode(analysis_result, 128 * 1024)
        if (
            not isinstance(analysis_result, dict)
            or analysis_result.get("schema_version") != "sec_know.rule_analysis.v1"
        ):
            return outcome
        status = analysis_result.get("status")
        if status not in NONPROPOSAL | {"proposal", "analysis_failed"}:
            return outcome
        if analysis_result.get("response_source") not in ("model", "synthetic_reply"):
            return outcome
        outcome["analysis_status"] = status
        outcome["response_source"] = analysis_result["response_source"]
        if status == "analysis_failed":
            return stop("analysis_failed")
        if status != "proposal" and analysis_result.get("recommendation") is not None:
            return outcome
    except (ValueError, TypeError, RecursionError):
        return outcome
    try:
        request = build_analysis_request(materials)
        if request is None:
            if (
                status == "insufficient_evidence"
                and type(analysis_result.get("generation_calls")) is int
                and analysis_result["generation_calls"] == 0
                and analysis_result.get("error_code") == "no_usable_material_or_rule_over_budget"
            ):
                return stop(status, status="not_applicable")
            return stop("no_usable_material")
        context = request["context"]
        outcome["coverage"] = _coverage(materials, context)
        outcome["material_issues"] = copy.deepcopy(context["material_issues"])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return stop("invalid_materials")
    expected = {
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": request["prompt_sha256"],
        "material_mode": context["mode"],
        "material_source": context["material_source"],
        "rule_source": context["rule"]["source"],
    }
    if any(analysis_result.get(key) != value for key, value in expected.items()):
        return stop("prompt_mismatch")
    outcome.update({key: copy.deepcopy(value) for key, value in expected.items()})
    if status != "proposal":
        return stop(status, status="not_applicable")
    try:
        saved = analysis_result["recommendation"]
        if not isinstance(saved, dict) or "recommendation_id" not in saved:
            return stop("recommendation_mismatch")
        record = RuleRecommendation.model_validate(saved)
        rule = context["rule"]
        identities = ("company_code", "rule_code", "rule_name", "rule_config_version")
        refs = record.evidence.sample_alert_ids
        projected_ids = {sample["alert_id"] for sample in context["samples"]}
        evidence = Evidence(
            window_start=context["window"]["start"],
            window_end=context["window"]["end"],
            alert_count=context["window"]["count"],
            sample_alert_ids=refs,
        )
        if (
            any(getattr(record, key) != rule[key] for key in identities)
            or record.filter.topic != rule["topic"]
            or record.evidence != evidence
            or not set(refs).issubset(projected_ids)
            or record.issue != analysis_result.get("issue")
            or record.recommendation != analysis_result.get("advice")
            or record.certainty != analysis_result.get("certainty")
        ):
            return stop("recommendation_mismatch")
        evaluation = evaluate_filter(record.filter.model_dump(mode="json"), context["samples"])
        outcome["evaluation"] = evaluation
        supported = {row["alert_id"] for row in evaluation["results"] if row["status"] == "matched"}
        unsupported = [ref for ref in refs if ref not in supported]
        if unsupported:
            return stop("cited_samples_not_supported", unsupported_sample_alert_ids=unsupported)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return stop("recommendation_mismatch")
    try:
        # 使用新提供器，先按 topic+编号唯一匹配，再验证企业，不以企业筛选隐藏歧义。
        fresh = load_rules().get_rule(rule["rule_code"], topic=rule["topic"])
        validate_rule_match(fresh, rule_code=rule["rule_code"], topic=rule["topic"], company_code=rule["company_code"])
        if _config_bytes(fresh) != _config_bytes(rule):
            return stop("configuration_changed")
        outcome["configuration_check"] = {
            "status": "same_provider_configuration",
            "source_kind": fresh["source"]["kind"],
            "rule_config_version": fresh["rule_config_version"],
            "online_zeus_verified": False,
        }
    except Exception as error:
        # 查询失败时不回退到旧配置；只暴露错误类型，不回显连接信息或异常原文。
        return stop("configuration_unavailable", error_type=type(error).__name__)
    return stop("local_checks_passed", status="reviewable", recommendation=record.model_dump(mode="json"))
