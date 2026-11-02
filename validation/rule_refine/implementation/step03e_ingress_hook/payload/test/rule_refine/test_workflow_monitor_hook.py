"""执行真实路由函数的隔离测试；业务依赖为替身，不启动应用或模型。"""

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
