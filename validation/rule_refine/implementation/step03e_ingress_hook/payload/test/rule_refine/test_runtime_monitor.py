"""入口监测隔离测试；无网络、数据库、Celery 或模型调用。"""

import copy
import importlib
import os
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_monitor_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
module = importlib.import_module(package.__name__ + ".runtime_monitor")
common = importlib.import_module(package.__name__ + ".rule_config_source")


def sample(count=2):
    hits = [{"topic": "demo_topic", "ruleCode": f"RULE_{index}"} for index in range(count)]
    return {
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": "101",
        "alert_data": {"alert": {"alertId": "101", "hitLog": hits + [dict(hits[0])]}},
    }


class Rules:
    def get_rule(self, rule_code, *, company_code=None, topic=None):
        return {
            "company_code": "config_company",
            "topic": topic,
            "rule_code": rule_code,
            "rule_name": "人工规则",
            "rule_config_version": "demo-v1",
            "source": {"kind": "test_provider"},
        }


def outcome(*, added=True, triggered=False):
    return {"added": added, "triggered": triggered, "pending_signal": {"signal_id": "test_signal"}}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.enter(patch.dict(os.environ, {"RULE_REFINE_MONITOR_ENABLED": "1"}, clear=True))
        self.enter(patch.object(module, "_logger"))
        self.enter(patch.object(module, "_capacity", threading.BoundedSemaphore(4)))
        self.enter(patch.object(module, "_initializing", threading.Lock()))
        self.enter(patch.object(module, "_monitor", None))
        self.enter(patch.object(module, "_retry_at", 0.0))
        self.counter = Mock(mode="live")
        self.counter.observe.return_value = outcome()
        self.monitor = module.RuleMonitor(Rules(), self.counter)
        self.factory = self.enter(patch.object(module, "_build_monitor", return_value=self.monitor))

    def enter(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def test_disabled_and_other_workflows_do_not_initialize(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(module.observe_workflow_request(object()), {"status": "disabled"})
        for changes in ({"app_code": "other"}, {"flow_id": "other"}):
            self.assertEqual(module.observe_workflow_request({**sample(), **changes})["reason"], "other_workflow")
        self.factory.assert_not_called()

    def test_invalid_enable_setting_is_visible_without_initializing(self):
        with patch.dict(os.environ, {"RULE_REFINE_MONITOR_ENABLED": "true"}):
            result = module.observe_workflow_request(sample())
        self.assertEqual((result["status"], result["reason"]), ("degraded", "invalid_enable_setting"))
        self.factory.assert_not_called()

    def test_enabled_uses_provider_live_mode_and_keeps_input_unchanged(self):
        payload = sample()
        before = copy.deepcopy(payload)
        result = module.observe_workflow_request(types.SimpleNamespace(**payload))
        self.assertEqual(payload, before)
        self.assertEqual((result["status"], result["counted_rules"], result["duplicate_hits"]), ("ready", 2, 1))
        self.assertEqual(result["rule_providers"], ["test_provider"])
        for call in self.counter.observe.call_args_list:
            observation = call.args[0]
            self.assertEqual(observation["mode"], "live")
            self.assertEqual(observation["company_code"], "config_company")
            self.assertEqual(observation["company_scope_source"], "rule_config_unique_match")
        self.factory.assert_called_once()

    def test_duplicates_report_existing_pending_without_acknowledging(self):
        self.counter.observe.side_effect = [
            outcome(triggered=True),
            outcome(),
            outcome(added=False),
            outcome(added=False),
        ]
        first = module.observe_workflow_request(sample())
        second = module.observe_workflow_request(sample())
        self.assertEqual((first["added_rules"], first["new_signals"]), (2, 1))
        self.assertEqual((second["added_rules"], second["duplicate_rules"], second["pending_signals"]), (0, 2, 2))
        self.counter.acknowledge.assert_not_called()
        self.factory.assert_called_once()

    def test_unknown_rules_are_visible_and_other_rules_count(self):
        original = self.monitor.rules.get_rule

        def lookup(code, **kwargs):
            if code != "RULE_0":
                raise common.RuleNotFoundError("private")
            return original(code, **kwargs)

        self.monitor.rules.get_rule = Mock(side_effect=lookup)
        result = module.observe_workflow_request(sample())
        self.assertEqual((result["status"], result["counted_rules"]), ("partial", 1))
        self.assertEqual(result["issue_counts"], {"rule_not_found": 1})
        self.assertNotIn("private", str(result))

    def test_provider_failure_releases_capacity_and_sanitizes_log(self):
        self.monitor.rules.get_rule = Mock(side_effect=RuntimeError("SECRET_PAYLOAD"))
        result = module.observe_workflow_request(sample())
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("SECRET_PAYLOAD", str(module._logger.mock_calls))
        self.assertEqual(result["status"], "degraded")
        for _ in range(4):
            self.assertTrue(module._capacity.acquire(blocking=False))
        self.assertFalse(module._capacity.acquire(blocking=False))

    def test_counter_error_preserves_confirmed_counts_and_marks_uncertain(self):
        self.counter.observe.side_effect = [outcome(), OSError("SECRET")]
        result = module.observe_workflow_request(sample(3))
        self.assertEqual((result["counted_rules"], result["unconfirmed_rules"], result["unstarted_rules"]), (1, 1, 1))
        self.assertEqual(result["reason"], "counter_error")
        self.assertNotIn("SECRET", str(result))

    def test_soft_budget_stops_new_counts_without_cancelling_old(self):
        with patch.object(module.time, "monotonic", side_effect=[0.0, 2.0]):
            result = self.monitor.observe(sample(3), deadline=1.0)
        self.assertEqual((result["counted_rules"], result["unstarted_rules"], result["reason"]), (1, 2, "time_budget"))
        self.counter.observe.assert_called_once()

    def test_maximum_eight_rules_is_visible(self):
        result = self.monitor.observe(sample(10), deadline=float("inf"))
        self.assertEqual((result["counted_rules"], result["unstarted_rules"], result["reason"]), (8, 2, "rule_limit"))

    def test_capacity_rejection_has_no_wait_or_factory_work(self):
        for _ in range(4):
            module._capacity.acquire()
        self.assertEqual(module.observe_workflow_request(sample())["reason"], "capacity_exhausted")
        self.factory.assert_not_called()

    def test_safe_request_correlation_is_stable_across_retry_and_failures(self):
        first = module.observe_workflow_request(sample())["request_key"]
        self.assertEqual(len(first), 71)
        retried = {**sample(), "session_id": "another_session"}
        self.counter.observe.side_effect = OSError("private alert payload")
        self.assertEqual(module.observe_workflow_request(retried)["request_key"], first)
        for _ in range(4):
            module._capacity.acquire()
        self.assertEqual(module.observe_workflow_request(retried)["request_key"], first)
        changed = module.observe_workflow_request({**retried, "alert_id": "102"})
        self.assertNotEqual(changed["request_key"], first)
        self.assertNotIn("private alert payload", str(module._logger.mock_calls))

    def test_initialization_failure_backs_off_then_can_recover(self):
        self.factory.side_effect = [ValueError("SECRET_PATH"), self.monitor]
        with patch.object(module.time, "monotonic", return_value=100):
            first = module.observe_workflow_request(sample())
            second = module.observe_workflow_request(sample())
        self.assertEqual(first["reason"], "initialization_error")
        self.assertEqual(second["reason"], "initialization_backoff")
        self.factory.assert_called_once()
        with patch.object(module.time, "monotonic", return_value=131):
            self.assertEqual(module.observe_workflow_request(sample())["status"], "ready")
        self.assertEqual(self.factory.call_count, 2)
        self.assertNotIn("SECRET_PATH", str(module._logger.mock_calls))

    def test_busy_initialization_is_skipped_and_logging_failure_is_contained(self):
        module._initializing.acquire()
        module._logger.log.side_effect = RuntimeError("logging failure")
        self.assertEqual(module.observe_workflow_request(sample())["reason"], "initialization_busy")
        self.factory.assert_not_called()

    def test_non_live_counter_is_rejected(self):
        with self.assertRaises(ValueError):
            module.RuleMonitor(Rules(), Mock(mode="demo"))

    def test_factory_requires_configuration_without_loading_dependencies(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            # Call original factory, captured before replacing it in setUp.
            BUILD_MONITOR()

    def test_factory_pins_url_overrides_and_preserves_counter_defaults(self):
        client = Mock()
        client.connection_pool.connection_kwargs = {"socket_timeout": 99, "socket_connect_timeout": 88}
        redis = types.ModuleType("redis")
        redis.Redis = Mock()
        redis.Redis.from_url.return_value = client
        backoff = types.ModuleType("redis.backoff")
        backoff.NoBackoff = Mock()
        retry = types.ModuleType("redis.retry")
        retry.Retry = Mock()
        config = types.ModuleType("util.root_config")
        config.REDIS_URL = "redis://private.invalid/0?socket_timeout=99"
        csv = types.ModuleType(package.__name__ + ".csv_config_source")
        csv.CsvRuleConfigSource = Mock(return_value=Rules())
        counter = types.ModuleType(package.__name__ + ".counter")
        counter.RedisRuleCounter = Mock(return_value=self.counter)
        modules = {item.__name__: item for item in (redis, backoff, retry, config, csv, counter)}
        with (
            patch.dict(sys.modules, modules),
            patch.dict(os.environ, {"RULE_REFINE_CSV_PATH": "/fake.csv", "RULE_REFINE_NAMESPACE": "test"}),
        ):
            result = BUILD_MONITOR()
        self.assertIsInstance(result, module.RuleMonitor)
        counter.RedisRuleCounter.assert_called_once_with(client, mode="live", namespace="test", transaction_retries=3)
        self.assertEqual(client.connection_pool.max_connections, 16)
        self.assertEqual(client.connection_pool.connection_kwargs["socket_timeout"], 0.5)
        self.assertEqual(client.connection_pool.connection_kwargs["socket_connect_timeout"], 0.5)
        self.assertEqual(client.connection_pool.connection_kwargs["retry_on_error"], [])
        self.assertFalse(client.connection_pool.connection_kwargs["retry_on_timeout"])
        self.assertTrue(all(call.args[1] == 0 for call in retry.Retry.call_args_list))


BUILD_MONITOR = module._build_monitor
if __name__ == "__main__":
    unittest.main(verbosity=2)
