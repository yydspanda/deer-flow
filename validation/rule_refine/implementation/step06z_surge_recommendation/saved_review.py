"""Recover both saved AI answers and a local report without calling any service."""

import json
import sys
import tempfile
from pathlib import Path

import ai_review
import saved_generation as saved


def parse_phase(originals, context, phase):
    try:
        draft = ai_review.parse_reply(
            originals[f"{phase}/assistant-content.txt"].decode("utf-8"),
            rule=context["rule"],
            visible_samples=context["current_samples"] + context["history_samples"],
            phase=phase,
        )
        return {
            "format_valid": True,
            "draft": draft,
            "error_code": None,
            "error_path": None,
        }
    except ai_review.ReplyValidationError as error:
        return {
            "format_valid": False,
            "draft": None,
            "error_code": error.code,
            "error_path": error.path,
        }


def verify_chain(generation, review, generation_draft):
    if generation["rule"] != review["rule"]:
        raise ValueError("saved_review_rule_differs_from_generation")
    expected_draft = {
        key: generation_draft[key]
        for key in ai_review.REPLY_KEYS
        if key in generation_draft
    }
    if review.get("draft") != expected_draft:
        raise ValueError("saved_review_did_not_receive_original_draft")
    if review.get("draft_model_extra_fields", {}) != generation_draft.get(
        "model_extra_fields", {}
    ):
        raise ValueError("saved_review_did_not_receive_generation_extra_fields")
    if "draft_validation_issues" in review and review[
        "draft_validation_issues"
    ] != generation_draft.get("validation_issues", []):
        raise ValueError("saved_review_draft_issues_changed")
    original_rows = {
        row["alert_id"]: row
        for row in generation["current_samples"] + generation["history_samples"]
    }
    reviewed_rows = {
        row["alert_id"]: row
        for row in review["current_samples"] + review["history_samples"]
    }
    if not set(generation_draft["sample_alert_ids"]).issubset(reviewed_rows):
        raise ValueError("saved_review_missing_cited_originals")
    if any(
        original_rows[identity] != reviewed_rows[identity]
        for identity in original_rows.keys() & reviewed_rows.keys()
    ):
        raise ValueError("saved_review_shared_sample_changed")


