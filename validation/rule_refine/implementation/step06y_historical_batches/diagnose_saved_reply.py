"""Replay a saved 6Y output contract, without services or application changes."""

import argparse
import hashlib
import json
import math
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from pydantic import Field, ValidationError, create_model

MODULES = (
    "condition_values",
    "contracts",
    "limits",
    "rule_config_source",
    "materials",
    "analysis",
)
RELATIVE_ROOT = Path("my_workflows/zeus/rule_refine")
FIELDS = {
    "status",
    "issue",
    "recommendation",
    "certainty",
    "sample_alert_ids",
    "filter",
    "topic",
    "expression",
    "name",
    "value",
    "operator",
}
MESSAGES = {
    "string_too_long": "字符串超过字段长度上限",
    "string_too_short": "字符串未达到字段长度下限",
    "string_type": "字段必须为字符串",
    "list_type": "字段必须为列表",
    "dict_type": "字段必须为对象",
    "model_type": "输出或字段必须为对象",
    "missing": "缺少必填字段",
    "extra_forbidden": "出现协议未声明的字段",
    "literal_error": "字段值不在协议枚举中",
    "too_long": "集合超过协议数量上限",
    "too_short": "集合未达到协议数量下限",
    "value_error": "字段组合或自定义校验未通过",
}
VALIDATOR_REASONS = {
    "样本引用不能重复": (
        "duplicate_sample_references",
        "sample_alert_ids 中存在重复引用",
    ),
    "新增排除建议需要条件和样本": (
        "proposal_requires_filter_and_samples",
        "proposal 状态必须同时提供 filter 和非空 sample_alert_ids",
    ),
    "非建议状态不能携带过滤条件": (
        "non_proposal_filter_must_be_null",
        "非 proposal 状态的 filter 必须为 null",
    ),
    "invalid_filter_aliases": (
        "invalid_filter_aliases",
        "filter 必须含 1～32 个条件，别名为大写字母开头的字母、数字或下划线且不超过 32 字符",
    ),
    "表达式含不支持的字符": (
        "unsupported_expression_characters",
        "expression 含协议不支持的字符",
    ),
    "表达式过长": (
        "too_many_expression_tokens",
        "expression 的条件、运算符和括号合计超过 256 个",
    ),
    "括号位置不正确或嵌套过深": (
        "invalid_opening_parenthesis_or_depth",
        "expression 左括号位置不正确或嵌套超过 32 层",
    ),
    "括号不匹配或缺少条件": (
        "unmatched_parenthesis_or_missing_condition",
        "expression 右括号不匹配或括号前缺少条件",
    ),
    "运算符前缺少条件": (
        "missing_condition_before_operator",
        "expression 运算符前缺少条件",
    ),
    "缺少运算符或引用了未定义条件": (
        "missing_operator_or_undefined_condition",
        "expression 缺少运算符或引用了未定义的条件别名",
    ),
    "表达式不完整": ("incomplete_expression", "expression 结尾缺少条件或括号未闭合"),
}
MISSING = object()


class DiagnosticError(ValueError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError("duplicate_metadata_key")
        result[key] = value
    return result


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)


