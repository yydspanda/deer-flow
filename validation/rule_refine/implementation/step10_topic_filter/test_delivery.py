"""Offline transaction checks for topic delivery; no business application imports."""

import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "topic_package_builder", HERE / "build_command.py"
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
INSTALLER = types.ModuleType("topic_install_test")
exec(
    compile(BUILDER.installer_source(), "<topic-installer>", "exec"), INSTALLER.__dict__
)

OLD_FILE = BUILDER.CONFIG
NEW_FILE = BUILDER.MODULE + "topic_install_fixture.py"
TEST_FILE = BUILDER.TEST + "test_topic_install_fixture.py"
TEST_SOURCE = """import unittest
class Fixture(unittest.TestCase):
    def test_offline(self):
        self.assertEqual(2 + 2, 4)
if __name__ == "__main__":
    unittest.main()
"""


def packet(before):
    after = before + b"\n# Synthetic installation fixture only.\n"
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BUILDER.BASE.BASELINE_SHA256,
        "topic_contract_version": BUILDER.CONTRACT,
        "files": {
            OLD_FILE: {
                "old_sha256": BUILDER.BASE.sha(before),
                "new_sha256": BUILDER.BASE.sha(after),
                "content": after.decode("utf-8"),
            },
            NEW_FILE: {
                "old_sha256": None,
                "new_sha256": BUILDER.BASE.sha(b'CONTRACT = "fixture"\n'),
                "content": 'CONTRACT = "fixture"\n',
            },
        },
        "dependencies": {},
        "test_files": {TEST_FILE: TEST_SOURCE},
        "tests": [TEST_FILE],
    }


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="topic-delivery-test-")
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "results"
        self.original = BUILDER.baseline()[OLD_FILE]
        target = self.root / OLD_FILE
        target.parent.mkdir(parents=True)
        target.write_bytes(self.original)
        target.chmod(0o640)
        self.packet = packet(self.original)
        self.sentinels = {
            "run.sh": b"export RULE_REFINE_MONITOR_ENABLED=0\n",
            "config/celery_task.prd.conf": b"production worker remains at 27\n",
            "config/rule_refine.topic.json": b'{"enabled":false}\n',
            "saved/REPORT.md": b"previous answer remains unchanged\n",
        }
        for name, raw in self.sentinels.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_sentinels(self):
        for name, raw in self.sentinels.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_install_backup_idempotence_and_configuration_preservation(self):
        result = INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["tests_passed"], 1)
        self.assertEqual(result["files_changed"], 2)
        self.assertEqual(result["check"], "rule_refine_topic_filter_install")
        self.assertFalse(result["automatic_restart"])
        self.assertFalse(result["monitor_enabled_by_install"])
        self.assertFalse(result["launch_script_updated"])
        self.assertTrue(result["configuration_files_modified"])
        self.assertEqual(
            (Path(result["backup_dir"]) / "before" / OLD_FILE).read_bytes(),
            self.original,
        )
        self.assertEqual((self.root / OLD_FILE).stat().st_mode & 0o777, 0o640)
        self.assert_sentinels()
        repeated = INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual(repeated["status"], "already_installed")
        self.assertEqual(repeated["files_changed"], 0)
        self.assertFalse(repeated["configuration_files_modified"])
        self.assert_sentinels()

    def test_unknown_source_stops_before_tests_or_backup(self):
        changed = self.original + b"\n# Operator edit.\n"
        (self.root / OLD_FILE).write_bytes(changed)
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "unknown_project_file_version"
            ):
                INSTALLER.apply(self.root, self.data, self.packet)
            checks.assert_not_called()
        self.assertEqual((self.root / OLD_FILE).read_bytes(), changed)
        self.assertFalse((self.root / NEW_FILE).exists())
        self.assertFalse(self.data.exists())

    def test_failed_regression_preserves_original_sources(self):
        self.packet["test_files"][TEST_FILE] = TEST_SOURCE.replace(
            "2 + 2, 4", "2 + 2, 5"
        )
        with self.assertRaisesRegex(INSTALLER.InstallError, "isolated_tests_failed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD_FILE).read_bytes(), self.original)
        self.assertFalse((self.root / NEW_FILE).exists())
        self.assertFalse(self.data.exists())

    def test_network_operation_is_blocked_in_install_tests(self):
        self.packet["test_files"][TEST_FILE] = TEST_SOURCE.replace(
            "self.assertEqual(2 + 2, 4)",
            'import socket; socket.getaddrinfo("synthetic.invalid", 443)',
        )
        with self.assertRaisesRegex(INSTALLER.InstallError, "isolated_tests_failed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD_FILE).read_bytes(), self.original)

    def test_write_failure_rolls_back_config_and_new_module(self):
        original_replace = INSTALLER._replace
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic write failure")
            return original_replace(*args, **kwargs)

        with patch.object(INSTALLER, "_replace", side_effect=fail_second):
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "installation_failed_rolled_back"
            ):
                INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD_FILE).read_bytes(), self.original)
        self.assertFalse((self.root / NEW_FILE).exists())
        self.assert_sentinels()
        backups = list(self.data.glob("topic-filter-install-*"))
        self.assertEqual(len(backups), 1)
        rollback = json.loads(
            (backups[0] / "rollback.json").read_text(encoding="utf-8")
        )
        self.assertFalse(rollback["retained_concurrent_or_unavailable_files"])

    def test_launcher_and_topic_runtime_config_cannot_enter_install_payload(self):
        for name in ("run.sh", "config/rule_refine.topic.json", "util/root_config.py"):
            candidate = packet(self.original)
            candidate["files"][name] = candidate["files"].pop(NEW_FILE)
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    INSTALLER.InstallError, "unexpected_operator_patch_path"
                ),
            ):
                INSTALLER.apply(self.root, self.data, candidate)
        self.assert_sentinels()

    def test_symlink_target_is_rejected(self):
        target = self.root / NEW_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(self.root / "saved/REPORT.md")
        with self.assertRaisesRegex(INSTALLER.InstallError, "symlink_not_allowed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assert_sentinels()

    def test_wrong_topic_contract_is_rejected(self):
        self.packet["topic_contract_version"] = "previous-rule-only-contract"
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "unexpected_topic_contract"
        ):
            INSTALLER.apply(self.root, self.data, self.packet)

    def test_baseline_contains_latest_complete_output_not_previous_payload(self):
        installed = BUILDER.baseline()
        for name, entry in BUILDER.PREV.contents()["files"].items():
            self.assertEqual(BUILDER.BASE.sha(installed[name]), entry["new_sha256"])
        self.assertNotIn(BUILDER.COMPACT.INLINE_OMISSION, installed)

    def test_generated_install_is_separate_from_live_manual_command(self):
        packet = BUILDER.contents()
        command = BUILDER.COMPACT.packtext(
            BUILDER.COMPACT.make_bundle(
                packet, BUILDER.baseline(), BUILDER.installer_source()
            )
        )
        self.assertNotIn("--live", command)
        self.assertNotIn("CHECK-TOPIC-REAL-CSV", command)
        self.assertFalse(
            any(name.endswith("manual_runner.py") for name in packet["files"])
        )
        manual = BUILDER.load(
            "topic_manual_test_builder", HERE / "build_manual.py"
        ).make_command()
        self.assertIn("最多2次模型请求和1次真实ZEUS小规则提交", manual)
        self.assertIn(
            'sys.modules["runner"].main(["--live"], tool_hashes=tool_hashes)', manual
        )
        self.assertIn("${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}", manual)
        self.assertNotIn("env env_profile=LOCAL", manual)


if __name__ == "__main__":
    unittest.main(verbosity=2)
