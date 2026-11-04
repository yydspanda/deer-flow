"""Optional two-request LIVE acceptance using the seeded DEV history fixture."""

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import uuid
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit


def modules(root):
    package = types.ModuleType("_installed_surge_acceptance_" + uuid.uuid4().hex)
    package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package

    def load(name):
        return importlib.import_module(package.__name__ + "." + name)

    spec = importlib.util.spec_from_file_location(
        "_surge_seed", root / "test/rule_refine/check_surge_dev_history.py"
    )
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    return seed, load


def prepare(root, environ):
    seed, load = modules(root)
    seed._require_local(environ)
    database = seed._path(root)
    recall = seed._recall(database)
    if not recall["recall_passed"]:
        raise ValueError("run_check_surge_dev_history_first")
    settings = load("surge_settings").load_settings(root)
    analysis, reason = load("background_service")._settings(root, environ)
    if analysis is None or not settings["enabled"]:
        raise ValueError(reason or "surge_disabled")
    budget = load("surge_runtime").budget_from_settings(settings)
    rules = seed.FixtureRules()
    signal = {
        "schema_version": "sec_know.rule_burst_signal.v1",
        "mode": "demo",
        "namespace": "synthetic-dev-surge-acceptance",
        "signal_id": str(uuid.uuid4()),
        "source_app": "zeus",
        "company_code": seed.COMPANY,
        "topic": seed.TOPIC,
        "rule_code": seed.RULE,
        "sample_alert_ids": [seed.ALERT_PREFIX + f"current-{i:02}" for i in range(6)],
        "time_basis": "synthetic_fixture_not_live_rate",
    }
    source = load("surge_history").SurgeTaskSource(database, rules)
    case = source.load_case(signal)
    # Bind the explicit test premise to the original synthetic evidence, not
    # merely its identities. Never apply that premise to altered fixture text.
    expected = {}
    for row in seed.fixture_rows():
        alert = json.loads(row["alert_data"])["alert"]
        expected[alert["alertId"]] = {
            "occurred_at": alert["createAt"],
            "raw_logs": alert["hitLog"][0]["zeusRawLogs"],
        }
    for sample in case["current_samples"] + case["history_samples"]:
        actual = {key: sample[key] for key in ("occurred_at", "raw_logs")}
        if actual != expected.get(sample["alert_id"]):
            raise ValueError("synthetic_fixture_content_changed")
    # Operator premise is explicit synthetic test metadata, never a real approval.
    operator_context = "这是人工DEV串联测试。当前固定inventory-probe巡检为人为构造的已知测试任务；历史为人为构造的其他命令。请比较两组原日志，给出有依据的测试过滤建议。此说明不适用于任何真实业务告警。"
    preview = load("surge_sampling").select(
        case, budget, operator_context=operator_context, independent_initial=True
    )
    return {
        "root": root,
        "load": load,
        "seed": seed,
        "database": database,
        "analysis": analysis,
        "budget": budget,
        "rules": rules,
        "signal": signal,
        "case": case,
        "operator_context": operator_context,
        "preview": preview,
    }


def execute(prepared):
    root, load, analysis = prepared["root"], prepared["load"], prepared["analysis"]
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

    if rc.ENV != "LOCAL" or Path(rc.DB_PATH).resolve() != prepared["database"]:
        raise ValueError("loaded_LOCAL_database_profile_mismatch")
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
    model, budget = analysis["model"], prepared["budget"]
    policy = get_model_policy(model)
    if policy is None:
        raise ValueError("model_policy_missing")
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix="surge-dev-history-live-", dir=parent))
    print("本次结果目录：" + str(directory), flush=True)
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
            lease.assert_valid()
            print("开始AI阶段：" + request["phase"], flush=True)
            result = load("surge_runtime").call_model(
                client, request, model=model, budget=budget
            )
            try:
                if result["transport"]["error_code"] == "request_failed":
                    breaker.record_failure(model)
                else:
                    breaker.record_success(model)
            except Exception as error:
                result["transport"]["circuit_update_error_type"] = type(error).__name__
            return result

        processor = load("surge_processor").SurgeProcessor(
            directory,
            load_case=lambda signal: prepared["case"],
            load_rules=lambda: prepared["seed"].FixtureRules(),
            generate=generate,
            model=model,
            budget=budget,
            operator_context=prepared["operator_context"],
        )
        outcome = processor.process(prepared["signal"])
        lease.assert_valid()
    summary = {
        "check": "rule_refine_dev_history_live",
        "material_source": "synthetic_dev_fixture",
        "database_scope": "project_LOCAL_sqlite_read_only",
        "current_count": len(prepared["case"]["current_samples"]),
        "history_count": len(prepared["case"]["history_samples"]),
        "real_business_history_verified": False,
        "model": model,
        "model_output_token_limit": None,
        "redis_usage": "existing_model_capacity_and_circuit_only",
        "celery_dispatch_tested": False,
        "business_alerts_submitted": 0,
        "zeus_callback_calls": 0,
        "database_rows_written_this_command": 0,
        "automatic_apply": False,
        "case_sha256": hashlib.sha256(
            load("surge_ai_review").encoded(prepared["case"])
        ).hexdigest(),
        **outcome,
    }
    target = directory / "summary.json"
    with target.open("x", encoding="utf-8") as stream:
        os.chmod(target, 0o600)
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument(
        "--live", action="store_true", help="Ask LIVE before at most two SDK requests"
    )
    args = parser.parse_args(argv)
    try:
        prepared = prepare(Path(args.project_root).absolute(), dict(os.environ))
        preview = prepared["preview"]
        print(
            json.dumps(
                {
                    "status": "ready",
                    "model": prepared["analysis"]["model"],
                    "material_source": "synthetic_dev_fixture",
                    "current_available": len(prepared["case"]["current_samples"]),
                    "history_available": len(prepared["case"]["history_samples"]),
                    "generation_selection": preview["selection"],
                    "context_budget": prepared["budget"].audit(),
                    "max_sdk_requests": 2,
                    "model_calls_this_command": 0,
                    "automatic_apply": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        if not args.live:
            return 0
        print(
            "将使用DEV人工历史及当前日志进行生成和AI复核，最多2次SDK请求，代理内部可能重试；不执行过滤、不提交告警或规则。",
            flush=True,
        )
        with (
            open("/dev/tty", "r", encoding="utf-8") as terminal_input,
            open("/dev/tty", "w", encoding="utf-8") as terminal_output,
        ):
            terminal_output.write("确认调用请输入 LIVE，其他输入退出：")
            terminal_output.flush()
            if terminal_input.readline().strip() != "LIVE":
                return 0
        summary = execute(prepared)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["status"] in {"received", "not_applicable"} else 1
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "rule_refine_dev_history_live",
                    "status": "failed",
                    "error_type": type(error).__name__,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
