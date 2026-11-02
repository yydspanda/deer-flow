"""可选接收侧监测；默认关闭，统计故障不改变原任务提交结果。"""

import hashlib
import json
import logging
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)
_capacity = threading.BoundedSemaphore(4)
_initializing = threading.Lock()
_monitor = None
_retry_at = 0.0


class RuleMonitor:
    """提供器可替换；一秒为停止开始下一条计数的预算，不取消正在提交的事务。"""

    def __init__(self, rules, counter):
        if counter.mode != "live":
            raise ValueError("入口监测只允许 live 计数器")
        self.rules, self.counter = rules, counter

    def observe(self, payload, *, deadline):
        from .ingress import normalize_received_alert

        normalized = normalize_received_alert(payload, self.rules, received_at=datetime.now(timezone.utc), mode="live")
        observations = normalized["observations"]
        result = {
            "status": normalized["status"],
            "rule_count": len(observations),
            "counted_rules": 0,
            "added_rules": 0,
            "duplicate_rules": 0,
            "new_signals": 0,
            "pending_signals": 0,
            "unconfirmed_rules": 0,
            "unstarted_rules": 0,
            "issue_counts": dict(Counter(item["code"] for item in normalized["issues"])),
            "duplicate_hits": normalized["duplicate_hit_count"],
            "rule_providers": sorted({item["rule_source"]["kind"][:64] for item in observations})[:8],
        }
        for index, observation in enumerate(observations):
            if index == 8:
                result.update(status="degraded", reason="rule_limit", unstarted_rules=len(observations) - index)
                break
            if time.monotonic() >= deadline:
                result.update(status="degraded", reason="time_budget", unstarted_rules=len(observations) - index)
                break
            try:
                outcome = self.counter.observe(observation)
            except Exception as error:
                # A lost response may follow a committed transaction: never label it uncounted.
                result.update(
                    status="degraded",
                    reason="counter_error",
                    error_type=type(error).__name__,
                    unconfirmed_rules=1,
                    unstarted_rules=len(observations) - index - 1,
                )
                break
            result["counted_rules"] += 1
            result["added_rules"] += int(outcome["added"])
            result["duplicate_rules"] += int(not outcome["added"])
            result["new_signals"] += int(outcome["triggered"])
            result["pending_signals"] += int(outcome["pending_signal"] is not None)
        return result


def _build_monitor():
    path = os.environ.get("RULE_REFINE_CSV_PATH", "").strip()
    namespace = os.environ.get("RULE_REFINE_NAMESPACE", "").strip()
    if not path or not namespace:
        raise ValueError("启用监测需配置 CSV 路径和稳定命名空间")
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from util.root_config import REDIS_URL

    from .counter import RedisRuleCounter
    from .csv_config_source import CsvRuleConfigSource

    rules = CsvRuleConfigSource(path)
    client = Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        socket_keepalive=True,
        health_check_interval=30,
        max_connections=16,
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        retry_on_error=[],
    )
    # URL query options take precedence in from_url; pin bounds before any connection is made.
    client.connection_pool.connection_kwargs.update(
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        retry_on_error=[],
    )
    client.connection_pool.max_connections = 16
    try:
        return RuleMonitor(rules, RedisRuleCounter(client, mode="live", namespace=namespace, transaction_retries=3))
    except Exception:
        client.close()
        raise


def _ready_monitor():
    global _monitor, _retry_at
    if _monitor is not None:
        return _monitor, None
    if time.monotonic() < _retry_at:
        return None, {"status": "degraded", "reason": "initialization_backoff"}
    if not _initializing.acquire(blocking=False):
        return None, {"status": "degraded", "reason": "initialization_busy"}
    try:
        if time.monotonic() < _retry_at:
            return None, {"status": "degraded", "reason": "initialization_backoff"}
        if _monitor is None:
            try:
                _monitor = _build_monitor()
            except Exception as error:
                _retry_at = time.monotonic() + 30
                return None, {
                    "status": "degraded",
                    "reason": "initialization_error",
                    "error_type": type(error).__name__,
                }
        return _monitor, None
    finally:
        _initializing.release()


def _emit(result, request_key=None):
    if request_key is not None:
        result["request_key"] = request_key
    try:
        level = logging.INFO if result["status"] == "ready" else logging.WARNING
        _logger.log(level, "rule_refine_monitor %s", json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception:
        # An optional logging handler must not change the original workflow response.
        pass
    return result


def observe_workflow_request(flow_task_msg):
    """在认证/请求校验后、原 CRUD 前调用；不等模型结果，按 Redis 实际计数时间分窗。"""
    acquired = False
    request_key = None
    started = time.monotonic()
    try:
        enabled = os.environ.get("RULE_REFINE_MONITOR_ENABLED", "0")
        if enabled == "0":
            return {"status": "disabled"}

        def field(key):
            return flow_task_msg.get(key) if isinstance(flow_task_msg, dict) else getattr(flow_task_msg, key, None)

        if field("app_code") != "zeus" or field("flow_id") != "alert_agent":
            return {"status": "skipped", "reason": "other_workflow"}
        alert_id = field("alert_id")
        if type(alert_id) is int and alert_id >= 0:
            alert_id = str(alert_id)
        if isinstance(alert_id, str) and 1 <= len(alert_id) <= 512:
            identity = json.dumps(["zeus", "alert_agent", alert_id.strip()], separators=(",", ":"), ensure_ascii=False)
            request_key = "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if enabled != "1":
            return _emit({"status": "degraded", "reason": "invalid_enable_setting"}, request_key)
        acquired = _capacity.acquire(blocking=False)
        if not acquired:
            return _emit({"status": "degraded", "reason": "capacity_exhausted"}, request_key)
        monitor, failure = _ready_monitor()
        if failure is not None:
            return _emit(failure, request_key)
        payload = {key: field(key) for key in ("app_code", "flow_id", "alert_id", "alert_data")}
        result = monitor.observe(payload, deadline=started + 1.0)
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        return _emit(result, request_key)
    except Exception as error:
        return _emit({"status": "degraded", "reason": "monitor_error", "error_type": type(error).__name__}, request_key)
    finally:
        if acquired:
            _capacity.release()
