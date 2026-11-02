"""Offline tests for a read-only readiness inspection; no actual HTTP requests."""

import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("readiness_check", HERE / "check.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class Response:
    def __init__(self, status, body):
        self.status, self.body, self.read_limits = status, body, []

    def read(self, limit):
        self.read_limits.append(limit)
        return self.body[:limit]


class Connection:
    def __init__(self, response):
        self.response, self.requests, self.closed = response, [], False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def inspect_http(self, status, body):
        response = Response(status, body)
        connection = Connection(response)
        factory = Mock(return_value=connection)
        result = CHECK.api_schema(factory)
        factory.assert_called_once_with("127.0.0.1", 8090, timeout=3)
        self.assertEqual(
            connection.requests,
            [(("GET", "/openapi.json"), {"headers": {"Accept": "application/json"}})],
        )
        self.assertTrue(connection.closed)
        return result, response

    def test_schema_get_only_reports_operation_without_response_contents(self):
        body = json.dumps(
            {"paths": {"/workflow/task": {"post": {"description": "secret-body"}}}}
        ).encode()
        result, _ = self.inspect_http(200, body)
        self.assertEqual(result["status"], "schema_available")
        self.assertTrue(result["workflow_task_documented"])
        self.assertFalse(result["business_post_sent"])
        self.assertFalse(result["authentication_tested"])
        self.assertNotIn("secret-body", json.dumps(result))

    def test_redirect_and_auth_errors_do_not_follow_or_read_bodies(self):
        for status in (301, 302, 307, 401, 403):
            with self.subTest(status=status):
                result, response = self.inspect_http(status, b"secret-response")
                self.assertEqual(result["status"], "schema_not_available")
                self.assertEqual(response.read_limits, [])
                self.assertNotIn("secret-response", json.dumps(result))

    def test_response_size_is_bounded_and_invalid_json_is_not_echoed(self):
        limit = 4 * 1024 * 1024
        result, response = self.inspect_http(200, b"x" * (limit + 2))
        self.assertEqual(result["status"], "schema_too_large")
        self.assertEqual(response.read_limits, [limit + 1])
        result, _ = self.inspect_http(200, b"secret-invalid-json")
        self.assertEqual(result["status"], "unavailable_or_invalid_response")
        self.assertNotIn("secret-invalid-json", json.dumps(result))

    def test_non_local_returns_before_api_or_filesystem_queries(self):
        api = Mock(side_effect=AssertionError("must not query"))
        result = CHECK.check(self.root / "nonexistent", {"env_profile": "STG"}, api)
        self.assertEqual(result["status"], "skipped_non_local")
        api.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_database_is_not_created(self):
        result = CHECK.task_materials(self.root)
        self.assertEqual(result, {"status": "database_missing_not_created"})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_sqlite_counts_only_target_rows_and_preserves_database(self):
        path = self.root / "nas/data/sec_know_model.sqlite"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as database:
            database.execute(
                "CREATE TABLE uas_app_workflow_task_status (id TEXT, app_code TEXT, flow_id TEXT, created_date TEXT, alert_data TEXT)"
            )
            database.executemany(
                "INSERT INTO uas_app_workflow_task_status VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        "1",
                        "zeus",
                        "alert_agent",
                        "2026-10-29",
                        '{"private-payload":"do-not-print"}',
                    ),
                    ("2", "zeus", "alert_agent", "2026-10-29", "null"),
                    ("3", "zeus", "alert_agent", "2026-10-29", None),
                    ("4", "other", "alert_agent", "2026-10-29", "secret-other"),
                ],
            )
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = CHECK.task_materials(self.root)
        self.assertEqual(result["task_rows"], 3)
        self.assertEqual(result["rows_with_non_null_alert_data"], 1)
        self.assertFalse(result["payload_contents_read"])
        self.assertFalse(result["payload_validity_verified"])
        self.assertNotIn("private-payload", json.dumps(result))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_source_is_parsed_not_imported_and_hook_order_is_checked(self):
        path = self.root / "service/routers/workflows.py"
        path.parent.mkdir(parents=True)
        path.write_text(
            'raise RuntimeError("must not execute source")\ndef flow_task_router(msg):\n    observe_workflow_request(msg)\n    return crud.workflow_task.create_work_flow_task(msg)\n',
            encoding="utf-8",
        )
        result = CHECK.hook_on_disk(self.root)
        self.assertTrue(result["hook_before_task_creation"])
        self.assertFalse(result["running_process_code_verified"])
        path.write_text(
            "def flow_task_router(msg):\n    crud.workflow_task.create_work_flow_task(msg)\n    observe_workflow_request(msg)\n",
            encoding="utf-8",
        )
        self.assertFalse(CHECK.hook_on_disk(self.root)["hook_before_task_creation"])

    def test_config_and_environment_secrets_are_never_exposed(self):
        config = self.root / "config"
        config.mkdir()
        for name in ("rule_refine.local.json", "rule_refine.analysis.local.json"):
            (config / name).write_text(
                json.dumps({"enabled": True, "private_setting": "secret-config-value"}),
                encoding="utf-8",
            )
        result = CHECK.check(
            self.root,
            {"ENV_PROFILE": "LOCAL", "RULE_REFINE_NAMESPACE": "secret-env-value"},
            query_api=lambda: {"status": "schema_available"},
        )
        output = json.dumps(result)
        self.assertNotIn("secret-config-value", output)
        self.assertNotIn("secret-env-value", output)
        self.assertTrue(result["monitor_file"]["enabled_on_disk"])
        self.assertTrue(result["shell_monitor_override_present"])
        self.assertFalse(result["running_process_settings_verified"])
        self.assertEqual(result["real_ingress_acceptance"], "not_tested")


if __name__ == "__main__":
    unittest.main(verbosity=2)
