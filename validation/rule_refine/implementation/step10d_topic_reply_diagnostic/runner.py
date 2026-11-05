"""Inspect final model output without changing prompts or creating a ZEUS rule."""

import argparse
import copy
import hashlib
import json
import math
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from diagnostic_contract import EXPECTED_SOURCES
from topic_materials import ask, fingerprint, prepare

AI_CONTRACT = "topic-condition-tree-v1"
MAX_CONTENT_BYTES = 64 * 1024
MAX_METADATA_BYTES = 64 * 1024


def validate_sources(root):
    for name, digest in EXPECTED_SOURCES.items():
        path = Path(root) / name
        if not path.is_file() or fingerprint(path) != digest:
            # Only a reviewed relative filename, never source text or credentials.
            raise ValueError("diagnostic_source_mismatch:" + name)


def write_new(path, raw):
    path = Path(path)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path, value):
    raw = (
        json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False).encode("utf-8")
        + b"\n"
    )
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("diagnostic_metadata_over_limit")
    write_new(path, raw)


def safe_transport(value):
    value = value if isinstance(value, dict) else {}
    result = {}
    for key in (
        "schema_version",
        "transport_mode",
        "requested_model",
        "returned_model",
        "finish_reason",
        "error_code",
        "error_type",
        "endpoint_sha256",
        "request_config_sha256",
    ):
        item = value.get(key)
        if (
            item is None
            or isinstance(item, str)
            and len(item) <= 256
            and item.isprintable()
        ):
            result[key] = item
    for key in (
        "sdk_max_retries",
        "sdk_request_calls",
        "timeout_seconds",
        "model_output_token_limit",
        "upstream_attempts",
        "http_status",
        "elapsed_ms",
    ):
        item = value.get(key)
        if (
            item is None
            or type(item) in (int, float)
            and math.isfinite(item)
            and 0 <= item <= 10**15
        ):
            result[key] = item
    for key in ("response_received", "response_format_enforcement_verified"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    if value.get("requested_response_format") == {"type": "json_object"}:
        result["requested_response_format"] = {"type": "json_object"}
    usage = value.get("usage")
    result["usage"] = {
        key: usage.get(key)
        if isinstance(usage, dict)
        and type(usage.get(key)) is int
        and 0 <= usage[key] <= 10**15
        else None
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    return result


def capture_reply(directory, phase, request, response):
    """Keep exact final content before parsing. Never serialize a response object."""
    if phase not in {"generate", "review"} or request.get("phase") != phase:
        raise ValueError("diagnostic_invalid_phase")
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("diagnostic_directory_required")
    content = response.get("content") if isinstance(response, dict) else None
    raw = (
        content.encode("utf-8", errors="surrogatepass")
        if isinstance(content, str)
        else None
    )
    prefix = (
        raw[:MAX_CONTENT_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
        if raw is not None
        else None
    )
    path = directory / (phase + ".final.content.txt")
    if prefix is not None:
        write_new(path, prefix)
    metadata = {
        "phase": phase,
        "content_source": "choices[0].message.content_via_installed_call_model",
        "prompt_version": request.get("prompt_version"),
        "prompt_sha256": request.get("prompt_sha256"),
        "content_file": path.name if prefix is not None else None,
        "content_present": raw is not None,
        "full_content_bytes": len(raw) if raw is not None else None,
        "full_content_sha256": hashlib.sha256(raw).hexdigest()
        if raw is not None
        else None,
        "saved_content_bytes": len(prefix) if prefix is not None else 0,
        "saved_content_sha256": hashlib.sha256(prefix).hexdigest()
        if prefix is not None
        else None,
        "content_truncated": len(prefix) != len(raw) if raw is not None else False,
        "capture_limit_bytes": MAX_CONTENT_BYTES,
        "transport": safe_transport(
            response.get("transport") if isinstance(response, dict) else None
        ),
        "request_messages_saved": False,
        "separate_reasoning_saved": False,
        "final_content_may_echo_source_data": True,
    }
    write_json(directory / (phase + ".reply.json"), metadata)
    return metadata


def process(prepared, directory, generate):
    """Use the installed workflow; replace its entire delivery entry point."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    replies = directory / "replies"
    replies.mkdir(mode=0o700)
    captures = []

    def capture(request):
        if len(captures) >= 2 or request.get("phase") != (
            "generate" if not captures else "review"
        ):
            raise ValueError("diagnostic_model_call_limit")
        # Persist attempt identity before invoking transport. No raw prompt.
        phase = request["phase"]
        write_json(
            replies / (phase + ".started.json"),
            {
                "phase": phase,
                "prompt_version": request.get("prompt_version"),
                "prompt_sha256": request.get("prompt_sha256"),
            },
        )
        answer = generate(request)
        captures.append(capture_reply(replies, phase, request, answer))
        return answer

    def forbidden_submit(_):
        raise RuntimeError("diagnostic_submission_forbidden")

    base = prepared["load"]("topic_processor").TopicProcessor

    class DiagnosticProcessor(base):
        def _deliver(self, folder, signal, job_id, candidate, *, calls):
            self._verify_candidate(folder, signal, job_id, candidate)
            return self._finish(
                folder,
                signal,
                job_id,
                "diagnostic_completed",
                calls=calls,
                reason="diagnostic_only_not_submitted",
                ai_review_completed=True,
                analysis_status=candidate["analysis_status"],
                recommendation_ready=candidate["recommendation_ready"],
            )

    processor = DiagnosticProcessor(
        directory,
        load_case=lambda _: copy.deepcopy(prepared["case"]),
        load_rules=prepared["rules_factory"],
        generate=capture,
        model=prepared["model"],
        budget=prepared["budget"],
        submit=forbidden_submit,
        operator_context=prepared["operator_context"],
        delivery_directory=directory / "unused-delivery-directory",
        delivery_scope="diagnostic-only",
        ai_contract=AI_CONTRACT,
    )
    outcome = processor.process(prepared["signal"])
    return {
        **outcome,
        "diagnostic_only": True,
        "reply_captures": captures,
        "zeus_posts_this_command": 0,
        "submission_enabled": False,
        "diagnostic_directory": str(directory),
        "application_code_updated": False,
    }


def preview_summary(prepared):
    selection = prepared["preview"]
    return {
        "check": "topic_reply_diagnostic_preview",
        "status": "ready",
        "model": prepared["model"],
        "ai_contract": AI_CONTRACT,
        "current_available": len(prepared["case"]["current_samples"]),
        "history_selected": len(prepared["case"]["history_samples"]),
        "generation_selection": selection["selection"],
        "input_bytes": selection["request"]["input_bytes"],
        "estimated_input_tokens": selection["request"]["estimated_input_tokens"],
        "input_token_budget": prepared["budget"].input_token_budget,
        "max_sdk_requests": 2,
        "sdk_max_retries": 0,
        "max_zeus_posts": 0,
        "final_content_limit_bytes_per_phase": MAX_CONTENT_BYTES,
        "original_prompts_and_sampling_unchanged": True,
        "reproduces_previous_response": False,
        "request_messages_saved": False,
        "separate_reasoning_saved": False,
        "application_code_updated": False,
        "model_calls_this_command": 0,
    }


def execute(prepared):
    for path, digest in prepared["snapshot"].items():
        if fingerprint(path) != digest:
            raise ValueError("prepared_sources_changed")
    sys.path.insert(0, str(prepared["root"]))
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

    if rc.ENV != prepared["profile"]:
        raise ValueError("loaded_manual_profile_mismatch")
    endpoint, key = rc.LITELLM_OPENAI_SERVER, APP_KEY_DICT.get("litellm_key")
    url = urlsplit(endpoint)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not isinstance(key, str)
        or not key
    ):
        raise ValueError("invalid_manual_model_proxy")
    model, budget = prepared["model"], prepared["budget"]
    policy = get_model_policy(model)
    if policy is None:
        raise ValueError("manual_model_policy_missing")
    base = Path(rc.DATA_DIR) / "rule_refine/topic_reply_diagnostics"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    container = Path(tempfile.mkdtemp(prefix="diagnostic-", dir=base))
    directory = container / "analysis"
    print("本次诊断目录：" + str(directory), flush=True)
    calls = 0
    with ExitStack() as stack:
        redis = Redis.from_url(rc.REDIS_URL, decode_responses=True)
        stack.callback(redis.close)
        redis.connection_pool.connection_kwargs.update(
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
            retry_on_error=[],
        )
        redis.connection_pool.max_connections = 8
        http = stack.enter_context(
            httpx.Client(trust_env=False, timeout=budget.timeout_seconds)
        )
        client = OpenAI(
            base_url=endpoint,
            api_key=key,
            max_retries=0,
            timeout=budget.timeout_seconds,
            http_client=http,
        )
        stack.callback(client.close)
        breaker = RedisCircuitBreaker(redis)
        semaphore = RedisModelSemaphore(
            redis, lease_ms=policy.lease_ms, heartbeat_ms=policy.heartbeat_ms
        )
        breaker.assert_closed(model)
        lease = stack.enter_context(
            semaphore.acquire(policy.semaphore_key, policy.concurrency, wait_timeout=0)
        )

        def generate(request):
            nonlocal calls
            if calls >= 2:
                raise ValueError("diagnostic_model_call_limit")
            lease.assert_valid()
            calls += 1
            print("开始诊断AI阶段：" + request["phase"], flush=True)
            answer = prepared["load"]("surge_runtime").call_model(
                client, request, model=model, budget=budget
            )
            try:
                if answer["transport"]["error_code"] == "request_failed":
                    breaker.record_failure(model)
                else:
                    breaker.record_success(model)
            except Exception as error:
                answer["transport"]["circuit_update_error_type"] = type(error).__name__
            return answer

        result = process(prepared, directory, generate)
        lease.assert_valid()
    result.update(
        check="topic_reply_diagnostic",
        model_calls_this_command=calls,
        client_retries=0,
        upstream_attempts=None,
        business_database_rows_written=0,
        redis_usage="existing_model_capacity_and_circuit_only",
        source_snapshot_unchanged=all(
            fingerprint(p) == h for p, h in prepared["snapshot"].items()
        ),
        material_source="real_historical_exports",
        counterfactual_rule=True,
        observed_live_surge=False,
        prompts_and_sampling_unchanged=True,
    )
    write_json(directory / "diagnostic-summary.json", result)
    write_json(
        directory / "source-audit.json",
        {
            "source_sha256": prepared["snapshot"],
            "tool_sha256": prepared.get("tool_hashes", {}),
            "source_material_sha256": [
                {"kind": s["kind"], "sha256": s.get("file_sha256")}
                for s in prepared["audit"].get("sources", [])
            ],
        },
    )
    return result


def main(argv=None, *, tool_hashes=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_sources(args.project_root)
        prepared = prepare(args.project_root, dict(os.environ))
        prepared["tool_hashes"] = dict(tool_hashes or {})
        print(
            json.dumps(preview_summary(prepared), ensure_ascii=False, indent=2),
            flush=True,
        )
        if not args.live:
            return 0
        if prepared["case"]["execute_type"]["status"] != "resolved":
            print("executeType未确定，仅预览。", flush=True)
            return 1
        print(
            "沿用当前提示词和取样，新做最多2次模型请求；不提交ZEUS、不重试。每阶段最终回答最多保存64KiB，可能含模型复述的日志内容，请留在内网；不保存请求原文或独立思考字段。",
            flush=True,
        )
        if ask("确认诊断调用请输入 LIVE，其他输入退出：") != "LIVE":
            return 0
        result = execute(prepared)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        print(
            "请回传上方诊断汇总；最终回答原文保存在诊断目录/replies，先留在内网。",
            flush=True,
        )
        return 0 if result.get("status") == "diagnostic_completed" else 1
    except Exception as error:
        code = (
            str(error)
            if isinstance(error, ValueError)
            and str(error).startswith("diagnostic_source_mismatch:")
            else None
        )
        print(
            json.dumps(
                {
                    "check": "topic_reply_diagnostic",
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_code": code,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
