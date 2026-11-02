"""真实 Redis 人工样例测试；随机隔离键，只删除本次创建的精确键。"""

import importlib
import json
import os
import sys
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from redis import Redis
from redis.client import Pipeline
from redis.exceptions import ConnectionError, ResponseError, WatchError

project = Path(__file__).resolve().parents[2]
package = types.ModuleType("_rule_refine_counter_test")
package.__path__ = [str(project / "my_workflows/zeus/rule_refine")]
sys.modules[package.__name__] = package
counter_module = importlib.import_module(package.__name__ + ".counter")
Counter = counter_module.RedisRuleCounter
CounterBusyError = counter_module.CounterBusyError
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
OPTIONS = dict(window_seconds=10, minimum_count=3, growth_ratio=2, cooldown_seconds=10, dedup_seconds=60)


def client():
    try:
        if url := os.environ.get("RULE_REFINE_TEST_REDIS_URL"):
            connection = Redis.from_url(url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
        else:
            sys.path.insert(0, str(project))
            from util.redis_client import get_redis

            connection = get_redis()
        connection.ping()
        return connection
    except Exception as error:
        raise RuntimeError("Redis 连接检查失败：" + type(error).__name__ + "；未开始计数测试") from None


def observation(alert_id="a", seconds=0, **changes):
    return {
        "mode": "demo",
        "source_app": "zeus",
        "company_code": "demo_company",
        "company_scope_source": "explicit",
        "topic": "demo_topic",
        "rule_code": "DEMO_RULE",
        "rule_name": "人工规则",
        "rule_config_version": "demo-v1",
        "alert_id": str(alert_id),
        "received_at": (NOW + timedelta(seconds=seconds)).isoformat(),
        **changes,
    }


class CounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.redis = client()

    def setUp(self):
        self.keys = set()
        self.counter = self.make_counter()

    def tearDown(self):
        for key in self.keys:
            self.redis.delete(key)

    def make_counter(self, **options):
        return Counter(self.redis, **{**OPTIONS, **options})

    def send(self, alert_id="a", seconds=0, counter=None, **changes):
        counter = counter or self.counter
        item = observation(alert_id, seconds, mode=counter.mode, **changes)
        self.keys.update(counter._keys(item))
        return counter.observe(item)

    def trigger(self):
        self.send("a", 0)
        self.send("b", 1)
        return self.send("c", 2)["pending_signal"]

    def test_duplicate_first_received_time_and_restart(self):
        self.assertTrue(self.send()["added"])
        restarted = self.make_counter(namespace=self.counter.namespace)
        repeated = self.send("a", 9, counter=restarted)
        self.assertFalse(repeated["added"])
        self.assertEqual(repeated["current_count"], 1)
        later = self.send("b", 11, counter=restarted)
        self.assertEqual((later["current_count"], later["previous_count"]), (1, 1))

    def test_adjacent_windows_boundary_and_out_of_order_arrival(self):
        self.send("a", 0)
        at_boundary = self.send("b", 10)
        self.assertEqual((at_boundary["current_count"], at_boundary["previous_count"]), (1, 1))
        self.send("c", 20)
        late = self.send("d", 15)
        self.assertEqual((late["current_count"], late["previous_count"]), (2, 1))
        self.assertTrue(late["baseline_ready"])

    def test_cold_start_signal_is_persisted_and_ack_is_conditional(self):
        signal = self.trigger()
        self.assertEqual(signal["reason"], "high_volume")
        self.assertEqual(signal["current_count"], 3)
        self.assertEqual(set(signal["sample_alert_ids"]), {"a", "b", "c"})
        restarted = self.make_counter(namespace=self.counter.namespace)
        self.assertEqual(restarted.pending(observation()), signal)
        again = self.send("d", 3, counter=restarted)
        self.assertFalse(again["triggered"])
        self.assertEqual(again["pending_signal"], signal)
        keys = restarted._keys(observation())
        self.assertTrue(all(self.redis.ttl(key) > 0 for key in keys[:2]))
        self.assertEqual(self.redis.ttl(keys[2]), -1)
        self.assertFalse(restarted.acknowledge(observation(), "wrong-id"))
        self.assertEqual(restarted.pending(observation()), signal)
        self.assertTrue(restarted.acknowledge(observation(), signal["signal_id"]))
        self.assertIsNone(restarted.pending(observation()))
        self.assertFalse(restarted.acknowledge(observation(), signal["signal_id"]))

    def test_full_baseline_requires_growth_not_just_absolute_count(self):
        self.send("seed", 0)
        self.send("p1", 5)
        signal = self.send("p2", 9)["pending_signal"]
        self.counter.acknowledge(observation(), signal["signal_id"])
        self.send("c1", 15)
        self.send("c2", 16)
        stable = self.send("c3", 20)
        self.assertTrue(stable["baseline_ready"])
        self.assertEqual((stable["current_count"], stable["previous_count"]), (3, 2))
        self.assertFalse(stable["triggered"])
        increased = self.send("c4", 21)
        self.assertTrue(increased["triggered"])
        self.assertEqual(increased["growth_ratio"], 2)
        self.assertEqual(increased["pending_signal"]["reason"], "growth")

    def test_zero_baseline_uses_high_volume_without_infinite_ratio(self):
        self.send("seed", 0)
        self.send("a", 21)
        self.send("b", 22)
        result = self.send("c", 23)
        self.assertTrue(result["baseline_ready"])
        self.assertEqual(result["previous_count"], 0)
        self.assertIsNone(result["growth_ratio"])
        self.assertEqual(result["pending_signal"]["reason"], "high_volume")

    def test_ack_does_not_bypass_cooldown(self):
        signal = self.trigger()
        self.counter.acknowledge(observation(), signal["signal_id"])
        self.assertFalse(self.send("d", 3)["triggered"])
        self.assertFalse(self.send("e", 11)["triggered"])
        self.assertTrue(self.send("f", 12)["triggered"])

    def test_duplicate_cannot_trigger_after_cooldown(self):
        signal = self.trigger()
        self.counter.acknowledge(observation(), signal["signal_id"])
        for identity, timestamp in [("d", 8), ("e", 9), ("f", 10)]:
            self.assertFalse(self.send(identity, timestamp)["triggered"])
        duplicate = self.send("c", 12)
        self.assertEqual(duplicate["current_count"], 3)
        self.assertFalse(duplicate["added"])
        self.assertFalse(duplicate["triggered"])

    def test_scope_mode_and_automatic_namespace_are_isolated(self):
        self.send()
        for field in ("company_code", "source_app", "topic", "rule_code"):
            result = self.send(**{field: "different"})
            self.assertTrue(result["added"])
            self.assertEqual(result["current_count"], 1)
        for other in (self.make_counter(), self.make_counter(mode="replay")):
            result = self.send(counter=other)
            self.assertTrue(result["added"])
            self.assertEqual(result["current_count"], 1)
            self.assertNotEqual(other.namespace, self.counter.namespace)

    def test_concurrent_duplicate_and_threshold_create_one_signal(self):
        self.keys.update(self.counter._keys(observation()))
        with ThreadPoolExecutor(max_workers=8) as pool:
            same = list(pool.map(lambda _: self.counter.observe(observation()), range(16)))
            unique = list(pool.map(lambda i: self.counter.observe(observation(str(i), 1)), range(16)))
        self.assertEqual(sum(item["added"] for item in same), 1)
        self.assertEqual(sum(item["triggered"] for item in unique), 1)
        signals = {item["pending_signal"]["signal_id"] for item in unique if item["pending_signal"]}
        self.assertEqual(len(signals), 1)
        self.assertEqual(self.counter.observe(observation("a", 1))["current_count"], 17)

    def test_retention_is_bounded_and_expired_id_can_be_counted_again(self):
        self.send("a", 0)
        result = self.send("a", 61)
        self.assertTrue(result["added"])
        self.assertEqual((result["current_count"], result["previous_count"]), (1, 0))
        old = self.send("late", 0)
        self.assertTrue(old["too_old"])
        self.assertFalse(old["added"])
        self.assertFalse(old["triggered"])

    def test_lost_response_retry_recovers_same_pending_signal(self):
        self.send("a", 0)
        self.send("b", 1)
        execute = Pipeline.execute
        lost = False

        def lose_response(pipe, *args, **kwargs):
            nonlocal lost
            result = execute(pipe, *args, **kwargs)
            if not lost:
                lost = True
                raise ConnectionError("injected response loss after Redis execution")
            return result

        with patch.object(Pipeline, "execute", new=lose_response):
            with self.assertRaises(ConnectionError):
                self.send("c", 2)
        pending = self.counter.pending(observation())
        self.assertIsNotNone(pending)
        repeated = self.send("c", 2)
        self.assertEqual(repeated["current_count"], 3)
        self.assertFalse(repeated["added"])
        self.assertFalse(repeated["triggered"])
        self.assertEqual(repeated["pending_signal"], pending)

    def test_live_uses_redis_clock_instead_of_payload_future_time(self):
        counter = self.make_counter(mode="live", namespace=uuid4().hex, minimum_count=1)
        item = observation(mode="live", received_at="2099-01-01T00:00:00+00:00")
        self.keys.update(counter._keys(item))
        signal = counter.observe(item)["pending_signal"]
        seconds, micros = self.redis.time()
        server_ms = seconds * 1000 + micros // 1000
        self.assertLess(abs(signal["window_end_ms"] - server_ms), 5000)
        self.assertEqual(signal["time_basis"], "redis_observed_at")
        self.assertFalse(counter.observe(item)["added"])

    def test_invalid_options_observations_and_connection_failure(self):
        for options in [
            {"window_seconds": 0},
            {"minimum_count": True},
            {"growth_ratio": float("nan")},
            {"dedup_seconds": 20},
            {"mode": "live"},
            {"transaction_retries": 0},
            {"transaction_retries": True},
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.make_counter(**options)
        for changes in [{"mode": "live"}, {"received_at": "2026-01-01T00:00:00"}, {"alert_id": ""}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.counter.observe(observation(**changes))
        failing = Mock()
        failing.pipeline.side_effect = ConnectionError("injected connection failure")
        with self.assertRaises(ConnectionError):
            Counter(failing, **OPTIONS).observe(observation())

    def test_wrong_redis_type_does_not_partially_write_other_keys(self):
        keys = self.counter._keys(observation())
        self.keys.update(keys)
        self.redis.set(keys[0], "test-wrong-type")
        with self.assertRaisesRegex(ResponseError, "^rule counter key type mismatch$"):
            self.counter.observe(observation())
        self.assertFalse(self.redis.exists(keys[1]))
        self.assertFalse(self.redis.exists(keys[2]))
        value = self.redis.get(keys[0])
        self.assertIn(value, ("test-wrong-type", b"test-wrong-type"))

    def test_observe_and_acknowledge_work_without_lua(self):
        error = ResponseError("unknown command EVAL")
        with (
            patch.object(Redis, "eval", side_effect=error),
            patch.object(Redis, "evalsha", side_effect=error),
            patch.object(Redis, "register_script", side_effect=error),
        ):
            signal = self.trigger()
            self.assertEqual(signal["current_count"], 3)
            self.assertTrue(self.counter.acknowledge(observation(), signal["signal_id"]))

    def test_real_watch_conflict_keeps_racing_count_and_existing_signal(self):
        self.send("a", 0)
        self.send("b", 1)
        execute = Pipeline.execute
        racing = []
        injected = False

        def race_before_execute(pipe, *args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                racing.append(self.counter.observe(observation("racer", 2)))
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=race_before_execute):
            result = self.send("c", 2)
        self.assertTrue(racing[0]["triggered"])
        self.assertTrue(result["added"])
        self.assertFalse(result["triggered"])
        self.assertEqual(result["current_count"], 4)
        self.assertEqual(result["pending_signal"], racing[0]["pending_signal"])
        self.assertEqual(self.redis.zcard(self.counter._keys(observation())[0]), 4)

    def test_acknowledge_watch_conflict_preserves_replacement(self):
        signal = self.trigger()
        replacement = {**signal, "signal_id": uuid4().hex}
        pending_key = self.counter._keys(observation())[2]
        execute = Pipeline.execute
        injected = False

        def replace_before_execute(pipe, *args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                self.redis.set(pending_key, json.dumps(replacement))
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=replace_before_execute):
            acknowledged = self.counter.acknowledge(observation(), signal["signal_id"])
        self.assertFalse(acknowledged)
        self.assertEqual(self.counter.pending(observation()), replacement)

    def test_repeated_watch_conflicts_fail_explicitly_without_partial_count(self):
        counter = self.make_counter(transaction_retries=2)
        keys = counter._keys(observation())
        self.keys.update(keys)
        execute = Pipeline.execute
        attempts = 0

        def always_race(pipe, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            self.redis.hset(keys[1], "test_collision", attempts)
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=always_race):
            with self.assertRaises(CounterBusyError):
                counter.observe(observation())
        self.assertEqual(attempts, 2)
        self.assertFalse(self.redis.exists(keys[0]))
        self.assertIsNone(counter.pending(observation()))

    def test_connection_wrapped_as_watch_error_is_not_retried(self):
        keys = self.counter._keys(observation())
        self.keys.update(keys)
        failure = WatchError("A ConnectionError occurred while watching one or more keys")
        with patch.object(Pipeline, "execute", side_effect=failure) as execute:
            with self.assertRaises(WatchError) as raised:
                self.counter.observe(observation())
        self.assertIs(raised.exception, failure)
        self.assertIsNone(failure.__cause__)
        self.assertEqual(execute.call_count, 1)
        for key in keys:
            self.assertFalse(self.redis.exists(key))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        connection = client()
        counter = Counter(connection, **OPTIONS)
        keys = counter._keys(observation())
        try:
            results = [counter.observe(observation(name, i)) for i, name in enumerate(["a", "b", "c", "c"])]
            print(
                json.dumps(
                    {
                        "mode": "demo",
                        "submitted": 4,
                        "unique_count": results[-1]["current_count"],
                        "new_signal_count": sum(r["triggered"] for r in results),
                        "reason": results[-1]["pending_signal"]["reason"],
                        "duplicate_added": results[-1]["added"],
                        "model_calls": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            for key in keys:
                connection.delete(key)
    else:
        unittest.main(verbosity=2)
