"""Explicit LOCAL-only live runner for the isolated synthetic probe."""

import hashlib
import importlib
import importlib.metadata
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import probe

FIXTURE_SHA256 = "503dd5b8d7636a6b99bf49b8d628cfe0f58705ea7aa802e4d7c208693bc2bbe2"


def preflight(root, fixture, expected):
    if any(
        os.environ.get(name, "LOCAL").upper() != "LOCAL"
        for name in ("env_profile", "ENV_PROFILE")
    ):
        raise ValueError("local_only")
    root = Path(root).resolve()
    raw = Path(fixture).read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError("synthetic_fixture_hash_mismatch")
    case = json.loads(raw)
    if case != probe.synthetic_data("healthcase-v1"):
        raise ValueError("synthetic_fixture_content_mismatch")
    if not expected:
        raise ValueError("module_pins_missing")
    for relative, digest in expected.items():
        path = root / relative
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("module_version_mismatch: " + relative)
    # Import only the owned module directory, without loading the workflow package.
    package = ModuleType("_rule_refine_probe_" + uuid4().hex)
    package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package
    modules = SimpleNamespace(
        **{
            name: importlib.import_module(package.__name__ + "." + name)
            for name in (
                "counter",
                "ingress",
                "signal_processing",
                "pending_signal",
                "local_sink",
                "analysis",
                "model_adapter",
                "background_service",
                "background_tick",
                "task_source",
            )
        }
    )
    config = root / "config/rule_refine.analysis.local.json"
    config_raw = config.read_bytes()
    if len(config_raw) > 16384:
        raise ValueError("analysis_configuration_too_large")
    settings = json.loads(config_raw, object_pairs_hook=_unique)
    if not isinstance(settings, dict) or set(settings) - {
        "enabled",
        "model",
        "state_dir",
    }:
        raise ValueError("analysis_configuration_invalid")
    model = settings.get("model", "DeepSeek_V32")
    if (
        not isinstance(model, str)
        or not 1 <= len(model) <= 128
        or model != model.strip()
        or not model.isprintable()
    ):
        raise ValueError("model_name_invalid")
    sys.path.insert(0, str(root))
    import util.root_config as rc
    from config.model_limits import get_model_policy
    from nas.config import APP_KEY_DICT

    if getattr(rc, "ENV", None) != "LOCAL":
        raise ValueError("loaded_profile_not_local")
    endpoint, key = rc.LITELLM_OPENAI_SERVER, APP_KEY_DICT.get("litellm_key")
    url = urlsplit(endpoint)
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not isinstance(key, str)
        or not key
    ):
        raise ValueError("local_model_proxy_required")
    policy = get_model_policy(model)
    if policy is None:
        raise ValueError("model_policy_missing")
    return SimpleNamespace(
        root=root,
        case=case,
        modules=modules,
        settings=settings,
        rc=rc,
        key=key,
        endpoint=endpoint,
        model=model,
        policy=policy,
        pins=expected,
        fixture_sha256=FIXTURE_SHA256,
        configuration_sha256=hashlib.sha256(config_raw).hexdigest(),
    )


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_configuration_key")
        result[key] = value
    return result


def confirm_live():
    print("确认执行请输入 LIVE，其他输入退出：", end="", flush=True)
    with open("/dev/tty", "r", encoding="utf-8") as terminal:
        return terminal.readline().strip() == "LIVE"


