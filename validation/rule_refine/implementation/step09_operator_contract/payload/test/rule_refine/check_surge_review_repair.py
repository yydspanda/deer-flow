"""Re-review one verified saved job; preserve samples and spend at most one SDK call."""

import argparse
import copy
import hashlib
import importlib
import json
import os
import sys
import types
import uuid
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_SOURCE = "surge-real-csv-runtime-z4l8mtrg"
MODULE = "my_workflows/zeus/rule_refine"
SAFE_STOP_CODES = {
    "explicit_LOCAL_profile_required",
    "loaded_LOCAL_profile_required",
    "input_budget_exceeded",
    "request_structure_over_budget",
    "analysis_disabled",
    "surge_disabled",
    "prepared_source_or_configuration_changed",
    "source_requires_one_completed_job",
    "source_directory_must_not_be_symlink",
    "source_jobs_must_not_be_symlink",
    "source_job_directory_invalid",
    "source_job_identity_mismatch",
    "saved_two_phase_job_required",
    "saved_generation_proposal_required",
    "saved_event_field_policy_required",
    "saved_review_draft_link_mismatch",
    "saved_review_draft_issues_mismatch",
    "result_identity_or_hash_mismatch",
    "artifact_hash_mismatch",
    "phase_chain_mismatch",
    "phase_hash_link_mismatch",
    "parsed_response_mismatch",
    "prompt_context_mismatch",
    "prompt_rule_mismatch",
    "selected_evidence_mismatch",
    "field_usage_contract_mismatch",
    "field_usage_contract_version_mismatch",
    "field_usage_contract_version_missing",
    "operator_contract_version_mismatch",
    "loopback_model_proxy_required",
    "model_policy_missing",
}


def modules(root):
    package = types.ModuleType("_saved_review_repair_" + uuid.uuid4().hex)
    package.__path__ = [str(root / MODULE)]
    sys.modules[package.__name__] = package
    return lambda name: importlib.import_module(package.__name__ + "." + name)


def _unavailable(*args, **kwargs):
    raise AssertionError("saved_review_must_not_load_business_data_or_generate")


def _folder(source):
    source = Path(source).expanduser().absolute()
    if source.is_symlink():
        raise ValueError("source_directory_must_not_be_symlink")
    if (source / "result.json").is_file():
        folder = source
    else:
        jobs = source / "jobs"
        if jobs.is_symlink():
            raise ValueError("source_jobs_must_not_be_symlink")
        candidates = list(jobs.glob("*/result.json"))
        if len(candidates) != 1:
            raise ValueError("source_requires_one_completed_job")
        folder = candidates[0].parent
    if folder.is_symlink() or folder.parent.name != "jobs":
        raise ValueError("source_job_directory_invalid")
    return folder


def _fingerprint(load, path):
    return hashlib.sha256(load("surge_processor")._read_bytes(path)).hexdigest()


def verify_snapshot(prepared):
    for name, expected in prepared["snapshot"].items():
        if _fingerprint(prepared["load"], Path(name)) != expected:
            raise ValueError("prepared_source_or_configuration_changed")


