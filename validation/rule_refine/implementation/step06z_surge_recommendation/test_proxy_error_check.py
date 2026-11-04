"""Diagnose existing logs without application imports, requests or secret output."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import proxy_error_check as diagnostic


class ProxyErrorTests(unittest.TestCase):
    def test_error_excerpt_preserves_reason_but_drops_auth_and_echoed_prompt(self):
        line = (
            'BadRequestError: context length exceeded, api_key="test-secret" '
            "Authorization: Bearer sk-example-secret https://private.invalid/?key=abc "
            '"messages": [{"content":"raw-alert-contents"}]'
        )
        cleaned = diagnostic.clean(line)
        self.assertIn("context length exceeded", cleaned)
        for secret in (
            "test-secret",
            "sk-example-secret",
            "private.invalid",
            "raw-alert-contents",
        ):
            self.assertNotIn(secret, cleaned)

    def test_only_error_near_saved_run_is_selected_from_timestamped_logs(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            report = root / "report"
            (report / "generate").mkdir(parents=True)
            (report / "generate/request-started.json").write_text("{}")
            (report / "generate/result.json").write_text("{}")
            logs = root / "logs"
            logs.mkdir()
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            (logs / "litellm.log").write_text(
                "[2001-01-01 01:01:01] BadRequestError: unrelated-old-error\n"
                f"[{stamp}] BadRequestError: maximum context length exceeded\n",
                encoding="utf-8",
            )
            result = diagnostic.inspect(root, report, logs)
            self.assertEqual(result["status"], "error_lines_found")
            self.assertEqual(result["files"][0]["matching_lines_in_tail"], 1)
            self.assertNotIn("unrelated-old-error", json.dumps(result))
            self.assertEqual(result["model_calls"], 0)

    def test_missing_logs_does_not_invent_failure_reason_or_make_files(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            result = diagnostic.inspect(root, root / "report", root / "missing")
            self.assertEqual(result["status"], "no_error_detail_found")
            self.assertEqual(result["files"], [])
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