def execute(prepared, output):
    import httpx
    from openai import OpenAI
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from config.circuit_breaker import ModelCircuitOpen, RedisCircuitBreaker
    from util.model_semaphore import ModelSemaphoreTimeout, RedisModelSemaphore

    p = prepared
    with ExitStack() as stack:
        redis_client = Redis.from_url(p.rc.REDIS_URL, decode_responses=True)
        stack.callback(redis_client.close)
        redis_client.connection_pool.connection_kwargs.update(
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
            retry_on_error=[],
        )
        redis_client.connection_pool.max_connections = 8
        http = stack.enter_context(httpx.Client(trust_env=False, timeout=240))
        client = OpenAI(
            base_url=p.endpoint,
            api_key=p.key,
            max_retries=0,
            timeout=240,
            http_client=http,
        )
        stack.callback(client.close)
        breaker = RedisCircuitBreaker(redis_client)
        semaphore = RedisModelSemaphore(
            redis_client, lease_ms=p.policy.lease_ms, heartbeat_ms=p.policy.heartbeat_ms
        )
        guarded = None

        def wrap_processor(processor):
            nonlocal guarded
            guarded = p.modules.background_service._ModelGuard(
                processor,
                semaphore,
                breaker,
                p.policy,
                p.model,
                (ModelCircuitOpen, ModelSemaphoreTimeout),
            )
            return guarded

        def analyze(materials):
            guarded.lease.assert_valid()
            result = p.modules.model_adapter.analyze_with_model(
                materials,
                client,
                model=p.model,
                transport_mode="live",
            )
            # Persist the returned result even if the shared lease was lost meanwhile.
            # The outer guard checks again before the consumer can acknowledge it.
            transport = result["model_transport"]
            if transport["sdk_request_calls"]:
                try:
                    if transport["error_code"] == "request_failed":
                        breaker.record_failure(p.model)
                    else:
                        breaker.record_success(p.model)
                except Exception as error:
                    transport["circuit_update_error_type"] = type(error).__name__
            return result

        return probe.run_check(
            output,
            modules=p.modules,
            redis_client=redis_client,
            analyze=analyze,
            wrap_processor=wrap_processor,
            case=p.case,
        )


def main():
    folder = Path(__file__).resolve().parent
    result = None
    try:
        p = preflight(
            Path.cwd(),
            Path.home() / "sec-rule-refine-data/synthetic-healthcase-v1.json",
            json.loads((folder / "pins.json").read_text(encoding="utf-8")),
        )
        print(
            "预检通过：临时SQLite + 隔离Redis + 后台处理函数；3条人工告警、1次重发；模型=" + p.model,
            flush=True,
        )
        print(
            "最多1次真实SDK请求（代理内部可能重试），超时240秒；结果仅本地保存。",
            flush=True,
        )
        if not confirm_live():
            print(
                json.dumps({"status": "cancelled", "model_calls": 0, "redis_calls": 0})
            )
            return 0
        output = folder / "result"
        versions = {}
        for name in ("openai", "httpx", "redis", "pydantic"):
            versions[name] = importlib.metadata.version(name)
        probe.save(
            folder,
            "manifest.json",
            {
                "material_source": "synthetic_fixture",
                "rule_provider": "synthetic_fixture",
                "fixture_sha256": p.fixture_sha256,
                "module_sha256": p.pins,
                "runner_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "probe_sha256": hashlib.sha256(
                    (folder / "probe.py").read_bytes()
                ).hexdigest(),
                "analysis_configuration_sha256": p.configuration_sha256,
                "requested_model": p.model,
                "transport_mode": "live",
                "confirmation": "LIVE",
                "endpoint_sha256": hashlib.sha256(p.endpoint.encode()).hexdigest(),
                "timeout_seconds": 240,
                "sdk_retries": 0,
                "upstream_attempts": None,
                "versions": versions,
                "counter_mode": "live",
                "counter_namespace_scope": "isolated_synthetic",
                "database_scope": "temporary_sqlite",
                "background_tick_invocation": "manual_in_process",
                "minimum_count": 3,
                "window_seconds": 3600,
                "shared_model_capacity_guard": True,
                "business_database_calls": 0,
                "real_ingress_connected": False,
                "celery_dispatch_tested": False,
                "automatic_apply": False,
            },
        )
        print("结果目录：" + str(output), flush=True)
        print("开始后台函数隔离验证；最多一次SDK请求，请等待。", flush=True)
        result = execute(p, output)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if result["status"] == "passed" else 1
    except Exception as error:
        # Do not echo connection errors or application configuration with credentials.
        result = {
            "check": "rule_refine_background_materials",
            "status": "failed",
            "error_type": type(error).__name__,
            "output_dir": str(folder),
        }
        if isinstance(error, ValueError) and str(error).startswith(
            (
                "local_",
                "loaded_",
                "synthetic_",
                "module_",
                "analysis_",
                "model_",
                "duplicate_",
            )
        ):
            result["reason"] = str(error)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