def prepare(root, source_dir, environ):
    profiles = [
        environ[key] for key in ("env_profile", "ENV_PROFILE") if key in environ
    ]
    if not profiles or any(value.upper() != "LOCAL" for value in profiles):
        raise ValueError("explicit_LOCAL_profile_required")
    root = Path(root).expanduser().absolute()
    load = modules(root)
    processor = load("surge_processor")
    ai = load("surge_ai_review")
    project_paths = sorted((root / MODULE).glob("*.py")) + [
        root / "config/rule_refine.analysis.local.json",
        root / "config/rule_refine.surge.local.json",
        Path(__file__).resolve(),
    ]
    project_snapshot = {str(path): _fingerprint(load, path) for path in project_paths}
    settings = load("surge_settings").load_settings(root)
    analysis, reason = load("background_service")._settings(root, environ)
    if not settings["enabled"] or analysis is None:
        raise ValueError(reason or "surge_disabled")
    budget = load("surge_runtime").budget_from_settings(settings)
    folder = _folder(source_dir)
    read = processor._read
    result_bytes = processor._read_bytes(folder / "result.json")
    record = json.loads(result_bytes)
    original = read(folder / "input.json")
    outcome = {**record["outcome"], "record_id": record["record_id"]}
    verifier = processor.SurgeProcessor(
        folder.parent.parent,
        load_case=_unavailable,
        load_rules=_unavailable,
        generate=_unavailable,
        model=original["model"],
        budget=budget,
    )
    verifier.verify_durable_result(original["signal"], outcome)
    if folder.name != record["job_id"]:
        raise ValueError("source_job_identity_mismatch")
    if [item["phase"] for item in record["phases"]] != ["generate", "review"]:
        raise ValueError("saved_two_phase_job_required")
    case = read(folder / "case.json")
    case_source = case.get("source", {})
    counterfactual = processor._rule_snapshot(case["rule"]) != processor._rule_snapshot(
        case.get("original_rule", case["rule"])
    )
    generation = read(folder / "generate/parsed.json")
    draft = generation.get("reply")
    if not isinstance(draft, dict) or draft.get("status") != "proposal":
        raise ValueError("saved_generation_proposal_required")
    selection = read(folder / "review/selection.json")
    previous_request = read(folder / "review/request.json")
    context = previous_request["context"]
    if ai.request_field_usage_version(previous_request) is None:
        raise ValueError("saved_event_field_policy_required")
    if context.get("draft") != ai.review_draft_projection(
        draft,
        operator_contract_version=ai.request_operator_contract_version(
            previous_request
        ),
    ):
        raise ValueError("saved_review_draft_link_mismatch")
    if context.get("draft_validation_issues", []) != draft.get("validation_issues", []):
        raise ValueError("saved_review_draft_issues_mismatch")
    # The existing builder checks the entire unchanged sample set against the
    # current operator budget. A failure stops; no truncation or budget increase.
    request = ai.build_request(
        case["rule"],
        selection["current_samples"],
        selection["history_samples"],
        context["coverage"],
        draft=draft,
        operator_context=context["operator_context"],
        phase="review",
        limits=budget,
    )
    ai.request_field_usage_version(request)
    ai.request_operator_contract_version(request)
    snapshot = {
        **project_snapshot,
        str(folder / "result.json"): hashlib.sha256(result_bytes).hexdigest(),
        **{str(folder / name): digest for name, digest in record["artifacts"].items()},
    }
    prepared = {
        "root": root,
        "load": load,
        "analysis": analysis,
        "budget": budget,
        "source_folder": folder,
        "snapshot": snapshot,
        "request": request,
        "selection": copy.deepcopy(selection["selection"]),
        "rule": copy.deepcopy(case["rule"]),
        "source": {
            "source_directory": str(folder),
            "source_record_id": record["record_id"],
            "source_result_sha256": snapshot[str(folder / "result.json")],
            "original_requested_model": original["model"],
            "generation_prompt_sha256": read(folder / "generate/request.json")[
                "prompt_sha256"
            ],
            "generation_response_sha256": generation["response_sha256"],
            "original_review_prompt_sha256": previous_request["prompt_sha256"],
            "original_review_selection_sha256": processor._hash(selection),
            "source_artifact_hashes": dict(record["artifacts"]),
            "saved_chain_verified": True,
            "original_report_unchanged": True,
            "generation_origin": "saved_model_response",
            "material_source": case_source.get(
                "material_source", "saved_materials_unspecified"
            ),
            "source_kind": case_source.get("kind"),
            "source_coverage": copy.deepcopy(case["coverage"]),
            "old_review_answer_used_as_input": False,
            "review_samples_reused": True,
            "additional_sampling_performed": False,
            "historical_configuration_reconstruction": False,
            "configuration_scope": "saved_counterfactual_rule_not_current_online_verification"
            if counterfactual
            else "saved_rule_snapshot_not_current_online_verification",
        },
    }
    verify_snapshot(prepared)
    return prepared


