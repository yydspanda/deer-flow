"""Run the full copyable installer against both shipped internal source states."""

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


builder = load("unified_rule_delivery_builder", HERE / "build_command.py")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="unified-delivery-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / "sec_know_model"
        self.root.mkdir()
        self.packet = builder.contents()
        self.command = builder.build()
        interpreter = self.root / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
            encoding="utf-8",
        )
        interpreter.chmod(0o700)

    def prepare(self, optional_v=False):
        old = builder.pins(
            "step06v_apply_numeric_budget" if optional_v else "step06m_apply_parser"
        )
        for relative, expected in old.items():
            if not relative.startswith(builder.MODULE):
                continue
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(builder.find_source(relative, expected).read_bytes())
        for relative in self.packet["files"]:
            if not relative.startswith(builder.TEST):
                continue
            candidates = builder.old_test_sources(relative)
            if not optional_v:
                candidates = [
                    p
                    for p in candidates
                    if p.relative_to(builder.IMPL).parts[0] < "step06n"
                ]
            if candidates:
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(candidates[-1].read_bytes())
        untouched = self.root / "nas/data/tasks.sqlite"
        untouched.parent.mkdir(parents=True)
        untouched.write_bytes(b"opaque synthetic file, not a database")

    def invoke(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        result = subprocess.run(
            ["bash"],
            input=self.command,
            text=True,
            capture_output=True,
            env=env,
            timeout=220,
            check=False,
        )
        return result, json.loads(result.stdout[result.stdout.index("{") :])

    def assert_installed(self):
        result, report = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["status"], "installed")
        self.assertGreater(report["tests_passed"], 100)
        self.assertTrue(report["restart_required"])
        for field in (
            "model_calls",
            "database_calls",
            "redis_calls",
            "business_alerts_submitted",
        ):
            self.assertEqual(report[field], 0)
        for relative, entry in self.packet["files"].items():
            if relative != builder.POLICY:
                self.assertEqual(
                    builder.sha((self.root / relative).read_bytes()),
                    entry["new_sha256"],
                )
        self.assertEqual(
            (self.root / "nas/data/tasks.sqlite").read_bytes(),
            b"opaque synthetic file, not a database",
        )
        self.assertFalse((Path(report["backup_dir"]) / "before/nas").exists())
        return report

    def test_install_from_confirmed_version_and_repeat_without_old_evidence(self):
        self.prepare()
        self.assert_installed()
        result, report = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["status"], "already_installed")
        self.assertEqual(report["files_changed"], 0)

    def test_install_after_optional_v_and_preserve_custom_configuration(self):
        self.prepare(optional_v=True)
        path = self.root / builder.POLICY
        path.parent.mkdir(parents=True)
        raw = b'{"input_kib":256,"output_kib":128,"timeout_seconds":300}\n'
        path.write_bytes(raw)
        report = self.assert_installed()
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(report["limits"]["input_kib"], 256)
        self.assertEqual(report["limits"]["output_kib"], 128)

    def test_unknown_edit_and_bad_policy_fail_before_code_changes(self):
        self.prepare()
        path = self.root / (builder.MODULE + "analysis.py")
        old = path.read_bytes()
        path.write_bytes(old + b"\n# unknown operator edit\n")
        result, report = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["reason"], "unknown_project_file_version")
        self.assertEqual(report["file"], builder.MODULE + "analysis.py")
        self.assertFalse((self.root / (builder.MODULE + "limits.py")).exists())
        path.write_bytes(old)
        config = self.root / builder.POLICY
        config.parent.mkdir(parents=True)
        config.write_bytes(b'{"input_kib":"unlimited"}')
        result, report = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["reason"], "limits_configuration_invalid")
        self.assertEqual(path.read_bytes(), old)

    def test_python311_syntax_and_shell_entry(self):
        for relative, entry in self.packet["files"].items():
            if relative.endswith(".py"):
                ast.parse(entry["content"], feature_version=(3, 11))
        ast.parse(
            (HERE / "install.py").read_text(encoding="utf-8"), feature_version=(3, 11)
        )
        result = subprocess.run(
            ["bash", "-n"], input=self.command, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
