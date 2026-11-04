"""One fresh assessment of frozen review evidence, without the old proposed filter."""

import copy
import json
import tempfile
from pathlib import Path

import ai_review
import resume_review
import surge_runner as runner

RESPONSE_FORMAT = {"type": "json_object"}
VALID_STATUSES = {"proposal", "no_change", "insufficient_evidence"}


def prepare(source, root, *, prepare_saved=resume_review.prepare):
    p, recovered = prepare_saved(source, root, reuse_review_samples=True)
    previous = recovered["review_selection"]
    context = previous["request"]["context"]
    current, history = context["current_samples"], context["history_samples"]
    coverage = {
        **context["coverage"],
        "phase": "generate",
        "current_model_samples": len(current),
        "history_model_samples": len(history),
        "new_current_samples": 0,
        "reused_current_samples": len(current),
        "new_history_samples": 0,
        "reused_history_samples": len(history),
        "current_sample_basis": "unchanged_previous_review_samples",
    }
    request = ai_review.build_request(
        context["rule"],
        current,
        history,
        coverage,
        draft=None,
        operator_context=context["operator_context"],
        phase="generate",
        independent=True,
        limits=p.limits,
    )
    p.independent_selection = {
        "request": request,
        "current_samples": copy.deepcopy(current),
        "history_samples": copy.deepcopy(history),
        "selection": {
            "phase": "assessment",
            "current_count": len(current),
            "history_count": len(history),
            "history_available_count": len(p.case["history_samples"]),
            "new_current_count": 0,
            "reused_current_count": len(current),
            "review_samples_reused": True,
            "selection_basis": "unchanged_previous_review_samples",
            "additional_sampling_performed": False,
            "filter_replay_performed": False,
        },
    }
    provenance = {
        "source_artifacts": recovered["provenance"],
        "original_proposal_not_provided": True,
        "original_review_not_provided": True,
        "raw_review_materials_unchanged": True,
        "independent_prompt_sha256": request["prompt_sha256"],
        "independent_prompt_version": request["prompt_version"],
        "review_method": "independent_reassessment",
    }
    runner.verify(p)
    return p, provenance


def _recorded_calls(child):
    result_file = child / "result.json"
    if result_file.is_file():
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            return result.get("model_transport", {}).get("sdk_request_calls")
        except (ValueError, OSError):
            return None
    return None if (child / "request-started.json").exists() else 0


def single_chain(p, directory, invoke, *, check=None, recovered_generation=None):
    """Run once; preserve invalid output without turning it into a recommendation."""
    selection = p.independent_selection
    child = directory / "assessment"
    final = None
    verify = check or (lambda: runner.verify(p))
    summary = {
        "check": "rule_refine_independent_assessment",
        "status": "failed",
        "analysis_status": "analysis_failed",
        "rule_code": p.case["rule"]["rule_code"],
        "model": p.model,
        "material_source": "historical_csv",
        "review_method": "independent_reassessment",
        "ai_review_completed": False,
        "original_proposal_not_provided": True,
        "original_review_not_provided": True,
        "review_samples_reused": True,
        "selection": selection["selection"],
        "sdk_request_calls": 0,
        "recommendation_ready": False,
        "semantic_quality_verified": False,
        "validation_scope": ai_review.VALIDATION_SCOPE,
        "requested_response_format": RESPONSE_FORMAT,
        "response_format_enforcement_verified": False,
        "model_output_token_limit": None,
        "additional_sampling_performed": False,
        "observed_live_surge": False,
        "history_normal_labels_assumed": False,
        "original_report_unchanged": False,
        "application_code_updated": False,
        "business_database_calls": 0,
        "business_alerts_submitted": 0,
        "zeus_callback_calls": 0,
        "filter_replay_performed": False,
        "filter_coverage_measured": False,
        "automatic_apply": False,
        "output_dir": str(directory),
    }
    try:
        if recovered_generation is not None:
            raise ValueError("independent_assessment_does_not_accept_a_draft")
        verify()
        child.mkdir(mode=0o700)
        print("开始独立分析：只使用同一批原始材料，不提供旧过滤条件。", flush=True)
        try:
            result = invoke(p, selection, child)
        except Exception:
            summary["sdk_request_calls"] = _recorded_calls(child)
            raise
        transport = result.get("model_transport", {})
        summary.update(
            sdk_request_calls=transport.get("sdk_request_calls"),
            transport=transport,
            error_code=result.get("error_code"),
            error_path=result.get("error_path"),
            response_sha256=result.get("response_sha256"),
        )
        verify()
        summary["original_report_unchanged"] = True
        if result.get("format_valid"):
            candidate = result["draft"]
            if candidate["status"] not in VALID_STATUSES:
                raise ValueError("unexpected_assessment_status")
            ready = candidate.get("recommendation_ready", False)
            summary.update(
                status=candidate["status"] if ready else "needs_revision",
                analysis_status=candidate["status"],
                recommendation_ready=ready,
                output_warnings=candidate.get("output_warnings", []),
                validation_issues=candidate.get("validation_issues", []),
            )
            artifact = {
                **candidate,
                "rule_code": p.case["rule"]["rule_code"],
                "rule_config_version": p.case["rule"]["rule_config_version"],
                "review_method": "independent_reassessment",
                "ai_review_completed": False,
                "semantic_quality_verified": False,
                "automatic_apply": False,
                "filter_replay_performed": False,
                "filter_coverage_measured": False,
                "additional_sampling_performed": False,
            }
            runner.save(
                directory,
                "recommendation.json" if ready else "draft-recommendation.json",
                artifact,
            )
            final = candidate
        else:
            summary["stop_reason"] = "model_request_or_output_failed"
    except Exception as error:
        summary.update(
            status="failed",
            recommendation_ready=False,
            error_type=type(error).__name__,
            stop_reason="processing_failed",
        )
    runner.save(directory, "summary.json", summary)
    runner.save(
        directory, "REPORT.md", report(p, summary, final, child).encode("utf-8")
    )
    return summary


