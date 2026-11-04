"""Continue only AI review after offline revalidation of a saved generation."""

import json
import tempfile
from pathlib import Path

import ai_review
import sampling
import saved_generation
import saved_review
import surge_runner as runner

EXTRA_FILES = (
    "case-audit.json",
    "configuration-hashes.json",
    "generate/selection.json",
)


def source_path(scope, audit, pins):
    sources = [item for item in audit["sources"] if item["scope"] == scope]
    if not sources:
        return None
    if len(sources) != 1:
        raise ValueError("ambiguous_saved_input")
    paths = [
        Path(path)
        for path, digest in pins.items()
        if digest == sources[0]["file_sha256"] and Path(path).is_absolute()
    ]
    if len(paths) != 1:
        raise ValueError("saved_input_path_not_unique")
    if runner.file_hash(paths[0]) != sources[0]["file_sha256"]:
        raise ValueError("saved_input_has_changed")
    return paths[0]


def prepare(source, root, *, prepare_case=runner.prepare, reuse_review_samples=False):
    source = Path(source).expanduser().resolve()
    originals, request, previous, context, old_tools = saved_generation.load_original(
        source
    )
    originals.update({name: (source / name).read_bytes() for name in EXTRA_FILES})
    audit = saved_generation.unpack(originals[EXTRA_FILES[0]])
    pins = saved_generation.unpack(originals[EXTRA_FILES[1]])
    chosen = saved_generation.unpack(originals[EXTRA_FILES[2]])
    current, history = context["current_samples"], context["history_samples"]
    draft = ai_review.parse_reply(
        originals["generate/assistant-content.txt"].decode("utf-8"),
        rule=context["rule"],
        visible_samples=current + history,
    )
    # Load unchanged source data for fresh review examples. Never regenerate the
    # first prompt or silently replace its model-visible samples with a new draw.
    current_path = source_path("current_file", audit, pins)
    history_path = source_path("history_file", audit, pins)
    if current_path is None:
        raise ValueError("saved_current_input_missing")
    cutoff = audit["cutoff"] if audit["cutoff_basis"] == "operator_supplied" else None
    p = prepare_case(root, current_path, history_path, cutoff=cutoff)
    if (
        p.case["coverage"] != audit["coverage"]
        or p.case["audit"]["source_rule_file_sha256"]
        != audit["source_rule_file_sha256"]
    ):
        raise ValueError("saved_case_has_changed")
    if ai_review._rule(p.case["rule"]) != context["rule"]:
        raise ValueError("saved_rule_has_changed")
    if p.model != previous["model_transport"]["requested_model"]:
        raise ValueError("configured_model_differs_from_saved_generation")
    for rows, source_rows in (
        (current, p.case["current_samples"]),
        (history, p.case["history_samples"]),
    ):
        available = {row["alert_id"]: row for row in ai_review._samples(source_rows)}
        if any(available.get(row["alert_id"]) != row for row in rows):
            raise ValueError("saved_visible_samples_changed")
    if (
        chosen.get("phase") != "generate"
        or chosen.get("current_count") != len(current)
        or chosen.get("history_count") != len(history)
    ):
        raise ValueError("saved_selection_inconsistent")
    p.generation = {
        "request": request,
        "selection": chosen,
        "current_samples": current,
        "history_samples": history,
    }
    review_provenance = {}
    if reuse_review_samples:
        files, old_request, old_result, old_context, _ = saved_generation.load_original(
            source, phase="review"
        )
        if any(
            name in originals and originals[name] != raw for name, raw in files.items()
        ):
            raise ValueError("saved_artifacts_changed_during_read")
        originals.update(files)
        saved_review.verify_chain(context, old_context, draft)
        # Preserve the entire previously shown evidence, including minority
        # activities. The rejected review answer is provenance, never new input.
        for field in ("current_samples", "history_samples"):
            available = {
                row["alert_id"]: row for row in ai_review._samples(p.case[field])
            }
            if any(available.get(row["alert_id"]) != row for row in old_context[field]):
                raise ValueError("saved_review_visible_samples_changed")
        selection_raw = (source / "review/selection.json").read_bytes()
        originals["review/selection.json"] = selection_raw
        selection = saved_generation.unpack(selection_raw)
        if (
            selection.get("phase") != "review"
            or selection.get("current_count") != len(old_context["current_samples"])
            or selection.get("history_count") != len(old_context["history_samples"])
        ):
            raise ValueError("saved_review_selection_inconsistent")
        request = ai_review.build_request(
            old_context["rule"],
            old_context["current_samples"],
            old_context["history_samples"],
            old_context["coverage"],
            draft=draft,
            operator_context=old_context["operator_context"],
            phase="review",
            limits=p.limits,
        )
        expected_context = dict(old_context)
        catalog = sorted(
            ai_review._fields(
                old_context["current_samples"] + old_context["history_samples"]
            )
        )
        if (
            "visible_field_names" in old_context
            and old_context["visible_field_names"] != catalog
        ):
            raise ValueError("saved_review_field_catalog_changed")
        expected_context["visible_field_names"] = catalog
        if draft["validation_issues"]:
            expected_context["draft_validation_issues"] = draft["validation_issues"]
        if request["context"] != expected_context:
            raise ValueError("saved_review_context_changed")
        review = {
            "request": request,
            "selection": selection,
            "current_samples": old_context["current_samples"],
            "history_samples": old_context["history_samples"],
        }
        review_provenance = {
            "review_samples_reused": True,
            "previous_review_answer_used_as_input": False,
            "previous_review_prompt_sha256": old_request["prompt_sha256"],
            "previous_review_response_sha256": old_result["response_sha256"],
            "previous_review_model_transport": old_result["model_transport"],
            "review_prompt_version": request["prompt_version"],
            "review_prompt_sha256": request["prompt_sha256"],
            "derived_field_catalog_added": "visible_field_names" not in old_context,
            "raw_review_materials_unchanged": True,
        }
    else:
        review = sampling.select(
            p.case,
            p.limits,
            operator_context=context["operator_context"],
            draft=draft,
            generation=p.generation,
        )
    original_hashes = {
        name: saved_generation.sha(raw) for name, raw in originals.items()
    }
    p.snapshot.update(
        {str(source / name): digest for name, digest in original_hashes.items()}
    )
    runner.verify(p)
    recovered = {
        "request_raw": originals["generate/request.json"],
        "response_raw": originals["generate/assistant-content.txt"],
        "original_result_raw": originals["generate/result.json"],
        "result": {
            "status": draft["status"]
            if draft["recommendation_ready"]
            else "needs_revision",
            "analysis_status": draft["status"],
            "recommendation_ready": draft["recommendation_ready"],
            "validation_issues": draft["validation_issues"],
            "format_valid": True,
            "draft": draft,
            "response_sha256": previous["response_sha256"],
            "execution_mode": "saved_response_revalidation",
            "sdk_request_calls_this_command": 0,
            "model_transport": previous["model_transport"],
        },
        "review_selection": review,
        "provenance": {
            "original_report": str(source),
            "original_tool_hashes": old_tools,
            "original_artifact_sha256": original_hashes,
            "original_prompt_sha256": p.generation["request"]["prompt_sha256"],
            "original_response_sha256": previous["response_sha256"],
            "generation_reused": True,
            "original_report_unchanged": True,
            **review_provenance,
        },
    }
    return p, recovered


