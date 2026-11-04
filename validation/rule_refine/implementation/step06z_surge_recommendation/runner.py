"""Copyable internal-Mac spike: sample, propose, independently review, save locally."""

import hashlib
import importlib
import json
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import ai_review
import context_budget
import historical_adapter
import sampling
import surge_materials


OPERATOR_CONTEXT = (
    "运营反馈：本批明显突增与统一巡检、运维或扫描任务有关。"
    "请根据原始日志自行识别具体行为，推荐能够减少主要重复场景的过滤条件；"
    "不需要排除所有告警，不把少数其他行为顺带忽略。"
)
SOURCE_FILES = (
    "my_workflows/zeus/rule_refine/csv_config_source.py",
    "util/root_config.py",
    "util/model_semaphore.py",
    "config/model_limits.py",
    "config/circuit_breaker.py",
    "config/rule_refine.analysis.local.json",
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode(
        "utf-8"
    )


def save(directory, name, value):
    raw = value if isinstance(value, bytes) else encode(value)
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def source_snapshot(root, paths):
    return {str(path): file_hash(root / path) for path in paths}


def verify(prepared):
    if source_snapshot(prepared.root, prepared.snapshot) != prepared.snapshot:
        raise ValueError("inputs_or_configuration_changed_since_preview")


def load_provider(root):
    # Import the installed provider without importing the application package.
    package = ModuleType("_surge_review_" + uuid4().hex)
    package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + ".csv_config_source")


