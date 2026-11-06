"""Exercise the copyable installer with isolated legacy source and no services."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PAYLOAD = HERE.parent / "step10_topic_filter/payload"
COMMAND = HERE / "SET-POLL-120S.sh"
CONFIG = "config/celery_config.py"
TEST = "test/rule_refine/test_topic_beat.py"


class PollIntervalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project"
        for name in (CONFIG, TEST):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((PAYLOAD / name).read_bytes())
        source = (
            COMMAND.read_text(encoding="utf-8")
            .split("<<'PY_TOPIC_POLL_120S'\n", 1)[1]
            .split("\nPY_TOPIC_POLL_120S\n", 1)[0]
        )
        self.ns = {"__name__": "offline_installer"}
        exec(compile(source, str(COMMAND), "exec"), self.ns)
        self.ns["ROOT"] = self.root
        env = patch.dict(
            os.environ, {"SEC_RULE_REFINE_DATA_ROOT": str(self.root.parent / "backups")}
        )
        env.start()
        self.addCleanup(env.stop)

    def test_install_preserves_unrelated_bytes_and_is_idempotent(self):
        path = self.root / CONFIG
        original = path.read_bytes()
        prefix = b"# unrelated operator adjustment\nOTHER_JOB_INTERVAL = 30.0\n"
        path.write_bytes(prefix + original)
        result = self.ns["install"]()
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["files_changed"], 2)
        expected = prefix + original.replace(b'"schedule": 30.0', b'"schedule": 120.0')
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(
            (Path(result["backup_dir"]) / CONFIG).read_bytes(), prefix + original
        )
        self.assertEqual(self.ns["install"]()["status"], "already_installed")

    def test_unknown_interval_and_registration_fail_before_writes(self):
        path = self.root / CONFIG
        original = path.read_bytes()
        for altered in (
            original.replace(b'"schedule": 30.0', b'"schedule": 60.0'),
            original.replace(b'"expires": 25', b'"expires": 30'),
        ):
            with self.subTest(altered=altered != original):
                path.write_bytes(altered)
                with self.assertRaises(ValueError):
                    self.ns["install"]()
                self.assertEqual(path.read_bytes(), altered)
                self.assertFalse((self.root.parent / "backups").exists())

    def test_failed_regression_leaves_source_unchanged(self):
        path = self.root / TEST
        path.write_bytes(
            path.read_bytes().replace(
                b"self.assertEqual(calls, [])", b"self.fail('offline sentinel')"
            )
        )
        original = (self.root / CONFIG).read_bytes()
        with self.assertRaisesRegex(ValueError, "offline_regression_failed"):
            self.ns["install"]()
        self.assertEqual((self.root / CONFIG).read_bytes(), original)
        self.assertFalse((self.root.parent / "backups").exists())

    def test_second_write_failure_rolls_back_first(self):
        original = (self.root / CONFIG).read_bytes()
        real_replace = self.ns["replace"]

        def fail_test_file(path, raw, mode):
            if path == self.root / TEST:
                raise OSError("simulated write failure")
            real_replace(path, raw, mode)

        self.ns["replace"] = fail_test_file
        with self.assertRaises(OSError):
            self.ns["install"]()
        self.assertEqual((self.root / CONFIG).read_bytes(), original)

    def test_exact_shell_and_repeat(self):
        interpreter = self.root / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        env = dict(os.environ, SEC_KNOW_MODEL_ROOT=str(self.root))
        for expected in ('"status": "installed"', '"status": "already_installed"'):
            result = subprocess.run(
                ["bash", str(COMMAND)],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
