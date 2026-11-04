"""Recheck a saved reply missing certainty; never issue a new model request."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import context_budget
import diagnose_saved_reply as diagnostic
import review


class ReplayError(ValueError):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _save(directory, name, value):
    raw = value if isinstance(value, bytes) else review.encoded(value)
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _tool_hashes():
    result = {}
    for module in (context_budget, review, diagnostic, sys.modules[__name__]):
        digest = getattr(module, "__source_sha256__", None)
        if digest is None:
            digest = _sha(Path(module.__file__).read_bytes())
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReplayError("invalid_replay_source_hash")
        result[module.__name__] = digest
    return result


def _evaluation(project, pins, analysis):
    relative = diagnostic.RELATIVE_ROOT / "filter_evaluation.py"
    raw = (project / relative).read_bytes()
    if _sha(raw) != pins.get(relative.as_posix()):
        raise ReplayError("source_hash_mismatch:filter_evaluation")
    name = analysis.__package__ + ".filter_evaluation"
    module = ModuleType(name)
    module.__package__ = analysis.__package__
    module.__file__ = str(project / relative)
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec"), module.__dict__)
    return module


def replay(report_dir, project_root, *, output_parent=None, batch=1):
    directory = Path(report_dir).resolve()
    project = Path(project_root).resolve()
    if type(batch) is not int or batch < 1:
        raise ReplayError("invalid_batch_number")
    child = directory / f"batch-{batch:03d}"
    names = (
        "source-pins.json",
        "output-schema.json",
        "limits.json",
        *(
            f"{child.name}/{name}"
            for name in (
                "context.json",
                "request.json",
                "analysis.json",
                "summary.json",
                "assistant-content.txt",
            )
        ),
    )
    original_files = {name: (directory / name).read_bytes() for name in names}
    original_hashes = {name: _sha(raw) for name, raw in original_files.items()}

    def saved(name, *, root=False):
        key = name if root else f"{child.name}/{name}"
        return json.loads(original_files[key], object_pairs_hook=diagnostic._unique)

    old = diagnostic.diagnose(directory, project, batch=batch)
    errors = old.get("errors", [])
    if (
        old.get("status") != "contract_invalid"
        or len(errors) != 1
        or errors[0].get("path") != ["certainty"]
        or errors[0].get("error_type") != "missing"
    ):
        raise ReplayError("not_the_missing_certainty_case")
    pins = saved("source-pins.json", root=True)
    old_analysis, old_summary = saved("analysis.json"), saved("summary.json")
    context, request = saved("context.json"), saved("request.json")
    raw = original_files[f"{child.name}/assistant-content.txt"]
    if _sha(raw) != old_analysis.get("response_sha256"):
        raise ReplayError("saved_response_hash_mismatch")
    transport = old_analysis.get("model_transport") or {}
    if (
        not isinstance(transport, dict)
        or old["saved_transport"].get("finish_reason") != "stop"
        or transport.get("error_code") is not None
        or transport.get("error_type") is not None
        or old_summary.get("model_error_code") is not None
        or old_summary.get("model_error_type") is not None
    ):
        raise ReplayError("saved_transport_not_complete")
    if not isinstance(request, dict) or not isinstance(context, dict):
        raise ReplayError("invalid_saved_request")
    prompt_hash = _sha(review.encoded(request.get("messages")))
    if prompt_hash != request.get("prompt_sha256") or prompt_hash != old_analysis.get(
        "prompt_sha256"
    ):
        raise ReplayError("saved_prompt_hash_mismatch")
    messages = request["messages"]
    if (
        len(messages) != 2
        or messages[1].get("role") != "user"
        or json.loads(messages[1]["content"], object_pairs_hook=diagnostic._unique)
        != context
    ):
        raise ReplayError("saved_context_does_not_match_request")
    limits = context_budget.ContextBudget(**saved("limits.json", root=True))
    with diagnostic._analysis(project, pins) as analysis:
        # Require the original saved contract before applying the one-field change.
        if analysis.AnalysisDraft.model_json_schema() != saved(
            "output-schema.json", root=True
        ):
            raise ReplayError("saved_schema_mismatch")
        evaluation = _evaluation(project, pins, analysis)
        context_budget.install_compatibility(
            SimpleNamespace(analysis=analysis, filter_evaluation=evaluation)
        )
        regenerated = review.build_request(context, analysis, limits=limits)
        if regenerated["prompt_sha256"] != prompt_hash:
            raise ReplayError("replay_prompt_differs_from_original")
        tool_hashes = _tool_hashes()
        schema = analysis.AnalysisDraft.model_json_schema()
        saved_results = {}
        result = review.analyze(
            context,
            lambda unused_messages: raw.decode("utf-8"),
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=lambda name, value: saved_results.__setitem__(name, value),
            limits=limits,
            response_source="saved_model_response",
            expected_prompt_sha256=prompt_hash,
        )
    if any(
        _sha((directory / name).read_bytes()) != digest
        for name, digest in original_hashes.items()
    ):
        raise ReplayError("original_artifacts_changed_during_replay")
    parent = (
        Path(output_parent).resolve()
        if output_parent is not None
        else Path.home() / "sec-rule-refine-data"
    )
    if parent == directory or directory in parent.parents:
        raise ReplayError("output_must_be_outside_original_report")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="historical-certainty-replay-", dir=parent))
    status = (
        "manual_review"
        if result.get("status") == "manual_review"
        else "passed"
        if result.get("format_valid")
        else "blocked"
    )
    summary = {
        "check": "historical_missing_certainty_replay",
        "status": status,
        "execution_mode": "offline_replay",
        "response_origin": "saved_model_response",
        "batch": batch,
        "analysis_status": result["status"],
        "format_valid": result["format_valid"],
        "validation_stage": result.get("validation_stage"),
        "error_code": result.get("error_code"),
        "certainty_source": result.get("certainty_source"),
        "output_warnings": result.get("output_warnings", []),
        "model_calls": 0,
        "sdk_request_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "business_alerts_submitted": 0,
        "automatic_apply": False,
        "formal_recommendation_submitted": False,
        "application_code_updated": False,
        "original_report_unchanged": True,
        "original_prompt_preserved": True,
        "source_hashes_verified": True,
        "old_schema_verified": True,
        "saved_response_reads": result.get("saved_response_reads"),
        "model_visible_alerts": len(context["samples"]),
        "model_visible_logs": sum(
            len(sample["raw_logs"]) for sample in context["samples"]
        ),
        "sample_match_counts": result.get("evaluation", {}).get("counts"),
        "response_sha256": _sha(raw),
        "prompt_sha256": prompt_hash,
        "source_model_transport": old["saved_transport"],
        "engine_compatibility": "not_verified",
        "output_dir": str(output),
    }
    for name, value in (
        ("assistant-content.txt", raw),
        ("analysis.json", result),
        ("summary.json", summary),
        ("original-output-schema.json", saved("output-schema.json", root=True)),
        ("replay-output-schema.json", schema),
        ("original-diagnostic.json", old),
        ("context.json", context),
        ("request.json", request),
        (
            "provenance.json",
            {
                "original_report": str(directory),
                "original_file_sha256": original_hashes,
                "installed_source_pins": pins,
                "replay_tool_source_sha256": tool_hashes,
                "change": "missing_certainty_is_explicitly_unknown",
                "output_schema_changed": True,
                "raw_response_modified": False,
                "new_model_request_sent": False,
            },
        ),
    ):
        _save(output, name, value)
    body = review.report_text(context, result, summary)
    report = (
        "# 已保存回答离线复核\n\n"
        "本次没有调用模型；沿用原请求、原始回答及原始样本。"
        "模型缺少 certainty 说明，显示为“未提供”，不推测或补写模型把握。"
        "原报告未修改，以下内容为新校验逻辑的离线复核结果。\n\n" + body
    )
    _save(output, "REPORT.md", report.encode("utf-8"))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-parent", type=Path)
    parser.add_argument("--batch", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        result = replay(
            args.report_dir,
            args.project_root,
            output_parent=args.output_parent,
            batch=args.batch,
        )
    except Exception as error:
        result = {
            "check": "historical_missing_certainty_replay",
            "status": "replay_stopped",
            "model_calls": 0,
            "sdk_request_calls": 0,
            "application_code_updated": False,
            "original_report_unchanged": True,
            "error_type": type(error).__name__,
            "reason": str(error)
            if isinstance(error, (ReplayError, diagnostic.DiagnosticError))
            else "saved_files_or_dependencies_unavailable",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "replay_stopped" else 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
