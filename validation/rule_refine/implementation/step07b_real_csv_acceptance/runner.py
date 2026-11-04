"""Real CSV acceptance through the installed surge processor; models are opt-in."""

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import types
import uuid
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from materials import prepare_case

OPERATOR_CONTEXT = (
    "运营反馈这批告警与统一巡检、运维或扫描任务有关，希望针对主要重复场景推荐新增排除条件。"
    "请根据当前及历史原日志自主判断与提出条件，并保留其他不同活动。"
    "此说明不意味着所有相似命令都已确认为正常；历史告警也不是正常业务标签。"
    "规则是仅在内存中去掉事后新增排除项的对照版本，未提供预期答案或旧AI建议。"
)


def modules(root):
    package = types.ModuleType("_real_csv_surge_" + uuid.uuid4().hex)
    package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package
    return lambda name: importlib.import_module(package.__name__ + "." + name)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root, environ, *, current_path=None, history_path="auto", rules_path=None):
    values = [environ[key] for key in ("env_profile", "ENV_PROFILE") if key in environ]
    if not values or any(value.upper() != "LOCAL" for value in values):
        raise ValueError("explicit_LOCAL_profile_required")
    root = root.expanduser().absolute()
    load = modules(root)
    settings = load("surge_settings").load_settings(root)
    analysis, reason = load("background_service")._settings(root, environ)
    if analysis is None or not settings["enabled"]:
        raise ValueError(reason or "surge_disabled")
    local = dict(environ)
    monitor = load("local_settings").configure_rule_monitor(root, local)
    if monitor["status"] == "configuration_error":
        raise ValueError("monitor_configuration_invalid")
    rules_path = Path(
        rules_path
        or local.get("RULE_REFINE_CSV_PATH")
        or Path.home() / "sec-rule-refine-data/rules.csv"
    ).expanduser()
    if not rules_path.is_absolute():
        rules_path = root / rules_path
    current_path = Path(
        current_path or root / "nas/original/20261102175803453.csv"
    ).expanduser()
    if history_path == "auto":
        candidate = root / "nas/original/full_alert_2026_month_forth_sample_200.pkl"
        history_path = candidate if candidate.exists() else None
    elif history_path == "-":
        history_path = None
    elif history_path:
        history_path = Path(history_path).expanduser()

    def rules_factory():
        return load("csv_config_source").CsvRuleConfigSource(rules_path)

    case, signal, audit = prepare_case(
        root, current_path, history_path, load, rules_factory()
    )
    budget = load("surge_runtime").budget_from_settings(settings)
    preview = load("surge_sampling").select(
        case, budget, operator_context=OPERATOR_CONTEXT, independent_initial=True
    )
    paths = [
        *sorted((root / "my_workflows/zeus/rule_refine").glob("*.py")),
        root / "config/rule_refine.analysis.local.json",
        root / "config/rule_refine.surge.local.json",
        rules_path,
    ]
    snapshot = {str(path): fingerprint(path) for path in paths}
    return {
        "root": root,
        "load": load,
        "analysis": analysis,
        "budget": budget,
        "case": case,
        "signal": signal,
        "audit": audit,
        "preview": preview,
        "rules_factory": rules_factory,
        "snapshot": snapshot,
        "database": (root / "nas/data/sec_know_model.sqlite").resolve(),
    }


