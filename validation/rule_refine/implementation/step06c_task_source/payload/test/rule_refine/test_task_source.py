"""本地任务读取测试；使用临时人工数据库，不接触业务库、模型或 Redis。"""

import importlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("_rule_task_fixtures", Path(__file__).with_name("test_proposal_check.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
base = fixtures.fixtures.fixtures
source = importlib.import_module(base.package.__name__ + ".task_source")
pipeline = importlib.import_module(base.package.__name__ + ".signal_processing")
Sink = importlib.import_module(base.package.__name__ + ".local_sink").LocalRecommendationSink
UNSET = object()


def database(path):
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE uas_app_workflow_task_status (
            id TEXT PRIMARY KEY, app_code TEXT, flow_id TEXT, alert_id TEXT,
            status TEXT, created_date TEXT, alert_data JSON, agent_response JSON)""")


def insert(path, row, *, alert=UNSET, response=UNSET):
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO uas_app_workflow_task_status VALUES (?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["app_code"],
                row["flow_id"],
                row["alert_id"],
                row.get("status", "successed"),
                row["created_date"],
                json.dumps(row["alert_data"]) if alert is UNSET else alert,
                json.dumps(row["agent_response"]) if response is UNSET else response,
            ),
        )


def signal(ids=None):
    return {**base.signal(ids), "mode": "replay"}


def materials(sig, rows):
    return base.build(sig, fixtures.loader(fixtures.fixtures.materials())(), rows, source_kind="workflow_task_store")


class TaskSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "tasks.sqlite"
        database(self.path)
        self.reader = source.LocalWorkflowTaskSource(self.path)

    def test_scope_parameters_statuses_and_duplicate_tasks_are_preserved(self):
        for index, status in enumerate(("created", "running", "failed", "successed")):
            insert(self.path, {**base.row(task_id=str(index)), "status": status})
        for index, change in enumerate(({"app_code": "other"}, {"flow_id": "other"}, {"alert_id": "999"})):
            insert(self.path, {**base.row(task_id="other-" + str(index)), **change})
        quoted = "x' OR 1=1 --"
        insert(self.path, base.row(quoted, "quoted"))
        rows = self.reader.load_task_rows(signal(["101", "101", quoted]))
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["alert_id"] for r in rows}, {"101", quoted})
        self.assertEqual({r["status"] for r in rows}, {"created", "running", "failed", "successed"})
        data = materials(signal(), [r for r in rows if r["alert_id"] == "101"])
        self.assertEqual((len(data["samples"]), data["duplicate_task_rows"]), (1, 3))

    def test_task_creation_time_does_not_replace_signal_receipt_window(self):
        insert(self.path, {**base.row(), "created_date": "2027-01-01T00:00:00"})
        rows = self.reader.load_task_rows(signal())
        self.assertEqual(materials(signal(), rows)["status"], "ready")

    def test_invalid_raw_variants_remain_and_exclude_otherwise_valid_alerts(self):
        invalid = (b"{", b"\xff", b"x" * (source.ALERT_LIMIT + 1), None, b"null", b"[]", b'{"a":1,"a":2}')
        ids = []
        for index, raw in enumerate(invalid):
            aid = str(100 + index)
            ids.append(aid)
            insert(self.path, base.row(aid, "valid-" + aid))
            insert(self.path, base.row(aid, "bad-" + aid), alert=raw)
        rows = self.reader.load_task_rows(signal(ids))
        self.assertEqual(len(rows), 2 * len(ids))
        self.assertEqual(sum(row["alert_data"] is None for row in rows), len(ids))
        data = materials(signal(ids), rows)
        self.assertEqual(data["samples"], [])
        self.assertIn("sample_conflicting_or_invalid", {issue["code"] for issue in data["issues"]})

    def test_bad_optional_responses_are_omitted_with_material_issue(self):
        invalid = (b"{", b"\xff", b"x" * (source.RESPONSE_LIMIT + 1), b"NaN", b'{"a":1,"a":2}')
        ids = [str(101 + i) for i in range(len(invalid))]
        for aid, raw in zip(ids, invalid):
            insert(self.path, base.row(aid, aid), response=raw)
        rows = self.reader.load_task_rows(signal(ids))
        self.assertTrue(all(r["agent_response"] is None and r["agent_response_unavailable"] for r in rows))
        data = materials(signal(ids), rows)
        self.assertEqual((data["status"], len(data["samples"])), ("partial", len(ids)))
        self.assertEqual({i["code"] for i in data["issues"]}, {"stored_response_omitted"})

    def test_generic_json_responses_and_sql_null_remain_distinct(self):
        values = ("historical text", ["reference"], {"handling": "ignore"}, None)
        ids = [str(101 + i) for i in range(len(values))]
        for aid, value in zip(ids, values):
            insert(self.path, base.row(aid, aid), response=None if value is None else json.dumps(value))
        rows = self.reader.load_task_rows(signal(ids))
        self.assertEqual({r["alert_id"]: r["agent_response"] for r in rows}, dict(zip(ids, values)))
        self.assertTrue(all(not r["agent_response_unavailable"] for r in rows))

    def test_missing_database_is_not_created_and_missing_table_is_reported(self):
        path = self.root / "missing.sqlite"
        with self.assertRaises(source.TaskReadError):
            source.LocalWorkflowTaskSource(path).load_task_rows(signal())
        self.assertFalse(path.exists())
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE uas_app_workflow_task_status")
        with self.assertRaises(source.TaskReadError):
            self.reader.load_task_rows(signal())

    def test_more_than_one_hundred_task_rows_are_not_silently_truncated(self):
        for index in range(101):
            insert(self.path, base.row(task_id=str(index)))
        with self.assertRaises(source.TaskSampleLimitError):
            self.reader.load_task_rows(signal())

    def test_total_byte_limit_rejects_partial_read(self):
        insert(self.path, base.row())
        with patch.object(source, "TOTAL_LIMIT", 1), self.assertRaises(source.TaskSampleLimitError):
            self.reader.load_task_rows(signal())

    def test_invalid_queries_and_empty_references_do_not_connect(self):
        for changed in (
            None,
            {},
            {**signal(), "mode": "demo"},
            {**signal(), "source_app": "other"},
            signal([str(i) for i in range(21)]),
            signal([None]),
            signal([" "]),
        ):
            with patch.object(source.sqlite3, "connect") as connect, self.assertRaises(ValueError):
                self.reader.load_task_rows(changed)
            connect.assert_not_called()
        with patch.object(source.sqlite3, "connect") as connect:
            self.assertEqual(self.reader.load_task_rows(signal([])), [])
        connect.assert_not_called()

    def test_connection_is_read_only_closed_and_database_bytes_unchanged(self):
        insert(self.path, base.row())
        before = self.path.read_bytes()
        connections, modes = [], []
        original = sqlite3.connect

        class Tracked(sqlite3.Connection):
            def close(self):
                modes.append(self.execute("PRAGMA query_only").fetchone()[0])
                super().close()

        def open_tracked(address, **options):
            self.assertIn("?mode=ro", address)
            self.assertTrue(options["uri"])
            db = original(address, factory=Tracked, **options)
            connections.append(db)
            return db

        with patch.object(source.sqlite3, "connect", side_effect=open_tracked):
            self.reader.load_task_rows(signal())
            with patch.object(source, "TOTAL_LIMIT", 1), self.assertRaises(source.TaskSampleLimitError):
                self.reader.load_task_rows(signal())
        self.assertEqual(modes, [1, 1])
        self.assertEqual(self.path.read_bytes(), before)
        for db in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

    def test_reader_integrates_with_pipeline_and_retry_reuses_analysis(self):
        insert(self.path, base.row())
        read = Mock(side_effect=self.reader.load_task_rows)
        analyze = Mock(side_effect=fixtures.analyze)
        processor = pipeline.SignalProcessor(
            self.root / "pipeline",
            load_rules=fixtures.loader(fixtures.fixtures.materials()),
            load_task_rows=read,
            analyze=analyze,
            sink=Sink(self.root / "receipts"),
            source_kind="workflow_task_store",
        )
        first, second = processor.process(signal()), processor.process(signal())
        self.assertEqual((first["status"], second["status"]), ("received", "received"))
        self.assertTrue(second["analysis_reused"])
        read.assert_called_once()
        analyze.assert_called_once()


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "synthetic.sqlite"
            database(path)
            for row in (base.row(), base.row(task_id="duplicate"), base.row("102", "second")):
                insert(path, row)
            rows = source.LocalWorkflowTaskSource(path).load_task_rows(signal(["101", "102"]))
            data = materials(signal(["101", "102"]), rows)
            report = {
                "mode": "demo",
                "status": "passed",
                "database_scope": "temporary_sqlite",
                "task_rows": len(rows),
                "material_samples": len(data["samples"]),
                "duplicate_task_rows": data["duplicate_task_rows"],
                "real_business_data": False,
                "model_calls": 0,
                "redis_calls": 0,
            }
        report["cleanup_ok"] = not root.exists()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        unittest.main(verbosity=2)
