"""Offline installer transaction checks; no application or network calls."""

import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "topic_tree_builder", HERE / "build_command.py"
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
INSTALLER = types.ModuleType("topic_tree_install_test")
exec(compile(BUILDER.installer_source(), "<installer>", "exec"), INSTALLER.__dict__)
OLD = BUILDER.MODULE + "topic_ai.py"
NEW = BUILDER.MODULE + "topic_tree.py"
TEST = BUILDER.TEST + "test_topic_tree_fixture.py"
TEST_SOURCE = """import unittest
class Fixture(unittest.TestCase):
    def test_offline(self):
        self.assertEqual(2 + 2, 4)
if __name__ == "__main__":
    unittest.main()
"""


def packet(before):
    after = before + b"\n# Offline installation fixture.\n"
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BUILDER.BASE.BASELINE_SHA256,
        "topic_contract_version": BUILDER.PREV.PREV.PREV.CONTRACT,
        "topic_json_contract_version": BUILDER.PREV.PREV.CONTRACT,
        "topic_expression_contract_version": BUILDER.PREV.CONTRACT,
        "topic_tree_contract_version": BUILDER.CONTRACT,
        "files": {
            OLD: {
                "old_sha256": BUILDER.BASE.sha(before),
                "new_sha256": BUILDER.BASE.sha(after),
                "content": after.decode("utf-8"),
            },
            NEW: {
                "old_sha256": None,
                "new_sha256": BUILDER.BASE.sha(b'CONTRACT = "fixture"\n'),
                "content": 'CONTRACT = "fixture"\n',
            },
        },
        "dependencies": {},
        "test_files": {TEST: TEST_SOURCE},
        "tests": [TEST],
    }


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="topic-tree-install-test-")
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "backups"
        self.original = BUILDER.baseline()[OLD]
        target = self.root / OLD
        target.parent.mkdir(parents=True)
        target.write_bytes(self.original)
        target.chmod(0o640)
        self.packet = packet(self.original)
        self.sentinels = {
            "run.sh": b"export RULE_REFINE_MONITOR_ENABLED=0\n",
            "config/rule_refine.topic.json": b'{"enabled":false}\n',
            "config/celery_task.prd.conf": b"Pub concurrency remains 27\n",
            "old-results/generate.failed.json": b'{"error_code":"duplicate_json_key"}\n',
        }
        for name, raw in self.sentinels.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_unchanged(self):
        for name, raw in self.sentinels.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_install_backup_permissions_and_idempotence(self):
        result = INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual(result["check"], "rule_refine_topic_tree_install")
        self.assertEqual(result["topic_tree_contract_version"], BUILDER.CONTRACT)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["tests_passed"], 1)
        self.assertEqual(result["files_changed"], 2)
        self.assertFalse(result["configuration_files_modified"])
        self.assertFalse(result["automatic_restart"])
        self.assertFalse(result["monitor_enabled_by_install"])
        self.assertEqual(
            (Path(result["backup_dir"]) / "before" / OLD).read_bytes(), self.original
        )
        self.assertEqual((self.root / OLD).stat().st_mode & 0o777, 0o640)
        repeated = INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual(repeated["status"], "already_installed")
        self.assertEqual(repeated["files_changed"], 0)
        self.assert_unchanged()

    def test_unknown_source_never_overwritten(self):
        changed = self.original + b"\n# Operator changes.\n"
        (self.root / OLD).write_bytes(changed)
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "unknown_project_file_version"
            ):
                INSTALLER.apply(self.root, self.data, self.packet)
            checks.assert_not_called()
        self.assertEqual((self.root / OLD).read_bytes(), changed)
        self.assertFalse(self.data.exists())
        self.assert_unchanged()

    def test_failed_regression_never_changes_source(self):
        self.packet["test_files"][TEST] = TEST_SOURCE.replace("2 + 2, 4", "2 + 2, 5")
        with self.assertRaisesRegex(INSTALLER.InstallError, "isolated_tests_failed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD).read_bytes(), self.original)
        self.assertFalse((self.root / NEW).exists())
        self.assertFalse(self.data.exists())

    def test_network_is_blocked_in_offline_tests(self):
        self.packet["test_files"][TEST] = TEST_SOURCE.replace(
            "self.assertEqual(2 + 2, 4)",
            'import socket; socket.getaddrinfo("synthetic.invalid", 443)',
        )
        with self.assertRaisesRegex(INSTALLER.InstallError, "isolated_tests_failed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD).read_bytes(), self.original)

    def test_failed_write_rolls_back(self):
        original = INSTALLER._replace
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic write failure")
            return original(*args, **kwargs)

        with patch.object(INSTALLER, "_replace", side_effect=fail_second):
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "installation_failed_rolled_back"
            ):
                INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD).read_bytes(), self.original)
        self.assertFalse((self.root / NEW).exists())
        self.assert_unchanged()
        backups = list(self.data.glob("topic-tree-install-*"))
        self.assertEqual(len(backups), 1)
        rollback = json.loads(
            (backups[0] / "rollback.json").read_text(encoding="utf-8")
        )
        self.assertFalse(rollback["retained_concurrent_or_unavailable_files"])

    def test_wrong_parser_contract_is_rejected(self):
        self.packet["topic_tree_contract_version"] = "unknown"
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "unexpected_topic_tree_contract"
        ):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assertFalse(self.data.exists())

    def test_baseline_is_installed_step10b_not_old_zip(self):
        before = BUILDER.baseline()
        for name, entry in BUILDER.PREV.contents()["files"].items():
            self.assertEqual(BUILDER.BASE.sha(before[name]), entry["new_sha256"])
        self.assertNotIn(NEW, before)

    def test_cached_baseline_is_not_mutable_by_callers(self):
        before = BUILDER.baseline()
        expected = before[OLD]
        before[OLD] = b"caller-only change"
        self.assertEqual(BUILDER.baseline()[OLD], expected)

    def test_frozen_previous_commands_match_their_published_manifests(self):
        for directory, install_name in (
            (BUILDER.ORIGINAL, "INSTALL-TOPIC-FILTER.sh"),
            (BUILDER.PREVIOUS, "INSTALL-TOPIC-EXPRESSION-FIX.sh"),
        ):
            manifest = json.loads(
                (directory / "manifest.json").read_text(encoding="utf-8")
            )
            for filename, key in (
                (install_name, "command_sha256"),
                ("ENABLE-TOPIC-FILTER.sh", "enable_command_sha256"),
            ):
                self.assertEqual(
                    BUILDER.BASE.sha((directory / filename).read_bytes()), manifest[key]
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
