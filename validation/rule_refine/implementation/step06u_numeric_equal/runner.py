"""Replay a saved historical answer with explicit numeric-equality semantics."""

import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import diagnostic_runner
import historical_review
import inventory
import numeric_changes


def load_updated(sources):
    package = ModuleType("_numeric_equality_replay_" + uuid4().hex)
    package.__path__ = []
    sys.modules[package.__name__] = package
    loaded = {}
    order = (
        "contracts",
        "rule_config_source",
        "materials",
        "condition_values",
        "analysis",
        "filter_evaluation",
        "csv_config_source",
    )
    if set(sources) != set(order):
        raise ValueError("numeric_replay_sources_mismatch")
    for name in order:
        module = ModuleType(package.__name__ + "." + name)
        module.__package__ = package.__name__
        sys.modules[module.__name__] = module
        exec(
            compile(sources[name], "<numeric-replay-" + name + ">", "exec"),
            module.__dict__,
        )
        loaded[name] = module
    return SimpleNamespace(**loaded)


def compare_conditions(context, content, old, new):
    draft = old.analysis.AnalysisDraft.model_validate(
        json.loads(
            old.analysis._json_document(content),
            object_pairs_hook=old.analysis._unique_object,
            parse_constant=old.analysis._invalid_constant,
        )
    )
    if draft.status != "proposal" or draft.filter is None:
        return []
    available = {sample["alert_id"]: sample for sample in context["samples"]}
    if not set(draft.sample_alert_ids).issubset(available):
        raise ValueError("unknown_sample_reference")
    logs = [log for ref in draft.sample_alert_ids for log in available[ref]["raw_logs"]]
    result = []
    for number, condition in enumerate(
        draft.filter.__pydantic_extra__.values(), start=1
    ):
        matches = [
            new.condition_values.evaluate_condition(condition, log) for log in logs
        ]
        result.append(
            {
                "condition_number": number,
                "operator": condition.operator,
                "cited_log_count": len(logs),
                "numeric_field_logs": sum(
                    type(log.get(condition.name)) in (int, float) for log in logs
                ),
                "old_supported": old.analysis._supported(condition, logs),
                "new_supported": new.analysis._supported(condition, logs),
                "matching_logs": sum(match is True for match, _ in matches),
                "not_matching_logs": sum(match is False for match, _ in matches),
                "unknown_logs": sum(match is None for match, _ in matches),
                "unknown_reasons": dict(
                    Counter(reason for _, reason in matches if reason)
                ),
            }
        )
    return result


def replay(folder, packet):
    old_check, context, old = diagnostic_runner.saved_response_check(
        folder, packet["sources"]
    )
    if not old_check["saved_result_reproduced"]:
        raise ValueError("saved_result_not_reproduced")
    raw = inventory.bounded_read(folder / "assistant-content.txt", 16384)
    if diagnostic_runner.digest(raw) != old_check["response_sha256"]:
        raise ValueError("saved_response_hash_mismatch")
    content = raw.decode("utf-8")
    pins = diagnostic_runner.read_json(folder / "source-pins.json")
    updated = numeric_changes.patched_sources(
        packet["sources"], pins, packet["comparator_source"]
    )
    new = load_updated(updated)
    # Keep the frozen original prompt. This run invokes no model under a new prompt.
    if new.analysis.SYSTEM != old.analysis.SYSTEM:
        raise ValueError("replay_prompt_changed")
    result = historical_review.analyze(
        context,
        lambda messages: content,
        analysis=new.analysis,
        evaluator=new.filter_evaluation.evaluate_filter,
        save=lambda name, value: None,
        response_source="saved_model_response",
    )
    condition_results = compare_conditions(context, content, old, new)
    return (
        context,
        result,
        {
            "check": "historical_numeric_equal_replay",
            "status": "passed" if result["format_valid"] else "blocked",
            "execution_mode": "offline_replay",
            "material_source": "historical_export",
            "response_origin": "saved_model_response",
            "response_sha256": old_check["response_sha256"],
            "original_prompt_sha256": old_check["prompt_sha256"],
            "original_failure_stage": old_check["validation_stage"],
            "old_format_valid": old_check["format_valid"],
            "new_format_valid": result["format_valid"],
            "analysis_status": result["status"],
            "error_code": result.get("error_code"),
            "model_visible_alerts": len(context["samples"]),
            "model_visible_logs": sum(
                len(sample["raw_logs"]) for sample in context["samples"]
            ),
            "preview_materials_used": False,
            "new_model_request_sent": False,
            "model_calls": 0,
            "sdk_request_calls": 0,
            "redis_calls": 0,
            "database_calls": 0,
            "business_alerts_submitted": 0,
            "automatic_apply": False,
            "formal_recommendation_submitted": False,
            "application_code_updated": False,
            "original_report_unchanged": True,
            "comparison_version": new.condition_values.COMPARISON_VERSION,
            "original_prompt_preserved": True,
            "new_prompt_compliance_tested": False,
            "engine_compatibility": "not_verified",
            "conditions": condition_results,
            "sample_match_counts": result.get("evaluation", {}).get("counts"),
            "validator_source_sha256": {
                name: diagnostic_runner.digest(updated[name].encode("utf-8"))
                for name in ("condition_values", "analysis", "filter_evaluation")
            },
        },
    )


def save(folder, name, value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    )
    fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def main(packet):
    try:
        folder = Path(sys.argv[1]).expanduser().resolve()
        context, result, summary = replay(folder, packet)
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "historical_numeric_equal_replay",
                    "status": "stopped",
                    "model_calls": 0,
                    "application_code_updated": False,
                    "failure": diagnostic_runner.stop_reason(error),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(exist_ok=True, mode=0o700)
    output = Path(tempfile.mkdtemp(prefix="historical-numeric-replay-", dir=parent))
    summary["output_dir"] = str(output)
    save(output, "summary.json", summary)
    save(
        output,
        "analysis.json",
        {
            "execution_mode": "offline_replay",
            "model_calls": 0,
            "generation_callback_basis": "one_saved_reply_callback_not_new_generation",
            "analysis": result,
        },
    )
    header = (
        "本报告离线复核同一份历史模型回答，只新增数字等值比较。未重新调用模型，未修改原报告或应用。\n"
        "仍使用原模型可见样本；新预算预览的11条告警未参与本次核对。原模型提示词要求字符串字段，"
        "本次仅评估数字等值语义下的样本支持，不证明原模型遵守了原提示词，也不证明ZEUS兼容或业务场景可忽略。\n\n"
    )
    save(
        output,
        "REPORT.md",
        (header + historical_review.report_text(context, result, summary)).encode(
            "utf-8"
        ),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1
