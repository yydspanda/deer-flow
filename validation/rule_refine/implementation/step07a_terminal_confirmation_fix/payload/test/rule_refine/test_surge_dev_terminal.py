"""Real non-seekable terminal regression; model execution is replaced locally."""

import importlib.util
import io
import os
import pty
import select
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "surge_live_terminal_test", Path(__file__).with_name("check_surge_dev_live.py")
)
LIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIVE)


class TerminalTests(unittest.TestCase):
    def prepared(self):
        return {
            "preview": {"selection": {"current_count": 3, "history_count": 20}},
            "analysis": {"model": "synthetic-model"},
            "case": {"current_samples": [None] * 6, "history_samples": [None] * 20},
            "budget": SimpleNamespace(audit=lambda: {"model_output_token_limit": None}),
        }

    def run_terminal(self, answer):
        master, slave = pty.openpty()
        try:
            terminal_path = os.ttyname(slave)
            original_open = open

            def open_terminal(path, *args, **kwargs):
                if str(path) == "/dev/tty":
                    kwargs["opener"] = lambda name, flags: os.open(
                        name, flags | os.O_NOCTTY
                    )
                return original_open(
                    terminal_path if str(path) == "/dev/tty" else path,
                    *args,
                    **kwargs,
                )

            # Canonical terminal input, independent from redirected/heredoc stdin.
            os.write(master, answer.encode("utf-8"))
            stdin = io.StringIO("heredoc script body; never an approval\n")
            output = io.StringIO()
            with (
                patch.object(LIVE, "prepare", return_value=self.prepared()),
                patch.object(
                    LIVE, "execute", return_value={"status": "received"}
                ) as execute,
                patch("builtins.open", side_effect=open_terminal),
                patch("sys.stdin", stdin),
                patch("sys.stdout", output),
            ):
                result = LIVE.main(["--live"])
            self.assertEqual(stdin.tell(), 0)
            self.assertTrue(select.select([master], [], [], 2)[0])
            prompt = os.read(master, 65536).decode("utf-8")
            self.assertIn("确认调用请输入 LIVE", prompt)
            return result, output.getvalue(), execute.call_count
        finally:
            os.close(master)
            os.close(slave)

    def test_real_terminal_live_after_redirected_stdin_executes_once(self):
        status, output, calls = self.run_terminal("LIVE\n")
        self.assertEqual(status, 0, output)
        self.assertEqual(calls, 1)
        self.assertIn('"status": "received"', output)

    def test_real_terminal_decline_never_executes(self):
        status, output, calls = self.run_terminal("NO\n")
        self.assertEqual(status, 0, output)
        self.assertEqual(calls, 0)

    def test_real_terminal_blank_never_executes(self):
        status, output, calls = self.run_terminal("\n")
        self.assertEqual(status, 0, output)
        self.assertEqual(calls, 0)

    def test_real_terminal_eof_never_executes(self):
        status, output, calls = self.run_terminal("\x04")
        self.assertEqual(status, 0, output)
        self.assertEqual(calls, 0)

    def test_missing_controlling_terminal_does_not_consume_stdin_approval(self):
        stdin, output = io.StringIO("LIVE\n"), io.StringIO()
        with (
            patch.object(LIVE, "prepare", return_value=self.prepared()),
            patch.object(LIVE, "execute") as execute,
            patch("builtins.open", side_effect=OSError("no controlling terminal")),
            patch("sys.stdin", stdin),
            patch("sys.stdout", output),
        ):
            status = LIVE.main(["--live"])
        self.assertEqual(status, 1)
        self.assertEqual(stdin.tell(), 0)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