def preview(prepared):
    request = prepared["request"]
    return {
        "status": "ready_for_review_only",
        "model": prepared["analysis"]["model"],
        "original_requested_model": prepared["source"]["original_requested_model"],
        "generation_reused": True,
        "generation_model_calls_this_command": 0,
        "review_samples_reused": True,
        "previous_review_answer_used_as_input": False,
        "source_record_id": prepared["source"]["source_record_id"],
        "selection": prepared["selection"],
        "current_count": len(request["context"]["current_samples"]),
        "history_count": len(request["context"]["history_samples"]),
        "review_prompt_version": request["prompt_version"],
        "operator_contract_version": request["context"]["operator_contract_version"],
        "field_usage_policy_version": request["context"]["field_usage_policy_version"],
        "input_bytes": request["input_bytes"],
        "estimated_input_tokens": request["estimated_input_tokens"],
        "input_token_budget": request["input_token_budget"],
        "model_output_token_limit": None,
        "max_sdk_requests": 1,
        "business_database_calls": 0,
        "automatic_apply": False,
    }


def run(prepared, output_dir, generate):
    """One injectable review attempt; never invoke or rewrite the original job."""
    verify_snapshot(prepared)
    folder = Path(output_dir).expanduser().absolute()
    source = prepared["source_folder"].resolve()
    target = folder.resolve()
    if target == source or source in target.parents or target in source.parents:
        raise ValueError("output_must_be_separate_from_source_job")
    folder.mkdir(mode=0o700)
    module = prepared["load"]("surge_processor")
    ai = prepared["load"]("surge_ai_review")
    write = module._write
    request = copy.deepcopy(prepared["request"])
    write(folder / "source.json", prepared["source"])
    write(folder / "source-hashes.json", prepared["snapshot"])
    write(folder / "request.json", request)
    verify_snapshot(prepared)
    write(
        folder / "started.json",
        {
            "request_sha256": module._hash(request),
            "model": prepared["analysis"]["model"],
        },
    )
    response = None
    error_code = None
    error_type = None
    parsed = None
    try:
        response = generate(copy.deepcopy(request))
        # Persist the transport and exact answer before any contract validation.
        write(folder / "response.json", response)
        if not isinstance(response, dict) or not isinstance(
            response.get("transport"), dict
        ):
            raise ValueError("invalid_model_adapter_result")
        transport = response["transport"]
        if transport.get("error_code") or not isinstance(response.get("content"), str):
            error_code = "model_request_failed"
        elif transport.get("finish_reason") != "stop":
            error_code = "model_response_not_complete"
        else:
            parsed = ai.parse_reply(
                response["content"],
                rule=prepared["rule"],
                visible_samples=request["context"]["current_samples"]
                + request["context"]["history_samples"],
                phase="review",
                field_usage_policy_version=ai.request_field_usage_version(request),
                operator_contract_version=ai.request_operator_contract_version(request),
            )
    except Exception as error:
        error_type = type(error).__name__
        error_code = (
            error.code
            if isinstance(error, ai.ReplyValidationError)
            else "review_request_or_recording_failed"
        )
        write(
            folder / "error.json", {"error_type": error_type, "error_code": error_code}
        )
    status = "blocked"
    if parsed is not None:
        status = "received" if parsed["status"] == "proposal" else "not_applicable"
        if parsed["validation_issues"]:
            status = "manual_review"
        write(folder / "parsed.json", parsed)
    source_unchanged = True
    try:
        verify_snapshot(prepared)
    except (OSError, ValueError):
        source_unchanged = False
        error_code = "source_or_configuration_changed_after_request"
        if parsed is not None:
            status = "manual_review"
    transport = response.get("transport", {}) if isinstance(response, dict) else {}
    summary = {
        "check": "rule_refine_saved_review_repair",
        "status": status,
        "analysis_status": parsed["status"] if parsed else "analysis_failed",
        "recommendation_ready": status == "received",
        "ai_review_completed": parsed is not None,
        "generation_reused": True,
        "generation_model_calls_this_command": 0,
        "review_samples_reused": True,
        "additional_sampling_performed": False,
        "previous_review_answer_used_as_input": False,
        "source_record_id": prepared["source"]["source_record_id"],
        "rule_code": prepared["rule"]["rule_code"],
        "material_source": prepared["source"]["material_source"],
        "model": prepared["analysis"]["model"],
        "review_prompt_version": request["prompt_version"],
        "operator_contract_version": request["context"]["operator_contract_version"],
        "field_usage_policy_version": request["context"]["field_usage_policy_version"],
        "current_count": len(request["context"]["current_samples"]),
        "history_count": len(request["context"]["history_samples"]),
        "model_output_token_limit": None,
        "sdk_request_calls": transport.get("sdk_request_calls"),
        "request_attempts": 1,
        "upstream_attempts": transport.get("upstream_attempts"),
        "transport": transport,
        "error_code": error_code,
        "error_type": error_type,
        "validation_issues": parsed["validation_issues"] if parsed else [],
        "business_database_calls": 0,
        "business_alerts_submitted": 0,
        "zeus_callback_calls": 0,
        "automatic_apply": False,
        "filter_replay_performed": False,
        "filter_coverage_measured": False,
        "semantic_quality_verified": False,
        "candidate_engine_availability_verified": False,
        "configuration_scope": prepared["source"]["configuration_scope"],
        "configuration_current_check": "not_performed_saved_rule_only",
        "original_report_unchanged": True if source_unchanged else None,
        "original_report_modified_by_tool": False,
        "source_snapshot_unchanged_after_request": source_unchanged,
        "application_code_updated": False,
        "output_dir": str(folder),
        "report_file": str(folder / "REPORT.md"),
    }
    report = [
        "# 规则告警突增建议：复用草案，仅重新复核",
        "",
        "规则：" + prepared["rule"]["rule_code"] + "；状态：" + status + "。",
        "沿用已验证保存链中的生成草案和同一批复核原文；未提供旧复核回答，也没有重新生成或补样。",
        "本次使用保存的规则快照；没有查询当前线上配置。原报告保留。",
        "材料来源："
        + str(prepared["source"]["material_source"])
        + "（沿用原任务记录，本次不重新召回）。",
        f"当前 {summary['current_count']} 条、历史 {summary['history_count']} 条；最多一次新SDK请求。",
        "字段用途/格式/引用检查不是业务正确性或上线批准；未执行过滤、未测量全量覆盖率，未提交或应用规则。",
    ]
    if not source_unchanged:
        report += [
            "",
            "请求后发现源文件或配置变化；已保存收到的原文，本结果需人工核对，不自动重试。",
        ]
    if parsed:
        if parsed["validation_issues"]:
            report += [
                "",
                "## 条件仍需修改",
                "",
                json.dumps(parsed["validation_issues"], ensure_ascii=False, indent=2),
            ]
        report += [
            "",
            "## 问题",
            "",
            parsed["issue"],
            "",
            "## 建议及前提",
            "",
            parsed["recommendation"],
            "",
            parsed["certainty"],
            "",
            "## 条件建议",
            "",
            "实际排除范围以JSON为准；说明文字不会自动增加条件。",
            "",
            "```json",
            json.dumps(parsed["filter"], ensure_ascii=False, indent=2),
            "```",
            "",
            parsed.get("review_notes") or "",
        ]
    else:
        report += [
            "",
            "本次复核尚未通过："
            + str(error_code)
            + "。原始响应若已收到，保存在response.json中。",
        ]
    write(folder / "REPORT.md", ("\n".join(report) + "\n").encode("utf-8"), raw=True)
    write(folder / "summary.json", summary)
    return summary


