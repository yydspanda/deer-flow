"""Explicit activation writes only reviewed settings/marker and never starts services."""

import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = load("topic_enable_test_builder", "build_command.py")
ENABLE = load("topic_enable_test_module", "enable_topic.py")
INSTALLER = types.ModuleType("topic_enable_test_installer")
exec(
    compile(BUILDER.installer_source(), "<topic-installer>", "exec"), INSTALLER.__dict__
)


class EnableTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="topic-enable-test-")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.backups = Path(self.temporary.name) / "backups"
        sources = BUILDER.baseline()
        sources.update(
            {
                name: entry["content"].encode("utf-8")
                for name, entry in BUILDER.contents()["files"].items()
            }
        )
        self.expected = {}
        for name, raw in sources.items():
            if name.startswith(BUILDER.TEST):
                continue
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.expected[name] = INSTALLER.digest(raw)
        self.csv = self.root / "nas/original/rules.csv"
        self.csv.parent.mkdir(parents=True)
        self.csv.write_text("synthetic,fixture\n", encoding="utf-8")
        self.run = b"#!/bin/bash\nexport RULE_REFINE_MONITOR_ENABLED=0\n# Remaining launcher content is preserved.\n"
        (self.root / "run.sh").write_bytes(self.run)
        (self.root / "run.sh").chmod(0o750)
        self.old_config = {
            "config/rule_refine.local.json": {
                "enabled": True,
                "namespace": "old-rule-only",
                "csv_path": "nas/original/rules.csv",
            },
            "config/rule_refine.analysis.local.json": {
                "model": "Pub_DeepSeek_V4_Flash"
            },
            "config/rule_refine.surge.local.json": {
                "context_tokens": 262144,
                "input_fraction": 0.7,
                "timeout_seconds": 300,
                "operator_context": "Synthetic owner context",
            },
        }
        for name, value in self.old_config.items():
            self.write_json(name, value)

    def tearDown(self):
        self.temporary.cleanup()

    def write_json(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def run_enable(self, env):
        return ENABLE.configure(self.root, self.backups, env, self.expected, INSTALLER)

    def assert_existing_config_unchanged(self):
        for name, value in self.old_config.items():
            self.assertEqual(
                json.loads((self.root / name).read_text(encoding="utf-8")), value
            )

    def test_local_reuses_csv_model_budget_without_changing_launcher(self):
        result = self.run_enable({"env_profile": "LOCAL"})
        value = json.loads((self.root / ENABLE.CONFIG).read_text(encoding="utf-8"))
        self.assertTrue(value["enabled"])
        self.assertEqual(value["csv_path"], str(self.csv))
        self.assertEqual(value["namespace"], "topic-v1:local")
        self.assertEqual(value["model"], "Pub_DeepSeek_V4_Flash")
        self.assertEqual(value["input_fraction"], 0.7)
        self.assertEqual(value["timeout_seconds"], 300)
        self.assertEqual((self.root / "run.sh").read_bytes(), self.run)
        self.assertFalse(result["launch_script_updated"])
        self.assertFalse(result["services_started"])
        self.assertEqual(result["zeus_callback_calls"], 0)
        self.assert_existing_config_unchanged()
        self.assertEqual(
            self.run_enable({"env_profile": "LOCAL"})["status"], "already_enabled"
        )

    def test_prd_preserves_launcher_except_exact_monitor_marker(self):
        result = self.run_enable(
            {
                "env_profile": "prd",
                "SEC_TOPIC_CSV_PATH": str(self.csv),
                "RULE_REFINE_MONITOR_ENABLED": "0",
            }
        )
        self.assertTrue(result["launch_script_updated"])
        self.assertTrue(result["current_process_disable_requires_restart"])
        self.assertEqual(
            (self.root / "run.sh").read_bytes(),
            self.run.replace(b"ENABLED=0", b"ENABLED=1"),
        )
        self.assertEqual((self.root / "run.sh").stat().st_mode & 0o777, 0o750)
        self.assertEqual(
            (Path(result["backup_dir"]) / "before/run.sh").read_bytes(), self.run
        )
        self.assertEqual(result["namespace"], "topic-v1:prd")
        self.assert_existing_config_unchanged()
        repeated = self.run_enable({"env_profile": "PRD"})
        self.assertEqual(repeated["status"], "already_enabled")

    def test_prd_requires_explicit_existing_csv_on_first_enable(self):
        for env in (
            {"env_profile": "PRD"},
            {"env_profile": "PRD", "SEC_TOPIC_CSV_PATH": str(self.root / "absent.csv")},
        ):
            with self.subTest(env=env), self.assertRaises(INSTALLER.InstallError):
                self.run_enable(env)
        self.assertFalse((self.root / ENABLE.CONFIG).exists())
        self.assertEqual((self.root / "run.sh").read_bytes(), self.run)
        self.assertFalse(self.backups.exists())

    def test_stg_or_conflicting_profiles_rejected_without_writes(self):
        for env in (
            {"env_profile": "STG"},
            {"env_profile": "PRD", "ENV_PROFILE": "LOCAL"},
        ):
            with (
                self.subTest(env=env),
                self.assertRaisesRegex(
                    INSTALLER.InstallError, "topic_enable_requires_LOCAL_or_PRD"
                ),
            ):
                self.run_enable(env)
        self.assertFalse((self.root / ENABLE.CONFIG).exists())

    def test_existing_namespace_is_preserved(self):
        self.write_json(
            ENABLE.CONFIG,
            {
                "enabled": False,
                "csv_path": str(self.csv),
                "namespace": "synthetic-existing-topic-namespace",
            },
        )
        result = self.run_enable({"env_profile": "LOCAL"})
        self.assertEqual(result["namespace"], "synthetic-existing-topic-namespace")
        before = Path(result["backup_dir"]) / "before" / ENABLE.CONFIG
        self.assertFalse(json.loads(before.read_text(encoding="utf-8"))["enabled"])

    def test_conflicting_old_namespace_or_csv_cannot_be_silently_reused(self):
        cases = (
            {"RULE_REFINE_NAMESPACE": "old-rule-only"},
            {"RULE_REFINE_CSV_PATH": str(self.root / "another.csv")},
            {"RULE_REFINE_MONITOR_ENABLED": "0"},
        )
        for override in cases:
            with (
                self.subTest(override=override),
                self.assertRaisesRegex(
                    INSTALLER.InstallError, "conflicting_environment_override"
                ),
            ):
                self.run_enable({"env_profile": "LOCAL", **override})
        self.assertFalse((self.root / ENABLE.CONFIG).exists())

    def test_matching_explicit_overrides_are_accepted(self):
        result = self.run_enable(
            {
                "env_profile": "LOCAL",
                "RULE_REFINE_MONITOR_ENABLED": "1",
                "RULE_REFINE_NAMESPACE": "topic-v1:local",
                "RULE_REFINE_CSV_PATH": "nas/original/rules.csv",
            }
        )
        self.assertEqual(result["status"], "enabled_for_next_start")

    def test_unknown_environment_override_is_rejected_without_value_disclosure(self):
        with self.assertRaisesRegex(
            INSTALLER.InstallError,
            "unreviewed_rule_refine_environment_override:RULE_REFINE_UNKNOWN",
        ) as caught:
            self.run_enable({"RULE_REFINE_UNKNOWN": "private-value"})
        self.assertNotIn("private-value", str(caught.exception))

    def test_unreviewed_run_marker_is_not_overwritten(self):
        bad = self.run + b"export RULE_REFINE_MONITOR_ENABLED=0\n"
        (self.root / "run.sh").write_bytes(bad)
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "review_run_sh_monitor_marker"
        ):
            self.run_enable({"env_profile": "PRD", "SEC_TOPIC_CSV_PATH": str(self.csv)})
        self.assertEqual((self.root / "run.sh").read_bytes(), bad)
        self.assertFalse((self.root / ENABLE.CONFIG).exists())

    def test_installation_drift_prevents_activation(self):
        name = BUILDER.MODULE + "topic_settings.py"
        (self.root / name).write_bytes(
            (self.root / name).read_bytes() + b"\n# Operator edit.\n"
        )
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "topic_installation_version_mismatch"
        ):
            self.run_enable({"env_profile": "LOCAL"})
        self.assertFalse((self.root / ENABLE.CONFIG).exists())

    def test_only_captured_verified_settings_bytes_are_evaluated(self):
        name = BUILDER.MODULE + "topic_settings.py"
        read = INSTALLER._read
        captured = False

        def change_after_capture(root, relative):
            nonlocal captured
            found = read(root, relative)
            if relative == name and not captured:
                captured = True
                (self.root / name).write_text(
                    'raise RuntimeError("UNVERIFIED_SOURCE_EXECUTED")\n',
                    encoding="utf-8",
                )
            return found

        with patch.object(INSTALLER, "_read", side_effect=change_after_capture):
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "topic_installation_changed_during_enable"
            ):
                self.run_enable({"env_profile": "LOCAL"})
        self.assertFalse((self.root / ENABLE.CONFIG).exists())

    def test_launch_write_failure_rolls_back_new_config(self):
        replace = INSTALLER._replace
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic launcher write failure")
            return replace(*args, **kwargs)

        with patch.object(INSTALLER, "_replace", side_effect=fail_second):
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "topic_enable_failed_rolled_back"
            ):
                self.run_enable(
                    {"env_profile": "PRD", "SEC_TOPIC_CSV_PATH": str(self.csv)}
                )
        self.assertFalse((self.root / ENABLE.CONFIG).exists())
        self.assertEqual((self.root / "run.sh").read_bytes(), self.run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