def report(p, summary, final, child):
    chosen = summary["selection"]
    lines = [
        "# 规则突增建议：独立分析",
        "",
        f"规则：{p.case['rule']['rule_code']}；状态：{summary['status']}。",
        f"本次材料：当前{chosen['current_count']}条、历史{chosen['history_count']}条。",
        "",
        "沿用上次复核展示的完整原日志；没有提供旧草案、旧复核回答或预期过滤条件。",
        "本次是一次新的独立分析，不代表原来的生成与复核两步已经通过。",
        "样本没有新增，结构校验不证明业务判断正确。JSON模式仅为请求设置，服务是否强制执行尚未验证。",
        "",
    ]
    if not chosen["history_count"]:
        lines += ["没有突增前历史对照，不能据此证明前后变化。", ""]
    if final is not None:
        for title, value in (
            ("主要场景", final["issue"]),
            ("建议及前提", final["recommendation"]),
            ("模型判断把握", final["certainty"]),
            ("分析说明", final.get("review_notes") or "未提供"),
        ):
            lines += [f"## {title}", "", value, ""]
        lines += [
            "## 条件建议",
            "",
            "```json",
            json.dumps(final["filter"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        if final.get("validation_issues"):
            lines += [
                "以下字段问题尚未解决；草案已保存，未生成可采纳的条件。",
                "",
                json.dumps(final["validation_issues"], ensure_ascii=False, indent=2),
                "",
            ]
        if final.get("model_extra_fields"):
            lines += [
                "## 模型补充信息",
                "",
                json.dumps(final["model_extra_fields"], ensure_ascii=False, indent=2),
                "",
            ]
        if final.get("sample_request"):
            lines += ["模型附带的补充取样请求已保留；本次没有追加取样或模型调用。", ""]
    else:
        lines += [
            f"未形成可用结果：{summary.get('error_code') or summary.get('stop_reason')}。",
            "",
        ]
        answer = child / "assistant-content.txt"
        if answer.is_file():
            lines += [
                "## 模型已返回的原文（未通过校验）",
                "",
                "\n".join(
                    "    " + line
                    for line in answer.read_text(encoding="utf-8").splitlines()
                ),
                "",
            ]
    lines += [
        "未执行过滤条件，未测量全量覆盖率或线上降量率；结果仅本地保存，未提交或应用规则。",
        "",
    ]
    return "\n".join(lines)


def main(*, tool_hashes=None):
    output = None
    try:
        root = Path.home() / "sec_know_model"
        default = Path.home() / "sec-rule-refine-data/surge-review-v3-0uw8bmkc"
        source = (
            Path(runner.ask(f"原报告目录（回车用 {default}）：") or default)
            .expanduser()
            .resolve()
        )
        p, provenance = prepare(source, root)
        chosen = p.independent_selection
        print(
            json.dumps(
                {
                    "status": "ready_for_independent_assessment",
                    "model": p.model,
                    "original_proposal_not_provided": True,
                    "original_review_not_provided": True,
                    "raw_review_materials_unchanged": True,
                    "selection": chosen["selection"],
                    "prompt_version": chosen["request"]["prompt_version"],
                    "input_bytes": chosen["request"]["input_bytes"],
                    "estimated_input_tokens": chosen["request"][
                        "estimated_input_tokens"
                    ],
                    "context_budget": p.limits.audit(),
                    "requested_response_format": RESPONSE_FORMAT,
                    "response_format_enforcement_verified": False,
                    "max_sdk_requests": 1,
                    "timeout_seconds": p.limits.timeout_seconds,
                    "model_output_token_limit": None,
                    "automatic_apply": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            "只对同一批原日志独立分析一次，最多1次SDK请求；代理内部可能重试。结果另存，不修改原报告或规则。"
        )
        if runner.ask("确认调用请输入 LIVE，其他输入退出：") != "LIVE":
            print("已退出，未调用模型。")
            return 0
        runner.verify(p)
        parent = Path.home() / "sec-rule-refine-data"
        parent.mkdir(exist_ok=True, mode=0o700)
        output = Path(tempfile.mkdtemp(prefix="surge-independent-", dir=parent))
        print("本次独立分析目录：" + str(output), flush=True)
        for name, value in (
            ("provenance.json", provenance),
            ("case-audit.json", p.case["audit"]),
            ("coverage.json", chosen["request"]["context"]["coverage"]),
            ("configuration-hashes.json", p.snapshot),
            ("tool-hashes.json", tool_hashes or {}),
        ):
            runner.save(output, name, value)
        summary = runner.live_chain(
            p, output, processor=single_chain, response_format=RESPONSE_FORMAT
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("\n" + (output / "REPORT.md").read_text(encoding="utf-8"))
        return 0 if summary["status"] in VALID_STATUSES | {"needs_revision"} else 2
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "reason": str(error)[:300]
                    if isinstance(error, ValueError)
                    else "assessment_preparation_or_recording_failed",
                    "output_dir": str(output) if output else None,
                    "automatic_apply": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
