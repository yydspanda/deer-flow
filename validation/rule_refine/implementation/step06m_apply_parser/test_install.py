"""Install the parser fix into a disposable project; no external services."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
IMPLEMENTATION = HERE.parent
REPOSITORY = HERE.parents[3]
ANALYSIS = "my_workflows/zeus/rule_refine/analysis.py"
TEST = "test/rule_refine/test_analysis.py"
NEW_SHA = "c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0"
NEW_TEST_SHA = "fe9e13b15690b8947968e7f203f0f1af4c66828c79cd516514514c5a1ac65992"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HERE))
        spec = importlib.util.spec_from_file_location(
            "apply_fenced_parser_test", HERE / "install.py"
        )
        cls.install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.install)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(HERE))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="parser-install-tests-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / "sec_know_model"
        self.root.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(
            os.environ, HOME=str(self.home), env_profile="LOCAL", ENV_PROFILE="LOCAL"
        ).start()
        for payload in sorted(IMPLEMENTATION.glob("*/payload")):
            for path in payload.rglob("*.py"):
                target = self.root / path.relative_to(payload)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        for relative in (ANALYSIS, TEST):
            shutil.copyfile(
                IMPLEMENTATION / "step04b_analysis/payload" / relative,
                self.root / relative,
            )
        pins = json.loads(
            (IMPLEMENTATION / "step06j_synthetic_chain/pins.json").read_text(
                encoding="utf-8"
            )
        )
        with ZipFile(REPOSITORY / "validation/sec_know_model.zip") as archive:
            for name in pins:
                target = self.root / name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        interpreter = self.root / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            "#!/bin/sh\nexec " + sys.executable + ' "$@"\n', encoding="utf-8"
        )
        interpreter.chmod(0o700)
        self.evidence = self.home / "sec-rule-refine-data/fenced-json-replay-test"
        self.evidence.mkdir(parents=True)
        self.summary = {
            "check": "saved_model_reply_revalidation",
            "status": "passed",
            "execution_mode": "offline_replay",
            "response_origin": "saved_model_response",
            "analysis_status": "proposal",
            "validation_error": None,
            "delivery_status": "received",
            "check_reason": "local_checks_passed",
            "model_calls": 0,
            "redis_calls": 0,
            "database_calls": 0,
            "automatic_apply": False,
            "application_code_updated": False,
            "original_report_unchanged": True,
            "source_record_id": "26e971c7feb5d8c968854edcc47b4000b811e28b5788b30e6b03bf3690589186",
            "response_sha256": "10dc131e79722ae54191572a0518cd9523b2e8b597c01c5b2b92c788faa69000",
            "parser_sha256": NEW_SHA,
            "prompt_sha256": "0" * 64,
            "output_dir": str(self.evidence),
        }
        self.write_summary()
        self.original = {
            relative: (self.root / relative).read_bytes()
            for relative in (ANALYSIS, TEST)
        }

    def write_summary(self):
        (self.evidence / "summary.json").write_text(
            json.dumps(self.summary), encoding="utf-8"
        )

    def assert_unchanged(self):
        for relative, raw in self.original.items():
            self.assertEqual((self.root / relative).read_bytes(), raw)

    def test_install_runs_tests_and_keeps_private_backup_and_evidence(self):
        evidence = (self.evidence / "summary.json").read_bytes()
        report = self.install.apply(self.root, self.evidence)
        self.assertEqual(report["status"], "installed", report)
        self.assertEqual(digest((self.root / ANALYSIS).read_bytes()), NEW_SHA)
        self.assertEqual(digest((self.root / TEST).read_bytes()), NEW_TEST_SHA)
        self.assertEqual((self.evidence / "summary.json").read_bytes(), evidence)
        backup_dirs = list((self.home / "sec-rule-refine-data").glob("parser-fix-*"))
        self.assertEqual(len(backup_dirs), 1, report)
        files = [path for path in backup_dirs[0].rglob("*") if path.is_file()]
        for raw in self.original.values():
            matches = [path for path in files if path.read_bytes() == raw]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].stat().st_mode & 0o777, 0o600)

    def test_already_installed_runs_checks_and_does_not_duplicate_backup(self):
        for relative in (ANALYSIS, TEST):
            shutil.copyfile(
                IMPLEMENTATION / "step06l_fenced_json/payload" / relative,
                self.root / relative,
            )
        before = {
            relative: (self.root / relative).read_bytes()
            for relative in (ANALYSIS, TEST)
        }
        with patch.object(
            self.install, "run_checks", wraps=self.install.run_checks
        ) as checks:
            report = self.install.apply(self.root, self.evidence)
        checks.assert_called_once()
        self.assertEqual(report["status"], "already_installed", report)
        self.assertEqual(report["tests_passed"], 18)
        for relative, raw in before.items():
            self.assertEqual((self.root / relative).read_bytes(), raw)

    def test_test_file_already_updated_can_finish_runtime_source_update(self):
        shutil.copyfile(
            IMPLEMENTATION / "step06l_fenced_json/payload" / TEST,
            self.root / TEST,
        )
        report = self.install.apply(self.root, self.evidence)
        self.assertEqual(report["status"], "installed", report)
        self.assertEqual(report["tests_passed"], 18)
        self.assertEqual(digest((self.root / ANALYSIS).read_bytes()), NEW_SHA)
        self.assertEqual(digest((self.root / TEST).read_bytes()), NEW_TEST_SHA)

    def test_source_replace_failure_restores_test_and_retry_can_finish(self):
        actual_replace = self.install.atomic_replace
        order = []

        def fail_source(path, raw, expected):
            order.append(str(Path(path).relative_to(self.root)))
            if Path(path) == self.root / ANALYSIS:
                raise OSError("injected source replace failure")
            return actual_replace(path, raw, expected)

        with patch.object(self.install, "atomic_replace", side_effect=fail_source):
            with self.assertRaisesRegex(OSError, "injected source replace failure"):
                self.install.apply(self.root, self.evidence)
        self.assertEqual(order, [TEST, ANALYSIS, TEST])
        self.assert_unchanged()
        self.assertFalse(list(self.root.rglob(".parser-fix-*")))
        report = self.install.apply(self.root, self.evidence)
        self.assertEqual(report["status"], "installed", report)
        self.assertEqual(digest((self.root / ANALYSIS).read_bytes()), NEW_SHA)
        self.assertEqual(digest((self.root / TEST).read_bytes()), NEW_TEST_SHA)

    def test_unexpected_application_change_is_rejected_without_overwrite(self):
        changed = self.original[ANALYSIS] + b"\n# operator edit\n"
        (self.root / ANALYSIS).write_bytes(changed)
        with self.assertRaises(Exception):
            self.install.apply(self.root, self.evidence)
        self.assertEqual((self.root / ANALYSIS).read_bytes(), changed)
        self.assertEqual((self.root / TEST).read_bytes(), self.original[TEST])

    def test_failed_or_wrong_parser_evidence_is_rejected_before_application_edit(self):
        for changes in (
            {"status": "failed"},
            {"parser_sha256": "0" * 64},
            {"model_calls": 1},
            {"original_report_unchanged": False},
        ):
            with self.subTest(changes=changes):
                before = dict(self.summary)
                self.summary.update(changes)
                self.write_summary()
                with self.assertRaises(Exception):
                    self.install.apply(self.root, self.evidence)
                self.assert_unchanged()
                self.summary = before
                self.write_summary()


if __name__ == "__main__":
    unittest.main(verbosity=2)
