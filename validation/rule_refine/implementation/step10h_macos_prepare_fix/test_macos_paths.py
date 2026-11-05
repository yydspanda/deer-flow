"""Reproduce the old Mac path-alias failure, then check the exact minimal fix."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import build_command as builder

# Frozen tests provide only synthetic files; they never import the legacy app.
sys.path.insert(0, str(builder.PREVIOUS))
fixtures = builder.load("macos_prepare_fixtures", builder.PREVIOUS / "test_prepare.py")
BUNDLE = builder.make_bundle()
ORIGINAL = builder.load("macos_prepare_original", builder.PREVIOUS / "prepare.py")
FIXED = types.ModuleType("macos_prepare_fixed")
exec(compile(BUNDLE["prepare"], "<fixed-prepare>", "exec"), FIXED.__dict__)


class MacPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="macos-prepare-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "app"
        self.root.mkdir()
        self.backups = self.base / "backups"
        fixtures.populate(self.root)
        self.real_tmp = self.base / "private-var"
        self.real_tmp.mkdir()
        self.alias_tmp = self.base / "var"
        self.alias_tmp.symlink_to(self.real_tmp, target_is_directory=True)
        original_temp_directory = tempfile.TemporaryDirectory

        def aliased_temporary(*args, **kwargs):
            kwargs["dir"] = str(self.alias_tmp)
            return original_temp_directory(*args, **kwargs)

        self.aliased_temporary = aliased_temporary

    def snapshot(self):
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")
        }

    def prepare(self, module):
        with (
            patch.object(
                tempfile, "TemporaryDirectory", side_effect=self.aliased_temporary
            ),
            patch.object(fixtures.INSTALLER, "run_checks", return_value=454),
        ):
            return module.prepare(
                self.root,
                self.backups,
                {},
                BUNDLE,
                fixtures.RECONSTRUCT,
                fixtures.INSTALLER,
            )

    def test_original_script_reproduces_same_error_before_writes(self):
        before = self.snapshot()
        with self.assertRaisesRegex(
            fixtures.INSTALLER.InstallError, "production_settings_check_failed"
        ):
            self.prepare(ORIGINAL)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.backups.exists())

    def test_fixed_alias_path_prepares_and_is_idempotent(self):
        result = self.prepare(FIXED)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["files_changed"], 5)
        self.assertFalse(result["services_started"])
        self.assertEqual(self.prepare(FIXED)["status"], "already_prepared")

    def test_invalid_production_config_is_still_rejected(self):
        path = self.root / FIXED.PRD_CONFIG
        path.write_text(json.dumps({"namespace": "bad namespace\n"}), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(
            fixtures.INSTALLER.InstallError, "production_settings_check_failed"
        ):
            self.prepare(FIXED)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.backups.exists())

    def test_all_application_payload_and_checks_remain_identical(self):
        old = builder.PREV.make_bundle()
        self.assertEqual(
            {k: v for k, v in BUNDLE.items() if k != "prepare"},
            {k: v for k, v in old.items() if k != "prepare"},
        )
        fixed = BUNDLE["prepare"].replace(
            "            # macOS /var and /private/var can denote the same directory.\n"
            "            stage = Path(directory).resolve()\n",
            "            stage = Path(directory)\n",
        )
        self.assertEqual(fixed, old["prepare"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