def main(*, tool_hashes=None, reuse_review_samples=False):
    output = None
    try:
        root = Path.home() / "sec_know_model"
        default = (
            Path.home()
            / "sec-rule-refine-data"
            / (
                "surge-review-v2-ly8xsiwm"
                if reuse_review_samples
                else "surge-ai-review-dvy79hvr"
            )
        )
        source = (
            Path(runner.ask(f"原报告目录（回车用 {default}）：") or default)
            .expanduser()
            .resolve()
        )
        print(
            "沿用原草案及同一批复核材料，使用新版提示词重新复核一次。"
            if reuse_review_samples
            else "离线校验原回答并准备复核材料，不重跑生成。",
            flush=True,
        )
        if reuse_review_samples:
            p, recovered = prepare(source, root, reuse_review_samples=True)
        else:
            p, recovered = prepare(source, root)
        draft, review = recovered["result"]["draft"], recovered["review_selection"]
        print(
            json.dumps(
                {
                    "status": "ready_for_review_only",
                    "model": p.model,
                    "generation_reused": True,
                    "review_samples_reused": reuse_review_samples,
                    "review_prompt_version": review["request"]["prompt_version"],
                    "generation_model_calls_this_command": 0,
                    "generation_status": draft["status"],
                    "generation_filter": draft["filter"],
                    "output_warnings": draft["output_warnings"],
                    "review_selection": review["selection"],
                    "input_bytes": review["request"]["input_bytes"],
                    "estimated_input_tokens": review["request"][
                        "estimated_input_tokens"
                    ],
                    "visible_field_count": len(
                        review["request"]["context"]["visible_field_names"]
                    ),
                    "context_budget": p.limits.audit(),
                    "max_sdk_requests": 1,
                    "model_output_token_limit": None,
                    "automatic_apply": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            "只调用第二步AI复核，最多1次SDK请求；代理内部可能重试，超时600秒。结果另存，不修改原报告或规则。"
        )
        if runner.ask("确认复核请输入 LIVE，其他输入退出：") != "LIVE":
            print("已退出，未调用模型。")
            return 0
        runner.verify(p)
        parent = Path.home() / "sec-rule-refine-data"
        version = ai_review.PROMPT_VERSION.rsplit("-", 1)[-1]
        prefix = (
            f"surge-review-{version}-" if reuse_review_samples else "surge-review-only-"
        )
        output = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        print("本次复核目录：" + str(output), flush=True)
        for name, value in (
            ("provenance.json", recovered["provenance"]),
            ("case-audit.json", p.case["audit"]),
            ("coverage.json", p.case["coverage"]),
            ("configuration-hashes.json", p.snapshot),
            ("tool-hashes.json", tool_hashes or {}),
        ):
            runner.save(output, name, value)
        summary = runner.live_chain(p, output, recovered_generation=recovered)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("\n" + (output / "REPORT.md").read_text(encoding="utf-8"))
        return 0 if summary["status"] in {"reviewed", "needs_revision"} else 2
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "reason": str(error)[:300]
                    if isinstance(error, ValueError)
                    else "review_preparation_or_recording_failed",
                    "output_dir": str(output) if output else None,
                    "automatic_apply": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
