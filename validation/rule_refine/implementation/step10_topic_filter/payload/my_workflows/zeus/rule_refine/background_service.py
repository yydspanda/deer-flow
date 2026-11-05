"""既有 Celery 的 LOCAL 后台适配；独立开关默认关闭。"""

import hashlib
import json
import logging
import os
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from .limits import get_limits
from .surge_settings import load_settings as load_surge_settings

TASK_NAME = "tasks.task.rule_refine_tick"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _SetupError(ValueError):
    pass


def _settings(root, env):
    if any(env.get(key, "LOCAL").upper() != "LOCAL" for key in ("env_profile", "ENV_PROFILE")):
        return None, "local_only"
    path = root / "config/rule_refine.analysis.local.json"
    if not path.exists():
        return None, "analysis_disabled"
    from .local_settings import _object

    if path.stat().st_size > 16384:
        raise ValueError("分析配置过大")
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object)
    if not isinstance(data, dict) or set(data) - {"enabled", "model", "state_dir"}:
        raise ValueError("分析配置字段无效")
    if type(data.get("enabled", False)) is not bool:
        raise ValueError("分析开关必须为布尔值")
    if not data.get("enabled", False):
        return None, "analysis_disabled"
    model = data.get("model", "Pub_DeepSeek_V4_Flash")
    if not isinstance(model, str) or not 1 <= len(model) <= 128 or model != model.strip() or not model.isprintable():
        raise ValueError("模型名无效")
    state_dir = data.get("state_dir", str(Path.home() / "sec-rule-refine-data/background"))
    if not isinstance(state_dir, str) or not state_dir.strip():
        raise ValueError("保存目录无效")
    path = Path(state_dir).expanduser()
    return {
        "model": model,
        "state_dir": str(path if path.is_absolute() else root / path),
    }, None


class _ModelGuard:
    def __init__(self, processor, semaphore, breaker, policy, model, waiting_errors):
        self.processor, self.semaphore, self.breaker = processor, semaphore, breaker
        self.policy, self.model, self.waiting_errors = policy, model, waiting_errors
        self.lease = None

    def __getattr__(self, name):
        return getattr(self.processor, name)

    def process(self, signal):
        try:
            self.breaker.assert_closed(self.model)
            lease = self.semaphore.acquire(self.policy.semaphore_key, self.policy.concurrency, wait_timeout=0)
        except self.waiting_errors:
            return {
                "schema_version": "sec_know.rule_signal_processing.v1",
                "status": "capacity_wait",
                "model_calls_this_attempt": 0,
                "automatic_apply": False,
            }
        try:
            with lease:
                self.lease = lease
                lease.assert_valid()
                outcome = self.processor.process(signal)
                lease.assert_valid()
                return outcome
        finally:
            self.lease = None