def render(summary, results, raw_review):
    lines = [
        "# 规则突增过滤建议：已保存AI复核结果",
        "",
        "沿用已完成的生成和复核请求及原回答，本次离线校验、重新出报告；没有再次调用模型。",
        "原报告和应用代码不修改，建议仅保存在本地。",
        "本次只恢复输出结构与两阶段关联，没有重新评估建议内容；原回答的矛盾或不当判断仍需复核。",
        "",
        f"规则：{summary['rule_code']}；检查状态：{summary['status']}；分析状态：{summary['analysis_status']}。",
        "",
    ]
    for phase in summary["phases"]:
        label = "生成" if phase["phase"] == "generate" else "复核"
        lines += [
            f"{label}材料：当前{phase['current_count']}条、历史{phase['history_count']}条。",
            "",
        ]
    if summary["phases"][-1]["history_count"] == 0:
        lines += ["本次没有突增前历史对照，不能据此证明前后变化。", ""]
    draft = results["review"]["draft"] if summary["saved_chain_verified"] else None
    if draft is not None:
        if not summary["recommendation_ready"]:
            lines += [
                "## 待调整的字段",
                "",
                "模型已返回建议。下列字段不在本次实际日志字段目录中，原建议完整保留，但条件尚不可直接采纳。没有自动猜测或替换字段。",
                "",
            ]
            for issue in draft["validation_issues"]:
                lines.append(
                    f"- {'.'.join(issue['path'])}：`{issue['field']}` 未出现在本次日志字段中。"
                )
            lines += [
                "",
                "本次可见字段：",
                "",
                ", ".join(summary["visible_field_names"]),
                "",
            ]
        lines += [
            "## 主要场景",
            "",
            draft["issue"],
            "",
            "## 建议及前提",
            "",
            draft["recommendation"],
            "",
            draft["certainty"],
            "",
            "## 条件建议",
            "",
            "```json",
            json.dumps(draft["filter"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## AI复核说明",
            "",
            draft.get("review_notes") or "未提供独立说明",
            "",
        ]
        for phase in ("generate", "review"):
            extra = results[phase]["draft"].get("model_extra_fields", {})
            if extra:
                label = "生成" if phase == "generate" else "复核"
                lines += [
                    f"## {label}回答的补充字段（原样保留）",
                    "",
                    "以下为模型补充内容，没有合并到工具状态或自动执行配置中。",
                    "",
                    "```json",
                    json.dumps(extra, ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
    else:
        lines += [
            "## 尚未通过的检查",
            "",
            json.dumps(
                [
                    {key: item[key] for key in ("phase", "error_code", "error_path")}
                    for item in summary["phases"]
                    if item["error_code"]
                ],
                ensure_ascii=False,
            ),
            "",
            "## 保存的复核原文",
            "",
            "\n".join("    " + line for line in raw_review.split("\n")),
            "",
        ]
    lines += [
        "没有执行程序过滤试算，没有测量全量覆盖率或线上降量率，未应用或提交ZEUS规则。",
        "",
    ]
    return "\n".join(lines)


def replay(source, *, output_parent=None, tool_hashes=None):
    source = Path(source).expanduser().resolve()
    originals, contexts, requests, previous, results = {}, {}, {}, {}, {}
    old_tools = None
    for phase in ("generate", "review"):
        raw_files, request, old, context, tools = saved.load_original(
            source, phase=phase
        )
        if any(
            name in originals and originals[name] != raw
            for name, raw in raw_files.items()
        ):
            raise ValueError("saved_artifacts_changed_during_read")
        originals.update(raw_files)
        contexts[phase], requests[phase], previous[phase] = context, request, old
        results[phase] = parse_phase(raw_files, context, phase)
        old_tools = tools
    if results["generate"]["format_valid"]:
        verify_chain(
            contexts["generate"], contexts["review"], results["generate"]["draft"]
        )
    complete = all(item["format_valid"] for item in results.values())
    ready = complete and results["review"]["draft"]["recommendation_ready"]
    if any((source / name).read_bytes() != raw for name, raw in originals.items()):
        raise ValueError("source_changed_during_replay")
    parent = Path(output_parent).resolve() if output_parent else source.parent
    if parent == source or source in parent.parents:
        raise ValueError("output_must_be_outside_original_report")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = Path(tempfile.mkdtemp(prefix="surge-final-replay-", dir=parent))
    summary = {
        "check": "rule_refine_saved_review_replay",
        "status": "passed" if ready else "needs_revision" if complete else "blocked",
        "execution_mode": "offline_replay",
        "response_origin": "saved_model_responses",
        "rule_code": contexts["review"]["rule"]["rule_code"],
        "analysis_status": results["review"]["draft"]["status"]
        if complete
        else "analysis_failed",
        "ai_review_response_recovered": complete,
        "saved_chain_verified": complete,
        "recommendation_ready": ready,
        "visible_field_names": sorted(
            ai_review._fields(
                contexts["review"]["current_samples"]
                + contexts["review"]["history_samples"]
            )
        ),
        "validation_scope": "saved_output_structure_and_chain_only",
        "semantic_quality_verified": False,
        "model_calls": 0,
        "sdk_request_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "application_code_updated": False,
        "original_report_unchanged": True,
        "original_prompts_preserved": True,
        "automatic_apply": False,
        "formal_recommendation_submitted": False,
        "filter_replay_performed": False,
        "filter_coverage_measured": False,
        "phases": [],
        "output_dir": str(output),
    }
    for phase in ("generate", "review"):
        context, result = contexts[phase], results[phase]
        draft = result["draft"]
        summary["phases"].append(
            {
                "phase": phase,
                "format_valid": result["format_valid"],
                "analysis_status": draft["status"] if draft else "analysis_failed",
                "current_count": len(context["current_samples"]),
                "history_count": len(context["history_samples"]),
                "previous_error_code": previous[phase].get("error_code"),
                "error_code": result["error_code"],
                "error_path": result["error_path"],
                "output_warnings": draft["output_warnings"] if draft else [],
                "validation_issues": draft["validation_issues"] if draft else [],
                "recommendation_ready": draft["recommendation_ready"]
                if draft
                else False,
                "ignored_sample_request": draft.get("ignored_sample_request")
                if draft
                else None,
                "extra_field_names": sorted(draft.get("model_extra_fields", {}))
                if draft
                else [],
                "response_sha256": previous[phase]["response_sha256"],
                "prompt_sha256": requests[phase]["prompt_sha256"],
                "original_model_transport": previous[phase]["model_transport"],
                "sdk_request_calls_this_command": 0,
            }
        )
        child = output / phase
        child.mkdir(mode=0o700)
        for name in ("request.json", "assistant-content.txt"):
            saved.save(child, name, originals[f"{phase}/{name}"])
        saved.save(child, "original-result.json", originals[f"{phase}/result.json"])
        saved.save(child, "revalidated-result.json", result)
    if complete:
        rule = contexts["review"]["rule"]
        final = results["review"]["draft"]
        recommendation = {
            **{
                key: rule[key]
                for key in ai_review.RULE_KEYS
                if key in rule and key != "configuration"
            },
            **final,
            "change_type": "add_exclusion" if final["filter"] else None,
            "review_method": "ai_sample_review",
            "recovered_offline": True,
            "automatic_apply": False,
            "filter_replay_performed": False,
            "filter_coverage_measured": False,
        }
        saved.save(
            output,
            "recommendation.json" if ready else "draft-recommendation.json",
            recommendation,
        )
    saved.save(
        output,
        "provenance.json",
        {
            "original_report": str(source),
            "original_tool_hashes": old_tools,
            "original_artifact_sha256": {
                name: saved.sha(raw) for name, raw in originals.items()
            },
            "replay_tool_hashes": tool_hashes or {},
            "change": "preserve_advice_and_report_all_field_issues_without_ready_export",
        },
    )
    saved.save(output, "summary.json", summary)
    saved.save(
        output,
        "REPORT.md",
        render(
            summary, results, originals["review/assistant-content.txt"].decode("utf-8")
        ).encode("utf-8"),
    )
    return summary


def main(*, tool_hashes=None):
    try:
        default = Path.home() / "sec-rule-refine-data/surge-review-v2-ly8xsiwm"
        if len(sys.argv) > 1:
            source = sys.argv[1]
        else:
            print(f"已完成复核请求的报告目录（回车用 {default}）：", end="", flush=True)
            with open("/dev/tty", "r", encoding="utf-8") as terminal:
                source = terminal.readline().strip() or default
        print(
            "离线恢复两次已保存回答及最终报告；不调用模型，不修改原报告。", flush=True
        )
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
                    else "saved_artifact_unavailable",
                    "model_calls": 0,
                    "application_code_updated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
