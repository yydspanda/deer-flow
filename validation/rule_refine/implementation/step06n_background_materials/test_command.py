"""Verify the reused runner gates and complete copyable command."""

import ast
import base64
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
build_command = importlib.import_module("build_command")

spec = importlib.util.spec_from_file_location(
    "_background_materials_runner_tests",
    HERE.parent / "step06j_synthetic_chain/test_runner.py",
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.HERE = HERE
RunnerGateTests = base.RunnerGateTests
RunnerExecutionTests = base.RunnerExecutionTests


class CommandTests(unittest.TestCase):
    def originals(self):
        files = {
            name: (build_command.PREVIOUS / name).read_bytes()
            for name in ("runner.py", "probe.py", "pins.json")
        }
        return files

    def payload(self):
        script = (HERE / "CHECK-BACKGROUND-MATERIALS.sh").read_text(encoding="utf-8")
        body = script.split("<<'PY_CHECK'\n", 1)[1].rsplit("\nPY_CHECK\n", 1)[0]
        assignments = {}
        for node in ast.parse(body).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {"payload", "expected_payload_sha256"}:
                    assignments[node.targets[0].id] = ast.literal_eval(node.value)
        return body, assignments["payload"], assignments["expected_payload_sha256"]

    def test_transform_preserves_inputs_and_pins_match_installed_fix(self):
        source = self.originals()
        before = dict(source)
        changed = build_command.prepare(source)
        self.assertEqual(source, before)
        expected = json.loads((HERE.parent / "step06m_apply_parser/current-pins.json").read_text())
        self.assertEqual(json.loads(changed["pins.json"]), expected)
        self.assertEqual(changed["base_probe.py"], source["probe.py"])
        self.assertIn(b"manual_in_process", changed["runner.py"])
        for name, raw in changed.items():
            if name.endswith(".py"):
                compile(raw, name, "exec")

    def test_unknown_old_tool_or_parser_is_rejected(self):
        for filename in ("runner.py", "pins.json"):
            with self.subTest(filename=filename):
                files = self.originals()
                files[filename] = b"{}"
                with self.assertRaises(ValueError):
                    build_command.prepare(files)

    def test_generated_script_syntax_and_full_probe_are_present(self):
        path = HERE / "CHECK-BACKGROUND-MATERIALS.sh"
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        _, payload, expected = self.payload()
        files = build_command.unpack(payload, expected)
        for name in ("probe.py", "diagnostics.py", "entry.py"):
            self.assertEqual(files[name], (HERE / name).read_bytes())

    def test_generated_command_ignores_different_old_helpers_and_preserves_all_old_files(self):
        body, payload, expected = self.payload()
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            source = home / "sec-rule-refine-data/synthetic-chain-diagnostic-o4olbtkt"
            source.mkdir(parents=True)
            for filename, raw in self.originals().items():
                (source / filename).write_bytes(raw)
            for filename in ("diagnostics.py", "entry.py"):
                (source / filename).write_text("raise RuntimeError('must never import legacy helper')\n", encoding="utf-8")
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            reads = []
            real_read = Path.read_bytes

            def read(path):
                reads.append(path)
                return real_read(path)

            with patch.object(Path, "home", return_value=home), patch.object(Path, "read_bytes", read), \
                    patch.object(subprocess, "call", return_value=0) as launch, \
                    contextlib.redirect_stdout(base.io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    exec(compile(body, "<complete-user-command>", "exec"), {})
            self.assertEqual(caught.exception.code, 0)
            launch.assert_called_once()
            folder = Path(launch.call_args.args[0][-1]).parent
            self.assertEqual({p.name for p in reads}, set(build_command.BASIC_PINS))
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})
            files = build_command.unpack(payload, expected)
            files = {**build_command.prepare(self.originals()), **files}
            self.assertEqual({p.name for p in folder.iterdir()}, set(files))
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            for filename, raw in files.items():
                self.assertEqual((folder / filename).read_bytes(), raw)
                self.assertEqual(stat.S_IMODE((folder / filename).stat().st_mode), 0o600)

    def test_bad_basic_files_never_publish_a_folder(self):
        _, payload, expected = self.payload()
        bundled = build_command.unpack(payload, expected)
        for filename in build_command.BASIC_PINS:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as name:
                source = Path(name) / "source"
                source.mkdir()
                for key, raw in self.originals().items():
                    (source / key).write_bytes(raw)
                (source / filename).write_bytes(b"mismatch")
                with patch.object(build_command.tempfile, "mkdtemp") as create:
                    with self.assertRaises(ValueError):
                        build_command.publish(source, bundled)
                    create.assert_not_called()

    def test_bad_payload_is_rejected_before_tools_are_read_or_published(self):
        body, _, expected = self.payload()
        for payload in ("!invalid!", "eJw=", "", base64.b64encode(zlib.compress(b"x" * (256 * 1024 + 1))).decode()):
            with self.subTest(payload=payload[:20]), patch.object(Path, "read_bytes") as read, \
                    patch.object(build_command.tempfile, "mkdtemp") as create, \
                    patch.object(subprocess, "call") as launch, contextlib.redirect_stdout(base.io.StringIO()):
                tree = ast.parse(body)
                for node in tree.body:
                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "payload":
                        node.value = ast.Constant(payload)
                with self.assertRaises(SystemExit) as caught:
                    exec(compile(ast.fix_missing_locations(tree), "<damaged-command>", "exec"), {})
                self.assertEqual(caught.exception.code, 1)
                read.assert_not_called()
                create.assert_not_called()
                launch.assert_not_called()

    def test_payload_file_list_and_python_syntax_are_validated(self):
        for data in ({"../escape.py": "pass"}, {"probe.py": "def !!!", "diagnostics.py": "pass", "entry.py": "pass"}):
            raw = json.dumps(data).encode()
            payload = base64.b64encode(zlib.compress(raw)).decode()
            with self.assertRaises((ValueError, SyntaxError)):
                build_command.unpack(payload, hashlib.sha256(raw).hexdigest())

    def test_cancel_has_no_services_or_model_call(self):
        RunnerGateTests.setUpClass()
        try:
            runner = RunnerGateTests.module
            with patch.object(runner, "preflight", return_value=base.SimpleNamespace(model="synthetic")), \
                    patch.object(runner, "confirm_live", return_value=False), \
                    patch.object(runner, "execute") as execute, \
                    base.contextlib.redirect_stdout(base.io.StringIO()):
                self.assertEqual(runner.main(), 0)
            execute.assert_not_called()
        finally:
            RunnerGateTests.tearDownClass()


if __name__ == "__main__":
    unittest.main(verbosity=2)
