"""Offline release-launcher checks; never execute the original service launcher."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "_rule_release_disable_patch", HERE / "patch.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
BUILDER_SPEC = importlib.util.spec_from_file_location(
    "_rule_release_disable_builder", HERE / "build_command.py"
)
builder = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(builder)
ZIP = HERE.parents[2] / "sec_know_model.zip"
EXPECTED_BASE = "125020a4f1bc5934d35bcdea1ed9648fbc78d87244c92519abe915f393d8a8d7"
BACKUP_NAME = "run.sh.before-rule-refine-release-disable"


class ReleaseDisableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ZIP.is_file():
            raise unittest.SkipTest(
                "The private reviewed source ZIP is required for these offline checks"
            )
        with zipfile.ZipFile(ZIP) as archive:
            cls.original = archive.read("run.sh")
        if hashlib.sha256(cls.original).hexdigest() != EXPECTED_BASE:
            raise AssertionError("reviewed run.sh fixture has changed")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "run.sh"
        self.target.write_bytes(self.original)
        self.target.chmod(0o751)
        self.backup = self.root / BACKUP_NAME
        self.local = self.root / "local_run.sh"
        self.local.write_bytes(b"#!/bin/bash\n# Synthetic untouched LOCAL launcher\n")
        self.config = self.root / "config/rule_refine.local.json"
        self.config.parent.mkdir()
        self.config.write_bytes(b'{"enabled":true,"namespace":"synthetic-dev"}\n')

    def test_pin_and_single_insert_preserve_every_original_byte(self):
        self.assertEqual(module.BASE_SHA256, EXPECTED_BASE)
        self.assertIsInstance(module.BLOCK, bytes)
        self.assertTrue(
            module.BLOCK.startswith(b"\n\n# BEGIN RULE_REFINE_RELEASE_DISABLED\n")
        )
        self.assertTrue(module.BLOCK.endswith(b"# END RULE_REFINE_RELEASE_DISABLED\n"))
        self.assertIn(b"export RULE_REFINE_MONITOR_ENABLED=0\n", module.BLOCK)
        first, body = self.original.split(b"\n", 1)
        result = module.patched_bytes(self.original)
        self.assertEqual(result, first + b"\n" + module.BLOCK + body)
        self.assertEqual(result.replace(module.BLOCK, b"", 1), self.original)
        self.assertEqual(result.count(module.BLOCK), 1)

    def test_bytes_transform_is_idempotent(self):
        result = module.patched_bytes(self.original)
        self.assertEqual(module.patched_bytes(result), result)

    def test_unknown_original_or_modified_patched_source_is_rejected(self):
        for source in (
            self.original + b"\n# unrelated business modification\n",
            self.original.replace(b"#!/bin/bash", b"#!/bin/sh", 1),
            module.patched_bytes(self.original) + b"\n# changed after patch\n",
            b"",
        ):
            with self.subTest(source_hash=hashlib.sha256(source).hexdigest()):
                with self.assertRaises(ValueError):
                    module.patched_bytes(source)

    def test_install_has_private_original_backup_and_preserves_other_files(self):
        local_before = self.local.read_bytes()
        config_before = self.config.read_bytes()
        result = module.install(self.root)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["files_changed"], 1)
        self.assertFalse(result["application_code_updated"])
        self.assertTrue(result["launch_script_updated"])
        self.assertEqual(Path(result["backup_file"]), self.backup)
        self.assertEqual(self.backup.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o751)
        self.assertEqual(self.target.read_bytes(), module.patched_bytes(self.original))
        self.assertEqual(self.local.read_bytes(), local_before)
        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(
            {
                str(path.relative_to(self.root))
                for path in self.root.rglob("*")
                if path.is_file()
            },
            {"run.sh", BACKUP_NAME, "local_run.sh", "config/rule_refine.local.json"},
        )

    def test_repeat_does_not_replace_target_or_backup(self):
        module.install(self.root)
        before = self.target.stat().st_mtime_ns, self.backup.stat().st_mtime_ns
        result = module.install(self.root)
        self.assertEqual(result["status"], "already_installed")
        self.assertEqual(result["files_changed"], 0)
        self.assertFalse(result["launch_script_updated"])
        self.assertEqual(
            (self.target.stat().st_mtime_ns, self.backup.stat().st_mtime_ns), before
        )
        self.assertEqual(self.target.read_bytes().count(module.BLOCK), 1)

    def test_unknown_release_source_never_creates_backup_or_modifies_source(self):
        modified = self.original + b"\n# another release change\n"
        self.target.write_bytes(modified)
        with self.assertRaises(ValueError):
            module.install(self.root)
        self.assertEqual(self.target.read_bytes(), modified)
        self.assertFalse(self.backup.exists())

    def test_conflicting_backup_is_preserved_and_stops_before_write(self):
        self.backup.write_bytes(b"do not overwrite another backup")
        with self.assertRaises(ValueError):
            module.install(self.root)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.backup.read_bytes(), b"do not overwrite another backup")

    def test_matching_original_backup_can_be_reused(self):
        self.backup.write_bytes(self.original)
        self.backup.chmod(0o600)
        result = module.install(self.root)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(self.backup.read_bytes(), self.original)

    def test_source_symlink_is_rejected_without_touching_destination(self):
        actual = self.root / "actual-run.sh"
        self.target.rename(actual)
        self.target.symlink_to(actual.name)
        with self.assertRaises((ValueError, OSError)):
            module.install(self.root)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(actual.read_bytes(), self.original)
        self.assertFalse(self.backup.exists())

    def test_backup_symlink_is_rejected_without_touching_destination(self):
        actual = self.root / "actual-backup"
        actual.write_bytes(self.original)
        self.backup.symlink_to(actual.name)
        with self.assertRaises((ValueError, OSError)):
            module.install(self.root)
        self.assertTrue(self.backup.is_symlink())
        self.assertEqual(actual.read_bytes(), self.original)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_nonregular_source_is_rejected(self):
        self.target.unlink()
        self.target.mkdir()
        with self.assertRaises((ValueError, OSError)):
            module.install(self.root)
        self.assertTrue(self.target.is_dir())
        self.assertFalse(self.backup.exists())

    def test_hardlinked_source_is_rejected_without_touching_alias(self):
        alias = self.root / "alias-run.sh"
        os.link(self.target, alias)
        with self.assertRaises(ValueError):
            module.install(self.root)
        self.assertEqual(alias.read_bytes(), self.original)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.backup.exists())

    def test_atomic_replace_failure_preserves_original_launcher(self):
        original_replace = os.replace

        def reject_launcher_replace(source, destination, *args, **kwargs):
            if Path(destination) == self.target:
                raise OSError("synthetic atomic replace failure")
            return original_replace(source, destination, *args, **kwargs)

        with patch.object(module.os, "replace", side_effect=reject_launcher_replace):
            with self.assertRaises(OSError):
                module.install(self.root)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o751)
        if self.backup.exists():
            self.assertEqual(self.backup.read_bytes(), self.original)

    def test_export_overrides_inherited_enable_and_reaches_child_process(self):
        # Only run the inserted prefix: the original launcher contains real cleanup commands.
        prefix = b"#!/bin/bash\n" + module.BLOCK
        command = prefix + b"bash -c 'printf %s \"$RULE_REFINE_MONITOR_ENABLED\"'\n"
        result = subprocess.run(
            ["bash"],
            input=command,
            env={**os.environ, "RULE_REFINE_MONITOR_ENABLED": "1"},
            capture_output=True,
            check=True,
            timeout=5,
        )
        self.assertEqual(result.stdout, b"0")

    def test_complete_patched_launcher_passes_syntax_only(self):
        result = subprocess.run(
            ["bash", "-n"],
            input=module.patched_bytes(self.original),
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(
            result.returncode, 0, result.stderr.decode("utf-8", errors="replace")
        )

    def test_copy_command_embeds_current_source_exactly_and_parses(self):
        source = (HERE / "patch.py").read_text(encoding="utf-8")
        command = builder.command(source)
        opening = "<<'SOC_RELEASE_DISABLE_PYTHON'\n"
        embedded = command.split(opening, 1)[1].rsplit(
            "SOC_RELEASE_DISABLE_PYTHON\n", 1
        )[0]
        self.assertEqual(embedded, source)
        result = subprocess.run(
            ["bash", "-n"],
            input=command.encode("utf-8"),
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(
            result.returncode, 0, result.stderr.decode("utf-8", errors="replace")
        )

    def test_full_copy_command_only_patches_temporary_project(self):
        project = self.root / "sec_know_model"
        project.mkdir()
        target = project / "run.sh"
        target.write_bytes(self.original)
        target.chmod(0o750)
        source = (HERE / "patch.py").read_text(encoding="utf-8")
        result = subprocess.run(
            ["bash"],
            input=builder.command(source).encode("utf-8"),
            env={
                **os.environ,
                "HOME": str(self.root),
                "RULE_REFINE_MONITOR_ENABLED": "1",
            },
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(
            result.returncode, 0, result.stderr.decode("utf-8", errors="replace")
        )
        stdout = result.stdout.decode("utf-8")
        report, _ = json.JSONDecoder().raw_decode(stdout[stdout.index("{") :])
        self.assertEqual(report["status"], "installed")
        self.assertFalse(report["services_started"])
        self.assertFalse(report["automatic_restart"])
        self.assertFalse(report["running_production_verified"])
        self.assertEqual(target.read_bytes(), module.patched_bytes(self.original))
        self.assertEqual((project / BACKUP_NAME).read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o750)

    def test_full_copy_command_rejects_unreviewed_release_without_modification(self):
        project = self.root / "sec_know_model"
        project.mkdir()
        target = project / "run.sh"
        unknown = self.original + b"\n# new release-specific change\n"
        target.write_bytes(unknown)
        source = (HERE / "patch.py").read_text(encoding="utf-8")
        result = subprocess.run(
            ["bash"],
            input=builder.command(source).encode("utf-8"),
            env={**os.environ, "HOME": str(self.root)},
            capture_output=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        stdout = result.stdout.decode("utf-8")
        report, _ = json.JSONDecoder().raw_decode(stdout[stdout.index("{") :])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "unreviewed_run_sh")
        self.assertEqual(target.read_bytes(), unknown)
        self.assertFalse((project / BACKUP_NAME).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
