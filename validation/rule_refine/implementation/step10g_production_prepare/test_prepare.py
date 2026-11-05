"""Release preparation failure/transaction tests; no app or service execution."""

import csv
import io
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import build_command as builder
import prepare

BEFORE = builder.baseline()
BUNDLE = builder.make_bundle()
INSTALLER = types.ModuleType("release_preparation_test_installer")
exec(compile(BUNDLE["installer"], "<installer>", "exec"), INSTALLER.__dict__)
namespace = {}
exec(compile(BUNDLE["reconstructor"], "<reconstruct>", "exec"), namespace)
RECONSTRUCT = namespace["reconstruct"]
RUN = (
    prepare.START
    + "\n# Keep monitoring off for the previous release.\nexport RULE_REFINE_MONITOR_ENABLED=0\n"
    + prepare.END
    + "\n# Sentinel: never execute the existing cleanup/startup below.\nexit 93\n"
).encode()


def synthetic_catalog():
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(
        [
            "company_code",
            "topic",
            "rule_code",
            "rule_name",
            "terms",
            "standard_field_config",
        ]
    )
    writer.writerow(
        [
            "SYNTHETIC",
            "synthetic-topic",
            "SYNTHETIC-RULE",
            "Synthetic release test",
            "[]",
            "{}",
        ]
    )
    return text.getvalue().encode()


def populate(root):
    for name, raw in BEFORE.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (root / "run.sh").write_bytes(RUN)
    (root / "run.sh").chmod(0o750)
    csv_path = root / "original-rule-catalog.csv"
    csv_path.write_bytes(synthetic_catalog())
    (root / "config/rule_refine.local.json").write_text(
        json.dumps(
            {"enabled": True, "namespace": "mac-local-only", "csv_path": str(csv_path)}
        ),
        encoding="utf-8",
    )
    (root / "config/celery_task.prd.conf").write_text(
        "Pub concurrency = 27\n", encoding="utf-8"
    )
    (root / "local_run.sh").write_text("exit 94\n", encoding="utf-8")
    return csv_path


class PrepareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="prd-prepare-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "app"
        self.root.mkdir()
        self.backups = Path(temporary.name) / "backups"
        self.csv_path = populate(self.root)

    def snapshot(self):
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")
        }

    def run_prepare(self, env=None):
        return prepare.prepare(
            self.root,
            self.backups,
            {} if env is None else env,
            BUNDLE,
            RECONSTRUCT,
            INSTALLER,
        )

    def test_prepare_idempotent_preserves_local_stg_queues_and_backups(self):
        original = self.snapshot()
        with patch.object(INSTALLER, "run_checks", return_value=454):
            result = self.run_prepare()
            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["files_changed"], 5)
            self.assertEqual(self.run_prepare()["status"], "already_prepared")
        self.assertEqual(result["rule_catalog_count"], 1)
        self.assertFalse(result["services_started"])
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["zeus_posts"], 0)
        changed = set(result["changed_files"])
        for name, raw in original.items():
            if name not in changed:
                self.assertEqual((self.root / name).read_bytes(), raw, name)
        self.assertEqual(
            (self.root / prepare.CATALOG).read_bytes(), self.csv_path.read_bytes()
        )
        value = json.loads((self.root / prepare.PRD_CONFIG).read_text())
        self.assertEqual(value["namespace"], "topic-v1:prd")
        self.assertEqual(value["csv_path"], prepare.CATALOG)
        self.assertEqual(value["operator_context"], "")
        self.assertEqual(
            (Path(result["backup_dir"]) / "before/run.sh").read_bytes(), RUN
        )
        self.assertEqual((self.root / "run.sh").stat().st_mode & 0o777, 0o750)

    def test_missing_catalog_does_not_write(self):
        self.csv_path.unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "configured_rule_catalog_not_readable"
        ):
            self.run_prepare()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.backups.exists())

    def test_alert_csv_rejected_without_replacing_sources(self):
        self.csv_path.write_bytes(b"alert_id,alert_data\n123,{}\n")
        before = self.snapshot()
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "not_a_valid_rule_configuration_catalog"
        ):
            self.run_prepare()
        self.assertEqual(before, self.snapshot())

    def test_source_drift_rejected(self):
        path = self.root / builder.MODULE / "topic_delivery.py"
        path.write_bytes(path.read_bytes() + b"\n# changed independently\n")
        before = self.snapshot()
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "dependency_version_mismatch"
        ):
            self.run_prepare()
        self.assertEqual(before, self.snapshot())

    def test_unknown_launcher_block_rejected(self):
        (self.root / "run.sh").write_text(
            "export RULE_REFINE_MONITOR_ENABLED=0\n", encoding="utf-8"
        )
        before = self.snapshot()
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "review_run_sh_monitor_block"
        ):
            self.run_prepare()
        self.assertEqual(before, self.snapshot())

    def test_regression_failure_leaves_project_unchanged(self):
        before = self.snapshot()
        with patch.object(
            INSTALLER,
            "run_checks",
            side_effect=INSTALLER.InstallError("isolated_tests_failed"),
        ):
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "isolated_tests_failed"
            ):
                self.run_prepare()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.backups.exists())

    def test_mid_write_failure_rolls_back(self):
        before = self.snapshot()
        real_replace = INSTALLER._replace
        count = 0

        def fail_once(*args):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError("synthetic disk failure")
            return real_replace(*args)

        with (
            patch.object(INSTALLER, "run_checks", return_value=454),
            patch.object(INSTALLER, "_replace", side_effect=fail_once),
        ):
            with self.assertRaisesRegex(
                INSTALLER.InstallError, "release_prepare_failed_rolled_back"
            ):
                self.run_prepare()
        self.assertEqual(before, self.snapshot())

    def test_only_production_marker_changes_in_full_launcher(self):
        result = prepare.launcher(b"#!/bin/bash\n" + RUN, INSTALLER)
        old_block = RUN.decode().split(prepare.END)[0] + prepare.END
        self.assertEqual(
            result,
            (
                "#!/bin/bash\n" + RUN.decode().replace(old_block, prepare.NEW_BLOCK)
            ).encode(),
        )

    def test_launcher_block_enable_only_prd(self):
        # Execute ONLY the newly authored export block; never the real launcher.
        for profile in ("prd", "PRD", "stg", "STG", "LOCAL", ""):
            env = {
                k: v
                for k, v in os.environ.items()
                if k
                not in {"env_profile", "ENV_PROFILE", "RULE_REFINE_MONITOR_ENABLED"}
            }
            env["env_profile"] = profile
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    prepare.NEW_BLOCK + '\nprintf "%s" "$RULE_REFINE_MONITOR_ENABLED"',
                ],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            self.assertEqual(result.stdout, "1" if profile.lower() == "prd" else "0")

    def test_different_configured_catalogs_require_explicit_choice(self):
        other = self.root / "other.csv"
        other.write_bytes(
            synthetic_catalog().replace(b"SYNTHETIC-RULE", b"SYNTHETIC-OTHER")
        )
        (self.root / prepare.LOCAL_CONFIGS[0]).write_text(
            json.dumps({"csv_path": str(other)}), encoding="utf-8"
        )
        before = self.snapshot()
        with self.assertRaisesRegex(
            INSTALLER.InstallError, "configured_rule_catalogs_differ"
        ):
            self.run_prepare()
        self.assertEqual(before, self.snapshot())
        with patch.object(INSTALLER, "run_checks", return_value=454):
            result = self.run_prepare({"SEC_TOPIC_CSV_PATH": str(self.csv_path)})
        self.assertEqual(
            result["rule_catalog_sha256"], INSTALLER.digest(self.csv_path.read_bytes())
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
