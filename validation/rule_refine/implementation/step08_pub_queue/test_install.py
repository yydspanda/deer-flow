"""Installer transactions against private sources held only in temporary directories."""

from __future__ import annotations

import json
import ast
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_command
import install as installer


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.choices = build_command.variants()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="offline-queue-installer-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.make_project("project")
        self.backups = self.base / "backups"

    def make_project(self, name, latest=True):
        project = self.base / name
        project.mkdir(mode=0o700)
        for index, (path, variants) in enumerate(self.choices.items()):
            file = project / path
            file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            file.write_bytes(variants[-1 if latest else 0])
            file.chmod(
                0o750 if path.endswith(".sh") else (0o640 if index % 2 else 0o600)
            )
        return project

    def snapshot(self, project=None):
        """Never use private source bytes as assertion operands or failure output."""
        project = self.project if project is None else project
        result = {}
        for path in sorted(project.rglob("*")):
            relative = str(path.relative_to(project))
            if path.is_symlink():
                result[relative] = ("symlink", os.readlink(path))
            elif path.is_file():
                result[relative] = (
                    installer.digest(path.read_bytes()),
                    stat.S_IMODE(path.stat().st_mode),
                )
        return result

    def test_real_regressions_install_backups_and_second_run_idempotence(self):
        self.assertEqual(len(self.choices), 22)
        before = self.snapshot()
        result = installer.install(self.project, self.backups)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["tests_passed"], 33)
        self.assertTrue(result["regression_tests_run_this_command"])
        self.assertTrue(result["installed_state_verified"])
        self.assertTrue(result["production_monitor_disabled_in_launcher"])
        self.assertEqual(result["stg_alert_model"], "Qwen38_27B")
        self.assertEqual(result["stg_alert_queue"], "PingAnGPT_R_queue")
        self.assertEqual(result["stg_alert_shared_capacity"], 6)
        self.assertFalse(result["services_started"])
        self.assertFalse(result["automatic_restart"])
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["redis_calls"], 0)
        self.assertEqual(result["database_calls"], 0)
        self.assertEqual(result["queue_tasks_deleted_or_migrated"], 0)
        after = self.snapshot()
        for name in result["changed_paths"]:
            self.assertEqual(before[name][1], after[name][1], name)
            backup = Path(result["backup_dir"]) / "backup" / name
            self.assertEqual(
                installer.digest(backup.read_bytes()), before[name][0], name
            )
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600, name)
        with patch.object(
            installer,
            "run_regressions",
            side_effect=AssertionError("must not repeat regression run"),
        ):
            second = installer.install(self.project, self.backups)
        self.assertEqual(second["status"], "already_installed")
        self.assertEqual(second["tests_passed"], 0)
        self.assertFalse(second["regression_tests_run_this_command"])
        self.assertEqual(second["files_changed"], 0)
        self.assertEqual(self.snapshot(), after)

    def test_unknown_source_hash_refuses_all_writes(self):
        path = self.project / installer.PATHS[-1]
        path.write_bytes(path.read_bytes() + b"\n# unreviewed local change\n")
        before = self.snapshot()
        with patch.object(installer, "run_regressions") as regressions:
            with self.assertRaisesRegex(installer.InstallError, "unreviewed_source:"):
                installer.install(self.project, self.backups)
        regressions.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backups.exists())

    def test_symlink_refuses_all_writes(self):
        path = self.project / "config/model_limits.py"
        outside = self.base / "outside.py"
        outside.write_text("# unrelated file\n", encoding="utf-8")
        path.unlink()
        path.symlink_to(outside)
        before = self.snapshot()
        with patch.object(installer, "run_regressions") as regressions:
            with self.assertRaisesRegex(installer.InstallError, "symlink_not_allowed:"):
                installer.install(self.project, self.backups)
        regressions.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(outside.read_text(encoding="utf-8"), "# unrelated file\n")
        self.assertFalse(self.backups.exists())

    def test_retired_local_analysis_config_migrates_only_model_preserving_enabled(self):
        for index, (model, enabled) in enumerate(
            (name, enabled) for name in installer.RETIRED for enabled in (True, False)
        ):
            with self.subTest(model=model, enabled=enabled):
                project = self.make_project(f"config-{index}")
                config = project / installer.LOCAL_CONFIG
                value = {
                    "enabled": enabled,
                    "model": model,
                    "state_dir": "offline-fixture-state",
                }
                config.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
                config.chmod(0o640)
                with patch.object(installer, "run_regressions", return_value=33):
                    result = installer.install(project, self.backups)
                self.assertEqual(
                    json.loads(config.read_text(encoding="utf-8")),
                    dict(value, model=installer.PUB),
                )
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
                self.assertIn(installer.LOCAL_CONFIG, result["changed_paths"])
                backup = Path(result["backup_dir"]) / "backup" / installer.LOCAL_CONFIG
                self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), value)

    def test_mixed_install_state_refuses_all_writes(self):
        path = installer.PATHS[0]
        file = self.project / path
        file.write_bytes(installer.transform(path, file.read_bytes()))
        before = self.snapshot()
        with patch.object(installer, "run_regressions") as regressions:
            with self.assertRaisesRegex(
                installer.InstallError, "mixed_install_state_review_required"
            ):
                installer.install(self.project, self.backups)
        regressions.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backups.exists())

    def test_second_replace_failure_rolls_back_content_and_modes(self):
        before = self.snapshot()
        real_replace = installer.replace_file
        attempts = []

        def fail_second(path, data, mode):
            attempts.append(str(path.relative_to(self.project)))
            if len(attempts) == 2:
                raise OSError("synthetic replacement failure")
            return real_replace(path, data, mode)

        with patch.object(installer, "run_regressions", return_value=33):
            with patch.object(installer, "replace_file", side_effect=fail_second):
                with self.assertRaisesRegex(
                    installer.InstallError, "install_failed_rolled_back:"
                ):
                    installer.install(self.project, self.backups)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0], attempts[2])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(list(self.backups.glob("queue-pub27-*"))), 1)

    def test_baseline_without_guard_does_not_claim_monitor_disabled(self):
        project = self.make_project("baseline", latest=False)
        with patch.object(installer, "run_regressions", return_value=33):
            result = installer.install(project, self.backups)
        self.assertEqual(result["status"], "installed")
        self.assertFalse(result["production_monitor_disabled_in_launcher"])
        self.assertTrue(result["installed_target_static_checks_passed"])
        self.assertFalse(result["running_production_verified"])

    def test_check_only_preserves_all_content_and_modes(self):
        before = self.snapshot()
        with patch.object(installer, "run_regressions", return_value=33):
            result = installer.install(self.project, self.backups, check_only=True)
        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["files_changed"], 0)
        self.assertGreater(result["files_planned"], 0)
        self.assertFalse(result["installed_state_verified"])
        self.assertFalse(result["application_code_updated"])
        self.assertEqual(self.snapshot(), before)

    def test_complete_copyable_command_without_repository_or_private_zip(self):
        self.add_unrelated_local_changes()
        command = self.base / "INSTALL.sh"
        command.write_bytes(
            (build_command.HERE / "INSTALL-PUB-QUEUE-27.sh").read_bytes()
        )
        environment = dict(
            os.environ,
            SEC_KNOW_PROJECT_ROOT=str(self.project),
            SEC_KNOW_BACKUP_PARENT=str(self.backups),
        )
        result = subprocess.run(
            ["bash", str(command)],
            cwd=self.base,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertTrue(result.returncode == 0, "standalone installer failed")
        receipts = list(self.backups.glob("queue-pub27-*/result.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "installed")
        self.assertEqual(receipt["tests_passed"], 33)
        self.assertEqual(len(receipt["scoped_validation_files"]), 4)
        self.assertTrue(receipt["production_monitor_disabled_in_launcher"])
        self.assertEqual(receipt["model_calls"], 0)

    def add_unrelated_local_changes(self):
        preserved = {}
        name = "llm_service/litellm_proxy_config.prd.yaml"
        path = self.project / name
        addition = "  - model_name: Additional_Offline_Test\n    litellm_params:\n      model: openai/Additional_Offline_Test\n      api_base: http://unused.invalid/v1\n      api_key: synthetic-only\n\n"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("litellm_settings:"), 1)
        path.write_text(
            text.replace("litellm_settings:", addition + "litellm_settings:", 1),
            encoding="utf-8",
        )
        preserved[name] = addition
        name = "llm_service/openai_completion.py"
        path = self.project / name
        text = path.read_text(encoding="utf-8")
        registry = installer.assignment(text, "PRD_MODEL_CONFIGS")
        lines = text.splitlines(keepends=True)
        addition = (
            '    "Additional_Offline_Test": {"type": "OPENAI", "endpoint": "unused"},\n'
        )
        lines.insert(registry.keys[0].lineno - 1, addition)
        path.write_text("".join(lines), encoding="utf-8")
        preserved[name] = addition
        name = "my_workflows/zeus/flows/alert_controller.py"
        path = self.project / name
        text = path.read_text(encoding="utf-8").replace(
            'mode="online"', 'mode="valid"', 1
        )
        cls = next(
            node
            for node in ast.parse(text).body
            if isinstance(node, ast.ClassDef) and node.name == "DataController"
        )
        lines = text.splitlines(keepends=True)
        addition = "@not_executed_during_offline_validation\n"
        lines.insert(cls.lineno - 1, addition)
        path.write_text("".join(lines), encoding="utf-8")
        preserved[name] = addition
        name = "test/test_all_models.py"
        path = self.project / name
        text = path.read_text(encoding="utf-8").replace(
            'models = ["DeepSeek_V4_Flash"]',
            "# The local override was intentionally removed by its owner.",
            1,
        )
        addition = 'raise AssertionError("smoke script must never run during installer tests")\n'
        path.write_text(text + "\n" + addition, encoding="utf-8")
        preserved[name] = addition
        return preserved

    def test_scoped_four_file_changes_preserved_real_regression_and_idempotence(self):
        preserved = self.add_unrelated_local_changes()
        before = self.snapshot()
        result = installer.install(self.project, self.backups)
        self.assertEqual(set(result["scoped_validation_files"]), set(preserved))
        self.assertEqual(result["tests_passed"], 33)
        for name, addition in preserved.items():
            self.assertTrue(
                addition in (self.project / name).read_text(encoding="utf-8"),
                "unrelated local addition changed",
            )
            self.assertFalse(result["source_validation"][name]["entire_file_reviewed"])
            self.assertTrue(
                result["source_validation"][name]["outside_target_bytes_preserved"]
            )
            self.assertEqual(
                installer.digest(
                    (Path(result["backup_dir"]) / "backup" / name).read_bytes()
                ),
                before[name][0],
            )
        after = self.snapshot()
        again = installer.install(self.project, self.backups)
        self.assertEqual(again["status"], "already_installed")
        self.assertEqual(again["tests_passed"], 0)
        self.assertEqual(self.snapshot(), after)

    def test_unknown_changed_routing_target_still_refuses_all_writes(self):
        self.add_unrelated_local_changes()
        path = self.project / "my_workflows/zeus/flows/alert_controller.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'model_name = "Qwen38_27B"', 'model_name = "unexpected_model"', 1
            ),
            encoding="utf-8",
        )
        before = self.snapshot()
        with patch.object(installer, "run_regressions") as regressions:
            with self.assertRaisesRegex(
                installer.InstallError, "target_region_check_failed:"
            ):
                installer.install(self.project, self.backups)
        regressions.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_user_confirmed_qwen_controller_is_exact_reviewed_variant(self):
        name = "my_workflows/zeus/flows/alert_controller.py"
        data = (self.project / name).read_bytes()
        self.assertEqual(installer.digest(data), "d24a93c167428804d1ac606f3a034b168592e3fd1e692d6cf1e26b1104329022")
        changed, proof = installer.reviewed_transform(name, data)
        self.assertEqual(proof["validation"], "whole_file_sha256")
        self.assertEqual(proof["state"], "before")
        self.assertTrue(b"model_name = resolve_alert_model(model_name, rc.ENV)" in changed)

    def test_unknown_after_needs_successful_same_project_receipt(self):
        self.add_unrelated_local_changes()
        with patch.object(installer, "run_regressions", return_value=33):
            result = installer.install(self.project, self.backups)
        before = self.snapshot()
        receipt_path = Path(result["backup_dir"]) / "result.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["project_root"] = str(self.base / "another-project")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(
            installer.InstallError, "scoped_after_receipt_missing"
        ):
            installer.install(self.project, self.backups)
        self.assertEqual(self.snapshot(), before)

    def test_receipt_failure_restores_all_modified_sources(self):
        self.add_unrelated_local_changes()
        before = self.snapshot()
        real_write = installer.write_private

        def fail_receipt(path, data):
            if path.name == "result.json":
                raise OSError("synthetic receipt write failure")
            return real_write(path, data)

        with patch.object(installer, "run_regressions", return_value=33):
            with patch.object(installer, "write_private", side_effect=fail_receipt):
                with self.assertRaisesRegex(
                    installer.InstallError, "receipt_write_failed_rolled_back:"
                ):
                    installer.install(self.project, self.backups)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
