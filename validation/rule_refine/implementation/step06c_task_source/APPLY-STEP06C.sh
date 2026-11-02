bash <<'SOC_STEP06C'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
import os
import tempfile
from pathlib import Path

root = Path.cwd()
dependencies = {'my_workflows/zeus/rule_refine/contracts.py': '59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a',
 'my_workflows/zeus/rule_refine/local_sink.py': '9120cfd91079acb79b17e8951af11f6d1b38f6aebf82b8d6ea9b2578581f47b0',
 'my_workflows/zeus/rule_refine/rule_config_source.py': 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941',
 'test/rule_refine/test_materials.py': '3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76',
 'my_workflows/zeus/rule_refine/analysis.py': '1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117',
 'test/rule_refine/test_analysis.py': 'a956151f1d3b2615cf745f1c879566af469fb18d8e8d1418c024fd2cf01d5f5f',
 'my_workflows/zeus/rule_refine/filter_evaluation.py': 'e47ec1171bb6ca5948f5b15fc0a8eaa158782f1d3910dbcad08d262eb8a615fe',
 'my_workflows/zeus/rule_refine/proposal_check.py': 'd6ca2c833edceab255c91798cef93d9687c93a641df4913f41c06db13dbb7338',
 'test/rule_refine/test_proposal_check.py': '97ad1061ffe1e77d8e6b1d90527529cf82062c305078ff1f72a371adc2da47b4',
 'my_workflows/zeus/rule_refine/saved_delivery.py': '82dd2169020258b003651915bfd9909794b26d45ae91897b75fbb47f766189e8',
 'my_workflows/zeus/rule_refine/signal_processing.py': 'df465f57c528d17f3fdfdf3c1dff8ae55739a05d27ffd414cc40954ed37efac0'}
for name, digest in dependencies.items():
    target = root / name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("停止：前序文件不存在或版本不同，未修改：" + name)
material = 'my_workflows/zeus/rule_refine/materials.py'
old_hash = '75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9'
new_hash = '5d06b99874b667f8c9edd1d8a2e2840fa25c1f663eca55e20d24f967239f0e33'
original = (root / material).read_bytes()
digest = hashlib.sha256(original).hexdigest()
if digest not in {old_hash, new_hash}:
    raise SystemExit("停止：materials.py 版本不同，未修改。")
updated = original.decode("utf-8")
if digest == old_hash:
    before = '        response = row.get("agent_response")\n'
    after = before + '        if row.get("agent_response_unavailable") is True:\n            issue("stored_response_omitted", alert_id)\n            response = None\n'
    if updated.count(before) != 1:
        raise SystemExit("停止：材料更新位置不匹配，未修改。")
    updated = updated.replace(before, after, 1)
backup = root / (material + ".before-step06c")
if backup.exists() and hashlib.sha256(backup.read_bytes()).hexdigest() != old_hash:
    raise SystemExit("停止：已有材料副本内容不同，未覆盖。")