def _execute(root, settings, env):
    # 仅在 LOCAL、分析开关和监测配置均通过后导入现有应用依赖。
    import httpx
    from openai import OpenAI
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    import util.root_config as rc
    from config.circuit_breaker import ModelCircuitOpen, RedisCircuitBreaker
    from config.model_limits import get_model_policy
    from nas.config import APP_KEY_DICT
    from util.model_semaphore import ModelSemaphoreTimeout, RedisModelSemaphore

    from .background_tick import run_tick
    from .counter import RedisRuleCounter
    from .csv_config_source import CsvRuleConfigSource
    from .local_sink import LocalRecommendationSink
    from .model_adapter import analyze_with_model
    from .signal_processing import SignalProcessor
    from .task_source import LocalWorkflowTaskSource

    if getattr(rc, "ENV", None) != "LOCAL":
        raise _SetupError("loaded_profile_not_local")
    surge_settings = load_surge_settings(root)
    if surge_settings["enabled"]:
        from .surge_runtime import budget_from_settings

        limits = budget_from_settings(surge_settings)
    else:
        limits = get_limits(root)
    model = settings["model"]
    policy = get_model_policy(model)
    if policy is None:
        raise _SetupError("model_policy_missing")
    endpoint, key = rc.LITELLM_OPENAI_SERVER, APP_KEY_DICT.get("litellm_key")
    url = urlsplit(endpoint)
    if url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.username or url.password or url.query or url.fragment or not isinstance(key, str) or not key:
        raise _SetupError("local_model_proxy_required")
    database = (root / "nas/data/sec_know_model.sqlite").resolve()
    if Path(rc.DB_PATH).resolve() != database:
        raise _SetupError("local_database_path_mismatch")
    if not database.is_file():
        raise _SetupError("local_database_missing")
    namespace = env["RULE_REFINE_NAMESPACE"]
    state_dir = Path(settings["state_dir"]) / hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
    csv_path = env["RULE_REFINE_CSV_PATH"]
    rules = CsvRuleConfigSource(csv_path)
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
        redis_client.connection_pool.max_connections = 8
        http_client = stack.enter_context(httpx.Client(trust_env=False, timeout=limits.timeout_seconds))
        client = OpenAI(
            base_url=endpoint,
            api_key=key,
            max_retries=0,
            timeout=limits.timeout_seconds,
            http_client=http_client,
        )
        stack.callback(client.close)
        counter = RedisRuleCounter(redis_client, mode="live", namespace=namespace, transaction_retries=3)
        breaker = RedisCircuitBreaker(redis_client)
        semaphore = RedisModelSemaphore(redis_client, lease_ms=policy.lease_ms, heartbeat_ms=policy.heartbeat_ms)

        def analyze(materials):
            guarded.lease.assert_valid()
            result = analyze_with_model(
                materials,
                client,
                model=model,
                transport_mode="live",
                timeout_seconds=limits.timeout_seconds,
            )
            guarded.lease.assert_valid()
            transport = result["model_transport"]
            if transport["sdk_request_calls"]:
                try:
                    if transport["error_code"] == "request_failed":
                        breaker.record_failure(model)
                    else:
                        breaker.record_success(model)
                except Exception as error:
                    transport["circuit_update_error_type"] = type(error).__name__
            return result

        if surge_settings["enabled"]:
            from .surge_runtime import build_processor, call_model

            def generate(request):
                guarded.lease.assert_valid()
                result = call_model(client, request, model=model, budget=limits)
                # Preserve a received answer before a subsequent lease check can
                # fail. _ModelGuard verifies the lease again before any ACK.
                transport = result["transport"]
                if transport["sdk_request_calls"]:
                    try:
                        if transport["error_code"] == "request_failed":
                            breaker.record_failure(model)
                        else:
                            breaker.record_success(model)
                    except Exception as error:
                        transport["circuit_update_error_type"] = type(error).__name__
                return result

            processor = build_processor(
                state_dir / "processing_surge_v1",
                database=database,
                rules=rules,
                load_rules=lambda: CsvRuleConfigSource(csv_path),
                counter=counter,
                redis_client=redis_client,
                generate=generate,
                model=model,
                settings=surge_settings,
            )
        else:
            processor = SignalProcessor(
                state_dir / "processing",
                load_rules=lambda: CsvRuleConfigSource(csv_path),
                load_task_rows=LocalWorkflowTaskSource(database).load_task_rows,
                analyze=analyze,
                sink=LocalRecommendationSink(state_dir / "receipts"),
                source_kind="workflow_task_store",
            )
        guarded = _ModelGuard(
            processor,
            semaphore,
            breaker,
            policy,
            model,
            (ModelCircuitOpen, ModelSemaphoreTimeout),
        )
        return run_tick(state_dir / "scheduler", rules=rules, counter=counter, processor=guarded)


def run_background(root=None, environ=None, *, execute=None):
    report = {"schema_version": "sec_know.rule_background.v1", "automatic_apply": False}
    try:
        root = PROJECT_ROOT if root is None else Path(root).resolve()
        env = dict(os.environ if environ is None else environ)
        if env.get("RULE_REFINE_MONITOR_ENABLED") != "0":
            from .topic_settings import load_settings as load_topic_settings

            topic_settings = load_topic_settings(root, env)
            if topic_settings is not None:
                from .topic_background import run_background as run_topic_background

                return run_topic_background(root, env, settings=topic_settings, execute=execute)
        settings, reason = _settings(root, env)
        if settings is None:
            return {**report, "status": "disabled", "reason": reason}
        from .local_settings import configure_rule_monitor

        monitor = configure_rule_monitor(root, env)
        if monitor["status"] != "configured" or not monitor["enabled"]:
            return {
                **report,
                "status": "disabled",
                "reason": "monitor_not_enabled_or_invalid",
            }
        return {**(execute or _execute)(root, settings, env), **report}
    except Exception as error:
        return {
            **report,
            "status": "failed",
            "error_type": type(error).__name__,
            "reason": str(error) if isinstance(error, _SetupError) else "background_check_or_execution_failed",
        }


def register_rule_refine_task(app):
    if TASK_NAME in app.tasks:
        return app.tasks[TASK_NAME]

    @app.task(name=TASK_NAME, ignore_result=True, max_retries=0)
    def rule_refine_tick():
        result = run_background()
        logging.getLogger(__name__).info("rule_refine_background %s", json.dumps(result, ensure_ascii=False))
        return result

    return rule_refine_tick