def save(directory, name, value):
    with (directory / name).open("x", encoding="utf-8") as stream:
        os.chmod(directory / name, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def process(prepared, directory, generate):
    """Runs the installed two-stage processor; injectable transport for offline tests."""
    load = prepared["load"]
    processor = load("surge_processor").SurgeProcessor(
        directory,
        load_case=lambda signal: prepared["case"],
        load_rules=prepared["rules_factory"],
        generate=generate,
        model=prepared["analysis"]["model"],
        budget=prepared["budget"],
        operator_context=OPERATOR_CONTEXT,
    )
    return processor.process(prepared["signal"])


def result_summary(prepared, outcome):
    phases = []
    folder = Path(outcome["output_dir"]) if outcome.get("output_dir") else None
    if folder:
        for phase in ("generate", "review"):
            response_file = folder / phase / "response.json"
            selection_file = folder / phase / "selection.json"
            if response_file.is_file():
                response = json.loads(response_file.read_text(encoding="utf-8"))
                selection = json.loads(selection_file.read_text(encoding="utf-8"))
                phases.append(
                    {
                        "phase": phase,
                        "selection": selection["selection"],
                        "transport": response.get("transport", {}),
                    }
                )
    return {
        "check": "rule_refine_real_csv_runtime",
        **outcome,
        "material_source": "real_historical_exports",
        "database_scope": "temporary_sqlite_from_exports_and_read_only_LOCAL_rows",
        "current_count": len(prepared["case"]["current_samples"]),
        "history_count": len(prepared["case"]["history_samples"]),
        "history_target": 20,
        "history_normal_labels_assumed": False,
        "history_gap_seconds": prepared["audit"].get("history_gap_seconds"),
        "model": prepared["analysis"]["model"],
        "model_output_token_limit": None,
        "automatic_apply": False,
        "application_code_updated": False,
        "business_database_rows_written": 0,
        "business_alerts_submitted": 0,
        "zeus_callback_calls": 0,
        "celery_dispatch_tested": False,
        "observed_live_surge": False,
        "rule_configuration": "counterfactual_in_memory_only",
        "filter_replay_performed": False,
        "filter_coverage_measured": False,
        "phases": phases,
        "report_file": str(folder / "REPORT.md") if folder else None,
    }


def execute(prepared):
    root, load = prepared["root"], prepared["load"]
    for path, digest in prepared["snapshot"].items():
        if fingerprint(Path(path)) != digest:
            raise ValueError("prepared_configuration_changed")
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
    model, budget = prepared["analysis"]["model"], prepared["budget"]
    policy = get_model_policy(model)
    if policy is None:
        raise ValueError("model_policy_missing")
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix="surge-real-csv-runtime-", dir=parent))
    print("本次结果目录：" + str(directory), flush=True)
    save(directory, "material-audit.json", prepared["audit"])
    save(directory, "source-hashes.json", prepared["snapshot"])
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

        outcome = process(prepared, directory, generate)
        lease.assert_valid()
    summary = result_summary(prepared, outcome)
    save(directory, "summary.json", summary)
    return summary


def ask(prompt):
    with (
        open("/dev/tty", "r", encoding="utf-8") as reader,
        open("/dev/tty", "w", encoding="utf-8") as writer,
    ):
        writer.write(prompt)
        writer.flush()
        return reader.readline().strip()


def main(argv=None, *, tool_hashes=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument("--current")
    parser.add_argument(
        "--history",
        default="auto",
        help="Optional small PKL; '-' disables extra file, retains LOCAL history",
    )
    parser.add_argument("--rules")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        prepared = prepare(
            Path(args.project_root),
            dict(os.environ),
            current_path=args.current,
            history_path=args.history,
            rules_path=args.rules,
        )
        prepared["audit"]["verification_tool_sha256"] = dict(tool_hashes or {})
        coverage = prepared["case"]["coverage"]
        preview = prepared["preview"]
        print(
            json.dumps(
                {
                    "status": "ready",
                    "model": prepared["analysis"]["model"],
                    "material_source": "real_historical_exports",
                    "current_available": coverage["current_alert_count"],
                    "history_selected": coverage["history_selected_unique_alerts"],
                    "history_target": 20,
                    "coverage": coverage,
                    "material_audit": prepared["audit"],
                    "generation_selection": preview["selection"],
                    "input_bytes": preview["request"]["input_bytes"],
                    "estimated_input_tokens": preview["request"][
                        "estimated_input_tokens"
                    ],
                    "context_tokens": prepared["budget"].context_tokens,
                    "input_token_budget": prepared["budget"].input_token_budget,
                    "model_output_token_limit": None,
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
            "将用真实CSV及实际召回的历史调用已安装的生成、AI复核流程，最多2次SDK请求；代理内部可能重试。"
            "模拟去掉事后排除条件，结果仅本地保存，不执行过滤或修改规则。重跑命令会新建结果并重新调用模型。",
            flush=True,
        )
        if ask("确认调用请输入 LIVE，其他输入退出：") != "LIVE":
            return 0
        summary = execute(prepared)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if summary.get("report_file") and Path(summary["report_file"]).is_file():
            print("\n建议正文：" + summary["report_file"], flush=True)
            print(Path(summary["report_file"]).read_text(encoding="utf-8"), flush=True)
        return 0 if summary["status"] in {"received", "not_applicable"} else 1
    except Exception as error:
        reason = str(error)
        print(
            json.dumps(
                {
                    "check": "rule_refine_real_csv_runtime",
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_code": reason
                    if re.fullmatch(r"[a-z_]{1,100}", reason)
                    else None,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