def prepare(root, current_path, history_path=None, *, cutoff=None):
    root = Path(root).resolve()
    if any(
        os.environ.get(key, "LOCAL").upper() != "LOCAL"
        for key in ("env_profile", "ENV_PROFILE")
    ):
        raise ValueError("this_test_requires_LOCAL_profile")
    csv = Path.home() / "sec-rule-refine-data/rules.csv"
    paths = [*SOURCE_FILES, str(csv), str(current_path)]
    if history_path:
        paths.append(str(history_path))
    snapshot = source_snapshot(root, paths)
    if file_hash(current_path) != surge_materials.ALERT_SHA:
        raise ValueError(
            "本次脚本用于已确认的20261102175803453.csv；输入摘要不同，请先核对文件。"
        )
    provider = load_provider(root).CsvRuleConfigSource(csv)
    case = surge_materials.build_case(
        current_path,
        provider,
        history_path=history_path,
        cutoff=cutoff,
        simulate_known_exclusion=True,
    )
    settings = json.loads((root / SOURCE_FILES[-1]).read_text(encoding="utf-8"))
    model = settings.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("analysis_model_not_configured")
    sys.path.insert(0, str(root))
    import util.root_config as rc
    from nas.config import APP_KEY_DICT
    from config.model_limits import get_model_policy

    url = urlsplit(rc.LITELLM_OPENAI_SERVER)
    if (
        rc.ENV != "LOCAL"
        or url.scheme not in {"http", "https"}
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("LOCAL_loopback_model_proxy_required")
    key, policy = APP_KEY_DICT.get("litellm_key"), get_model_policy(model)
    if not key or policy is None:
        raise ValueError("model_credentials_or_policy_missing")
    limits = context_budget.ContextBudget.for_model(model)
    generation = sampling.select(case, limits, operator_context=OPERATOR_CONTEXT)
    result = SimpleNamespace(
        root=root,
        case=case,
        model=model,
        key=key,
        rc=rc,
        policy=policy,
        endpoint=rc.LITELLM_OPENAI_SERVER,
        limits=limits,
        generation=generation,
        snapshot=snapshot,
    )
    verify(result)
    return result


def call_model(
    prepared,
    selection,
    directory,
    client,
    *,
    transport_mode="live",
    response_format=None,
):
    request = selection["request"]
    visible = selection["current_samples"] + selection["history_samples"]
    save(directory, "request.json", request)
    save(directory, "selection.json", selection["selection"])
    save(directory, "request-started.json", {"max_sdk_calls": 1})

    def collect(unused_material, generate, **unused_kwargs):
        result = {"status": "analysis_failed", "format_valid": False}
        try:
            text = generate(request["messages"])
            raw = text.encode("utf-8")
            save(directory, "assistant-content.txt", raw)
            result["response_sha256"] = hashlib.sha256(raw).hexdigest()
            result["draft"] = ai_review.parse_reply(
                text,
                rule=prepared.case["rule"],
                visible_samples=visible,
                phase=request["phase"],
            )
            ready = result["draft"].get("recommendation_ready", True)
            result.update(
                status=result["draft"]["status"] if ready else "needs_revision",
                format_valid=True,
                recommendation_ready=ready,
                validation_issues=result["draft"].get("validation_issues", []),
            )
        except Exception as error:
            # Never echo a provider exception which can include credentials/data.
            result.update(error_type=type(error).__name__)
            status = getattr(error, "status_code", None)
            if type(status) is int and 400 <= status < 600:
                result["http_status"] = status
                result["http_error_response_received"] = True
                # Keep the provider's explanation locally, like original alert
                # material. Never print response bodies or authentication headers.
                try:
                    body = getattr(error, "body", None)
                    if body is None:
                        response = getattr(error, "response", None)
                        body = getattr(response, "text", None)
                    raw = encode(
                        {
                            "http_status": status,
                            "error_type": type(error).__name__,
                            "body": body,
                        }
                    )
                    if len(raw) > prepared.limits.output_bytes:
                        result["provider_error_save_status"] = "exceeds_record_budget"
                    else:
                        save(directory, "provider-error.json", raw)
                        result["provider_error_save_status"] = "saved_locally"
                        result["provider_error_file"] = "provider-error.json"
                except Exception as save_error:
                    result["provider_error_save_status"] = "save_failed"
                    result["provider_error_save_error_type"] = type(save_error).__name__
            if isinstance(error, ai_review.ReplyValidationError):
                result["error_code"] = error.code
                result["error_path"] = getattr(error, "path", None)
            else:
                result["error_code"] = "generation_or_recording_failed"
        return result

    historical_adapter.get_limits = lambda: prepared.limits
    historical_adapter.analyze_materials = collect
    result = historical_adapter.analyze_with_model(
        {},
        client,
        model=prepared.model,
        transport_mode=transport_mode,
        timeout_seconds=prepared.limits.timeout_seconds,
        **({"response_format": response_format} if response_format is not None else {}),
    )
    save(directory, "result.json", result)
    return result


def run_chain(prepared, directory, invoke, *, check=None, recovered_generation=None):
    """Two independent requests. Invalid first output stops; no silent retries."""
    summary = {
        "check": "rule_refine_surge_ai_review",
        "status": "failed",
        "rule_code": prepared.case["rule"]["rule_code"],
        "model": prepared.model,
        "material_source": "historical_csv",
        "observed_live_surge": False,
        "history_available_count": len(prepared.case["history_samples"]),
        "history_count": len(prepared.generation["history_samples"]),
        "history_target": 20,
        "history_normal_labels_assumed": False,
        "filter_replay_performed": False,
        "filter_coverage_measured": False,
        "automatic_apply": False,
        "application_code_updated": False,
        "business_database_calls": 0,
        "business_alerts_submitted": 0,
        "zeus_callback_calls": 0,
        "online_history_connected": False,
        "output_dir": str(directory),
        "sdk_request_calls": 0,
        "phases": [],
        "model_output_token_limit": None,
        "generation_reused": recovered_generation is not None,
        "recommendation_ready": False,
    }
    if recovered_generation and recovered_generation["provenance"].get(
        "review_samples_reused"
    ):
        summary.update(
            {
                "review_samples_reused": True,
                "review_prompt_version": recovered_generation["provenance"][
                    "review_prompt_version"
                ],
                "previous_review_answer_used_as_input": False,
            }
        )
    final = None
    failed_reply = None
    try:
        selection = prepared.generation
        for phase in ("generate", "review"):
            reused = phase == "generate" and recovered_generation is not None
            if check:
                check()
            child = directory / phase
            child.mkdir(mode=0o700)
            print(
                "复用已保存的生成回答；不重新调用生成。"
                if reused
                else "开始生成建议。"
                if phase == "generate"
                else "开始AI复核，使用新的上下文核对原文和建议。",
                flush=True,
            )
            try:
                if reused:
                    result = recovered_generation["result"]
                    save(child, "request.json", recovered_generation["request_raw"])
                    save(child, "selection.json", selection["selection"])
                    save(
                        child,
                        "assistant-content.txt",
                        recovered_generation["response_raw"],
                    )
                    save(
                        child,
                        "original-result.json",
                        recovered_generation["original_result_raw"],
                    )
                    save(child, "result.json", result)
                else:
                    result = invoke(prepared, selection, child)
            except Exception:
                if (child / "result.json").is_file():
                    saved = json.loads(
                        (child / "result.json").read_text(encoding="utf-8")
                    )
                    calls = (
                        0
                        if reused
                        else saved.get("model_transport", {}).get("sdk_request_calls")
                    )
                    summary["sdk_request_calls"] = (
                        summary["sdk_request_calls"] + calls
                        if type(calls) is int
                        and summary["sdk_request_calls"] is not None
                        else None
                    )
                elif (child / "request-started.json").exists():
                    summary["sdk_request_calls"] = None
                raise
            transport = result.get("model_transport", {})
            count = 0 if reused else transport.get("sdk_request_calls")
            summary["sdk_request_calls"] = (
                summary["sdk_request_calls"] + count
                if isinstance(count, int) and summary["sdk_request_calls"] is not None
                else None
            )
            summary["phases"].append(
                {
                    "phase": phase,
                    "status": result["status"],
                    "selection": selection["selection"],
                    "transport": transport,
                    "transport_origin": "saved_request" if reused else "this_command",
                    "sdk_request_calls_this_command": count,
                    "output_warnings": result.get("draft", {}).get(
                        "output_warnings", []
                    ),
                    "recommendation_ready": result.get("draft", {}).get(
                        "recommendation_ready", False
                    ),
                    "validation_issues": result.get("draft", {}).get(
                        "validation_issues", []
                    ),
                    "error_code": result.get("error_code"),
                    "error_path": result.get("error_path"),
                    "http_status": result.get("http_status"),
                    "http_error_response_received": result.get(
                        "http_error_response_received", False
                    ),
                    "provider_error_save_status": result.get(
                        "provider_error_save_status"
                    ),
                    "provider_error_file": result.get("provider_error_file"),
                }
            )
            summary["history_count"] = selection["selection"]["history_count"]
            if check:
                check()
            if not result.get("format_valid"):
                summary["stop_reason"] = "model_request_or_output_failed"
                answer_path = child / "assistant-content.txt"
                if answer_path.is_file():
                    failed_reply = answer_path.read_text(encoding="utf-8")
                break
            if phase == "generate":
                selection = (
                    recovered_generation["review_selection"]
                    if reused
                    else sampling.select(
                        prepared.case,
                        prepared.limits,
                        operator_context=OPERATOR_CONTEXT,
                        draft=result["draft"],
                        generation=prepared.generation,
                    )
                )
            else:
                final = result["draft"]
                ready = final.get("recommendation_ready", True)
                save(
                    directory,
                    "recommendation.json" if ready else "draft-recommendation.json",
                    {
                        "rule_code": prepared.case["rule"]["rule_code"],
                        "topic": prepared.case["rule"]["topic"],
                        "company_code": prepared.case["rule"]["company_code"],
                        "rule_config_version": prepared.case["rule"][
                            "rule_config_version"
                        ],
                        "change_type": "add_exclusion" if final["filter"] else None,
                        **final,
                        "review_method": "ai_sample_review",
                        "automatic_apply": False,
                        "filter_replay_performed": False,
                        "filter_coverage_measured": False,
                    },
                )
                summary.update(
                    status="reviewed" if ready else "needs_revision",
                    analysis_status=final["status"],
                    recommendation_ready=ready,
                    validation_issues=final.get("validation_issues", []),
                )
                if not ready:
                    summary["visible_field_names"] = sorted(
                        ai_review._fields(
                            selection["current_samples"] + selection["history_samples"]
                        )
                    )
    except Exception as error:
        final = None
        summary.update(
            status="failed",
            stop_reason="processing_failed",
            error_type=type(error).__name__,
        )
    save(directory, "summary.json", summary)
    save(
        directory,
        "REPORT.md",
        report(prepared, summary, final, failed_reply=failed_reply).encode("utf-8"),
    )
    return summary


def report(prepared, summary, final, *, failed_reply=None):
    lines = [
        "# 规则突增过滤建议",
        "",
        f"规则：{prepared.case['rule']['rule_code']} / {prepared.case['rule']['rule_name']}",
        f"状态：{summary['status']}；可用历史：{summary['history_available_count']}/20条。",
        "",
        "使用历史导出材料和模拟规则，手工触发AI生成与复核。未修改或提交线上规则。",
        "目标是推荐能够减少主要重复场景的条件，不要求排除全部告警。",
        "",
    ]
    if summary.get("generation_reused"):
        lines += ["生成阶段复用原回答；本次仅第二步AI复核可能产生新的SDK请求。", ""]
    if summary.get("review_samples_reused"):
        lines += [
            "复核沿用上次展示的同一批原始材料，使用新版提示词重新请求；旧复核回答未作为本次输入。",
            "新回答是否改善需核对正文；格式通过不等于业务结论正确。",
            "",
        ]
    for phase in summary["phases"]:
        name = "生成" if phase["phase"] == "generate" else "复核"
        chosen = phase["selection"]
        lines += [
            f"{name}材料：当前{chosen['current_count']}条、历史{chosen['history_count']}条。",
            "",
        ]
    if summary["history_count"] == 0:
        lines += ["未提供突增前的历史对照；以下不能解释为已验证前后变化。", ""]
    if final:
        if not final.get("recommendation_ready", True):
            lines += [
                "## 待修改的字段",
                "",
                "模型已返回建议，以下字段在本次原日志中未找到；原建议保留供修改，尚未形成可提交的条件。",
                "程序没有猜测替换字段，也没有执行过滤条件。",
                "",
            ]
            for issue in final.get("validation_issues", []):
                lines += [
                    f"- `{'.'.join(map(str, issue.get('path', [])))}`："
                    f"{issue.get('field', '')}（{issue.get('code', '')}）"
                ]
            lines += [
                "",
                "本次日志可用字段：",
                "",
                "```json",
                json.dumps(
                    summary.get("visible_field_names", []), ensure_ascii=False, indent=2
                ),
                "```",
                "",
            ]
        lines += [
            "## 主要场景",
            "",
            final["issue"],
            "",
            "## 建议",
            "",
            final["recommendation"],
            "",
            final["certainty"],
            "",
            "## 过滤条件",
            "",
            "```json",
            json.dumps(final["filter"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## AI复核说明",
            "",
            str(final.get("review_notes", "")),
            "",
        ]
        if final.get("model_extra_fields"):
            lines += [
                "## 模型补充信息",
                "",
                "以下额外字段按模型原文保留，未作自动执行依据；其中的限制、否定或撤回意见也需一并阅读。",
                "",
                "```json",
                json.dumps(final["model_extra_fields"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
    else:
        lines += ["本次未完成两步AI复核；已保存成功返回的回答和失败位置。", ""]
        for phase in summary["phases"]:
            if phase.get("error_code"):
                lines += [
                    f"校验问题：{phase['phase']} / {phase['error_code']}；"
                    f"位置：{json.dumps(phase.get('error_path'), ensure_ascii=False)}。",
                    "",
                ]
        if failed_reply is not None:
            lines += [
                "## 模型已返回的原文（未通过校验）",
                "",
                "\n".join("    " + line for line in failed_reply.splitlines()),
                "",
            ]
    lines += [
        "未做程序过滤试算，未测量全量覆盖率或线上降量率。AI复核基于展示样本，实际降量在采纳后观察。",
        "",
    ]
    return "\n".join(lines)


def live_chain(
    prepared,
    directory,
    *,
    recovered_generation=None,
    processor=None,
    response_format=None,
):
    import httpx
    from openai import OpenAI
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from config.circuit_breaker import RedisCircuitBreaker
    from util.model_semaphore import RedisModelSemaphore

    p = prepared
    with ExitStack() as stack:
        redis = Redis.from_url(p.rc.REDIS_URL, decode_responses=True)
        stack.callback(redis.close)
        redis.connection_pool.connection_kwargs.update(
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
            retry_on_error=[],
        )
        breaker = RedisCircuitBreaker(redis)
        semaphore = RedisModelSemaphore(
            redis, lease_ms=p.policy.lease_ms, heartbeat_ms=p.policy.heartbeat_ms
        )
        http = stack.enter_context(
            httpx.Client(trust_env=False, timeout=p.limits.timeout_seconds)
        )
        client = OpenAI(
            base_url=p.endpoint,
            api_key=p.key,
            max_retries=0,
            timeout=p.limits.timeout_seconds,
            http_client=http,
        )
        stack.callback(client.close)

        def invoke(prepared, selection, child):
            breaker.assert_closed(p.model)
            with semaphore.acquire(
                p.policy.semaphore_key, p.policy.concurrency, wait_timeout=0
            ) as lease:
                lease.assert_valid()
                outcome = call_model(
                    prepared, selection, child, client, response_format=response_format
                )
                transport = outcome["model_transport"]
                if transport.get("sdk_request_calls"):
                    try:
                        if transport.get("error_code") == "request_failed":
                            breaker.record_failure(p.model)
                        else:
                            breaker.record_success(p.model)
                    except Exception as error:
                        outcome["circuit_update_error_type"] = type(error).__name__
                lease.assert_valid()
                return outcome

        return (processor or run_chain)(
            p,
            directory,
            invoke,
            check=lambda: verify(p),
            recovered_generation=recovered_generation,
        )


def ask(prompt):
    print(prompt, end="", flush=True)
    with open("/dev/tty", "r", encoding="utf-8") as terminal:
        return terminal.readline().strip()


def main(*, tool_hashes=None):
    directory = None
    try:
        root = Path.home() / "sec_know_model"
        default = root / "nas/original/20261102175803453.csv"
        current = (
            Path(ask(f"本次告警CSV完整路径（回车用 {default}）：") or default)
            .expanduser()
            .resolve()
        )
        historical = ask("突增前历史告警CSV完整路径（没有就回车）：")
        history = Path(historical).expanduser().resolve() if historical else None
        p = prepare(root, current, history)
        print(
            json.dumps(
                {
                    "status": "ready",
                    "model": p.model,
                    "coverage": p.case["coverage"],
                    "generation_selection": p.generation["selection"],
                    "input_bytes": p.generation["request"]["input_bytes"],
                    "context_budget": p.limits.audit(),
                    "max_sdk_requests": 2,
                    "filter_replay_performed": False,
                    "application_code_updated": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            "将生成建议并由AI复核，最多2次SDK请求；代理内部可能重试。仅本地保存结果，不提交告警或修改规则。"
        )
        if ask("确认调用模型请输入 LIVE，其他输入退出：") != "LIVE":
            print("已退出，未调用模型。")
            return 0
        verify(p)
        parent = Path.home() / "sec-rule-refine-data"
        parent.mkdir(exist_ok=True, mode=0o700)
        directory = Path(tempfile.mkdtemp(prefix="surge-ai-review-", dir=parent))
        print("本次结果目录：" + str(directory), flush=True)
        save(directory, "case-audit.json", p.case["audit"])
        save(directory, "coverage.json", p.case["coverage"])
        save(directory, "original-rule.json", p.case["original_rule"])
        save(directory, "simulated-rule.json", p.case["rule"])
        save(directory, "configuration-hashes.json", p.snapshot)
        save(directory, "tool-hashes.json", tool_hashes or {})
        summary = live_chain(p, directory)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("建议正文：" + str(directory / "REPORT.md"))
        print("\n" + (directory / "REPORT.md").read_text(encoding="utf-8"))
        return 0 if summary["status"] in {"reviewed", "needs_revision"} else 2
    except Exception as error:
        result = {
            "status": "failed",
            "error_type": type(error).__name__,
            "output_dir": str(directory) if directory else None,
        }
        if isinstance(error, (ValueError, FileNotFoundError)):
            # These exceptions occur in local preflight; no provider exception body.
            result["reason"] = str(error)[:300]
        if directory:
            save(directory, "failure.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
