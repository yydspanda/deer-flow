"""Revalidate a saved model answer offline; never rewrite source results or app code."""

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

OLD_SHA = "1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117"
NEW_SHA = "c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0"
BODY_SHA = "10dc131e79722ae54191572a0518cd9523b2e8b597c01c5b2b92c788faa69000"
PINS_SHA = "75a50783a9dae5016e0cae26f9380f73cf54480492c847169175aeb0eda4087c"
HELPER = '''def _json_document(raw):
    """Unwrap one complete JSON code fence; never extract JSON from mixed prose."""
    lines = raw.strip(" \\t\\r\\n").split("\\n")
    if (
        len(lines) >= 3
        and lines[0].rstrip(" \\t\\r").lower() in {"```json", "```"}
        and lines[-1].strip(" \\t\\r") == "```"
    ):
        return "\\n".join(lines[1:-1])
    return raw


'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(folder, name, data):
    fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def replay(root, source, *, expected_body=BODY_SHA):
    pins_raw = (source / "pins.json").read_bytes()
    if digest(pins_raw) != PINS_SHA:
        raise ValueError("工具版本清单不匹配")
    pins = json.loads(pins_raw)
    for relative, expected in pins.items():
        if digest((root / relative).read_bytes()) != expected:
            raise ValueError("项目模块版本不匹配：" + relative)
    original = (root / "my_workflows/zeus/rule_refine/analysis.py").read_bytes()
    if digest(original) != OLD_SHA:
        raise ValueError("分析模块版本不匹配")
    candidate = original.decode("utf-8")
    old_call = "json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)"
    if (
        candidate.count(old_call) != 1
        or candidate.count("def _supported(condition, logs):") != 1
    ):
        raise ValueError("修复位置不匹配")
    candidate = candidate.replace(
        "def _supported(condition, logs):",
        HELPER + "def _supported(condition, logs):",
        1,
    )
    candidate = candidate.replace(
        old_call, old_call.replace("raw,", "_json_document(raw),"), 1
    )
    if digest(candidate.encode("utf-8")) != NEW_SHA:
        raise ValueError("修复副本校验不通过")
    answer_bytes = (source / "assistant-content.txt").read_bytes()
    if len(answer_bytes) > 16384 or digest(answer_bytes) != expected_body:
        raise ValueError("已保存回答摘要不匹配")
    answer = answer_bytes.decode("utf-8")
    manifest = load_json(source / "diagnostic-manifest.json")
    diag_path = source / "diagnostics.py"
    if digest(diag_path.read_bytes()) != manifest["diagnostic_helper_sha256"]:
        raise ValueError("诊断工具摘要不匹配")
    spec = importlib.util.spec_from_file_location("_saved_output_diagnostic", diag_path)
    diagnostic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    package = ModuleType("_saved_output_replay_" + uuid4().hex)
    package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package
    module = ModuleType(package.__name__ + ".analysis")
    module.__package__ = package.__name__
    module._diagnostic_error = diagnostic.format_error
    sys.modules[module.__name__] = module
    before = """    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return {**result, "status": "analysis_failed", "recommendation": None, "error_code": "invalid_model_output"}"""
    after = """    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        return {**result, "status": "analysis_failed", "recommendation": None, "error_code": "invalid_model_output", "validation_error": _diagnostic_error(error)}"""
    if candidate.count(before) != 1:
        raise ValueError("诊断位置不匹配")
    instrumented = candidate.replace(before, after, 1)
    exec(compile(instrumented, "<offline_analysis>", "exec"), module.__dict__)
    delivery = importlib.import_module(package.__name__ + ".saved_delivery")
    sinks = importlib.import_module(package.__name__ + ".local_sink")
    summary = load_json(source / "result/summary.json")
    old_store = delivery.LocalAnalysisStore(source / "result/processing/artifacts")
    bundle = old_store.load(summary["record_id"])
    materials, previous = bundle["materials"], bundle["analysis"]
    if (
        materials.get("mode") != "demo"
        or materials.get("material_source") != "synthetic_fixture"
    ):
        raise ValueError("只允许人工材料回放")
    request = module.build_analysis_request(materials)
    if request is None or request["prompt_sha256"] != previous.get("prompt_sha256"):
        raise ValueError("材料或提示词与原回答不匹配")
    if previous.get("response_sha256") != expected_body:
        raise ValueError("原记录与保存的正文不匹配")
    result = module.analyze_materials(
        materials, lambda messages: answer, response_source="synthetic_reply"
    )
    verified = (
        result.get("generation_calls") == 1
        and result.get("model_calls") == 0
        and result.get("response_sha256") == expected_body
        and result.get("prompt_sha256") == previous["prompt_sha256"]
    )
    if not verified:
        raise ValueError("离线回答校验未按预期执行")
    result["response_origin"] = "saved_model_response"
    result["execution_mode"] = "offline_replay"
    folder = Path(tempfile.mkdtemp(prefix="fenced-json-replay-", dir=source.parent))
    store = delivery.LocalAnalysisStore(folder / "artifacts")
    saved = store.save(materials, result)

    def rules():
        return SimpleNamespace(
            get_rule=lambda *args, **kwargs: copy.deepcopy(
                load_json(source / "result/rule.json")
            )
        )

    checked = delivery.deliver_saved(
        store,
        saved["record_id"],
        rules,
        sinks.LocalRecommendationSink(folder / "receipts"),
    )
    passed = result["status"] in {
        "proposal",
        "no_change",
        "insufficient_evidence",
        "needs_existing_condition_change",
    } and checked["status"] in {"received", "not_applicable"}
    output = {
        "check": "saved_model_reply_revalidation",
        "status": "passed" if passed else "failed",
        "execution_mode": "offline_replay",
        "response_origin": "saved_model_response",
        "analysis_status": result["status"],
        "validation_error": result.get("validation_error"),
        "delivery_status": checked["status"],
        "check_reason": checked["check_reason"],
        "issue": result.get("issue"),
        "advice": result.get("advice"),
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "automatic_apply": False,
        "application_code_updated": False,
        "original_report_unchanged": True,
        "source_record_id": summary["record_id"],
        "response_sha256": expected_body,
        "prompt_sha256": previous["prompt_sha256"],
        "parser_sha256": NEW_SHA,
        "output_dir": str(folder),
    }
    save(folder, "summary.json", output)
    save(
        folder,
        "provenance.json",
        {
            "source_directory": str(source),
            "original_module_sha256": OLD_SHA,
            "parser_candidate_sha256": NEW_SHA,
            "diagnostic_parser_sha256": digest(instrumented.encode()),
            "source_record_id": summary["record_id"],
            "source_response_sha256": expected_body,
            "execution_mode": "offline_replay",
            "response_source_note": "synthetic_reply marks replay execution; original answer came from a prior real model call",
        },
    )
    return output


if __name__ == "__main__":
    try:
        report = replay(
            Path.cwd(),
            Path.home() / "sec-rule-refine-data/synthetic-chain-diagnostic-o4olbtkt",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["status"] == "passed" else 1)
    except Exception as error:
        report = {
            "status": "failed",
            "error_type": type(error).__name__,
            "model_calls": 0,
            "redis_calls": 0,
            "database_calls": 0,
        }
        if isinstance(error, ValueError) and str(error).startswith(
            (
                "工具版本",
                "项目模块",
                "分析模块",
                "修复位置",
                "修复副本",
                "已保存回答",
                "诊断工具",
                "诊断位置",
                "只允许",
                "材料或提示词",
                "原记录",
                "离线回答",
            )
        ):
            report["reason"] = str(error)
        print(json.dumps(report, ensure_ascii=False))
        raise SystemExit(1)
