"""Validate the complete copy/paste artifact without contacting any service."""

import base64
import contextlib
import http.client
import io
import json
import os
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import build_command


class Response:
    status = 200

    def read(self, limit):
        return b'{"paths":{"/workflow/task":{"post":{}}}}'


class Connection:
    calls = []

    def __init__(self, host, port, timeout):
        self.calls.append(("connect", host, port, timeout))

    def request(self, method, path, headers):
        self.calls.append((method, path))

    def getresponse(self):
        return Response()

    def close(self):
        pass


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.command = build_command.build()
        cls.runner = cls.command.split("<<'PY_REAL_INPUTS'\n", 1)[1].rsplit(
            "\nPY_REAL_INPUTS", 1
        )[0]

    def test_shell_syntax_and_embedded_files_exactly_match(self):
        subprocess.run(
            ["bash", "-n"],
            input=self.command,
            text=True,
            check=True,
            capture_output=True,
        )
        prefix = self.runner.split("if any(os.environ", 1)[0]
        namespace = {}
        exec(compile(prefix, "command-prefix", "exec"), namespace)
        files = json.loads(zlib.decompress(base64.b64decode(namespace["PAYLOAD"])))
        self.assertEqual(set(files), set(build_command.FILES))
        for name, source in files.items():
            self.assertEqual(
                source, (build_command.HERE / name).read_text(encoding="utf-8")
            )

    def test_full_local_runner_missing_materials_and_no_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Connection.calls = []
            output = io.StringIO()
            with (
                patch.dict(
                    os.environ, {"HOME": str(root), "env_profile": "LOCAL"}, clear=True
                ),
                patch("pathlib.Path.cwd", return_value=root),
                patch.object(http.client, "HTTPConnection", Connection),
                contextlib.redirect_stdout(output),
            ):
                exec(compile(self.runner, "complete-command", "exec"), {})
            result = json.loads(output.getvalue().split("\n", 1)[1])
            self.assertEqual(result["status"], "inspection_complete")
            self.assertTrue(result["ingress"]["api"]["workflow_task_documented"])
            self.assertEqual(result["business_alerts_submitted"], 0)
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["celery_tasks_dispatched"], 0)
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(
                Connection.calls,
                [("connect", "127.0.0.1", 8090, 3), ("GET", "/openapi.json")],
            )

    def test_nonlocal_exits_before_loading_or_querying(self):
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"env_profile": "STG"}, clear=True),
            patch.object(
                http.client, "HTTPConnection", side_effect=AssertionError("network")
            ),
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as caught,
        ):
            exec(compile(self.runner, "nonlocal-command", "exec"), {})
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "skipped_non_local")

    def test_corrupt_bundle_is_rejected_before_queries(self):
        runner = self.runner.replace('EXPECTED = "', 'EXPECTED = "corrupt-', 1)
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"env_profile": "LOCAL"}, clear=True),
            patch.object(
                http.client, "HTTPConnection", side_effect=AssertionError("network")
            ),
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as caught,
        ):
            exec(compile(runner, "invalid-command", "exec"), {})
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(
            json.loads(output.getvalue())["status"], "tool_preparation_failed"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
