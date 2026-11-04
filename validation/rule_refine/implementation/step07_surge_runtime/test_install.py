"""Installer tests use small synthetic files and never load the business app."""

import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "surge_runtime_installer_tests", HERE / "install.py"
)
install = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = install
spec.loader.exec_module(install)

ENTRY = """import unittest
from pathlib import Path
root = Path(__file__).resolve().parents[1]
class IsolatedTest(unittest.TestCase):
    def test_packaged_sources(self):
        self.assertEqual((root / "pkg/condition_values.py").read_text(), "VALUE = 'new comparator'\\n")
        self.assertEqual((root / "pkg/analysis.py").read_text(), "VALUE = 'new analysis'\\n")
        self.assertEqual((root / "pkg/contracts.py").read_text(), "CONTRACT = 'unchanged'\\n")
if __name__ == '__main__':
    unittest.main()
"""


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.evidence = self.base / "numeric-replay-188ctea2"
        self.evidence.mkdir()
        self.summary = {**install.EVIDENCE_EXPECTED, **install.EVIDENCE_HASHES}
        self.write_evidence()
        self.originals = {
            "pkg/condition_values.py": None,
            "pkg/filter_evaluation.py": b"VALUE = 'old evaluator'\n",
            "pkg/analysis.py": b"VALUE = 'old analysis'\n",
        }
        new = {
            "pkg/condition_values.py": "VALUE = 'new comparator'\n",
            "pkg/filter_evaluation.py": "VALUE = 'new evaluator'\n",
            "pkg/analysis.py": "VALUE = 'new analysis'\n",
        }
        self.packet = {
            "files": {
                name: {
                    "old_sha256": install.digest(old) if old is not None else None,
                    "new_sha256": install.digest(new[name].encode()),
                    "content": new[name],
                }
                for name, old in self.originals.items()
            },
            "dependencies": {
                "pkg/contracts.py": install.digest(b"CONTRACT = 'unchanged'\n")
            },
            "test_files": {"test/test_entry.py": ENTRY},
            "tests": ["test/test_entry.py"],
            "evidence_expectations": dict(install.EVIDENCE_HASHES),
        }
        for name, raw in self.originals.items():
            if raw is not None:
                self.write(name, raw)
        self.write("pkg/contracts.py", b"CONTRACT = 'unchanged'\n")
        self.write("nas/data/untouched.sqlite", b"synthetic opaque data")
        self.write("other.py", b"unrelated = True\n")

    def write(self, name, raw):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def write_evidence(self):
        (self.evidence / "summary.json").write_text(
            json.dumps(self.summary), encoding="utf-8"
        )

    def apply(self):
        return install.apply(self.root, self.evidence, self.packet)

    def assert_originals(self):
        for name, raw in self.originals.items():
            if raw is None:
                self.assertFalse((self.root / name).exists())
            else:
                self.assertEqual((self.root / name).read_bytes(), raw)

    def test_success_tests_real_staged_tree_and_preserves_unrelated_files(self):
        other = self.base / "other-project"
        other.mkdir()
        (other / "analysis.py").write_bytes(b"different project")
        result = self.apply()
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["tests_passed"], 1)
        self.assertEqual(result["files_changed"], 3)
        self.assertFalse(result["automatic_restart"])
        for name, entry in self.packet["files"].items():
            self.assertEqual((self.root / name).read_text(), entry["content"])
        self.assertFalse((self.root / "test/test_entry.py").exists())
        self.assertEqual(
            (self.root / "nas/data/untouched.sqlite").read_bytes(),
            b"synthetic opaque data",
        )
        self.assertEqual((self.root / "other.py").read_bytes(), b"unrelated = True\n")
        self.assertEqual((other / "analysis.py").read_bytes(), b"different project")
        backup = Path(result["backup_dir"])
        self.assertEqual(
            (backup / "before/pkg/analysis.py").read_bytes(),
            self.originals["pkg/analysis.py"],
        )
        self.assertFalse((backup / "before/nas").exists())
        self.assertFalse((backup / "before/pkg/condition_values.py").exists())

    def test_repeat_does_not_rewrite_or_create_new_backup(self):
        with patch.object(install, "run_checks", return_value=7):
            first = self.apply()
            identities = {
                name: (self.root / name).stat().st_ino for name in self.packet["files"]
            }
            second = self.apply()
        self.assertEqual(second["status"], "already_installed")
        self.assertEqual(second["files_changed"], 0)
        self.assertFalse(second["application_code_updated"])
        self.assertNotIn("backup_dir", second)
        self.assertTrue(Path(first["backup_dir"]).is_dir())
        self.assertEqual(
            identities,
            {name: (self.root / name).stat().st_ino for name in self.packet["files"]},
        )

    def test_partial_install_resumes_mixed_old_and_new(self):
        self.write(
            "pkg/condition_values.py",
            self.packet["files"]["pkg/condition_values.py"]["content"].encode(),
        )
        self.write(
            "pkg/filter_evaluation.py",
            self.packet["files"]["pkg/filter_evaluation.py"]["content"].encode(),
        )
        before = (self.root / "pkg/condition_values.py").stat().st_ino
        with patch.object(install, "run_checks", return_value=1):
            result = self.apply()
        self.assertEqual(result["files_changed"], 1)
        self.assertEqual((self.root / "pkg/condition_values.py").stat().st_ino, before)

    def test_unknown_edit_rejected_before_testing(self):
        self.write("pkg/analysis.py", b"unreviewed change\n")
        with patch.object(install, "run_checks") as checks:
            with self.assertRaisesRegex(
                install.InstallError, "unknown_project_file_version"
            ):
                self.apply()
        checks.assert_not_called()
        self.assertFalse((self.root / "pkg/condition_values.py").exists())
        self.assertEqual(
            (self.root / "pkg/analysis.py").read_bytes(), b"unreviewed change\n"
        )

    def test_conflicting_new_file_is_not_overwritten(self):
        self.write("pkg/condition_values.py", b"existing unrelated file\n")
        with self.assertRaisesRegex(
            install.InstallError, "unknown_project_file_version"
        ):
            self.apply()
        self.assertEqual(
            (self.root / "pkg/condition_values.py").read_bytes(),
            b"existing unrelated file\n",
        )

    def test_dependency_edit_rejected(self):
        self.write("pkg/contracts.py", b"different contract\n")
        with self.assertRaisesRegex(
            install.InstallError, "dependency_version_mismatch"
        ):
            self.apply()
        self.assert_originals()

    def test_version_errors_report_only_manifest_path_and_hash_without_content(self):
        private_source = b"synthetic_PRIVATE_SOURCE_DO_NOT_OUTPUT\n"
        for relative, code in (
            ("pkg/analysis.py", "unknown_project_file_version"),
            ("pkg/contracts.py", "dependency_version_mismatch"),
        ):
            original = (self.root / relative).read_bytes()
            try:
                with self.subTest(relative=relative):
                    self.write(relative, private_source)
                    with (
                        patch("sys.stdout", new_callable=io.StringIO) as stdout,
                        patch("sys.stderr", new_callable=io.StringIO) as stderr,
                    ):
                        with self.assertRaises(install.InstallError) as caught:
                            self.apply()
                    error = caught.exception
                    self.assertEqual(str(error), code)
                    self.assertEqual(error.args, (code,))
                    self.assertEqual(error.relative_file, relative)
                    self.assertEqual(
                        error.actual_sha256, install.digest(private_source)
                    )
                    diagnostic = json.dumps(
                        {
                            "error": str(error),
                            "file": error.relative_file,
                            "actual_sha256": error.actual_sha256,
                        }
                    )
                    self.assertNotIn(private_source.decode().strip(), diagnostic)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(stderr.getvalue(), "")
            finally:
                self.write(relative, original)

    def test_missing_versioned_file_reports_null_actual_hash(self):
        for relative, code in (
            ("pkg/analysis.py", "unknown_project_file_version"),
            ("pkg/contracts.py", "dependency_version_mismatch"),
        ):
            path = self.root / relative
            original = path.read_bytes()
            try:
                with self.subTest(relative=relative):
                    path.unlink()
                    with self.assertRaises(install.InstallError) as caught:
                        self.apply()
                    self.assertEqual(str(caught.exception), code)
                    self.assertEqual(caught.exception.relative_file, relative)
                    self.assertIsNone(caught.exception.actual_sha256)
            finally:
                self.write(relative, original)

    def test_failed_isolated_tests_do_not_install(self):
        self.packet["test_files"]["test/test_entry.py"] = ENTRY.replace(
            "'new analysis'", "'deliberately wrong'"
        )
        with self.assertRaisesRegex(install.InstallError, "isolated_tests_failed"):
            self.apply()
        self.assert_originals()

    def test_exception_rolls_back_written_existing_and_new_files(self):
        original_replace = install._replace
        order = []

        def fail_analysis(root, relative, raw, expected, mode, committed):
            order.append(relative)
            if relative == "pkg/analysis.py":
                raise OSError("synthetic write failure")
            return original_replace(root, relative, raw, expected, mode, committed)

        with (
            patch.object(install, "run_checks", return_value=1),
            patch.object(install, "_replace", side_effect=fail_analysis),
        ):
            with self.assertRaisesRegex(
                install.InstallError, "installation_failed_rolled_back"
            ):
                self.apply()
        self.assertEqual(
            order[:3],
            ["pkg/condition_values.py", "pkg/filter_evaluation.py", "pkg/analysis.py"],
        )
        self.assert_originals()

    def test_exception_after_publication_is_still_rolled_back(self):
        original_sync = install._fsync_directory
        failed = False

        def fail_first_sync(path):
            nonlocal failed
            if path == self.root / "pkg" and not failed:
                failed = True
                raise OSError("synthetic fsync failure")
            original_sync(path)

        with (
            patch.object(install, "run_checks", return_value=1),
            patch.object(install, "_fsync_directory", side_effect=fail_first_sync),
        ):
            with self.assertRaisesRegex(
                install.InstallError, "installation_failed_rolled_back"
            ):
                self.apply()
        self.assert_originals()

    def test_result_record_failure_rolls_back_completed_writes(self):
        original_save = install._save

        def fail_result(path, raw):
            if path.name == "result.json":
                raise OSError("synthetic audit failure")
            return original_save(path, raw)

        with (
            patch.object(install, "run_checks", return_value=1),
            patch.object(install, "_save", side_effect=fail_result),
        ):
            with self.assertRaisesRegex(
                install.InstallError, "installation_failed_rolled_back"
            ):
                self.apply()
        self.assert_originals()

    def test_rollback_preserves_unrelated_concurrent_edit(self):
        original_replace = install._replace

        def fail_after_edit(root, relative, raw, expected, mode, committed):
            if relative == "pkg/analysis.py":
                self.write("pkg/filter_evaluation.py", b"concurrent user change\n")
                raise OSError("synthetic write failure")
            return original_replace(root, relative, raw, expected, mode, committed)

        with (
            patch.object(install, "run_checks", return_value=1),
            patch.object(install, "_replace", side_effect=fail_after_edit),
        ):
            with self.assertRaisesRegex(
                install.InstallError, "installation_failed_rollback_incomplete"
            ):
                self.apply()
        self.assertEqual(
            (self.root / "pkg/filter_evaluation.py").read_bytes(),
            b"concurrent user change\n",
        )
        self.assertFalse((self.root / "pkg/condition_values.py").exists())
        self.assertEqual(
            (self.root / "pkg/analysis.py").read_bytes(),
            self.originals["pkg/analysis.py"],
        )

    def test_recreated_new_file_is_not_removed_even_when_content_matches(self):
        original_replace = install._replace

        def replace_new_then_fail(root, relative, raw, expected, mode, committed):
            if relative == "pkg/analysis.py":
                path = self.root / "pkg/condition_values.py"
                replacement = self.root / "pkg/replacement.py"
                replacement.write_bytes(path.read_bytes())
                os.replace(replacement, path)
                raise OSError("synthetic write failure")
            return original_replace(root, relative, raw, expected, mode, committed)

        with (
            patch.object(install, "run_checks", return_value=1),
            patch.object(install, "_replace", side_effect=replace_new_then_fail),
        ):
            with self.assertRaisesRegex(
                install.InstallError, "installation_failed_rollback_incomplete"
            ):
                self.apply()
        self.assertTrue((self.root / "pkg/condition_values.py").exists())

    def test_change_during_tests_is_not_overwritten(self):
        def change(staged, tests):
            self.write("pkg/analysis.py", b"concurrent edit during tests\n")
            return 1

        with patch.object(install, "run_checks", side_effect=change):
            with self.assertRaisesRegex(
                install.InstallError, "project_changed_during_tests"
            ):
                self.apply()
        self.assertFalse((self.root / "pkg/condition_values.py").exists())
        self.assertEqual(
            (self.root / "pkg/analysis.py").read_bytes(),
            b"concurrent edit during tests\n",
        )

    def test_symlink_file_and_parent_are_rejected(self):
        path = self.root / "pkg/analysis.py"
        destination = self.base / "external.py"
        destination.write_bytes(self.originals["pkg/analysis.py"])
        path.unlink()
        path.symlink_to(destination)
        with self.assertRaisesRegex(install.InstallError, "symlink_not_allowed"):
            self.apply()
        path.unlink()
        path.write_bytes(self.originals["pkg/analysis.py"])
        (self.root / "pkg").rename(self.root / "real-pkg")
        (self.root / "pkg").symlink_to(self.root / "real-pkg", target_is_directory=True)
        with self.assertRaisesRegex(install.InstallError, "symlink_not_allowed"):
            self.apply()

    def test_evidence_counters_require_integers_and_hashes_match(self):
        for field, value in (
            ("model_calls", False),
            ("preview_materials_used", True),
            ("response_sha256", "0" * 64),
        ):
            with self.subTest(field=field):
                self.summary = {
                    **install.EVIDENCE_EXPECTED,
                    **install.EVIDENCE_HASHES,
                    field: value,
                }
                self.write_evidence()
                with self.assertRaisesRegex(
                    install.InstallError, "offline_evidence_mismatch"
                ):
                    self.apply()
        self.assert_originals()

    def test_replay_analysis_hash_is_not_bound_to_installed_prompt_source(self):
        self.summary["validator_source_sha256"] = {"analysis": "a" * 64}
        self.write_evidence()
        with patch.object(install, "run_checks", return_value=1):
            self.assertEqual(self.apply()["status"], "installed")

    def test_manifest_escape_and_content_mismatch_are_rejected(self):
        original = copy.deepcopy(self.packet)
        self.packet["files"]["../outside.py"] = self.packet["files"].pop(
            "pkg/analysis.py"
        )
        with self.assertRaisesRegex(install.InstallError, "invalid_manifest_path"):
            self.apply()
        self.packet = original
        self.packet["files"]["pkg/analysis.py"]["content"] = "altered = True\n"
        with self.assertRaisesRegex(install.InstallError, "package_content_mismatch"):
            self.apply()
        self.assert_originals()

    def test_services_are_blocked_inside_test_process(self):
        self.packet["test_files"]["test/test_entry.py"] = """import sqlite3, unittest
class ServiceAttempt(unittest.TestCase):
    def test_no_db(self):
        sqlite3.connect("forbidden-business.sqlite")
if __name__ == '__main__':
    unittest.main()
"""
        with self.assertRaisesRegex(install.InstallError, "isolated_tests_failed"):
            self.apply()
        self.assert_originals()

    def test_same_project_lock_prevents_second_installer(self):
        with install._lock(self.root):
            with self.assertRaisesRegex(
                install.InstallError, "another_installer_running"
            ):
                self.apply()
        self.assert_originals()

    def add_surge_configuration(self):
        sources = {
            "config/rule_refine.surge.local.json": json.dumps(
                {"enabled": True, "context_tokens": 262144}
            ),
            "my_workflows/zeus/rule_refine/surge_budget.py": "class Budget: pass\n",
            "my_workflows/zeus/rule_refine/surge_settings.py": "from .surge_budget import Budget\ndef validate_settings(data):\n    if type(data.get('enabled')) is not bool or type(data.get('context_tokens')) is not int:\n        raise ValueError('invalid')\n    return dict(data)\n",
        }
        for name, source in sources.items():
            self.packet["files"][name] = {
                "content": source,
                "old_sha256": None,
                "new_sha256": install.digest(source.encode("utf-8")),
            }

    def test_existing_operator_configuration_is_validated_and_preserved_exactly(self):
        self.add_surge_configuration()
        name = "config/rule_refine.surge.local.json"
        raw = b'{ "enabled": false, "context_tokens": 1000000 }\n'
        self.write(name, raw)
        with patch.object(install, "run_checks", return_value=1):
            result = self.apply()
        self.assertEqual((self.root / name).read_bytes(), raw)
        self.assertEqual(result["surge_settings"]["context_tokens"], 1000000)
        self.assertIs(result["surge_settings"]["enabled"], False)
        self.assertEqual(result["configuration_file"], str(self.root / name))
        self.assertFalse((self.root / "config/rule_refine.limits.local.json").exists())

    def test_invalid_configuration_stops_before_tests_or_code_mutation(self):
        self.add_surge_configuration()
        name = "config/rule_refine.surge.local.json"
        self.write(name, b'{"enabled":"true","context_tokens":262144}')
        with patch.object(install, "run_checks") as run:
            with self.assertRaisesRegex(
                install.InstallError, "surge_configuration_invalid"
            ):
                self.apply()
        run.assert_not_called()
        self.assert_originals()

    def test_duplicate_configuration_field_is_rejected(self):
        self.add_surge_configuration()
        self.write(
            "config/rule_refine.surge.local.json",
            b'{"enabled":true,"enabled":false,"context_tokens":1}',
        )
        with self.assertRaisesRegex(
            install.InstallError, "surge_configuration_invalid"
        ):
            self.apply()
        self.assert_originals()

    def test_sqlite_guard_allows_temporary_database_and_memory(self):
        entry = """import sqlite3, tempfile, unittest
from pathlib import Path
class GuardTests(unittest.TestCase):
    def test_temporary_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'database.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('create table examples (value text)')
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                self.assertEqual(db.execute('select count(*) from examples').fetchone(), (0,))
        with sqlite3.connect(':memory:') as db:
            self.assertEqual(db.execute('select 1').fetchone(), (1,))
if __name__ == '__main__':
    unittest.main()
"""
        self.assertEqual(
            install.run_checks({"test.py": entry.encode()}, ["test.py"]), 1
        )

    def test_guard_blocks_business_databases_uri_escapes_symlinks_and_network(self):
        entry = """import sqlite3, tempfile, unittest, socket, subprocess
from pathlib import Path
class GuardTests(unittest.TestCase):
    def test_boundaries(self):
        for path in ('business.sqlite', Path.cwd() / 'business.sqlite', 'file:' + str(Path.cwd()) + '/business.sqlite?mode=ro', 'file://example.invalid/database.sqlite'):
            with self.assertRaises(RuntimeError):
                sqlite3.connect(path, uri=True)
        with tempfile.TemporaryDirectory() as folder:
            link = Path(folder) / 'escape'
            link.symlink_to(Path.cwd(), target_is_directory=True)
            with self.assertRaises(RuntimeError):
                sqlite3.connect(link / 'business.sqlite')
            with self.assertRaises(RuntimeError):
                sqlite3.connect('file:' + folder + '/%2e%2e/%2e%2e/business.sqlite?mode=ro', uri=True)
        with self.assertRaises(RuntimeError):
            socket.getaddrinfo('localhost', 80)
        with self.assertRaises(RuntimeError):
            subprocess.run(['true'])
if __name__ == '__main__':
    unittest.main()
"""
        self.assertEqual(
            install.run_checks({"test.py": entry.encode()}, ["test.py"]), 1
        )


if __name__ == "__main__":
    unittest.main()
