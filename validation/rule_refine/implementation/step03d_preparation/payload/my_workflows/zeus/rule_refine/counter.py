"""Redis 规则计数与待处理信号；沿用原项目 WATCH 事务，不依赖 Lua。"""

import hashlib
import json
import math
import random
import time
from datetime import datetime, timezone
from uuid import uuid4

from redis.exceptions import ResponseError, WatchError


class CounterBusyError(RuntimeError):
    """有限次数的事务竞争后仍未提交；调用方需记录失败并稍后重试。"""


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("计数身份字段必须是 1～512 字符的字符串")
    return value


def _decoded(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


class RedisRuleCounter:
    def __init__(
        self,
        client,
        *,
        mode="demo",
        namespace=None,
        window_seconds=3600,
        minimum_count=1000,
        growth_ratio=3,
        cooldown_seconds=3600,
        dedup_seconds=86400,
        transaction_retries=32,
    ):
        if not isinstance(mode, str) or mode not in {"demo", "replay", "live"}:
            raise ValueError("计数模式无效")
        if namespace is None:
            if mode == "live":
                raise ValueError("实时计数需由服务配置提供稳定 namespace")
            namespace = uuid4().hex
        for value in (window_seconds, minimum_count, cooldown_seconds, dedup_seconds):
            if type(value) is not int or not 1 <= value <= 31536000:
                raise ValueError("窗口、数量、冷却与保留期必须为有界正整数")
        if type(growth_ratio) not in (int, float) or not math.isfinite(growth_ratio) or growth_ratio <= 1:
            raise ValueError("增长倍数必须为大于 1 的有限数值")
        if dedup_seconds < 2 * window_seconds + cooldown_seconds:
            raise ValueError("去重保留期至少覆盖两个窗口加冷却期")
        if type(transaction_retries) is not int or not 1 <= transaction_retries <= 100:
            raise ValueError("事务重试次数必须为 1～100 的整数")
        self.client = client
        self.mode = mode
        self.namespace = _text(namespace)
        self.settings = (window_seconds, minimum_count, growth_ratio, cooldown_seconds, dedup_seconds)
        self.transaction_retries = transaction_retries

    def _keys(self, observation):
        if not isinstance(observation, dict) or observation.get("mode") != self.mode:
            raise ValueError("观察模式与计数器不一致")
        if self.mode == "live" and observation.get("company_scope_source") not in (
            "explicit",
            "csv_unique_match",
            "rule_config_unique_match",
        ):
            raise ValueError("实时计数必须具有明确企业范围")
        scope = [self.namespace, self.mode]
        scope.extend(_text(observation.get(k)) for k in ("source_app", "company_code", "topic", "rule_code"))
        digest = hashlib.sha256(json.dumps(scope, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        prefix = "sec_know:rule_refine:counter:v1:{" + digest + "}:"
        return [prefix + suffix for suffix in ("events", "state", "pending")]

    @staticmethod
    def _signal(signal):
        if signal:
            for key in ("window_start", "window_end"):
                signal[key] = datetime.fromtimestamp(signal[key + "_ms"] / 1000, timezone.utc).isoformat()
        return signal

    @staticmethod
    def _backoff(attempt, error):
        # redis-py may wrap a lost connection while watching as WatchError.
        # Preserve that failure instead of treating it as ordinary contention.
        if str(error) != "Watched variable changed." or error.__cause__ is not None:
            raise error
        time.sleep(random.uniform(0.001, min(0.05, 0.005 * (attempt + 1))))

    def observe(self, observation):
        keys = self._keys(observation)
        alert_id = _text(observation.get("alert_id"))
        value = observation.get("received_at")
        if not isinstance(value, str):
            raise ValueError("received_at 必须是带时区时间字符串")
        at = datetime.fromisoformat(value)
        if at.utcoffset() is None:
            raise ValueError("received_at 必须包含时区")
        milliseconds = int(at.timestamp() * 1000)
        w, minimum, ratio, cooldown, retention = self.settings
        ttl = retention + 2 * w + cooldown
        w, cooldown, retention = w * 1000, cooldown * 1000, retention * 1000
        template = {
            "schema_version": "sec_know.rule_burst_signal.v1",
            "signal_id": uuid4().hex,
            "mode": self.mode,
            "namespace": self.namespace,
            "time_basis": "redis_observed_at" if self.mode == "live" else "provided_received_at",
            **{k: observation[k] for k in ("source_app", "company_code", "topic", "rule_code")},
            "window_seconds": self.settings[0],
            "minimum_count": minimum,
            "growth_threshold": ratio,
            "cooldown_seconds": self.settings[3],
            "dedup_seconds": self.settings[4],
        }
        for attempt in range(self.transaction_retries):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(*keys)
                    # Keep the WATCH connection reserved until its write transaction.
                    # A separate pipeline batches reads from the same Redis primary;
                    # any intervening change still invalidates the original WATCH.
                    with self.client.pipeline(transaction=False) as read:
                        for key in keys:
                            read.type(key)
                        read.hgetall(keys[1])
                        read.get(keys[2])
                        read.zscore(keys[0], alert_id)
                        if self.mode == "live":
                            read.time()
                        snapshot = read.execute(raise_on_error=False)
                    for actual, kind in zip(snapshot[:3], ("zset", "hash", "string")):
                        if isinstance(actual, Exception):
                            raise actual
                        if _decoded(actual) not in ("none", kind):
                            raise ResponseError("rule counter key type mismatch")
                    for value in snapshot[3:]:
                        if isinstance(value, Exception):
                            raise value
                    state = {_decoded(k): int(v) for k, v in snapshot[3].items()}
                    raw, score = snapshot[4:6]
                    observed_at = milliseconds
                    if self.mode == "live":
                        seconds, micros = snapshot[6]
                        observed_at = seconds * 1000 + micros // 1000
                    pending = self._signal(json.loads(raw)) if raw else None
                    now = max(observed_at, state.get("watermark", observed_at))
                    cutoff = now - retention
                    coverage = max(state.get("coverage", now), cutoff)
                    added = observed_at > cutoff and (score is None or score <= cutoff)
                    with self.client.pipeline(transaction=False) as read:
                        read.zcount(keys[0], f"({now - w}", now)
                        read.zcount(keys[0], f"({now - 2 * w}", now - w)
                        current, previous = read.execute()
                    current += int(added and observed_at > now - w)
                    previous += int(added and now - 2 * w < observed_at <= now - w)
                    ready = now - coverage >= 2 * w
                    growth = current / previous if ready and previous else None
                    eligible = current >= minimum and (growth is None or growth >= ratio)
                    triggered = bool(
                        added
                        and observed_at > now - w
                        and eligible
                        and pending is None
                        and ("last_trigger" not in state or now - state["last_trigger"] >= cooldown)
                    )
                    updates = {"watermark": now, "coverage": coverage}
                    encoded = None
                    if triggered:
                        samples = [
                            (_decoded(identity), score)
                            for identity, score in pipe.zrevrangebyscore(
                                keys[0], now, f"({now - w}", start=0, num=20, withscores=True
                            )
                        ]
                        samples.append((alert_id, observed_at))
                        samples.sort(key=lambda pair: (pair[1], pair[0].encode("utf-8")), reverse=True)
                        pending = self._signal(
                            {
                                **template,
                                "reason": "growth" if growth is not None else "high_volume",
                                "current_count": current,
                                "previous_count": previous,
                                "baseline_ready": ready,
                                "growth_ratio": growth,
                                "window_start_ms": now - w,
                                "window_end_ms": now,
                                "sample_alert_ids": [identity for identity, _ in samples[:20]],
                            }
                        )
                        encoded = json.dumps(pending, ensure_ascii=False, allow_nan=False)
                        updates["last_trigger"] = now
                    pipe.multi()
                    pipe.zremrangebyscore(keys[0], "-inf", cutoff)
                    if added:
                        pipe.zadd(keys[0], {alert_id: observed_at}, nx=True)
                    pipe.hset(keys[1], mapping=updates)
                    if triggered:
                        pipe.set(keys[2], encoded)
                    pipe.expire(keys[0], ttl)
                    pipe.expire(keys[1], ttl)
                    pipe.execute()
                    return {
                        "added": added,
                        "too_old": observed_at <= cutoff,
                        "current_count": current,
                        "previous_count": previous,
                        "baseline_ready": ready,
                        "growth_ratio": growth,
                        "triggered": triggered,
                        "pending_signal": pending,
                    }
            except WatchError as error:
                self._backoff(attempt, error)
        raise CounterBusyError("计数事务竞争超限，未确认提交；请稍后重试同一告警")

    def pending(self, observation):
        raw = self.client.get(self._keys(observation)[2])
        return self._signal(json.loads(raw)) if raw else None

    def acknowledge(self, observation, signal_id):
        key = self._keys(observation)[2]
        signal_id = _text(signal_id)
        for attempt in range(self.transaction_retries):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if not raw or json.loads(raw)["signal_id"] != signal_id:
                        pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.delete(key)
                    return bool(pipe.execute()[0])
            except WatchError as error:
                self._backoff(attempt, error)
        raise CounterBusyError("信号确认事务竞争超限；请稍后重试")
