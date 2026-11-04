"""Offline patch transaction and packaging checks for a step07/07A installation."""

import ast
import base64
import copy
import importlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = load("review_sampling_patch_builder", HERE / "build_command.py")
BASELINE = BUILDER.baseline_builder()
INSTALLER = types.ModuleType("review_sampling_patch_installer")
sys.modules[INSTALLER.__name__] = INSTALLER
exec(
    compile(BUILDER.installer_source(), "<sampling-installer>", "exec"),
    INSTALLER.__dict__,
)


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
        for name, entry in self.packet["files"].items():
            if entry["old_sha256"]:
                original = baseline["files"].get(name)
                raw = (
                    original["content"].encode("utf-8")
                    if original
                    else BASELINE.find_source(name, entry["old_sha256"]).read_bytes()
                )
                self.write(name, raw)
        tool = "test/rule_refine/check_surge_dev_live.py"
        terminal_fix = HERE.parent / "step07a_terminal_confirmation_fix/payload" / tool
        self.write(tool, terminal_fix.read_bytes())
        self.write(
            "config/rule_refine.surge.local.json",
            b'{"context_tokens":1000000,"input_fraction":0.8}',
        )
        self.write(
            "config/rule_refine.analysis.local.json",
            b'{"model":"operator-selected-model"}',
        )
        self.write(
            "nas/data/sec_know_model.sqlite", b"OPAQUE PROJECT DATABASE: DO NOT OPEN"
        )
        self.write("nas/reports/old/REPORT.md", b"Previous immutable model answer\n")

    def write(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def snapshot(self):
        return {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.startswith(".rule-refine-")
        }

    def apply(self):
        return INSTALLER.apply(self.root, self.root.parent / "results", self.packet)

    def processor_modules(self):
        name = "_review_upgrade_" + uuid.uuid4().hex
        package = types.ModuleType(name)
        package.__path__ = [str(self.root / BUILDER.MODULE)]
        sys.modules[name] = package

        def clean():
            for loaded in list(sys.modules):
                if loaded == name or loaded.startswith(name + "."):
                    sys.modules.pop(loaded, None)

        self.addCleanup(clean)
        module = importlib.import_module(name + ".surge_processor")
        budget = importlib.import_module(name + ".surge_budget").ContextBudget()
        return module, budget

    def legacy_fixture(self):
        return load(
            "review_upgrade_legacy_fixture",
            BUILDER.BASE / "payload/test/rule_refine/test_surge_processor.py",
        )

    def upgraded_service(self, directory, fixtures):
        # The full installer run above covers isolated checks. These compatibility
        # cases focus on already-published records and do not need to rerun them.
        with patch.object(INSTALLER, "run_checks", return_value=56):
            self.apply()
        module, budget = self.processor_modules()
        generate = Mock(side_effect=AssertionError("saved_job_must_not_call_model"))
        service = module.SurgeProcessor(
            directory,
            load_case=Mock(side_effect=AssertionError("frozen_case_must_be_reused")),
            load_rules=lambda: types.SimpleNamespace(
                get_rule=lambda *args, **kwargs: fixtures.rule()
            ),
            generate=generate,
            model="synthetic-model",
            budget=budget,
        )
        return service, generate

    def test_completed_step07_job_reopens_after_upgrade_without_rewriting_or_calls(
        self,
    ):
        fixtures = self.legacy_fixture()
        module, budget = self.processor_modules()
        directory = self.root.parent / "completed-jobs"
        generate = Mock(
            side_effect=lambda request: fixtures.transport(
                json.dumps(fixtures.answer(request), ensure_ascii=False)
            )
        )
        old = module.SurgeProcessor(
            directory,
            load_case=lambda signal: fixtures.case(),
            load_rules=lambda: types.SimpleNamespace(
                get_rule=lambda *args, **kwargs: fixtures.rule()
            ),
            generate=generate,
            model="synthetic-model",
            budget=budget,
        )
        signal = fixtures.signal()
        first = old.process(signal)
        self.assertEqual(first["status"], "received")
        self.assertEqual(generate.call_count, 2)
        folder = Path(first["output_dir"])
        before = {
            path.relative_to(folder): path.read_bytes()
            for path in folder.rglob("*")
            if path.is_file()
        }
        upgraded, new_generate = self.upgraded_service(directory, fixtures)
        resumed = upgraded.process(signal)
        self.assertEqual(resumed["status"], "received")
        self.assertEqual(resumed["record_id"], first["record_id"])
        self.assertEqual(resumed["model_calls_this_attempt"], 0)
        self.assertTrue(upgraded.verify_durable_result(signal, resumed))
        new_generate.assert_not_called()
        self.assertEqual(
            {
                path.relative_to(folder): path.read_bytes()
                for path in folder.rglob("*")
                if path.is_file()
            },
            before,
        )

    def test_step07_crash_after_report_recovers_after_upgrade_without_model_calls(self):
        fixtures = self.legacy_fixture()
        module, budget = self.processor_modules()
        directory = self.root.parent / "interrupted-jobs"
        generate = Mock(
            side_effect=lambda request: fixtures.transport(
                json.dumps(fixtures.answer(request), ensure_ascii=False)
            )
        )
        old = module.SurgeProcessor(
            directory,
            load_case=lambda signal: fixtures.case(),
            load_rules=lambda: types.SimpleNamespace(
                get_rule=lambda *args, **kwargs: fixtures.rule()
            ),
            generate=generate,
            model="synthetic-model",
            budget=budget,
        )
        signal = fixtures.signal()
        write = module._write

        def interrupt_before_final_record(path, value, **kwargs):
            if path.name == "result.json":
                raise KeyboardInterrupt()
            return write(path, value, **kwargs)

        with patch.object(module, "_write", side_effect=interrupt_before_final_record):
            with self.assertRaises(KeyboardInterrupt):
                old.process(signal)
        self.assertEqual(generate.call_count, 2)
        folder = directory / "jobs" / module._identity(signal)[2]
        report = (folder / "REPORT.md").read_bytes()
        self.assertFalse((folder / "result.json").exists())
        frozen = {
            path.relative_to(folder): path.read_bytes()
            for path in folder.rglob("*")
            if path.is_file()
        }
        upgraded, new_generate = self.upgraded_service(directory, fixtures)
        resumed = upgraded.process(signal)
        self.assertEqual(resumed["status"], "received")
        self.assertEqual(resumed["model_calls_this_attempt"], 0)
        self.assertTrue(upgraded.verify_durable_result(signal, resumed))
        self.assertEqual((folder / "REPORT.md").read_bytes(), report)
        self.assertTrue((folder / "result.json").is_file())
        for name, raw in frozen.items():
            self.assertEqual((folder / name).read_bytes(), raw, str(name))
        new_generate.assert_not_called()

    def test_apply_offline_retest_and_repeat_preserve_7a_settings_database_reports(
        self,
    ):
        before = self.snapshot()
        first = self.apply()
        self.assertEqual(first["status"], "installed")
        self.assertGreaterEqual(first["tests_passed"], 46)
        self.assertEqual(first["files_changed"], len(self.packet["files"]))
        self.assertTrue(first["restart_required"])
        self.assertFalse(first["automatic_restart"])
        self.assertTrue(first["application_code_updated"])
        self.assertEqual(first["check"], "rule_refine_review_sampling_fix")
        self.assertEqual(first["model_calls"], 0)
        for name, raw in before.items():
            if name not in self.packet["files"]:
                self.assertEqual((self.root / name).read_bytes(), raw, name)
            else:
                self.assertEqual(
                    (Path(first["backup_dir"]) / "before" / name).read_bytes(),
                    raw,
                    name,
                )
        second = self.apply()
        self.assertEqual(second["status"], "already_installed")
        self.assertEqual(second["files_changed"], 0)
        self.assertEqual(second["tests_passed"], first["tests_passed"])

    def test_unknown_runtime_change_stops_before_tests_and_preserves_files(self):
        name = BUILDER.MODULE + "surge_sampling.py"
        self.write(name, b"# operator changed sampling\n")
        before = self.snapshot()
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "unknown_project_file_version"
            ):
                self.apply()
        checks.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_failed_offline_tests_leave_project_unmodified(self):
        before = self.snapshot()
        with patch.object(
            INSTALLER,
            "run_checks",
            side_effect=INSTALLER.InstallError("isolated_tests_failed"),
        ):
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "isolated_tests_failed"
            ):
                self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_packet_does_not_install_policy_tools_or_prior_test_fixtures(self):
        self.assertNotIn(BASELINE.POLICY, self.packet["files"])
        for name in ("check_surge_dev_live.py", "check_surge_dev_history.py"):
            self.assertNotIn(BUILDER.TEST + name, self.packet["files"])
            self.assertNotIn(BUILDER.TEST + name, self.packet["dependencies"])
        self.assertEqual(
            self.packet["tests"], [BUILDER.NEW_TEST, *BUILDER.REGRESSION_TESTS]
        )
        for name in BUILDER.REGRESSION_TESTS:
            self.assertIn(name, self.packet["test_files"])
            self.assertNotIn(name, self.packet["files"])

    def test_packaged_bytes_match_python311_sources_and_shell_has_no_live_command(self):
        source = BUILDER.runtime_source(self.packet)
        ast.parse(source, feature_version=(3, 11))
        literal = source.split('base64.b64decode("""', 1)[1].split('"""', 1)[0]
        bundle = json.loads(zlib.decompress(base64.b64decode(literal)))
        self.assertEqual(bundle["packet"], self.packet)
        self.assertEqual(bundle["installer"], BUILDER.installer_source())
        for name, entry in self.packet["files"].items():
            ast.parse(entry["content"], filename=name, feature_version=(3, 11))
        command = BUILDER.build()
        self.assertNotIn("--live", command)
        self.assertNotIn("local_run.sh", command)
        self.assertIn("umask 077", command)
        self.assertEqual(
            subprocess.run(
                ["bash", "-n"], input=command, text=True, capture_output=True
            ).returncode,
            0,
        )

    def test_invalid_manifest_hash_is_rejected_before_installing(self):
        damaged = copy.deepcopy(self.packet)
        name = BUILDER.MODULE + "surge_sampling.py"
        damaged["files"][name]["new_sha256"] = "0" * 64
        before = self.snapshot()
        with self.assertRaisesRegex(INSTALLER.InstallError, "package_content_mismatch"):
            INSTALLER.apply(self.root, self.root.parent / "results", damaged)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