files = {material: updated,
    'my_workflows/zeus/rule_refine/task_source.py': r'''"""从显式指定的本地 SQLite 任务表只读获取信号样本；不加载应用配置。"""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .analysis import _invalid_constant, _unique_object
from .materials import _encode, _text

ALERT_LIMIT = 1024 * 1024
RESPONSE_LIMIT = 16 * 1024
TOTAL_LIMIT = 8 * 1024 * 1024


class TaskReadError(ValueError):
    pass


class TaskSampleLimitError(TaskReadError):
    pass


def _decode(raw, size, limit, *, require_object=False):
    if size is None:
        return None, False
    if raw is None or size > limit:
        return None, True
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if require_object and not isinstance(value, dict):
            raise ValueError("原告警不是对象")
        _encode(value, limit)
        return value, False
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None, True


class LocalWorkflowTaskSource:
    """只按应用和引用告警 ID 取任务；规则与企业核对仍由材料组装负责。"""

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    def load_task_rows(self, signal):
        # 这里只校验查询所需字段；完整信号由材料组装校验。
        if (
            not isinstance(signal, dict)
            or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
            or signal.get("mode") not in {"live", "replay"}
            or signal.get("source_app") != "zeus"
        ):
            raise TaskReadError("需要 zeus 的 live/replay 信号")
        refs = signal.get("sample_alert_ids")
        if not isinstance(refs, list) or len(refs) > 20:
            raise TaskReadError("最多读取 20 个信号引用告警")
        refs = list(dict.fromkeys(_text(value) for value in refs))
        if not refs:
            return []
        if not self.path.is_file():
            raise TaskReadError("本地数据库文件不存在；未创建文件")
        placeholders = ",".join("?" for _ in refs)
        query = f"""
            SELECT id, app_code, flow_id, alert_id, status, created_date,
                   length(CAST(alert_data AS BLOB)) AS alert_size,
                   CASE WHEN length(CAST(alert_data AS BLOB)) <= ?
                        THEN CAST(alert_data AS BLOB) END AS alert_blob,
                   length(CAST(agent_response AS BLOB)) AS response_size,
                   CASE WHEN length(CAST(agent_response AS BLOB)) <= ?
                        THEN CAST(agent_response AS BLOB) END AS response_blob
            FROM uas_app_workflow_task_status
            WHERE app_code = ? AND flow_id = ? AND alert_id IN ({placeholders})
            ORDER BY created_date DESC, id DESC LIMIT 101
        """
        result, total = [], 0
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                deadline = time.monotonic() + 5
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                db.execute("BEGIN")
                cursor = db.execute(query, (ALERT_LIMIT, RESPONSE_LIMIT, "zeus", "alert_agent", *refs))
                for row in cursor:
                    if len(result) >= 100:
                        raise TaskSampleLimitError("引用告警对应任务超过 100 条；未截取部分记录")
                    total += sum(len(row[key]) for key in ("alert_blob", "response_blob") if row[key] is not None)
                    if total > TOTAL_LIMIT:
                        raise TaskSampleLimitError("本次任务材料超过 8 MiB；未返回部分记录")
                    alert, _ = _decode(row["alert_blob"], row["alert_size"], ALERT_LIMIT, require_object=True)
                    response, omitted = _decode(row["response_blob"], row["response_size"], RESPONSE_LIMIT)
                    result.append(
                        {
                            **{
                                key: row[key]
                                for key in ("id", "app_code", "flow_id", "alert_id", "status", "created_date")
                            },
                            "alert_data": alert,
                            "agent_response": response,
                            "agent_response_unavailable": omitted,
                        }
                    )
        except sqlite3.Error as error:
            raise TaskReadError("只读任务查询失败：" + type(error).__name__) from None
        return result
''',
    'test/rule_refine/check_local_task_source.py': r'''"""只读检查 LOCAL 任务读取适配器；只输出数量，不输出告警或模型正文。"""

import importlib
import json
import os
import sqlite3
import sys
import time
import types
from collections import Counter
from contextlib import closing
from pathlib import Path

root = Path(__file__).resolve().parents[2]
package = types.ModuleType("_rule_refine_local_task_check")
package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
sys.modules[package.__name__] = package
source = importlib.import_module(package.__name__ + ".task_source")


def main():
    summary = {
        "check": "rule_refine_task_source",
        "database_scope": "local_sqlite",
        "sample_basis": "alert_ids_from_latest_20_task_rows_diagnostic_only",
        "read_only": True,
        "model_calls": 0,
        "redis_calls": 0,
    }
    if os.environ.get("env_profile", "LOCAL").upper() != "LOCAL":
        return {**summary, "status": "skipped_non_local"}
    path = root / "nas/data/sec_know_model.sqlite"
    if not path.is_file():
        return {**summary, "status": "database_missing_not_created"}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            latest = db.execute(
                "SELECT alert_id FROM uas_app_workflow_task_status WHERE app_code=? AND flow_id=? "
                "ORDER BY created_date DESC, id DESC LIMIT 20",
                ("zeus", "alert_agent"),
            ).fetchall()
        refs = list(dict.fromkeys(row[0] for row in latest))
        # 此处仅复用信号中的查询字段，不构造突增事件、不派发分析。
        rows = source.LocalWorkflowTaskSource(path).load_task_rows(
            {
                "schema_version": "sec_know.rule_burst_signal.v1",
                "mode": "replay",
                "source_app": "zeus",
                "sample_alert_ids": refs,
            }
        )
        states = {"created", "queening", "running", "successed", "failed", "update", "delete"}
        return {
            **summary,
            "status": "checked" if rows else "no_local_samples",
            "requested_alerts": len(refs),
            "task_rows": len(rows),
            "distinct_alerts": len({row["alert_id"] for row in rows}),
            "task_status_counts": dict(Counter(row["status"] if row["status"] in states else "other" for row in rows)),
            "unusable_alert_data_rows": sum(row["alert_data"] is None for row in rows),
            "omitted_response_rows": sum(row["agent_response_unavailable"] for row in rows),
        }
    except (sqlite3.Error, ValueError) as error:
        return {**summary, "status": "check_failed", "error_type": type(error).__name__}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(1 if outcome["status"] == "check_failed" else 0)
''',
    'test/rule_refine/test_task_source.py': r'''"""本地任务读取测试；使用临时人工数据库，不接触业务库、模型或 Redis。"""

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
''',
}
expected = {'my_workflows/zeus/rule_refine/materials.py': '5d06b99874b667f8c9edd1d8a2e2840fa25c1f663eca55e20d24f967239f0e33',
 'my_workflows/zeus/rule_refine/task_source.py': 'ee225d85ee000eba1600698a54d65cb10e840a32179838b77fe651289e25cb8e',
 'test/rule_refine/check_local_task_source.py': '927807b115cf1c21cb66efd934ce56c63c871f845e992f52b709883a95633b80',
 'test/rule_refine/test_task_source.py': 'f89e2aa6bb05a0c7fac104ea5e57f538e66c3f2d9526f7d786089a047ad4558d'}
for name, content in files.items():
    raw = content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected[name]:
        raise SystemExit("停止：粘贴内容校验失败，未修改：" + name)
    compile(content, name, "exec")
    target = root / name
    if name != material and target.exists() and target.read_bytes() != raw:
        raise SystemExit("停止：已有不同版本，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if name == material and digest == old_hash:
        if not backup.exists():
            with backup.open("xb") as stream:
                stream.write(original)
        fd, temporary = tempfile.mkstemp(prefix=".step06c-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, target.stat().st_mode & 0o777)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        print("更新并保留原副本：" + name)
    elif not target.exists():
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("新增：" + name)
    else:
        print("已存在且一致：" + name)
PYWRITE
.venv/bin/python test/rule_refine/test_materials.py
.venv/bin/python test/rule_refine/test_task_source.py
.venv/bin/python test/rule_refine/test_task_source.py --demo
.venv/bin/python test/rule_refine/check_local_task_source.py
SOC_STEP06C
