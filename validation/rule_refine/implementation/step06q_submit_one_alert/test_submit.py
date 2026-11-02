import hashlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import submit as tool
import build_command

TASK = "00000000-0000-4000-8000-000000000001"


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "sec_know_model"
        self.root.mkdir()
        self.parent = self.home / "sec-rule-refine-data"
        self.directory = self.parent / tool.PREPARED_NAME
        self.directory.mkdir(parents=True)
        self.payload = {
            "app_code": "zeus",
            "flow_id": "alert_agent",
            "alert_id": "PRIVATE-ID",
            "alert_data": {
                "alert": {"alertId": "PRIVATE-ID", "content": "PRIVATE-CONTENT"}
            },
        }
        self.raw = tool.encode(self.payload)
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.patcher = patch.object(tool, "REQUEST_SHA", self.sha)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        (self.directory / "request.json").write_bytes(self.raw)
        (self.directory / "selection.json").write_text(
            json.dumps({"request_sha256": self.sha}), encoding="utf-8"
        )
        self.results = self.parent / ("ingress-one-submit-" + self.sha[:16])
        self.database = self.root / "nas/data/sec_know_model.sqlite"
        self.database.parent.mkdir(parents=True)
        with sqlite3.connect(self.database) as db:
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status (id TEXT, app_code TEXT, flow_id TEXT, alert_id TEXT, status TEXT, failed_count INTEGER, alert_data TEXT, agent_response TEXT)"
            )

    def row(self, task=TASK, data=None, status="successed"):
        with sqlite3.connect(self.database) as db:
            db.execute(
                "INSERT INTO uas_app_workflow_task_status VALUES (?,?,?,?,?,?,?,?)",
                (
                    task,
                    "zeus",
                    "alert_agent",
                    "PRIVATE-ID",
                    status,
                    0,
                    json.dumps(self.payload["alert_data"] if data is None else data),
                    "{}",
                ),
            )

    def test_prepared_hash_and_identity_preserve_original_body(self):
        raw, payload = tool.prepared(self.directory)
        self.assertEqual(raw, self.raw)
        self.assertEqual(payload, self.payload)
        (self.directory / "request.json").write_bytes(self.raw + b" ")
        with self.assertRaisesRegex(tool.CheckError, "prepared_request_changed"):
            tool.prepared(self.directory)

    def test_auth_extraction_executes_nothing_and_keeps_values_private(self):
        path = self.root / "nas/config.py"
        path.write_text(
            "raise RuntimeError('MUST NOT EXECUTE')\nif rc.ENV == 'LOCAL':\n    APP_KEY_DICT = {'ZUES': 'SECRET-TOKEN'}\nelse:\n    APP_KEY_DICT = {'ZUES': 'DIFFERENT'}\n",
            encoding="utf-8",
        )
        self.assertEqual(tool.local_token(self.root), "SECRET-TOKEN")
        path.write_text(
            "if rc.ENV == 'LOCAL':\n    APP_KEY_DICT = get_keys()\n", encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            tool.local_token(self.root)

    def test_local_api_process_check_is_not_a_worker_assertion(self):
        commands = []
        responses = iter(
            [
                "123\n123\n",
                f"p123\nfcwd\nn{self.root}\n",
                "python run.py env_profile=LOCAL SECRET=hidden",
            ]
        )

        def run(args):
            commands.append(args)
            return next(responses)

        result = tool.api_process(self.root, run)
        self.assertEqual(result["api_profile"], "LOCAL")
        self.assertFalse(result["callback_target_verified"])
        self.assertNotIn("hidden", json.dumps(result))
        responses = iter(["123", f"n{self.root}\n", "python run.py env_profile=PRD"])
        with self.assertRaisesRegex(tool.CheckError, "api_process_profile_not_local"):
            tool.api_process(self.root, run)

    def test_one_post_exact_body_with_no_redirect_retry_or_result_exposure(self):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(
            {"id": TASK, "result": "PRIVATE-REASONING"}
        ).encode()
        factory = Mock(return_value=connection)
        out = tool.post_once(self.raw, "SECRET-TOKEN", factory)
        factory.assert_called_once_with("127.0.0.1", 8090, timeout=30)
        connection.request.assert_called_once_with(
            "POST",
            "/workflow/task",
            body=self.raw,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer SECRET-TOKEN",
            },
        )
        self.assertEqual(out["task_id"], TASK)
        self.assertNotIn("PRIVATE", json.dumps(out))
        response.status = 302
        connection.reset_mock()
        out = tool.post_once(self.raw, "SECRET-TOKEN", factory)
        self.assertEqual(out["post_outcome"], "unconfirmed")
        self.assertEqual(connection.request.call_count, 1)

    def test_timeout_is_unconfirmed_and_never_retried(self):
        connection = Mock()
        connection.getresponse.side_effect = TimeoutError("SECRET detail")
        out = tool.post_once(self.raw, "SECRET", Mock(return_value=connection))
        self.assertEqual(out["post_outcome"], "unconfirmed")
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertEqual(connection.request.call_count, 1)
        connection.close.assert_called_once()

    def test_exact_task_requires_same_data_and_does_not_mutate_sqlite(self):
        self.results.mkdir()
        tool.save_once(self.results / "attempt.json", {"log_cursor": None})
        tool.save_once(
            self.results / "response.json",
            {"task_id": TASK, "post_outcome": "accepted"},
        )
        self.row(task="different")
        self.row(data={"wrong": True})
        before = self.database.read_bytes()
        out = tool.collect(self.root, self.results, self.payload, polls=1)
        self.assertFalse(out["task"]["completion_verified"])
        self.assertFalse(out["task"]["alert_data_matches_original"])
        self.assertEqual(before, self.database.read_bytes())
        self.assertNotIn("PRIVATE", json.dumps(out))

    def test_monitor_evidence_uses_only_new_matching_log_and_never_raw_contents(self):
        log = self.root / "nas/log/info.log"
        log.parent.mkdir()
        log.write_text("old PRIVATE-CONTENT", encoding="utf-8")
        cursor = tool.log_cursor(self.root)
        identity = json.dumps(
            ["zeus", "alert_agent", "PRIVATE-ID"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        key = "sha256:" + hashlib.sha256(identity.encode()).hexdigest()
        value = {
            "request_key": key,
            "status": "ready",
            "counted_rules": 1,
            "added_rules": 1,
            "new_signals": 0,
            "unexpected_secret": "PRIVATE",
        }
        with log.open("a", encoding="utf-8") as stream:
            stream.write("\nrule_refine_monitor " + json.dumps(value) + "\n")
        out = tool.monitor_receipt(self.root, "PRIVATE-ID", cursor)
        self.assertEqual(out["status"], "matching_log_found")
        self.assertEqual(out["counted_rules"], 1)
        self.assertNotIn("PRIVATE", json.dumps(out))
        log.unlink()
        self.assertEqual(
            tool.monitor_receipt(self.root, "PRIVATE-ID", cursor)["status"],
            "unconfirmed",
        )

    def run_main(self, confirmation, post):
        actual_open = open

        def tty_open(path, *args, **kwargs):
            return (
                io.StringIO(confirmation + "\n")
                if path == "/dev/tty"
                else actual_open(path, *args, **kwargs)
            )

        with (
            patch("pathlib.Path.home", return_value=self.home),
            patch("pathlib.Path.cwd", return_value=self.root),
            patch.dict(os.environ, {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}),
            patch.object(
                tool,
                "preflight",
                return_value=("SECRET-TOKEN", {"api_profile": "LOCAL"}),
            ) as preflight,
            patch.object(tool, "post_once", side_effect=post) as submit,
            patch("builtins.open", side_effect=tty_open),
            redirect_stdout(io.StringIO()) as out,
        ):
            tool.main()
            return out.getvalue(), preflight.call_count, submit.call_count

    def test_cancel_does_not_submit_or_create_attempt_marker(self):
        _, _, calls = self.run_main("NO", lambda *_: self.fail("must not post"))
        self.assertEqual(calls, 0)
        self.assertFalse((self.results / "attempt.json").exists())

    def test_durable_marker_precedes_post_and_reruns_collect_without_confirmation(self):
        def post(raw, token):
            self.assertTrue((self.results / "attempt.json").exists())
            self.assertEqual(raw, self.raw)
            self.row()
            return {"task_id": TASK, "post_outcome": "accepted"}

        out, _, calls = self.run_main("LIVE", post)
        self.assertEqual(calls, 1)
        self.assertNotIn("SECRET-TOKEN", out)
        self.assertNotIn("PRIVATE-ID", out)
        out, checks, calls = self.run_main("NO", lambda *_: self.fail("never repost"))
        self.assertEqual((checks, calls), (0, 0))
        self.assertIn('"completion_verified": true', out)
        self.assertEqual(
            stat.S_IMODE((self.results / "attempt.json").stat().st_mode), 0o600
        )

    def test_interrupted_or_lost_response_never_leads_to_resubmission(self):
        self.results.mkdir()
        tool.save_once(self.results / "attempt.json", {"log_cursor": None})
        out, checks, calls = self.run_main("LIVE", lambda *_: self.fail("never repost"))
        self.assertEqual((checks, calls), (0, 0))
        self.assertIn('"post_outcome": "unconfirmed"', out)

    def test_preexisting_business_row_blocks_new_submission(self):
        self.row()
        with (
            patch.object(tool, "SOURCE_PINS", {}),
            patch.object(tool, "api_process", return_value={}),
            patch.object(tool, "local_token") as token,
        ):
            with self.assertRaisesRegex(
                tool.CheckError, "alert_already_in_local_task_store"
            ):
                tool.preflight(self.root, self.payload)
            token.assert_not_called()

    def test_sql_payload_is_bounded_and_untyped_failure_count_is_not_exposed(self):
        self.row(data={"large": "x" * (2 * 1024 * 1024 + 1)})
        with sqlite3.connect(self.database) as db:
            db.execute(
                "UPDATE uas_app_workflow_task_status SET failed_count='PRIVATE-CORRUPTION'"
            )
        found = tool.rows(self.root, "PRIVATE-ID", TASK)
        self.assertIsNone(found[0][3])
        self.assertEqual(found[0][5], 1)
        self.results.mkdir()
        tool.save_once(self.results / "attempt.json", {"log_cursor": None})
        tool.save_once(
            self.results / "response.json",
            {"task_id": TASK, "post_outcome": "accepted"},
        )
        out = tool.collect(self.root, self.results, self.payload, polls=1)
        self.assertIsNone(out["task"]["failed_count"])
        self.assertNotIn("PRIVATE", json.dumps(out))

    def test_complete_copy_command_stops_non_local_before_reads_or_submission(self):
        command = build_command.build()
        source = command.split("<<'PY_ONE_ALERT'\n", 1)[1].rsplit("\nPY_ONE_ALERT", 1)[
            0
        ]
        output = subprocess.run(
            [sys.executable, "-B", "-c", source],
            env={**os.environ, "env_profile": "PRD", "HOME": str(self.home)},
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(output.returncode, 1)
        self.assertEqual(
            json.loads(output.stdout)["error_code"], "local_shell_required"
        )
        self.assertFalse(self.results.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