def execute(prepared):
    verify_snapshot(prepared)
    root, load = prepared["root"], prepared["load"]
    sys.path.insert(0, str(root))
    import httpx
    from openai import OpenAI
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    import util.root_config as rc
    from config.circuit_breaker import RedisCircuitBreaker
    from config.model_limits import get_model_policy
    from nas.config import APP_KEY_DICT
    from util.model_semaphore import RedisModelSemaphore

    if rc.ENV != "LOCAL":
        raise ValueError("loaded_LOCAL_profile_required")
    endpoint = rc.LITELLM_OPENAI_SERVER
    url = urlsplit(endpoint)
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("loopback_model_proxy_required")
    model, budget = prepared["analysis"]["model"], prepared["budget"]
    policy = get_model_policy(model)
    if policy is None:
        raise ValueError("model_policy_missing")
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Reserve a unique name, then let run exclusively create its output folder.
    directory = parent / ("surge-repair-review-" + uuid.uuid4().hex[:12])
    print("本次复核目录：" + str(directory), flush=True)
    with ExitStack() as stack:
        redis_client = Redis.from_url(rc.REDIS_URL, decode_responses=True)
        stack.callback(redis_client.close)
        redis_client.connection_pool.connection_kwargs.update(
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
            retry_on_error=[],
        )
        http = stack.enter_context(
            httpx.Client(trust_env=False, timeout=budget.timeout_seconds)
        )
        client = OpenAI(
            base_url=endpoint,
            api_key=APP_KEY_DICT["litellm_key"],
            max_retries=0,
            timeout=budget.timeout_seconds,
            http_client=http,
        )
        stack.callback(client.close)
        breaker = RedisCircuitBreaker(redis_client)
        semaphore = RedisModelSemaphore(
            redis_client, lease_ms=policy.lease_ms, heartbeat_ms=policy.heartbeat_ms
        )
        breaker.assert_closed(model)
        lease = stack.enter_context(
            semaphore.acquire(policy.semaphore_key, policy.concurrency, wait_timeout=0)
        )

        def generate(request):
            verify_snapshot(prepared)
            lease.assert_valid()
            print("复用生成草案和原复核样本；开始唯一一次AI复核。", flush=True)
            response = load("surge_runtime").call_model(
                client, request, model=model, budget=budget
            )
            try:
                if response["transport"]["error_code"] == "request_failed":
                    breaker.record_failure(model)
                else:
                    breaker.record_success(model)
            except Exception as error:
                response["transport"]["circuit_update_error_type"] = type(
                    error
                ).__name__
            return response

        return run(prepared, directory, generate)


