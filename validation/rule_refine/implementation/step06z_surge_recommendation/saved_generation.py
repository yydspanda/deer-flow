"""Revalidate the saved generation offline using exactly its original evidence."""

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import ai_review

FILES = (
    "generate/request.json",
    "generate/result.json",
    "generate/assistant-content.txt",
    "tool-hashes.json",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unpack(raw):
    return json.loads(raw, object_pairs_hook=ai_review._unique)


def wrapper_shape(text):
    """Report layout only, never alert fields, command values, or prose."""
    stripped = text.strip()
    opening = re.match(r"(`{3,}|~{3,})", stripped)
    closing = re.search(r"(`{3,}|~{3,})$", stripped)
    first_line = stripped.split("\n", 1)[0]
    return {
        "line_count_lf": text.count("\n") + 1,
        "opening_marker": opening.group(0)[0] if opening else None,
        "opening_marker_length": len(opening.group(0)) if opening else 0,
        "closing_marker": closing.group(0)[0] if closing else None,
        "closing_marker_length": len(closing.group(0)) if closing else 0,
        "opening_line_contains_object_start": "{" in first_line,
        "legacy_three_line_wrapper": (
            first_line.strip().lower() in {"```", "```json"}
            and stripped.rsplit("\n", 1)[-1].strip() == "```"
            and stripped.count("\n") >= 2
        ),
    }


def load_original(directory, *, phase="generate"):
    if phase not in {"generate", "review"}:
        raise ValueError("unknown_saved_phase")
    files = (
        f"{phase}/request.json",
        f"{phase}/result.json",
        f"{phase}/assistant-content.txt",
        "tool-hashes.json",
    )
    originals = {name: (directory / name).read_bytes() for name in files}
    request = unpack(originals[files[0]])
    result = unpack(originals[files[1]])
    response = originals[files[2]]
    tools = unpack(originals[files[3]])
    if sha(response) != result.get("response_sha256"):
        raise ValueError("saved_response_hash_mismatch")
    transport = result.get("model_transport", {})
    if (
        transport.get("response_received") is not True
        or transport.get("finish_reason") != "stop"
        or transport.get("error_code") is not None
        or transport.get("error_type") is not None
    ):
        raise ValueError("saved_transport_not_complete")
    messages = request.get("messages")
    if sha(ai_review.encoded(messages)) != request.get("prompt_sha256"):
        raise ValueError("saved_prompt_hash_mismatch")
    if (
        not isinstance(messages, list)
        or len(messages) != 2
        or messages[0].get("role") != "system"
        or messages[1].get("role") != "user"
        or not isinstance(messages[1].get("content"), str)
    ):
        raise ValueError("saved_messages_invalid")
    context = unpack(messages[1]["content"])
    if context != request.get("context"):
        raise ValueError("saved_context_mismatch")
    if request.get("phase") != phase or context.get("phase") != phase:
        raise ValueError(
            "saved_generation_required"
            if phase == "generate"
            else "saved_review_required"
        )
    if not isinstance(tools, dict) or not isinstance(tools.get("ai_review"), str):
        raise ValueError("original_parser_hash_missing")
    return originals, request, result, context, tools


def save(directory, name, value):
    raw = value if isinstance(value, bytes) else ai_review.encoded(value)
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def report(summary, draft):
    lines = [
        "# 突增建议：已保存回答离线复核",
        "",
        "沿用第一次模型回答和当时展示的原始样本；本次未调用模型，未补写建议。",
        "原报告保留，应用代码未修改。本次仅离线核对生成回答，不执行也不核验第二步AI复核。",
        "",
        f"检查状态：{summary['status']}；分析状态：{summary['analysis_status']}。",
        f"原请求展示：当前{summary['current_count']}条、历史{summary['history_count']}条。",
        "",
    ]
    if draft is not None:
        if draft["validation_issues"]:
            lines += [
                "## 待调整的字段",
                "",
                "建议已保留，字段问题应交给第二步复核修正。",
                "",
            ]
            for issue in draft["validation_issues"]:
                lines += [
                    f"- {'.'.join(issue['path'])}：`{issue['field']}` 未出现在原日志字段中。"
                ]
            lines += [""]
        lines += [
            "## 模型原建议",
            "",
            draft["issue"],
            "",
            draft["recommendation"],
            "",
            draft["certainty"],
            "",
            "```json",
            json.dumps(draft["filter"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        if draft.get("model_extra_fields"):
            lines += [
                "## 模型补充信息",
                "",
                "以下额外字段按模型原文保留，未作自动执行依据；其中的限制、否定或撤回意见也需一并阅读。",
                "",
                "```json",
                json.dumps(draft["model_extra_fields"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
    else:
        lines += [f"仍需处理：{summary['error_code']}；未生成可继续复核的草案。", ""]
    lines += ["没有执行过滤条件，没有测量全量覆盖率或实际降量。", ""]
    return "\n".join(lines)


def replay(source, *, output_parent=None, tool_hashes=None):
    source = Path(source).expanduser().resolve()
    originals, request, old, context, old_tools = load_original(source)
    text = originals[FILES[2]].decode("utf-8")
    current, history = context["current_samples"], context["history_samples"]
    draft, error_code, error_path = None, None, None
    try:
        draft = ai_review.parse_reply(
            text, rule=context["rule"], visible_samples=current + history
        )
    except ai_review.ReplyValidationError as error:
        error_code, error_path = error.code, error.path
    # Never consult current CSV/project settings or expand the original evidence.
    if any((source / name).read_bytes() != raw for name, raw in originals.items()):
        raise ValueError("source_changed_during_replay")
    parent = Path(output_parent).resolve() if output_parent else source.parent
    if parent == source or source in parent.parents:
        raise ValueError("output_must_be_outside_original_report")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = Path(tempfile.mkdtemp(prefix="surge-generation-replay-", dir=parent))
    summary = {
        "check": "rule_refine_saved_generation_replay",
        "status": "passed"
        if draft and draft["recommendation_ready"]
        else "needs_revision"
        if draft
        else "blocked",
        "execution_mode": "offline_replay",
        "response_origin": "saved_model_response",
        "rule_code": context["rule"]["rule_code"],
        "format_valid": draft is not None,
        "recommendation_ready": draft["recommendation_ready"] if draft else False,
        "validation_issues": draft["validation_issues"] if draft else [],
        "analysis_status": draft["status"] if draft else "analysis_failed",
        "output_warnings": draft["output_warnings"] if draft else [],
        "previous_error_code": old.get("error_code"),
        "error_code": error_code,
        "error_path": error_path,
        "wrapper_shape": wrapper_shape(text),
        "current_count": len(current),
        "history_count": len(history),
        "model_calls": 0,
        "sdk_request_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "application_code_updated": False,
        "original_report_unchanged": True,
        "original_prompt_preserved": True,
        "saved_artifact_hashes_verified": True,
        "installed_sources_checked": False,
        "ai_review_completed": False,
        "automatic_apply": False,
        "filter_replay_performed": False,
        "filter_coverage_measured": False,
        "response_sha256": sha(originals[FILES[2]]),
        "prompt_sha256": request["prompt_sha256"],
        "original_model_transport": old["model_transport"],
        "next_step": "ai_review_pending" if draft else "inspect_saved_answer",
        "output_dir": str(output),
    }
    provenance = {
        "original_report": str(source),
        "source_artifacts_sha256": {name: sha(raw) for name, raw in originals.items()},
        "original_tool_hashes": old_tools,
        "replay_tool_hashes": tool_hashes or {},
        "change": "complete_json_with_missing_closing_fence_is_preserved_and_warned",
    }
    for name, value in (
        ("request.json", originals[FILES[0]]),
        ("original-result.json", originals[FILES[1]]),
        ("assistant-content.txt", originals[FILES[2]]),
        ("provenance.json", provenance),
        ("summary.json", summary),
        ("REPORT.md", report(summary, draft).encode("utf-8")),
    ):
        save(output, name, value)
    if draft is not None:
        save(output, "draft.json", draft)
    return summary


def main(*, tool_hashes=None):
    try:
        default = Path.home() / "sec-rule-refine-data/surge-ai-review-dvy79hvr"
        if len(sys.argv) > 1:
            source = sys.argv[1]
        else:
            print(f"原报告目录（回车用 {default}）：", end="", flush=True)
            with open("/dev/tty", "r", encoding="utf-8") as terminal:
                source = terminal.readline().strip() or default
        print("离线复核已保存的模型回答；不调用模型，结果另存。", flush=True)
        summary = replay(source, tool_hashes=tool_hashes)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(
            "\n"
            + (Path(summary["output_dir"]) / "REPORT.md").read_text(encoding="utf-8")
        )
        return 0 if summary["status"] in {"passed", "needs_revision"} else 2
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "reason": str(error)
                    if isinstance(error, ValueError)
                    else "source_artifact_unavailable",
                    "model_calls": 0,
                    "application_code_updated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