@contextmanager
def _analysis(project, pins):
    """Execute only verified module bytes; never run project package __init__."""
    sources = {}
    for name in MODULES:
        relative = RELATIVE_ROOT / (name + ".py")
        expected = pins.get(relative.as_posix())
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise DiagnosticError("missing_source_pin:" + name)
        raw = (project / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise DiagnosticError("source_hash_mismatch:" + name)
        sources[name] = raw
    package_name = "_saved_rule_contract_" + uuid4().hex
    package = ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package
    try:
        for name, raw in sources.items():
            qualified = package_name + "." + name
            module = ModuleType(qualified)
            module.__package__ = package_name
            module.__file__ = str(project / RELATIVE_ROOT / (name + ".py"))
            sys.modules[qualified] = module
            setattr(package, name, module)
            exec(compile(raw, module.__file__, "exec"), module.__dict__)
        analysis = package.analysis
        analysis.AnalysisDraft = create_model(
            "HistoricalContextAnalysisDraft",
            __base__=analysis.AnalysisDraft,
            sample_alert_ids=(list[analysis.Text], Field(default_factory=list)),
        )
        yield analysis
    finally:
        for name in list(sys.modules):
            if name == package_name or name.startswith(package_name + "."):
                del sys.modules[name]


def _safe_path(location):
    result = []
    for index, part in enumerate(location):
        if type(part) is int:
            result.append(part)
        elif part in FIELDS:
            result.append(part)
        elif (
            index == 1
            and location[0] == "filter"
            and isinstance(part, str)
            and re.fullmatch(r"[A-Z][0-9]{0,2}", part)
        ):
            result.append(part)
        else:
            result.append("<unrecognized_field>")
    return result


def _actual(value, location):
    for part in location:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and type(part) is int and 0 <= part < len(value):
            value = value[part]
        else:
            return MISSING
    return value


def _shape(value):
    if value is MISSING:
        return {"actual_type": "missing"}
    kind = {
        str: "string",
        list: "array",
        dict: "object",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }.get(type(value), "unknown")
    result = {"actual_type": kind}
    if type(value) in (str, list, dict):
        result["actual_length"] = len(value)
    return result


def _errors(error, value):
    result = []
    for item in error.errors(include_url=False, include_input=False):
        kind = item["type"]
        location = item["loc"]
        row = {
            "path": _safe_path(location),
            "error_type": kind,
            "message": MESSAGES.get(kind, "输出字段未通过协议校验"),
            **_shape(_actual(value, location)),
        }
        custom = (item.get("ctx") or {}).get("error")
        if kind == "value_error" and type(custom) is ValueError:
            known = VALIDATOR_REASONS.get(str(custom))
            if known is not None:
                row["reason"], row["message"] = known
        for key in ("max_length", "min_length", "actual_length"):
            limit = (item.get("ctx") or {}).get(key)
            if type(limit) is int and limit >= 0:
                row["limit_" + key] = limit
        result.append(row)
    return result


def _transport(record, summary):
    original = record.get("model_transport") or {}
    original = original if isinstance(original, dict) else {}
    values = {**summary, **original}
    result = {}
    reason = values.get("finish_reason")
    if reason in {
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "function_call",
        None,
    }:
        result["finish_reason"] = reason
    usage = values.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {
            key: usage[key]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(usage.get(key)) is int and usage[key] >= 0
        }
    elapsed = values.get("elapsed_ms")
    if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
        result["elapsed_ms"] = elapsed
    return result


def diagnose(report_dir, project_root, *, batch=1):
    if type(batch) is not int or batch < 1:
        raise DiagnosticError("invalid_batch_number")
    directory, project = Path(report_dir), Path(project_root)
    child = directory / f"batch-{batch:03d}"
    pins, schema = (
        _json(directory / "source-pins.json"),
        _json(directory / "output-schema.json"),
    )
    record = _json(child / "analysis.json")
    if (
        not isinstance(pins, dict)
        or not isinstance(schema, dict)
        or not isinstance(record, dict)
    ):
        raise DiagnosticError("invalid_saved_metadata_shape")
    raw = (child / "assistant-content.txt").read_bytes()
    if hashlib.sha256(raw).hexdigest() != record.get("response_sha256"):
        raise DiagnosticError("saved_response_hash_mismatch")
    summary_path = child / "summary.json"
    summary = _json(summary_path) if summary_path.exists() else {}
    if not isinstance(summary, dict):
        raise DiagnosticError("invalid_saved_summary_shape")
    result = {
        "check": "historical_saved_output_contract",
        "execution_mode": "offline_replay",
        "batch": batch,
        "read_only": True,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "application_code_updated": False,
        "original_report_unchanged": True,
        "validation_scope": "output_contract_only",
        "full_proposal_validation_performed": False,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "saved_transport": _transport(record, summary),
        "saved_failure": {
            key: value if value in allowed else "other"
            for key, allowed in {
                "validation_stage": {"output_contract", "json", "evidence", None},
                "error_code": {
                    "invalid_output_contract",
                    "invalid_model_output",
                    "invalid_json",
                    None,
                },
                "error_type": {
                    "ValidationError",
                    "ValueError",
                    "TypeError",
                    "KeyError",
                    "AttributeError",
                    None,
                },
            }.items()
            for value in [record.get(key)]
            if value is None or isinstance(value, str)
        },
    }
    with _analysis(project, pins) as analysis:
        if analysis.AnalysisDraft.model_json_schema() != schema:
            raise DiagnosticError("saved_schema_mismatch")
        result.update(source_hashes_verified=True, saved_schema_verified=True)
        try:
            value = json.loads(
                analysis._json_document(raw.decode("utf-8")),
                object_pairs_hook=analysis._unique_object,
                parse_constant=analysis._invalid_constant,
            )
        except (ValueError, TypeError, RecursionError) as error:
            result.update(status="json_invalid", error_type=type(error).__name__)
            if isinstance(error, json.JSONDecodeError):
                result["position"] = {"line": error.lineno, "column": error.colno}
            return result
        try:
            analysis.AnalysisDraft.model_validate(value)
        except ValidationError as error:
            result.update(status="contract_invalid", errors=_errors(error, value))
        else:
            result.update(status="contract_valid", errors=[])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--batch", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        result = diagnose(args.report_dir, args.project_root, batch=args.batch)
    except Exception as error:
        result = {
            "check": "historical_saved_output_contract",
            "status": "diagnostic_stopped",
            "error_type": type(error).__name__,
            "model_calls": 0,
            "reason": str(error)
            if isinstance(error, DiagnosticError)
            else "saved_files_or_dependencies_unavailable",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "diagnostic_stopped" else 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
