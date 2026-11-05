"""Preview real exports; --live explicitly permits two LLM calls and one ZEUS POST."""

import argparse
import ast
import copy
import hashlib
import importlib
import json
import os
import sqlite3
import sys
import types
import uuid
from contextlib import ExitStack, closing
from pathlib import Path
from urllib.parse import urlsplit

from materials import prepare_case

OPERATOR_CONTEXT = (
    "本次为真实历史导出的手工验证，未观察到线上实时突增。"
    "运营反馈该批告警可能与统一巡检、运维或扫描相关，但不代表所有相似活动均已确认为正常。"
    "请根据原文提出topic检测前过滤建议，并复核对整个topic其他检测的潜在影响。"
    "原规则条件不会自动限制topic过滤。历史不是正常业务标签。"
    "规则配置仅在内存模拟去掉事后排除项，没有提供预期条件或旧AI答案。"
)


def modules(root):
    package = types.ModuleType("_topic_manual_" + uuid.uuid4().hex)
    package.__path__ = [str(Path(root) / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package
    return lambda name: importlib.import_module(package.__name__ + "." + name)


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def static_zeus_target(root, profile):
    """Only understand the reviewed literal ENV branch; never import configuration."""
    tree = ast.parse((Path(root) / "util/root_config.py").read_text(encoding="utf-8"))

    def writes(node, names):
        return any(
            isinstance(item, ast.Name)
            and isinstance(item.ctx, ast.Store)
            and item.id in names
            for item in ast.walk(node)
        )

    branches = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and writes(node, {"ZEUS_SYSTEM_URL"})
    ]
    if profile not in {"LOCAL", "PRD"} or len(branches) != 1:
        raise ValueError("static_zeus_target_unavailable")
    branch = branches[0]
    position = tree.body.index(branch)
    if (
        any(
            writes(node, {"ZEUS_SYSTEM_URL"})
            for node in tree.body
            if node is not branch
        )
        or any(writes(node, {"ENV"}) for node in tree.body[position + 1 :])
        or writes(branch, {"ENV"})
    ):
        raise ValueError("static_zeus_target_dynamic")
    while True:
        test = branch.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "ENV"
            and len(test.ops) == len(test.comparators) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
            and isinstance(test.comparators[0].value, str)
            and test.comparators[0].value in {"LOCAL", "STG", "PRD"}
        ):
            raise ValueError("static_zeus_target_dynamic")
        if test.comparators[0].value == profile:
            statements = branch.body
            break
        if len(branch.orelse) == 1 and isinstance(branch.orelse[0], ast.If):
            branch = branch.orelse[0]
        else:
            statements = branch.orelse
            break
    assignments = [node for node in statements if writes(node, {"ZEUS_SYSTEM_URL"})]
    if len(assignments) != 1 or not isinstance(assignments[0], ast.Assign):
        raise ValueError("static_zeus_target_unavailable")
    assignment = assignments[0]
    if (
        not any(
            isinstance(target, ast.Name) and target.id == "ZEUS_SYSTEM_URL"
            for target in assignment.targets
        )
        or not isinstance(assignment.value, ast.Constant)
        or not isinstance(assignment.value.value, str)
    ):
        raise ValueError("static_zeus_target_dynamic")
    value = assignment.value.value
    url = urlsplit(value)
    if (
        value != value.strip()
        or not value.isprintable()
        or url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("static_zeus_target_invalid")
    return {
        "base_url": value,
        "endpoint": value + "/public/syncSmallRule",
        "source": "static_root_config",
    }


def verify_runtime_target(rc, prepared):
    if getattr(rc, "ENV", None) != prepared["profile"]:
        raise ValueError("loaded_manual_profile_mismatch")
    if getattr(rc, "ZEUS_SYSTEM_URL", None) != prepared["zeus_target"]["base_url"]:
        raise ValueError("loaded_zeus_target_changed")


def sqlite_rows(path):
    """Only the disposable materials DB is passed here; originals stay read-only."""
    with closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            yield from db.execute(
                "SELECT alert_id, alert_data FROM uas_app_workflow_task_status WHERE app_code=? AND flow_id=?",
                ("zeus", "alert_agent"),
            )
        finally:
            db.rollback()


def material_loader(load):
    """Reuse 7B ingestion/audit while preserving actual current executeType."""

    def resolve(name):
        if name == "surge_history":
            return types.SimpleNamespace(
                SurgeTaskSource=lambda path, rules: load(
                    "topic_history"
                ).TopicTaskSource(rules, row_loader=lambda: sqlite_rows(path))
            )
        return load(name)

    return resolve


def prepare(
    root,
    environ,
    *,
    current_path=None,
    history_path="auto",
    rules_path=None,
    model=None,
    context_tokens=None,
    input_fraction=None,
    timeout_seconds=None,
):
    root = Path(root).expanduser().resolve()
    load = modules(root)
    settings_module = load("topic_settings")
    profile = settings_module.profile(environ)
    if profile not in {"LOCAL", "PRD"}:
        raise ValueError("manual_topic_requires_LOCAL_or_PRD")
    zeus_target = static_zeus_target(root, profile)
    settings = settings_module.load_settings(root, environ) or dict(
        settings_module.DEFAULTS
    )
    for key, value in (
        ("model", model),
        ("context_tokens", context_tokens),
        ("input_fraction", input_fraction),
        ("timeout_seconds", timeout_seconds),
    ):
        if value is not None:
            settings[key] = value
    model = settings["model"]
    if (
        not isinstance(model, str)
        or not model.strip()
        or model != model.strip()
        or len(model) > 128
    ):
        raise ValueError("invalid_manual_model")
    namespace = (
        environ.get("RULE_REFINE_NAMESPACE")
        or settings.get("namespace")
        or "topic-v1:" + profile.lower()
    )
    if (
        not isinstance(namespace, str)
        or not namespace.strip()
        or namespace != namespace.strip()
        or len(namespace) > 450
    ):
        raise ValueError("invalid_manual_namespace")
    rules_path = Path(
        rules_path
        or environ.get("RULE_REFINE_CSV_PATH")
        or settings.get("csv_path")
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
        root, current_path, history_path, material_loader(load), rules_factory()
    )
    case["coverage"] = {
        **case["coverage"],
        "target_executeType": case["execute_type"]["values"],
    }
    # Preserve replay provenance, but give each explicit manual run a new analysis ID.
    signal = {
        **signal,
        "namespace": namespace,
        "signal_id": "topic-manual-" + uuid.uuid4().hex,
    }
    budget = load("surge_runtime").budget_from_settings(settings)
    context = OPERATOR_CONTEXT + settings.get("operator_context", "")
    preview = load("surge_sampling").select(
        case,
        budget,
        operator_context=context,
        request_builder=load("topic_ai").build_request,
    )
    paths = [
        *sorted((root / "my_workflows/zeus/rule_refine").glob("*.py")),
        rules_path,
        root / "util/root_config.py",
    ]
    config_path = root / settings_module.CONFIG
    if config_path.exists():
        paths.append(config_path)
    snapshot = {str(path): fingerprint(path) for path in paths}
    return {
        "root": root,
        "profile": profile,
        "zeus_target": zeus_target,
        "namespace": namespace,
        "load": load,
        "settings": settings,
        "model": model,
        "budget": budget,
        "case": case,
        "signal": signal,
        "audit": audit,
        "preview": preview,
        "operator_context": context,
        "rules_factory": rules_factory,
        "snapshot": snapshot,
    }


def preview_summary(prepared):
    case, selection = prepared["case"], prepared["preview"]
    return {
        "check": "topic_filter_real_csv_preview",
        "status": "ready",
        "model": prepared["model"],
        "zeus_endpoint": prepared["zeus_target"]["endpoint"],
        "zeus_target_source": prepared["zeus_target"]["source"],
        "material_source": "real_historical_exports",
        "optimization_scope": "topic_before_detection",
        "current_available": len(case["current_samples"]),
        "history_selected": len(case["history_samples"]),
        "execute_type": case["execute_type"],
        "generation_selection": selection["selection"],
        "input_bytes": selection["request"]["input_bytes"],
        "estimated_input_tokens": selection["request"]["estimated_input_tokens"],
        "input_token_budget": prepared["budget"].input_token_budget,
        "model_output_token_limit": None,
        "max_sdk_requests": 2,
        "max_zeus_posts": 1,
        "counterfactual_rule": True,
        "observed_live_surge": False,
        "activation_state": "managed_by_zeus_not_verified",
        "model_calls_this_command": 0,
        "zeus_posts_this_command": 0,
        "business_database_rows_written": 0,
    }


def process(prepared, directory, generate, submit, *, delivery_directory):
    processor = prepared["load"]("topic_processor").TopicProcessor(
        directory,
        load_case=lambda signal: copy.deepcopy(prepared["case"]),
        load_rules=prepared["rules_factory"],
        generate=generate,
        model=prepared["model"],
        budget=prepared["budget"],
        submit=submit,
        operator_context=prepared["operator_context"],
        delivery_directory=delivery_directory,
        delivery_scope="live:" + prepared["namespace"],
    )
    return processor.process(prepared["signal"])


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

    verify_runtime_target(rc, prepared)
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
    base = (
        Path(rc.DATA_DIR)
        / "rule_refine/topic_filters"
        / hashlib.sha256(prepared["namespace"].encode("utf-8")).hexdigest()[:16]
    )
    directory = base / "manual" / uuid.uuid4().hex
    print("本次精简结果目录：" + str(directory), flush=True)
    calls = {"model": 0, "post": 0}
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
            if calls["model"] >= 2:
                raise ValueError("manual_model_call_limit")
            lease.assert_valid()
            calls["model"] += 1
            print("开始topic AI阶段：" + request["phase"], flush=True)
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

        def submit(payload):
            from my_workflows.zeus.flows.disposition_tools.small_rule_sync_client import (
                SmallRuleSyncClient,
            )

            if calls["post"] >= 1:
                raise ValueError("manual_submission_limit")
            lease.assert_valid()
            calls["post"] += 1
            return SmallRuleSyncClient().sync_small_rule([payload])

        outcome = process(
            prepared,
            directory,
            generate,
            submit,
            delivery_directory=base / "deliveries",
        )
        lease.assert_valid()
    folder = Path(outcome["output_dir"])
    audit = {
        "material_source": "real_historical_exports",
        "counterfactual_rule": True,
        "removed_condition_count": len(prepared["audit"].get("removed_conditions", [])),
        "source_files": [
            {"kind": s["kind"], "file_sha256": s.get("file_sha256")}
            for s in prepared["audit"].get("sources", [])
        ],
        "source_manifest_sha256": hashlib.sha256(
            json.dumps(prepared["snapshot"], sort_keys=True).encode()
        ).hexdigest(),
        "verification_tool_sha256": prepared.get("tool_hashes", {}),
        "business_database_rows_written": 0,
        "raw_logs_persisted": False,
    }
    prepared["load"]("surge_processor")._write(directory / "manual-audit.json", audit)
    summary = {
        "check": "topic_filter_real_csv_live",
        **outcome,
        "material_source": "real_historical_exports",
        "counterfactual_rule": True,
        "observed_live_surge": False,
        "model_calls_this_command": calls["model"],
        "zeus_posts_this_command": calls["post"],
        "business_database_rows_written": 0,
    }
    candidate_path = folder / "recommendation.json"
    if candidate_path.is_file():
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        summary["recommendation"] = {
            key: candidate.get(key)
            for key in ("filter", "executeType", "explanation", "recommendation_ready")
        }
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
    parser.add_argument("--history", default="auto")
    parser.add_argument("--rules")
    parser.add_argument("--model")
    parser.add_argument("--context-tokens", type=int)
    parser.add_argument("--input-fraction", type=float)
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        prepared = prepare(
            args.project_root,
            dict(os.environ),
            current_path=args.current,
            history_path=args.history,
            rules_path=args.rules,
            model=args.model,
            context_tokens=args.context_tokens,
            input_fraction=args.input_fraction,
            timeout_seconds=args.timeout_seconds,
        )
        prepared["tool_hashes"] = dict(tool_hashes or {})
        print(
            json.dumps(preview_summary(prepared), ensure_ascii=False, indent=2),
            flush=True,
        )
        if not args.live:
            return 0
        if prepared["case"]["execute_type"]["status"] != "resolved":
            print(
                "当前告警executeType缺失或混合，本次只预览，不调用模型或ZEUS。",
                flush=True,
            )
            return 1
        print(
            "将新做topic生成与复核，最多2次SDK请求；通过后最多1次POST，在ZEUS实际创建小规则。启停由ZEUS管理，不能保证默认禁用。不会自动重试提交；重跑会新做AI分析，同一目标及相同条件复用提交回执。",
            flush=True,
        )
        if ask("确认模型调用及创建ZEUS小规则请输入 LIVE，其他输入退出：") != "LIVE":
            return 0
        result = execute(prepared)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if result.get("status") in {"received", "not_applicable"} else 1
    except Exception as error:
        target_codes = {
            "static_zeus_target_unavailable",
            "static_zeus_target_dynamic",
            "static_zeus_target_invalid",
            "loaded_manual_profile_mismatch",
            "loaded_zeus_target_changed",
        }
        print(
            json.dumps(
                {
                    "check": "topic_filter_real_csv",
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_code": str(error) if str(error) in target_codes else None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
