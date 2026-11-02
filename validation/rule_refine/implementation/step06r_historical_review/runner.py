"""One explicit SDK request for historical samples; no ingress or business writes."""

import functools
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import warnings
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import historical_materials
import historical_review
import inventory


class NoUsableSamples(ValueError):
    def __init__(self, coverage):
        super().__init__("no_usable_samples_within_budget")
        self.coverage = coverage


def save(directory, name, value):
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    )
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def modules(root, pins):
    for name, expected in pins.items():
        if (
            hashlib.sha256(inventory.bounded_read(root / name, 1024 * 1024)).hexdigest()
            != expected
        ):
            raise ValueError("module_changed: " + name)
    package = ModuleType("_historical_review_" + uuid4().hex)
    package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package
    return SimpleNamespace(
        **{
            name: importlib.import_module(package.__name__ + "." + name)
            for name in (
                "csv_config_source",
                "analysis",
                "filter_evaluation",
                "model_adapter",
            )
        }
    )


def prepare(root, pins):
    if any(
        os.environ.get(k, "LOCAL").upper() != "LOCAL"
        for k in ("env_profile", "ENV_PROFILE")
    ):
        raise ValueError("local_only")
    loaded = modules(root, pins)
    path = root / "nas/original/full_alert_2026_month_forth_sample_200.pkl"
    raw = inventory.bounded_read(path, 64 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != historical_materials.SAMPLE_SHA:
        raise ValueError("historical_file_changed")
    csv = Path.home() / "sec-rule-refine-data/rules.csv"
    if (
        hashlib.sha256(inventory.bounded_read(csv, 16 * 1024 * 1024)).hexdigest()
        != historical_materials.CSV_SHA
    ):
        raise ValueError("csv_snapshot_changed")
    rules = loaded.csv_config_source.CsvRuleConfigSource(csv)
    if rules.file_sha256 != historical_materials.CSV_SHA:
        raise ValueError("csv_changed_while_reading")
    import pandas as pd

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        frame = inventory.RestrictedDataFrameUnpickler(io.BytesIO(raw)).load()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("dataframe_required")
    context = historical_materials.build_context(frame, rules, inventory)
    if not context["samples"]:
        raise NoUsableSamples(context["coverage"])
    request = historical_review.build_request(context, loaded.analysis)
    settings = json.loads(
        inventory.bounded_read(root / "config/rule_refine.analysis.local.json", 16384)
    )
    model = settings.get("model", "DeepSeek_V32")
    if (
        not isinstance(model, str)
        or not 1 <= len(model) <= 128
        or model != model.strip()
        or not model.isprintable()
    ):
        raise ValueError("invalid_model")
    # Source is pinned to the reviewed LOCAL configuration before importing it.
    sys.path.insert(0, str(root))
    import util.root_config as rc
    from nas.config import APP_KEY_DICT
    from config.model_limits import get_model_policy

    endpoint = urlsplit(rc.LITELLM_OPENAI_SERVER)
    if (
        rc.ENV != "LOCAL"
        or endpoint.scheme not in {"http", "https"}
        or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("local_model_proxy_required")
    policy = get_model_policy(model)
    key = APP_KEY_DICT.get("litellm_key")
    if policy is None or not isinstance(key, str) or not key:
        raise ValueError("model_configuration_missing")
    return SimpleNamespace(
        context=context,
        request=request,
        loaded=loaded,
        model=model,
        key=key,
        endpoint=rc.LITELLM_OPENAI_SERVER,
        rc=rc,
        policy=policy,
        csv=csv,
    )


def execute(prepared, directory):
    import httpx
    from openai import OpenAI
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from config.circuit_breaker import RedisCircuitBreaker
    from util.model_semaphore import RedisModelSemaphore

    p = prepared
    summary = {
        "check": "rule_refine_historical_review",
        "status": "failed",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "business_alerts_submitted": 0,
        "business_database_calls": 0,
        "zeus_callback_calls": 0,
        "live_counter_modified": False,
        "redis_usage": "existing_model_capacity_and_circuit_only",
        "automatic_apply": False,
        "formal_recommendation_submitted": False,
        "requested_model": p.model,
        "sdk_request_calls": 0,
        "analysis_status": "not_started",
        "rule_code": p.context["rule"]["rule_code"],
        "rule_config_version": p.context["rule"]["rule_config_version"],
        "coverage": p.context["coverage"],
        "output_dir": str(directory),
    }
    result = {}
    try:
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
            redis.connection_pool.max_connections = 8
            breaker = RedisCircuitBreaker(redis)
            semaphore = RedisModelSemaphore(
                redis, lease_ms=p.policy.lease_ms, heartbeat_ms=p.policy.heartbeat_ms
            )
            breaker.assert_closed(p.model)
            with semaphore.acquire(
                p.policy.semaphore_key, p.policy.concurrency, wait_timeout=0
            ) as lease:
                lease.assert_valid()
                http = stack.enter_context(httpx.Client(trust_env=False, timeout=240))
                client = OpenAI(
                    base_url=p.endpoint,
                    api_key=p.key,
                    timeout=240,
                    max_retries=0,
                    http_client=http,
                )
                stack.callback(client.close)
                # The imported adapter lives in this tool's private package. Reuse its
                # unchanged request/response checks with a manual-review collector.
                p.loaded.model_adapter.analyze_materials = functools.partial(
                    historical_review.analyze,
                    analysis=p.loaded.analysis,
                    evaluator=p.loaded.filter_evaluation.evaluate_filter,
                    save=lambda name, raw: save(directory, name, raw),
                )
                save(
                    directory,
                    "request-started.json",
                    {"prompt_sha256": p.request["prompt_sha256"], "max_sdk_calls": 1},
                )
                summary["sdk_request_calls"] = None
                result = p.loaded.model_adapter.analyze_with_model(
                    p.context, client, model=p.model, transport_mode="live"
                )
                transport = result["model_transport"]
                summary.update(
                    {
                        key: transport.get(key)
                        for key in (
                            "sdk_request_calls",
                            "returned_model",
                            "finish_reason",
                            "usage",
                            "elapsed_ms",
                            "upstream_attempts",
                        )
                    }
                )
                summary.update(
                    analysis_status=result["status"],
                    format_valid=result["format_valid"],
                    error_code=result.get("error_code"),
                    model_error_code=transport.get("error_code"),
                    model_error_type=transport.get("error_type"),
                )
                if transport["sdk_request_calls"]:
                    try:
                        if transport["error_code"] == "request_failed":
                            breaker.record_failure(p.model)
                        else:
                            breaker.record_success(p.model)
                    except Exception as error:
                        summary["circuit_update_error_type"] = type(error).__name__
                lease.assert_valid()
                fresh = p.loaded.csv_config_source.CsvRuleConfigSource(p.csv).get_rule(
                    p.context["rule"]["rule_code"], topic=p.context["rule"]["topic"]
                )
                same = all(
                    fresh.get(key) == value for key, value in p.context["rule"].items()
                )
                summary["configuration_unchanged"] = same
                summary["sample_match_counts"] = result.get("evaluation", {}).get(
                    "counts"
                )
                summary["status"] = (
                    "passed" if result["format_valid"] and same else "blocked"
                )
    except Exception as error:
        summary.update(status="failed", error_type=type(error).__name__)
    save(directory, "analysis.json", result)
    save(directory, "summary.json", summary)
    save(
        directory,
        "REPORT.md",
        historical_review.report_text(p.context, result, summary).encode("utf-8"),
    )
    return summary


def main(pins):
    try:
        p = prepare(Path.cwd(), pins)
    except Exception as error:
        safe_codes = {
            "local_only",
            "historical_file_changed",
            "csv_snapshot_changed",
            "csv_changed_while_reading",
            "dataframe_required",
            "invalid_model",
            "local_model_proxy_required",
            "model_configuration_missing",
            "no_usable_samples_within_budget",
            "rule_configuration_over_budget",
            "reviewed_prompt_changed",
            "context_over_budget",
        }
        code = str(error) if str(error) in safe_codes else "preparation_check_failed"
        print(
            json.dumps(
                {
                    "check": "rule_refine_historical_review",
                    "status": "preparation_stopped",
                    "error_code": code,
                    "error_type": type(error).__name__,
                    "coverage": getattr(error, "coverage", None),
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "ready",
                "rule_code": p.context["rule"]["rule_code"],
                "material_source": "historical_export",
                "coverage": p.context["coverage"],
                "model": p.model,
                "max_sdk_requests": 1,
                "live_counter_modified": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print(
        "仅用历史样本做一次规则级建议分析；不提交告警、不回调Zeus、不改规则。代理内部可能重试，SDK超时240秒。",
        flush=True,
    )
    print("确认调用模型请输入 LIVE，其他输入退出：", end="", flush=True)
    with open("/dev/tty", "r", encoding="utf-8") as terminal:
        if terminal.readline().strip() != "LIVE":
            print("已取消；未调用模型。")
            return 0
    parent = Path.home() / "sec-rule-refine-data"
    directory = Path(tempfile.mkdtemp(prefix="historical-rule-review-", dir=parent))
    print("本次报告目录：" + str(directory), flush=True)
    save(directory, "context.json", p.context)
    save(directory, "request.json", p.request)
    save(directory, "coverage.json", p.context["coverage"])
    save(directory, "source-pins.json", pins)
    save(
        directory,
        "output-schema.json",
        p.loaded.analysis.AnalysisDraft.model_json_schema(),
    )
    print("开始请求模型；完成后输出精简结果。", flush=True)
    summary = execute(p, directory)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1
