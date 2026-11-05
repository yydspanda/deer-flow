"""Offline install transaction checks; no application import or network calls."""

import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "operator_package_builder", HERE / "build_command.py"
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
INSTALLER = types.ModuleType("operator_install_test")
exec(
    compile(BUILDER.installer_source(), "<operator-installer>", "exec"),
    INSTALLER.__dict__,
)

OLD_FILE = BUILDER.MODULE + "contracts.py"
NEW_FILE = BUILDER.MODULE + "operators.py"
TEST_FILE = BUILDER.TEST + "test_operator_install_fixture.py"
TEST_SOURCE = """import unittest
class Fixture(unittest.TestCase):
    def test_offline(self):
        self.assertEqual(2 + 2, 4)
if __name__ == "__main__":
    unittest.main()
"""


def packet(before):
    after = before + b"\n# Isolated installation fixture only.\n"
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BUILDER.BASELINE_SHA256,
        "files": {
            OLD_FILE: {
                "old_sha256": BUILDER.sha(before),
                "new_sha256": BUILDER.sha(after),
                "content": after.decode("utf-8"),
            },
            NEW_FILE: {
                "old_sha256": None,
                "new_sha256": BUILDER.sha(b'CONTRACT = "fixture"\n'),
                "content": 'CONTRACT = "fixture"\n',
            },
        },
        "dependencies": {},
        "test_files": {TEST_FILE: TEST_SOURCE},
        "tests": [TEST_FILE],
    }


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="operator-delivery-test-")
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "results"
        self.original = BUILDER.baseline()[OLD_FILE]
        path = self.root / OLD_FILE
        path.parent.mkdir(parents=True)
        path.write_bytes(self.original)
        path.chmod(0o640)
        self.packet = packet(self.original)
        self.sentinels = {
            "run.sh": b"export RULE_REFINE_MONITOR_ENABLED=0\n",
            "config/celery_task.prd.conf": b"production worker remains at 27\n",
            "config/rule_refine.surge.local.json": b'{"enabled":false}\n',
            "saved/REPORT.md": b"previous model answer is unchanged\n",
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
        self.assertEqual(result["check"], "rule_refine_operator_contract_install")
        self.assertFalse(result["automatic_restart"])
        self.assertFalse(result["configuration_files_modified"])
        self.assertEqual(
            (Path(result["backup_dir"]) / "before" / OLD_FILE).read_bytes(),
            self.original,
        )
        self.assertEqual((self.root / OLD_FILE).stat().st_mode & 0o777, 0o640)
        self.assert_sentinels()
        repeated = INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual(repeated["status"], "already_installed")
        self.assertEqual(repeated["files_changed"], 0)
        self.assert_sentinels()

    def test_changed_production_source_stops_before_tests_and_writes(self):
        (self.root / OLD_FILE).write_bytes(self.original + b"\n# operator edit\n")
        with patch.object(INSTALLER, "run_checks") as checks:
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "unknown_project_file_version"
            ):
                INSTALLER.apply(self.root, self.data, self.packet)
            checks.assert_not_called()
        self.assertFalse((self.root / NEW_FILE).exists())
        self.assertFalse(self.data.exists())

    def test_failed_regression_leaves_original_sources(self):
        self.packet["test_files"][TEST_FILE] = TEST_SOURCE.replace(
            "2 + 2, 4", "2 + 2, 5"
        )
        with self.assertRaisesRegex(INSTALLER.InstallError, "isolated_tests_failed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD_FILE).read_bytes(), self.original)
        self.assertFalse((self.root / NEW_FILE).exists())
        self.assertFalse(self.data.exists())

    def test_network_operation_is_blocked_inside_install_checks(self):
        self.packet["test_files"][TEST_FILE] = TEST_SOURCE.replace(
            "self.assertEqual(2 + 2, 4)",
            'import socket; socket.getaddrinfo("synthetic.invalid", 443)',
        )
        with self.assertRaisesRegex(INSTALLER.InstallError, "isolated_tests_failed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assertEqual((self.root / OLD_FILE).read_bytes(), self.original)

    def test_write_failure_rolls_back_old_and_new_sources(self):
        original_replace = INSTALLER._replace
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
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
        backups = list(self.data.glob("operator-contract-install-*"))
        self.assertEqual(len(backups), 1)
        result = json.loads((backups[0] / "rollback.json").read_text(encoding="utf-8"))
        self.assertFalse(result["retained_concurrent_or_unavailable_files"])

    def test_launcher_or_configuration_cannot_enter_patch(self):
        self.packet["files"]["run.sh"] = self.packet["files"].pop(NEW_FILE)
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "unexpected_operator_patch_path"
        ):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assert_sentinels()

    def test_symlink_target_is_rejected(self):
        target = self.root / NEW_FILE
        target.symlink_to(self.root / "saved/REPORT.md")
        with self.assertRaisesRegex(INSTALLER.InstallError, "symlink_not_allowed"):
            INSTALLER.apply(self.root, self.data, self.packet)
        self.assert_sentinels()

    def test_generated_command_has_explicit_root_and_no_launcher(self):
        source = BUILDER.runtime_source(self.packet)
        self.assertIn("installer.apply(Path.cwd(), data_root", source)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("subprocess.run", source)
        compile(source, "<embedded-command>", "exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
