"""DEV seed/cleanup tests use disposable projects, never a configured application."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).with_name("check_surge_dev_history.py")
SPEC = importlib.util.spec_from_file_location("isolated_dev_history_seed", PATH)
SEED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEED)


class DevSeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "nas/data/sec_know_model.sqlite"
        self.path.parent.mkdir(parents=True)
        self.schema()

    def schema(self, extra="", check=""):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE IF EXISTS uas_app_workflow_task_status")
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status (id TEXT PRIMARY KEY, app_code TEXT NOT NULL, flow_id TEXT NOT NULL, alert_id TEXT NOT NULL, alert_data TEXT NOT NULL, status TEXT NOT NULL, failed_count INTEGER NOT NULL DEFAULT 0, agent_response TEXT, created_by TEXT, updated_by TEXT, created_date TEXT, updated_date TEXT"
                + extra
                + check
                + ")"
            )

    def run_seed(self, **kwargs):
        return SEED.run(self.root, environ={"env_profile": "LOCAL"}, **kwargs)

    def count(self):
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "SELECT COUNT(*) FROM uas_app_workflow_task_status"
            ).fetchone()[0]

    def add_foreign(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO uas_app_workflow_task_status (id,app_code,flow_id,alert_id,alert_data,status,agent_response) VALUES (?,?,?,?,?,?,?)",
                (
                    "real-task",
                    "zeus",
                    "alert_agent",
                    "real-alert",
                    json.dumps(
                        {
                            "alert": {
                                "alertId": "real-alert",
                                "createAt": "2020-01-01 00:00:00",
                                "hitLog": [
                                    {
                                        "topic": "business",
                                        "ruleCode": "real",
                                        "zeusRawLogs": [{"original": True}],
                                    }
                                ],
                            }
                        }
                    ),
                    "successed",
                    "PRIVATE_PRIOR_RESPONSE",
                ),
            )

    def foreign(self):
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "SELECT * FROM uas_app_workflow_task_status WHERE id='real-task'"
            ).fetchone()

    def test_seed_real_source_strict_nearest_twenty_across_days(self):
        result = self.run_seed()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["inserted"], 42)
        self.assertEqual(result["reused"], 0)
        self.assertTrue(result["recall_passed"])
        self.assertEqual(result["recalled_current_alerts"], 6)
        self.assertEqual(result["available_historical_alerts"], 32)
        self.assertEqual(result["recalled_historical_alerts"], 20)
        self.assertEqual(
            result["history_basis"], "nearest_20_distinct_alerts_strictly_before_cutoff"
        )
        self.assertEqual(result["time_basis"], "main_alert_createAt")
        self.assertFalse(result["source"]["task_created_date_used"])
        self.assertFalse(
            result["source"]["receipt_window"]["used_for_business_time_selection"]
        )
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(self.count(), 42)

    def test_repeat_does_not_insert_and_fixture_ids_are_deterministic(self):
        self.assertEqual(SEED.fixture_rows(), SEED.fixture_rows())
        self.run_seed()
        result = self.run_seed()
        self.assertEqual((result["inserted"], result["reused"]), (0, 42))
        self.assertTrue(result["recall_passed"])
        self.assertEqual(self.count(), 42)

    def test_synthetic_current_pattern_differs_from_varied_history(self):
        current, history = [], []
        for row in SEED.fixture_rows():
            payload = json.loads(row["alert_data"])
            self.assertEqual(payload["material_source"], "synthetic_dev_fixture")
            log = payload["alert"]["hitLog"][0]["zeusRawLogs"][0]
            self.assertEqual(log["material_source"], "synthetic_dev_fixture")
            if row["alert_id"].startswith(SEED.ALERT_PREFIX + "current-"):
                current.append(log)
            elif row["alert_id"].startswith(SEED.ALERT_PREFIX + "history-"):
                history.append(log)
        self.assertEqual(len(current), 6)
        self.assertEqual(
            {log["event_content"] for log in current},
            {"inventory-probe --collect versions"},
        )
        self.assertEqual(
            {log["process_name"] for log in current}, {"inventory-probe.exe"}
        )
        self.assertEqual({log["task_source"] for log in current}, {"planned_inventory"})
        for field in ("event_content", "process_name", "task_source"):
            historical_values = {log[field] for log in history}
            self.assertEqual(len(historical_values), 4)
            self.assertFalse(
                historical_values.intersection(log[field] for log in current)
            )

    def test_existing_business_rows_survive_seed_repeat_and_cleanup(self):
        self.add_foreign()
        before = self.foreign()
        for _ in range(2):
            self.assertEqual(self.run_seed()["status"], "passed")
            self.assertEqual(self.foreign(), before)
        result = self.run_seed(cleanup=True)
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(result["deleted"], 42)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.foreign(), before)
        self.assertEqual(self.run_seed(cleanup=True)["deleted"], 0)

    def test_changed_own_record_blocks_all_seed_and_cleanup_mutations(self):
        self.run_seed()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE uas_app_workflow_task_status SET status='running' WHERE id=?",
                (SEED.fixture_rows()[0]["id"],),
            )
        before = self.path.read_bytes()
        for cleanup in (False, True):
            with self.assertRaisesRegex(ValueError, "synthetic_fixture_record_changed"):
                self.run_seed(cleanup=cleanup)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.count(), 42)

    def test_foreign_task_with_fixture_alert_identity_is_not_overwritten(self):
        row = SEED.fixture_rows()[0]
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO uas_app_workflow_task_status (id,app_code,flow_id,alert_id,alert_data,status) VALUES (?,?,?,?,?,?)",
                (
                    "foreign-task",
                    "zeus",
                    "alert_agent",
                    row["alert_id"],
                    "{}",
                    "successed",
                ),
            )
        with self.assertRaisesRegex(ValueError, "synthetic_fixture_identity_conflict"):
            self.run_seed()
        self.assertEqual(self.count(), 1)

    def test_partial_insertion_failure_rolls_back_every_insert(self):
        self.schema(check=", CHECK (alert_id NOT LIKE '%future')")
        self.add_foreign()
        before = self.foreign()
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_seed()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.foreign(), before)

    def test_nonlocal_missing_or_conflicting_environment_refused(self):
        for environ in (
            {},
            {"env_profile": "DEV"},
            {"ENV_PROFILE": "PRD"},
            {"env_profile": "LOCAL", "ENV_PROFILE": "STG"},
        ):
            with (
                self.subTest(environ=environ),
                self.assertRaisesRegex(ValueError, "explicit_LOCAL_profile_required"),
            ):
                SEED.run(self.root, environ=environ)
        self.assertEqual(self.count(), 0)

    def test_uppercase_local_environment_is_supported(self):
        result = SEED.run(self.root, environ={"ENV_PROFILE": "LOCAL"})
        self.assertEqual(result["status"], "passed")

    def test_missing_database_is_never_created(self):
        missing_project = self.root / "missing"
        missing_project.mkdir()
        with self.assertRaisesRegex(ValueError, "existing_local_database_required"):
            SEED.run(missing_project, environ={"env_profile": "LOCAL"})
        self.assertEqual(list(missing_project.iterdir()), [])

    def test_symlink_project_and_database_are_refused(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink_path_not_allowed"):
            SEED.run(alias, environ={"env_profile": "LOCAL"})
        actual = self.path.with_name("actual.sqlite")
        self.path.rename(actual)
        self.path.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, "symlink_path_not_allowed"):
            self.run_seed()

    def test_symlink_wal_and_remote_paths_are_refused(self):
        Path(str(self.path) + "-wal").symlink_to(self.root / "nonexistent")
        with self.assertRaisesRegex(ValueError, "symlink_sqlite_sidecar_not_allowed"):
            self.run_seed()
        with self.assertRaisesRegex(ValueError, "local_project_path_required"):
            SEED.run("http://database.example/root", environ={"env_profile": "LOCAL"})

    def test_schema_unknown_required_columns_fail_without_schema_changes(self):
        self.schema(extra=", business_owner TEXT NOT NULL")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "unsupported_required_task_columns"):
            self.run_seed()
        self.assertEqual(self.path.read_bytes(), before)

    def test_extra_optional_and_literal_default_columns_are_checked_on_cleanup(self):
        self.schema(
            extra=", optional_note TEXT, priority INTEGER NOT NULL DEFAULT 9, label TEXT NOT NULL DEFAULT 'fixture''s default'"
        )
        result = self.run_seed()
        self.assertEqual(result["status"], "passed")
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE uas_app_workflow_task_status SET optional_note='changed' WHERE id=?",
                (SEED.fixture_rows()[0]["id"],),
            )
        with self.assertRaisesRegex(ValueError, "synthetic_fixture_record_changed"):
            self.run_seed(cleanup=True)
        self.assertEqual(self.count(), 42)

    def test_table_trigger_is_rejected_before_any_side_effect(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE trigger_result (value TEXT)")
            db.execute(
                "CREATE TRIGGER task_insert AFTER INSERT ON uas_app_workflow_task_status BEGIN INSERT INTO trigger_result VALUES ('side-effect'); END"
            )
        with self.assertRaisesRegex(ValueError, "task_table_triggers_require_review"):
            self.run_seed()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM trigger_result").fetchone()[0], 0
            )
        self.assertEqual(self.count(), 0)

    def test_cleanup_rejects_extra_marker_owned_record(self):
        self.run_seed()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO uas_app_workflow_task_status (id,app_code,flow_id,alert_id,alert_data,status,created_by) VALUES (?,?,?,?,?,?,?)",
                (
                    "unexpected",
                    "zeus",
                    "alert_agent",
                    "unexpected",
                    "{}",
                    "successed",
                    SEED.MARKER,
                ),
            )
        with self.assertRaisesRegex(ValueError, "synthetic_fixture_identity_conflict"):
            self.run_seed(cleanup=True)
        self.assertEqual(self.count(), 43)

    def test_minimal_business_schema_without_marker_columns_is_supported(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE uas_app_workflow_task_status")
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status (id TEXT PRIMARY KEY,app_code TEXT,flow_id TEXT,alert_id TEXT,alert_data TEXT,status TEXT)"
            )
        self.assertEqual(self.run_seed()["status"], "passed")
        self.assertEqual(self.run_seed()["reused"], 42)
        self.assertEqual(self.run_seed(cleanup=True)["deleted"], 42)


if __name__ == "__main__":
    unittest.main()