def ask(prompt):
    with (
        open("/dev/tty", "r", encoding="utf-8") as reader,
        open("/dev/tty", "w", encoding="utf-8") as writer,
    ):
        writer.write(prompt)
        writer.flush()
        return reader.readline().strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path.home() / "sec_know_model"))
    parser.add_argument("--source")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        source = args.source
        if source is None:
            default = Path.home() / "sec-rule-refine-data" / DEFAULT_SOURCE
            source = ask("原报告目录（回车用 " + str(default) + "）：") or str(default)
        print(
            "核对保存链，复用生成草案与同一批复核原文；不重新召回或生成。", flush=True
        )
        prepared = prepare(Path(args.project_root), source, dict(os.environ))
        print(json.dumps(preview(prepared), ensure_ascii=False, indent=2), flush=True)
        if not args.live:
            return 0
        print(
            "仅重新复核，最多1次SDK请求；代理内部可能重试。结果另存，不修改旧报告或规则。重新执行LIVE会再次请求模型。",
            flush=True,
        )
        if ask("确认复核请输入 LIVE，其他输入退出：") != "LIVE":
            return 0
        summary = execute(prepared)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print(Path(summary["report_file"]).read_text(encoding="utf-8"), flush=True)
        return 0 if summary["status"] in {"received", "not_applicable"} else 1
    except Exception as error:
        code = str(error).split(":", 1)[0]
        print(
            json.dumps(
                {
                    "check": "rule_refine_saved_review_repair",
                    "status": "stopped",
                    "error_type": type(error).__name__,
                    "error_code": code
                    if code in SAFE_STOP_CODES
                    else "review_preparation_or_execution_failed",
                    "automatic_resubmission": False,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
