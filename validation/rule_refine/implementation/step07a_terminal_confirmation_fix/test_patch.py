"""Delivered step07 -> tool-only fix; isolated replay, no service calls."""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = load("terminal_fix_test_builder", HERE / "build_command.py")
BASELINE = load("terminal_fix_test_baseline", BUILDER.BASE / "build_command.py")
INSTALLER = types.ModuleType("terminal_fix_test_installer")
sys.modules[INSTALLER.__name__] = INSTALLER
exec(compile(BUILDER.installer_source(), "<terminal-fix>", "exec"), INSTALLER.__dict__)


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.packet = BUILDER.contents()
        baseline = BASELINE.contents()
        for name, expected in self.packet["dependencies"].items():
            raw = (
                baseline["files"][name]["content"].encode("utf-8")
                if name in baseline["files"]
                else BASELINE.find_source(name, expected).read_bytes()
            )
            self.write(name, raw)
        self.original = (BUILDER.BASE / "payload" / BUILDER.TOOL).read_bytes()
        self.write(BUILDER.TOOL, self.original)
        self.write("config/rule_refine.surge.local.json", b'{"context_tokens":1000000}')
        self.write("nas/data/sec_know_model.sqlite", b"DO NOT OPEN OR RESEED")

    def write(self, name, raw):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def test_apply_retest_and_repeat_preserve_runtime_configuration_and_database(self):
        before = {
            p.relative_to(self.root): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }
        first = INSTALLER.apply(self.root, self.root.parent / "results", self.packet)
        self.assertEqual(first["status"], "installed")
        self.assertEqual(first["tests_passed"], 18)
        self.assertFalse(first["restart_required"])
        self.assertFalse(first["application_code_updated"])
        self.assertTrue(first["verification_tool_updated"])
        for path, raw in before.items():
            if str(path) != BUILDER.TOOL:
                self.assertEqual((self.root / path).read_bytes(), raw)
        self.assertEqual(
            (Path(first["backup_dir"]) / "before" / BUILDER.TOOL).read_bytes(),
            self.original,
        )
        second = INSTALLER.apply(self.root, self.root.parent / "results", self.packet)
        self.assertEqual(second["status"], "already_installed")
        self.assertEqual(second["files_changed"], 0)

    def test_unknown_tool_version_is_preserved(self):
        self.write(BUILDER.TOOL, b"# operator changed tool\n")
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "unknown_project_file_version"
        ):
            INSTALLER.apply(self.root, self.root.parent / "results", self.packet)
        self.assertEqual(
            (self.root / BUILDER.TOOL).read_bytes(), b"# operator changed tool\n"
        )


if __name__ == "__main__":
    unittest.main()
