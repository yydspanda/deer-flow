"""Offline 7E installation, frozen D/E compatibility, and one-call review command checks."""

import ast
import base64
import copy
import importlib
import importlib.util
import json
import shlex
import shutil
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


BUILDER = load("repair_first_patch_builder", HERE / "build_command.py")
INSTALLER = types.ModuleType("repair_first_patch_installer")
sys.modules[INSTALLER.__name__] = INSTALLER
exec(
    compile(BUILDER.installer_source(), "<repair-first-installer>", "exec"),
    INSTALLER.__dict__,
)


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.packet = BUILDER.contents()
        for name, digest in self.packet["dependencies"].items():
            self.write(name, BUILDER.previous_source(name, digest))
        for name, entry in self.packet["files"].items():
            if entry["old_sha256"] is not None:
                self.write(name, BUILDER.previous_source(name, entry["old_sha256"]))
        for name, entry in BUILDER.baseline_builder().contents()["files"].items():
            if name.startswith(BUILDER.TEST):
                self.write(name, entry["content"].encode("utf-8"))
        tool = "test/rule_refine/check_surge_dev_live.py"
        source = HERE.parent / "step07a_terminal_confirmation_fix/payload" / tool
        self.write(tool, source.read_bytes())
        self.write(
            "config/rule_refine.surge.local.json",
            b'{"context_tokens":1000000,"input_fraction":0.8}',
        )
        self.write(
            "config/rule_refine.analysis.local.json",
            b'{"model":"operator-selected-model"}',
        )
        self.write("nas/data/sec_know_model.sqlite", b"DO NOT OPEN PROJECT DATABASE")
        self.write("nas/reports/old/REPORT.md", b"Immutable previous model answer\n")

    def write(self, name, raw):
        path = self.root / name
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

    def modules(self, source_root=None):
        name = "_repair_first_upgrade_" + uuid.uuid4().hex
        package = types.ModuleType(name)
        package.__path__ = [str((source_root or self.root) / BUILDER.MODULE)]
        sys.modules[name] = package

        def clean():
            for loaded in list(sys.modules):
                if loaded == name or loaded.startswith(name + "."):
                    sys.modules.pop(loaded, None)

        self.addCleanup(clean)
        module = importlib.import_module(name + ".surge_processor")
        budget = importlib.import_module(name + ".surge_budget").ContextBudget()
        return module, budget

    @staticmethod
    def fixture():
        rule = {
            "company_code": "fixture",
            "topic": "fixture-topic",
            "rule_code": "fixture-rule",
            "rule_name": "Synthetic detection",
            "rule_config_version": "v1",
            "configuration": {"terms": []},
        }
        case = {
            "rule": rule,
            "original_rule": rule,
            "current_samples": [
                {
                    "alert_id": "new-" + str(i),
                    "raw_logs": [{"event_content": "scheduled collection"}],
                }
                for i in range(6)
            ],
            "history_samples": [
                {"alert_id": "old-1", "raw_logs": [{"event_content": "manual"}]}
            ],
            "coverage": {"current_alert_count": 6},
        }
        signal = {
            "schema_version": "sec_know.rule_burst_signal.v1",
            "signal_id": "fixture-signal",
            "mode": "demo",
            "namespace": "fixture",
            "source_app": "fixture",
            "company_code": "fixture",
            "topic": "fixture-topic",
            "rule_code": "fixture-rule",
            "sample_alert_ids": ["new-" + str(i) for i in range(6)],
            "current_count": 6,
            "previous_count": 1,
        }
        return rule, case, signal

    def service(self, directory, *, saved=False, source_root=None):
        module, budget = self.modules(source_root)
        rule, case, signal = self.fixture()

        def respond(request):
            answer = {
                "status": "proposal",
                "issue": "Synthetic repeated operation",
                "recommendation": "Exclude only the specified operation",
                "certainty": "fixture",
                "filter": {
                    "topic": rule["topic"],
                    "A": {
                        "name": "event_content",
                        "operator": "contain",
                        "value": "scheduled collection",
                    },
                    "expression": "A",
                },
                "sample_alert_ids": [
                    request["context"]["current_samples"][0]["alert_id"]
                ],
                "review_notes": "Synthetic transport only",
                "sample_request": None,
            }
            return {
                "content": json.dumps(answer, ensure_ascii=False),
                "transport": {
                    "sdk_request_calls": 1,
                    "response_received": True,
                    "finish_reason": "stop",
                    "error_code": None,
                    "requested_model": "fixture-model",
                },
            }

        generate = Mock(
            side_effect=AssertionError("saved_work_must_not_invoke_model")
            if saved
            else respond
        )
        processor = module.SurgeProcessor(
            directory,
            load_case=Mock(side_effect=lambda _: copy.deepcopy(case)),
            load_rules=lambda: types.SimpleNamespace(get_rule=lambda *a, **k: rule),
            generate=generate,
            model="fixture-model",
            budget=budget,
        )
        return module, processor, signal, generate

    def legacy_d_runtime(self):
        """Build an exact D runtime outside the E project being upgraded."""
        root = self.root.parent / "legacy-d"
        shutil.copytree(self.root / BUILDER.MODULE, root / BUILDER.MODULE)
        old = BUILDER.baseline_builder().baseline_builder().contents()
        for name, entry in old["files"].items():
            if name.startswith(BUILDER.MODULE):
                (root / name).write_text(entry["content"], encoding="utf-8")
        return root

    def test_completed_7e_job_retains_all_frozen_bytes_and_zero_new_calls(self):
        directory = self.root.parent / "completed"
        _, old, signal, generate = self.service(directory)
        original = old.process(signal)
        self.assertEqual(original["status"], "received")
        self.assertEqual(generate.call_count, 2)
        folder = Path(original["output_dir"])
        before = {
            p.relative_to(folder): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file()
        }
        with patch.object(INSTALLER, "run_checks", return_value=103):
            self.apply()
        _, current, _, no_generate = self.service(directory, saved=True)
        result = current.process(signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["record_id"], original["record_id"])
        self.assertEqual(result["model_calls_this_attempt"], 0)
        self.assertTrue(result["analysis_reused"])
        self.assertTrue(current.verify_durable_result(signal, result))
        no_generate.assert_not_called()
        self.assertEqual(
            {
                p.relative_to(folder): p.read_bytes()
                for p in folder.rglob("*")
                if p.is_file()
            },
            before,
        )

    def test_7e_crash_after_report_recovers_without_rewriting_or_calls(self):
        directory = self.root.parent / "interrupted"
        module, old, signal, generate = self.service(directory)
        write = module._write

        def crash_before_record(path, value, **kwargs):
            if path.name == "result.json":
                raise KeyboardInterrupt()
            return write(path, value, **kwargs)

        with patch.object(module, "_write", side_effect=crash_before_record):
            with self.assertRaises(KeyboardInterrupt):
                old.process(signal)
        self.assertEqual(generate.call_count, 2)
        folder = directory / "jobs" / module._identity(signal)[2]
        before = {
            p.relative_to(folder): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file()
        }
        self.assertIn(Path("REPORT.md"), before)
        self.assertNotIn(Path("result.json"), before)
        with patch.object(INSTALLER, "run_checks", return_value=103):
            self.apply()
        _, current, _, no_generate = self.service(directory, saved=True)
        result = current.process(signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["model_calls_this_attempt"], 0)
        self.assertTrue(current.verify_durable_result(signal, result))
        no_generate.assert_not_called()
        for name, raw in before.items():
            self.assertEqual((folder / name).read_bytes(), raw, str(name))

    def test_completed_7d_job_retains_all_frozen_bytes_and_zero_new_calls(self):
        directory = self.root.parent / "completed"
        _, old, signal, generate = self.service(
            directory, source_root=self.legacy_d_runtime()
        )
        original = old.process(signal)
        self.assertEqual(original["status"], "received")
        self.assertEqual(generate.call_count, 2)
        folder = Path(original["output_dir"])
        before = {
            p.relative_to(folder): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file()
        }
        with patch.object(INSTALLER, "run_checks", return_value=103):
            self.apply()
        _, current, _, no_generate = self.service(directory, saved=True)
        result = current.process(signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["record_id"], original["record_id"])
        self.assertEqual(result["model_calls_this_attempt"], 0)
        self.assertTrue(result["analysis_reused"])
        self.assertTrue(current.verify_durable_result(signal, result))
        no_generate.assert_not_called()
        self.assertEqual(
            {
                p.relative_to(folder): p.read_bytes()
                for p in folder.rglob("*")
                if p.is_file()
            },
            before,
        )

    def test_7d_crash_after_report_recovers_without_rewriting_or_calls(self):
        directory = self.root.parent / "interrupted"
        module, old, signal, generate = self.service(
            directory, source_root=self.legacy_d_runtime()
        )
        write = module._write

        def crash_before_record(path, value, **kwargs):
            if path.name == "result.json":
                raise KeyboardInterrupt()
            return write(path, value, **kwargs)

        with patch.object(module, "_write", side_effect=crash_before_record):
            with self.assertRaises(KeyboardInterrupt):
                old.process(signal)
        self.assertEqual(generate.call_count, 2)
        folder = directory / "jobs" / module._identity(signal)[2]
        before = {
            p.relative_to(folder): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file()
        }
        self.assertIn(Path("REPORT.md"), before)
        self.assertNotIn(Path("result.json"), before)
        with patch.object(INSTALLER, "run_checks", return_value=103):
            self.apply()
        _, current, _, no_generate = self.service(directory, saved=True)
        result = current.process(signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["model_calls_this_attempt"], 0)
        self.assertTrue(current.verify_durable_result(signal, result))
        no_generate.assert_not_called()
        for name, raw in before.items():
            self.assertEqual((folder / name).read_bytes(), raw, str(name))

    def test_install_and_repeat_preserve_7a_7e_configuration_database_and_reports(self):
        before = self.snapshot()
        first = self.apply()
        self.assertEqual(first["status"], "installed")
        self.assertGreater(first["tests_passed"], 103)
        self.assertEqual(first["files_changed"], len(self.packet["files"]))
        self.assertTrue(first["restart_required"])
        self.assertFalse(first["automatic_restart"])
        self.assertTrue(first["application_code_updated"])
        self.assertTrue(first["installed_state_verified"])
        self.assertEqual(first["check"], "rule_refine_repair_first_review_fix")
        for name in ("model_calls", "redis_calls", "business_database_calls"):
            self.assertEqual(first[name], 0)
        for name, raw in before.items():
            if name not in self.packet["files"]:
                self.assertEqual((self.root / name).read_bytes(), raw, name)
            else:
                self.assertEqual(
                    (Path(first["backup_dir"]) / "before" / name).read_bytes(), raw
                )
        second = self.apply()
        self.assertEqual(second["status"], "already_installed")
        self.assertEqual(second["files_changed"], 0)
        self.assertEqual(second["tests_passed"], first["tests_passed"])

    def test_unknown_patched_file_stops_before_checks_without_overwrite(self):
        self.write(BUILDER.MODULE + "surge_ai_review.py", b"# operator changes\n")
        before = self.snapshot()
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "unknown_project_file_version"
            ):
                self.apply()
        checks.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_existing_unreviewed_retry_tool_is_not_overwritten(self):
        self.write(BUILDER.TEST + "check_surge_review_repair.py", b"# local tool\n")
        before = self.snapshot()
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "unknown_project_file_version"
            ):
                self.apply()
        checks.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_previous_7d_ai_is_rejected_instead_of_skipping_7e(self):
        old_d = BUILDER.baseline_builder().baseline_builder().contents()
        name = BUILDER.MODULE + "surge_ai_review.py"
        self.write(name, old_d["files"][name]["content"].encode("utf-8"))
        before = self.snapshot()
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "unknown_project_file_version"
            ):
                self.apply()
        checks.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_old_unfixed_sampler_is_not_accepted_as_7c_dependency(self):
        original = (
            BUILDER.baseline_builder()
            .baseline_builder()
            .baseline_builder()
            .baseline_builder()
            .contents()
        )
        name = BUILDER.MODULE + "surge_sampling.py"
        self.write(name, original["files"][name]["content"].encode("utf-8"))
        before = self.snapshot()
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "dependency_version_mismatch"
            ):
                self.apply()
        checks.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_failed_regression_never_mutates_project(self):
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

    def test_manifest_pins_7e_and_only_stages_declared_expectation_updates(self):
        previous = BUILDER.baseline_builder().contents()
        for name in ("surge_ai_review.py",):
            self.assertEqual(
                self.packet["files"][BUILDER.MODULE + name]["old_sha256"],
                previous["files"][BUILDER.MODULE + name]["new_sha256"],
            )
        self.assertNotIn(BUILDER.MODULE + "surge_sampling.py", self.packet["files"])
        self.assertNotIn(BUILDER.MODULE + "surge_scope.py", self.packet["files"])
        for name in ("surge_processor.py", "surge_field_usage.py"):
            self.assertNotIn(BUILDER.MODULE + name, self.packet["files"])
            self.assertEqual(
                self.packet["dependencies"][BUILDER.MODULE + name],
                previous["files"][BUILDER.MODULE + name]["new_sha256"],
            )
        self.assertEqual(
            set(self.packet["files"]),
            set(BUILDER.RUNTIME_FILES)
            | set(BUILDER.NEW_TESTS)
            | set(BUILDER.TOOL_FILES),
        )
        self.assertTrue(
            all(
                name.startswith((BUILDER.MODULE, BUILDER.TEST))
                for name in self.packet["files"]
            )
        )
        for name in ("check_surge_dev_live.py", "check_surge_dev_history.py"):
            self.assertNotIn(BUILDER.TEST + name, self.packet["files"])
            self.assertNotIn(BUILDER.TEST + name, self.packet["dependencies"])
        originals = {
            **previous["test_files"],
            **{
                name: entry["content"]
                for name, entry in previous["files"].items()
                if name.startswith(BUILDER.TEST)
            },
        }
        for name in BUILDER.REGRESSION_TESTS:
            expected = originals[name]
            for old, (new, count) in BUILDER.STAGED_EXPECTATIONS.get(name, {}).items():
                self.assertEqual(expected.count(old), count)
                expected = expected.replace(old, new)
            self.assertEqual(self.packet["test_files"][name], expected)
        self.assertEqual(
            self.packet["tests"], [*BUILDER.NEW_TESTS, *BUILDER.REGRESSION_TESTS]
        )
        for name in BUILDER.REGRESSION_TESTS:
            self.assertIn(name, self.packet["test_files"])
            self.assertNotIn(name, self.packet["files"])

    def test_payload_roundtrip_python311_and_install_only_shell(self):
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
        subprocess.run(
            ["bash", "-n"], input=command, text=True, check=True, capture_output=True
        )

    def test_combined_shell_only_runs_review_tool_after_successful_install(self):
        BUILDER.build()
        command = (HERE / "INSTALL-AND-REVIEW.sh").read_text(encoding="utf-8")
        original = BUILDER.REVIEW_COMMAND
        self.assertEqual(command.count(original), 1)
        self.assertTrue(
            command.startswith(
                "bash <<'SOC_SURGE_REPAIR_INSTALL_AND_REVIEW'\nset -euo pipefail\n"
            )
        )
        self.assertLess(command.index("installer.apply("), command.index(original))
        self.assertIn("LIVE", command)
        self.assertNotIn("--yes", command)
        self.assertNotIn("CHECK-REAL-CSV", command)
        self.assertEqual(command.count("--live"), 1)
        self.assertIn("最多1次SDK请求", command)
        subprocess.run(
            ["bash", "-n"], input=command, text=True, check=True, capture_output=True
        )

    def test_failed_install_exits_combined_shell_before_retest(self):
        written = {}

        def capture(path, value, **kwargs):
            written[path.name] = value
            return len(value)

        with (
            patch.object(
                BUILDER, "runtime_source", return_value="raise SystemExit(19)\n"
            ),
            patch.object(Path, "write_text", capture),
        ):
            BUILDER.build()
        command = written["INSTALL-AND-REVIEW.sh"]
        original = BUILDER.REVIEW_COMMAND
        command = command.replace(original, "echo UNEXPECTED_RETEST\n")
        command = command.replace(
            'cd "$HOME/sec_know_model"', "cd " + shlex.quote(str(self.root))
        )
        interpreter = self.root / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        result = subprocess.run(["bash"], input=command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 19)
        self.assertNotIn("UNEXPECTED_RETEST", result.stdout)
        self.assertNotIn("安装成功", result.stdout)

    def test_manifest_tampering_stops_before_installation(self):
        damaged = copy.deepcopy(self.packet)
        name = BUILDER.MODULE + "surge_ai_review.py"
        damaged["files"][name]["new_sha256"] = "0" * 64
        before = self.snapshot()
        with self.assertRaisesRegex(INSTALLER.InstallError, "package_content_mismatch"):
            INSTALLER.apply(self.root, self.root.parent / "results", damaged)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
