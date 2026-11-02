"""Exercise the pasted control script with a real controlling PTY, without services."""

import errno
import json
import os
from pathlib import Path
import pty
import select
import signal
import stat
import sys
import tempfile
import time
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "CONTROL-STEP06H.sh"


class TerminalControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.project = self.home / "sec_know_model"
        python = self.project / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        files = {
            "config/model_limits.py": "def get_model_policy(model):\n    return object()\n",
            "my_workflows/zeus/rule_refine/csv_config_source.py": (
                "class CsvRuleConfigSource:\n"
                "    def __init__(self, path): pass\n"
                "    def list_rules(self): return [{}] * 911\n"
            ),
        }
        settings = HERE.parent / "step03g_persistent_settings/payload/my_workflows/zeus/rule_refine/local_settings.py"
        files["my_workflows/zeus/rule_refine/local_settings.py"] = settings.read_text(encoding="utf-8")
        for name, text in files.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (self.project / "config/rule_refine.local.json").write_text(
            json.dumps({"enabled": True, "csv_path": "fixture.csv", "namespace": "terminal-fixture"}),
            encoding="utf-8",
        )
        self.path = self.project / "config/rule_refine.analysis.local.json"
        self.settings = {"enabled": False, "model": "DeepSeek_V32", "state_dir": "~/fixture-results"}
        self.path.write_text(json.dumps(self.settings), encoding="utf-8")

    def interact(self, response):
        pid, descriptor = pty.fork()
        if pid == 0:
            env = {"HOME": str(self.home), "PATH": os.defpath, "env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}
            os.execve("/bin/bash", ["bash", str(SCRIPT)], env)
        output, sent, status = bytearray(), False, None
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([descriptor], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(descriptor, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        chunk = b""
                    output.extend(chunk)
                if not sent and "其他输入取消：".encode("utf-8") in output:
                    os.write(descriptor, b"\x04" if response is None else response.encode("utf-8") + b"\n")
                    sent = True
                done, current = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = current
                    break
            self.assertIsNotNone(status, "Control script did not exit within ten seconds")
        finally:
            os.close(descriptor)
            if status is None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), output.decode("utf-8", errors="replace"), sent

    def test_enable_from_pasted_script_reads_real_terminal(self):
        code, output, sent = self.interact("ENABLE")
        self.assertEqual(code, 0, output)
        self.assertTrue(sent, output)
        self.assertEqual(json.loads(self.path.read_text()), {**self.settings, "enabled": True})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_disable_works_even_if_csv_configuration_is_missing(self):
        self.path.write_text(json.dumps({**self.settings, "enabled": True}), encoding="utf-8")
        (self.project / "config/rule_refine.local.json").unlink()
        code, output, sent = self.interact("DISABLE")
        self.assertEqual(code, 0, output)
        self.assertTrue(sent, output)
        self.assertEqual(json.loads(self.path.read_text()), self.settings)

    def test_cancel_preserves_original_configuration(self):
        original = self.path.read_bytes()
        _, output, sent = self.interact("CANCEL")
        self.assertTrue(sent, output)
        self.assertIn("已取消", output)
        self.assertEqual(self.path.read_bytes(), original)

    def test_terminal_eof_preserves_original_configuration(self):
        original = self.path.read_bytes()
        _, output, sent = self.interact(None)
        self.assertTrue(sent, output)
        self.assertIn("已取消", output)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
