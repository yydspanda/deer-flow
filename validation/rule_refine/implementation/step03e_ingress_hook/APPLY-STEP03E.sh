bash <<'SOC_STEP03E'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYINSTALL'
import hashlib
import os
import stat
import tempfile
from pathlib import Path

if os.environ.get("RULE_REFINE_MONITOR_ENABLED", "0") != "0":
    raise SystemExit("停止：本步只安装默认关闭的监测；当前终端的监测开关已设置，请贴回该提示。")

requirements = {
    "my_workflows/zeus/rule_refine/counter.py": "2b05b81ee7f28034989b4866f5eed5dc2dc5de8a010b9152792d9715e02b95c2",
    "my_workflows/zeus/rule_refine/csv_config_source.py": "46c9e527b214160e5ba92d20a188ac092a18ebbf164839cbf42fd8872b30d807",
    "my_workflows/zeus/rule_refine/ingress.py": "3a7eac234cdb2bd24d2bbc2beb5b52a9ee26fa3199dd72e9b0f301679cb7889a",
    "my_workflows/zeus/rule_refine/rule_config_source.py": "dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941",
}
for name, expected in requirements.items():
    p = Path(name)
    if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
        raise SystemExit("停止：前一步文件版本不匹配，未修改：" + name)

updates = [
    ('my_workflows/zeus/rule_refine/runtime_monitor.py', None, 'd559868c3227f85e03f0a2e3ae978716ab7a84de89ccd945283fc8c4a845e948', r'''"""可选接收侧监测；默认关闭，统计故障不改变原任务提交结果。"""

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
'''),
    ('test/rule_refine/test_runtime_monitor.py', None, '61704010773d075df60101af75f93a9beb33eb2bca0039a330615409e46e58e4', r'''"""入口监测隔离测试；无网络、数据库、Celery 或模型调用。"""

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
'''),
    ('test/rule_refine/test_workflow_monitor_hook.py', None, '491e7e7ed7b50b8d26f6ece45b55ff8a2250421ebfe0dca805c3a2ebaefce644', r'''"""执行真实路由函数的隔离测试；业务依赖为替身，不启动应用或模型。"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class HttpError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class WorkflowHookTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.result = types.SimpleNamespace(id="test-task-id")
        self.create = Mock(side_effect=lambda _: self.calls.append("create") or self.result)
        self.observe = Mock(side_effect=lambda _: self.calls.append("observe"))
        self.logger = Mock()
        module = types.ModuleType("my_workflows.zeus.rule_refine.runtime_monitor")
        module.observe_workflow_request = self.observe
        self.modules = patch.dict(sys.modules, {module.__name__: module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.path = Path(__file__).resolve().parents[2] / "service/routers/workflows.py"
        tree = ast.parse(self.path.read_text(encoding="utf-8"))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "flow_task_router"]
        self.assertEqual(len(functions), 1)
        self.namespace = {
            "WorkFlowTaskInput": object,
            "schemas": types.SimpleNamespace(WorkflowTaskCreate=types.SimpleNamespace),
            "crud": types.SimpleNamespace(workflow_task=types.SimpleNamespace(create_work_flow_task=self.create)),
            "logger": self.logger,
            "HTTPException": HttpError,
            "to_task_out": lambda task: {"task_id": task.id},
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(self.path), "exec"), self.namespace)
        self.route = self.namespace["flow_task_router"]
        self.request = types.SimpleNamespace(
            app_code="zeus",
            flow_id="alert_agent",
            session_id="test-session",
            alert_id="test-alert",
            alert_data={"alert": {"alertId": "test-alert", "hitLog": []}},
        )

    def test_monitor_runs_before_business_and_does_not_change_return(self):
        before = json.dumps(vars(self.request), sort_keys=True)
        self.assertEqual(self.route(self.request), {"task_id": "test-task-id"})
        self.assertEqual(self.calls, ["observe", "create"])
        self.observe.assert_called_once_with(self.request)
        task = self.create.call_args.args[0]
        self.assertEqual(task.status, "created")
        self.assertEqual(task.alert_data, self.request.alert_data)
        self.assertEqual(before, json.dumps(vars(self.request), sort_keys=True))

    def test_monitor_exception_does_not_fail_business_or_log_payload(self):
        self.observe.side_effect = RuntimeError("private test detail")
        self.assertEqual(self.route(self.request), {"task_id": "test-task-id"})
        self.create.assert_called_once()
        self.assertNotIn("private test detail", str(self.logger.mock_calls))
        self.logger.warning.assert_called_once_with("rule_refine_ingress_unavailable error_type=%s", "RuntimeError")

    def test_monitor_import_failure_keeps_business_available(self):
        with patch.dict(sys.modules, {"my_workflows.zeus.rule_refine.runtime_monitor": None}):
            self.assertEqual(self.route(self.request), {"task_id": "test-task-id"})
        self.create.assert_called_once()
        self.observe.assert_not_called()

    def test_monitor_and_monitor_logging_failure_keep_business_available(self):
        self.observe.side_effect = RuntimeError("monitor failure")
        self.logger.warning.side_effect = OSError("logging failure")
        self.assertEqual(self.route(self.request), {"task_id": "test-task-id"})
        self.create.assert_called_once()

    def test_failed_task_creation_does_not_undo_received_observation(self):
        self.create.side_effect = RuntimeError("business failure")
        with self.assertRaises(HttpError) as caught:
            self.route(self.request)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.detail, "任务创建错误：business failure")
        self.observe.assert_called_once_with(self.request)

    def test_monitor_failure_does_not_mask_task_creation_failure(self):
        self.observe.side_effect = RuntimeError("monitor failure")
        self.create.side_effect = ValueError("business failure")
        with self.assertRaises(HttpError) as caught:
            self.route(self.request)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.detail, "任务创建错误：business failure")

    def test_invalid_task_schema_fails_before_monitor_and_dispatch(self):
        self.namespace["schemas"].WorkflowTaskCreate = Mock(side_effect=ValueError("invalid task"))
        with self.assertRaises(ValueError):
            self.route(self.request)
        self.observe.assert_not_called()
        self.create.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''),
 ]
route = Path("service/routers/workflows.py")
if not route.is_file() or route.is_symlink():
    raise SystemExit("停止：未找到预期的路由文件。")
raw = route.read_bytes()
digest = hashlib.sha256(raw).hexdigest()
old_route_hash = 'a0a3284aae432eb7a83f5503e739b4ec7792bcd7d83ccab4386c40893577747f'
new_route_hash = '5f0a814d49399e39e93387bfa2d5d3ed5655da59ab9dd5ee89e6108cb221a502'
if digest == old_route_hash:
    old = '                                           status="created")\n    try:\n'
    new = r'''                                           status="created")
    # Optional rule-volume observation; it does not depend on model/task success.
    try:
        from my_workflows.zeus.rule_refine.runtime_monitor import observe_workflow_request
        observe_workflow_request(flow_task_msg)
    except Exception as monitor_error:
        try:
            logger.warning("rule_refine_ingress_unavailable error_type=%s", type(monitor_error).__name__)
        except Exception:
            pass
    try:
'''
    text = raw.decode("utf-8")
    if text.count(old) != 1:
        raise SystemExit("停止：路由插入位置不匹配。")
    text = text.replace(old, new, 1)
elif digest == new_route_hash:
    text = raw.decode("utf-8")
else:
    raise SystemExit("停止：路由已有其他改动，未覆盖：" + str(route))
updates.append((str(route), old_route_hash, new_route_hash, text))
prepared = []
for name, old_hash, new_hash, text in updates:
    target = Path(name)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SystemExit("停止：目标不是普通文件：" + name)
    updated = text.encode("utf-8")
    if hashlib.sha256(updated).hexdigest() != new_hash:
        raise SystemExit("停止：复制内容校验失败，未开始修改：" + name)
    compile(updated, name, "exec")
    original = target.read_bytes() if target.exists() else None
    if original is not None:
        found = hashlib.sha256(original).hexdigest()
        if found == new_hash:
            print("内容一致：" + name)
            continue
        if old_hash is None or found != old_hash:
            raise SystemExit("停止：文件已有其他内容，未覆盖：" + name)
    elif old_hash is not None:
        raise SystemExit("停止：缺少原文件：" + name)
    backup = target.with_name(target.name + ".before-step03e")
    if original is not None and (backup.is_symlink() or (backup.exists() and backup.read_bytes() != original)):
        raise SystemExit("停止：旧文件副本不匹配：" + str(backup))
    prepared.append((target, backup, original, updated))
for target, backup, original, updated in prepared:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    if original is not None and not backup.exists():
        with backup.open("xb") as stream:
            stream.write(original)
    fd, temporary = tempfile.mkstemp(prefix=".rule-step03e-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("已更新：" + str(target))
PYINSTALL
.venv/bin/python test/rule_refine/test_runtime_monitor.py
.venv/bin/python test/rule_refine/test_workflow_monitor_hook.py
.venv/bin/python - <<'PYSUMMARY'
import json
print(json.dumps({
    "status": "passed",
    "hook_installed": True,
    "monitor_enabled": False,
    "rule_provider_when_enabled": "csv_snapshot",
    "real_traffic_tested": False,
    "model_calls": 0,
    "database_calls": 0,
    "redis_calls": 0,
}, ensure_ascii=False, indent=2))
PYSUMMARY
SOC_STEP03E
