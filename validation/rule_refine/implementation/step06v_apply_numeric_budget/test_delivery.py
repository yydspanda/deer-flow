"""Exercise the exact copyable command against a synthetic project tree."""

import ast
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("numeric_budget_delivery_builder", HERE / "build_command.py")
installer = load("numeric_budget_delivery_installer", HERE / "install.py")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="numeric-install-delivery-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.project = self.home / "sec_know_model"
        self.project.mkdir()
        self.evidence = (
            self.home / "sec-rule-refine-data/historical-numeric-replay-188ctea2"
        )
        self.evidence.mkdir(parents=True)
        (self.evidence / "summary.json").write_text(
            json.dumps({**installer.EVIDENCE_EXPECTED, **installer.EVIDENCE_HASHES}),
            encoding="utf-8",
        )
        for relative, previous in {
            **builder.OLD_FILES,
            **builder.DEPENDENCIES,
        }.items():
            if previous is None:
                continue
            target = self.project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((builder.IMPL / previous / relative).read_bytes())
        interpreter = self.project / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
            encoding="utf-8",
        )
        interpreter.chmod(0o700)
        untouched = self.project / "nas/data/tasks.sqlite"
        untouched.parent.mkdir(parents=True)
        untouched.write_bytes(b"not-a-real-database")
        self.command = builder.build()

    def invoke(self):
        environment = dict(os.environ)
        environment["HOME"] = str(self.home)
        return subprocess.run(
            ["bash"],
            input=self.command,
            text=True,
            capture_output=True,
            env=environment,
            timeout=100,
            check=False,
        )

    def test_copyable_command_installs_real_payload_and_is_idempotent(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout[result.stdout.index("{") :])
        self.assertEqual(report["status"], "installed")
        self.assertEqual(report["tests_passed"], 83)
        self.assertEqual(report["files_changed"], 9)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["redis_calls"], 0)
        self.assertEqual(report["database_calls"], 0)
        self.assertFalse(report["automatic_apply"])
        self.assertTrue(report["restart_required"])
        packet = builder.contents()
        for relative, entry in packet["files"].items():
            self.assertEqual(
                installer.digest((self.project / relative).read_bytes()),
                entry["new_sha256"],
            )
        self.assertEqual(
            (self.project / "nas/data/tasks.sqlite").read_bytes(),
            b"not-a-real-database",
        )
        self.assertFalse((Path(report["backup_dir"]) / "before/nas").exists())
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        repeated = json.loads(second.stdout[second.stdout.index("{") :])
        self.assertEqual(repeated["status"], "already_installed")
        self.assertEqual(repeated["files_changed"], 0)
        self.assertNotIn("backup_dir", repeated)

    def test_unknown_local_edit_stops_before_install(self):
        path = self.project / (builder.MODULE + "analysis.py")
        raw = path.read_bytes() + b"\n# existing local edit\n"
        path.write_bytes(raw)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(result.stdout[result.stdout.index("{") :])
        self.assertEqual(report["reason"], "unknown_project_file_version")
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(
            (self.project / (builder.MODULE + "condition_values.py")).exists()
        )

    def test_packaged_sources_support_python311_and_record_actual_pins(self):
        packet = builder.contents()
        for entry in packet["files"].values():
            ast.parse(entry["content"], feature_version=(3, 11))
        ast.parse(
            (HERE / "install.py").read_text(encoding="utf-8"), feature_version=(3, 11)
        )
        pins = json.loads((HERE / "current-pins.json").read_text(encoding="utf-8"))
        for relative, entry in packet["files"].items():
            if relative.startswith(builder.MODULE):
                self.assertEqual(pins[relative], entry["new_sha256"])
        syntax = subprocess.run(
            ["bash", "-n"], input=self.command, text=True, capture_output=True
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
