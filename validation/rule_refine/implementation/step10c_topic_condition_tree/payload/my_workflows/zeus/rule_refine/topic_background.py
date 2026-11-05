"""Topic advisory orchestration using existing model capacity and ZEUS client."""

import hashlib
import os
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from .topic_settings import load_settings, profile


def _execute(root, settings, env):
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

    from .background_service import _ModelGuard
    from .background_tick import run_tick
    from .counter import RedisRuleCounter
    from .csv_config_source import CsvRuleConfigSource
    from .surge_runtime import budget_from_settings, call_model, load_window_case
    from .topic_history import TopicTaskSource

    if rc.ENV != profile(env) or rc.ENV not in {"LOCAL", "PRD"}:
        raise ValueError("topic_profile_mismatch")
    from .topic_processor import TopicProcessor

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
        raise ValueError("invalid_topic_model_proxy")
    model = settings["model"]
    policy = get_model_policy(model)
    if policy is None:
        raise ValueError("topic_model_policy_missing")
    budget = budget_from_settings(settings)
    namespace, csv_path = env["RULE_REFINE_NAMESPACE"], env["RULE_REFINE_CSV_PATH"]
    directory = (
        Path(rc.DATA_DIR)
        / "rule_refine/topic_filters"
        / hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
    )
    rules = CsvRuleConfigSource(csv_path)
    source = TopicTaskSource(rules)
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
        counter = RedisRuleCounter(
            redis_client, mode="live", namespace=namespace, transaction_retries=3
        )
        breaker = RedisCircuitBreaker(redis_client)
        semaphore = RedisModelSemaphore(
            redis_client, lease_ms=policy.lease_ms, heartbeat_ms=policy.heartbeat_ms
        )

        def generate(request):
            guarded.lease.assert_valid()
            answer = call_model(client, request, model=model, budget=budget)
            transport = answer["transport"]
            if transport["sdk_request_calls"]:
                try:
                    if transport["error_code"] == "request_failed":
                        breaker.record_failure(model)
                    else:
                        breaker.record_success(model)
                except Exception as error:
                    transport["circuit_update_error_type"] = type(error).__name__
            return answer

        def submit(payload):
            from my_workflows.zeus.flows.disposition_tools.small_rule_sync_client import (
                SmallRuleSyncClient,
            )

            guarded.lease.assert_valid()
            return SmallRuleSyncClient().sync_small_rule([payload])

        processor = TopicProcessor(
            directory,
            ai_contract="topic-condition-tree-v1",
            load_case=lambda signal: load_window_case(
                source, counter, redis_client, signal
            ),
            load_rules=lambda: CsvRuleConfigSource(csv_path),
            generate=generate,
            model=model,
            budget=budget,
            submit=submit,
            operator_context=settings["operator_context"],
        )
        guarded = _ModelGuard(
            processor,
            semaphore,
            breaker,
            policy,
            model,
            (ModelCircuitOpen, ModelSemaphoreTimeout),
        )
        return run_tick(
            directory / "scheduler", rules=rules, counter=counter, processor=guarded
        )


def run_background(root, environ=None, *, settings=None, execute=None):
    report = {
        "schema_version": "sec_know.topic_background.v1",
        "automatic_apply": False,
        "activation_owner": "zeus",
    }
    try:
        env = dict(os.environ if environ is None else environ)
        if env.get("RULE_REFINE_MONITOR_ENABLED") == "0":
            return {**report, "status": "disabled", "reason": "monitor_disabled"}
        settings = load_settings(root, env) if settings is None else settings
        if settings is None or profile(env) not in {"LOCAL", "PRD"}:
            return {**report, "status": "disabled", "reason": "topic_disabled"}
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
            "reason": "topic_background_failed",
            "error_type": type(error).__name__,
        }
