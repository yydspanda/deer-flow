bash <<'SOC_STEP06A'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
from pathlib import Path

root = Path.cwd()
dependencies = {'my_workflows/zeus/rule_refine/contracts.py': '59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a', 'my_workflows/zeus/rule_refine/local_sink.py': '9120cfd91079acb79b17e8951af11f6d1b38f6aebf82b8d6ea9b2578581f47b0', 'my_workflows/zeus/rule_refine/rule_config_source.py': 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941', 'my_workflows/zeus/rule_refine/materials.py': '75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9', 'test/rule_refine/test_materials.py': '3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76', 'my_workflows/zeus/rule_refine/analysis.py': '1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117', 'test/rule_refine/test_analysis.py': 'a956151f1d3b2615cf745f1c879566af469fb18d8e8d1418c024fd2cf01d5f5f', 'my_workflows/zeus/rule_refine/filter_evaluation.py': 'e47ec1171bb6ca5948f5b15fc0a8eaa158782f1d3910dbcad08d262eb8a615fe', 'my_workflows/zeus/rule_refine/proposal_check.py': 'd6ca2c833edceab255c91798cef93d9687c93a641df4913f41c06db13dbb7338', 'test/rule_refine/test_proposal_check.py': '97ad1061ffe1e77d8e6b1d90527529cf82062c305078ff1f72a371adc2da47b4'}
for name, digest in dependencies.items():
    target = root / name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("停止：前序文件不存在或版本不同，未修改：" + name)
files = {
    'my_workflows/zeus/rule_refine/saved_delivery.py': r'''"""保存不可变分析包及各次模拟交付记录；交付重试只读取已保存分析。"""

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .analysis import _invalid_constant, _unique_object
from .materials import _encode
from .proposal_check import check_proposal

LIMIT = 4 * 1024 * 1024


class SavedAnalysisError(ValueError):
    pass


class SavedAnalysisConflictError(SavedAnalysisError):
    pass


def _read(path):
    if path.is_symlink():
        raise SavedAnalysisError("不读取符号链接记录")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise SavedAnalysisError("记录类型或大小无效")
        content = stream.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise SavedAnalysisError("记录过大")
    return content


def _publish(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".analysis-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
            return False
        except FileExistsError:
            if _read(path) != content:
                raise SavedAnalysisConflictError("已有不同内容，原记录未覆盖") from None
            return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def _bundle(materials, analysis):
    if not isinstance(materials, dict) or materials.get("schema_version") != "sec_know.rule_materials.v1":
        raise SavedAnalysisError("需要已有材料记录")
    if not isinstance(analysis, dict) or analysis.get("schema_version") != "sec_know.rule_analysis.v1":
        raise SavedAnalysisError("需要已有分析结果")
    if (materials.get("mode"), materials.get("material_source")) not in (
        ("demo", "synthetic_fixture"),
        ("live", "workflow_task_store"),
        ("replay", "workflow_task_store"),
    ):
        raise SavedAnalysisError("材料模式与来源不一致")
    return _encode(
        {"schema_version": "sec_know.rule_analysis_bundle.v1", "materials": materials, "analysis": analysis}, LIMIT
    )


class LocalAnalysisStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()

    def _path(self, record_id):
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{64}", record_id):
            raise SavedAnalysisError("分析记录标识无效")
        return self.directory / "records" / (record_id + ".json")

    def save(self, materials, analysis):
        content = _bundle(materials, analysis)
        record_id = hashlib.sha256(content).hexdigest()
        path = self._path(record_id)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        duplicate = _publish(path, content)
        return {"record_id": record_id, "duplicate": duplicate, "saved_file": str(path)}

    def load(self, record_id):
        content = _read(self._path(record_id))
        if hashlib.sha256(content).hexdigest() != record_id:
            raise SavedAnalysisError("分析记录摘要不一致")
        try:
            data = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            if data.get("schema_version") != "sec_know.rule_analysis_bundle.v1":
                raise ValueError("未知版本")
            if _bundle(data["materials"], data["analysis"]) != content:
                raise ValueError("记录不是原始规范化内容")
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise SavedAnalysisError("分析记录格式无效") from None
        return data


def _validate_receipt(receipt, recommendation):
    required = {"recommendation_id", "delivery_mode", "status", "applied", "duplicate", "content_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise SavedAnalysisError("模拟接收回执格式不正确")
    _encode(receipt, 16 * 1024)
    if (
        receipt["recommendation_id"] != recommendation["recommendation_id"]
        or receipt["content_sha256"] != hashlib.sha256(_encode(recommendation, LIMIT)).hexdigest()
        or receipt["delivery_mode"] != "mock"
        or receipt["status"] != "received"
        or receipt["applied"] is not False
        or type(receipt["duplicate"]) is not bool
    ):
        raise SavedAnalysisError("模拟接收回执与原建议不一致")


def deliver_saved(store, record_id, load_rules, sink):
    """每次重读配置并记录检查，随后用同一建议 ID 提交；不含模型生成入口。

    接收异常或回执无效记 delivery_unconfirmed，不能推断接收端没有保存。
    结果写盘错误原样抛出；交付可能已发生，重试继续沿用 record_id。
    """
    bundle = store.load(record_id)
    checked = check_proposal(bundle["materials"], bundle["analysis"], load_rules)
    attempt_id = uuid4().hex
    (store.directory / "attempts").mkdir(mode=0o700, exist_ok=True)
    folder = store.directory / "attempts" / record_id
    request = {
        "schema_version": "sec_know.rule_delivery_attempt.v1",
        "record_id": record_id,
        "attempt_id": attempt_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "submitting" if checked["status"] == "reviewable" else checked["status"],
        "check": checked,
    }
    _publish(folder / (attempt_id + ".request.json"), _encode(request, LIMIT))
    result = {
        "schema_version": "sec_know.rule_delivery_result.v1",
        "record_id": record_id,
        "attempt_id": attempt_id,
        "status": checked["status"],
        "check_status": checked["status"],
        "check_reason": checked["reason"],
        "delivery_mode": "mock",
        "automatic_apply": False,
        "receipt": None,
    }
    if checked["status"] == "reviewable":
        recommendation = checked["recommendation"]
        try:
            receipt = sink.submit(copy.deepcopy(recommendation))
            _validate_receipt(receipt, recommendation)
            result.update(status="received", receipt=copy.deepcopy(receipt))
        except Exception as error:
            result.update(status="delivery_unconfirmed", error_type=type(error).__name__)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    _publish(folder / (attempt_id + ".result.json"), _encode(result, LIMIT))
    return result
''',
    'test/rule_refine/test_saved_delivery.py': r'''"""本地分析留档与交付恢复测试；仅人工数据，不连接模型、数据库或 Redis。"""

import copy
import importlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_rule_saved_fixtures", Path(__file__).with_name("test_proposal_check.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
package = fixtures.fixtures.fixtures.package.__name__
delivery = importlib.import_module(package + ".saved_delivery")
Sink = importlib.import_module(package + ".local_sink").LocalRecommendationSink


class SavedDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = delivery.LocalAnalysisStore(self.root / "artifacts")
        self.data = fixtures.fixtures.materials()
        self.analysis = fixtures.analyze(self.data)
        self.load = fixtures.loader(self.data)
        self.sink = Sink(self.root / "receipts")

    def save(self):
        return self.store.save(self.data, self.analysis)

    def send(self, saved, sink=None):
        return delivery.deliver_saved(self.store, saved["record_id"], self.load, self.sink if sink is None else sink)

    def test_roundtrip_canonical_deduplication_and_unchanged_inputs(self):
        before = copy.deepcopy((self.data, self.analysis))
        first = self.save()
        second = self.store.save(dict(reversed(list(self.data.items()))), self.analysis)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["record_id"], second["record_id"])
        bundle = self.store.load(first["record_id"])
        self.assertEqual((bundle["materials"], bundle["analysis"]), before)
        self.assertEqual((self.data, self.analysis), before)
        self.analysis["diagnostic"] = "different content"
        third = self.save()
        self.assertNotEqual(first["record_id"], third["record_id"])
        self.assertEqual(self.store.load(first["record_id"]), bundle)

    def test_concurrent_identical_saves_publish_one_complete_record(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.save(), range(16)))
        self.assertEqual(len({row["record_id"] for row in results}), 1)
        self.assertEqual(sum(not row["duplicate"] for row in results), 1)
        self.assertEqual(len(list((self.store.directory / "records").iterdir())), 1)
        self.assertEqual(self.store.load(results[0]["record_id"])["analysis"], self.analysis)

    def test_new_storage_and_files_have_private_permissions(self):
        saved = self.save()
        self.send(saved)
        self.assertEqual(self.store.directory, (self.root / "artifacts").resolve())
        for path in self.store.directory.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_invalid_ids_corrupt_records_and_symlinks_are_rejected(self):
        saved = self.save()
        for identity in ("../outside", "x" * 64, "A" * 64, "", None):
            with self.subTest(identity=identity), self.assertRaises((ValueError, OSError)):
                self.store.load(identity)
        path = Path(saved["saved_file"])
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaises(ValueError):
            self.store.load(saved["record_id"])
        path.unlink()
        outside = self.root / "outside.json"
        outside.write_bytes(original)
        path.symlink_to(outside)
        with self.assertRaises((ValueError, OSError)):
            self.store.load(saved["record_id"])
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            self.store.load(saved["record_id"])

    def test_oversized_or_invalid_input_is_rejected_before_publication(self):
        for data, analysis in (
            (None, self.analysis),
            (self.data, None),
            ({**self.data, "large": "x" * (4 * 1024 * 1024)}, self.analysis),
        ):
            with self.assertRaises(ValueError):
                self.store.save(data, analysis)
        self.assertFalse(self.store.directory.exists())

    def test_repeat_delivery_uses_same_id_and_retains_attempt_history(self):
        saved = self.save()
        first, second = self.send(saved), self.send(saved)
        self.assertEqual((first["status"], second["status"]), ("received", "received"))
        self.assertFalse(first["receipt"]["duplicate"])
        self.assertTrue(second["receipt"]["duplicate"])
        self.assertEqual(first["receipt"]["recommendation_id"], self.analysis["recommendation"]["recommendation_id"])
        self.assertEqual(first["receipt"]["recommendation_id"], second["receipt"]["recommendation_id"])
        self.assertIs(second["automatic_apply"], False)
        self.assertEqual(len(list((self.store.directory / "attempts").rglob("*.result.json"))), 2)
        self.assertEqual(len(list(self.sink.directory.glob("*.json"))), 1)

    def test_lost_reply_after_receipt_is_unconfirmed_and_retry_is_duplicate(self):
        saved = self.save()

        def lost(payload):
            self.sink.submit(payload)
            raise TimeoutError("SECRET lost reply")

        first = self.send(saved, Mock(submit=Mock(side_effect=lost)))
        self.assertEqual(first["status"], "delivery_unconfirmed")
        self.assertNotIn("SECRET", json.dumps(first))
        retry = self.send(saved)
        self.assertEqual(retry["status"], "received")
        self.assertTrue(retry["receipt"]["duplicate"])
        self.assertEqual(retry["receipt"]["recommendation_id"], self.analysis["recommendation"]["recommendation_id"])

    def test_wrong_receipt_identity_hash_or_authority_is_not_accepted(self):
        saved = self.save()
        valid = self.sink.submit(self.analysis["recommendation"])
        for key, value in (
            ("recommendation_id", "wrong"),
            ("content_sha256", "0" * 64),
            ("status", "applied"),
            ("applied", True),
            ("duplicate", 1),
            ("delivery_mode", "live"),
            ("extra", "SECRET"),
        ):
            with self.subTest(key=key):
                outcome = self.send(saved, Mock(submit=Mock(return_value={**valid, key: value})))
                self.assertEqual(outcome["status"], "delivery_unconfirmed")
                self.assertIsNone(outcome["receipt"])

    def test_configuration_change_stops_new_attempt_and_keeps_old_receipt(self):
        saved = self.save()
        first = self.send(saved)
        self.load.return_value.get_rule.return_value["rule_config_version"] = "changed"
        sink = Mock()
        outcome = self.send(saved, sink)
        self.assertEqual((outcome["status"], outcome["check_reason"]), ("blocked", "configuration_changed"))
        sink.submit.assert_not_called()
        historical = self.store.directory / "attempts" / saved["record_id"] / (first["attempt_id"] + ".result.json")
        self.assertEqual(json.loads(historical.read_text(encoding="utf-8"))["status"], "received")

    def test_nonproposal_and_failed_analysis_are_saved_without_submission(self):
        for status, expected in (("insufficient_evidence", "not_applicable"), ("analysis_failed", "blocked")):
            if status == "analysis_failed":
                self.analysis = {**self.analysis, "status": status, "recommendation": None}
            else:
                answer = {**fixtures.fixtures.draft(), "status": status, "filter": None, "sample_alert_ids": []}
                self.analysis = fixtures.analyze(self.data, answer)
            sink = Mock()
            outcome = self.send(self.save(), sink)
            self.assertEqual(outcome["status"], expected)
            sink.submit.assert_not_called()

    def test_request_publication_failure_prevents_submission(self):
        saved, sink = self.save(), Mock()
        with patch.object(delivery, "_publish", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.send(saved, sink)
        sink.submit.assert_not_called()

    def test_result_publication_failure_preserves_receipt_and_retry_recovers(self):
        saved = self.save()
        publish = delivery._publish

        def fail_result(path, content):
            if str(path).endswith(".result.json"):
                raise OSError("result disk full")
            return publish(path, content)

        with patch.object(delivery, "_publish", side_effect=fail_result), self.assertRaises(OSError):
            self.send(saved)
        self.assertEqual(len(list(self.sink.directory.glob("*.json"))), 1)
        retry = self.send(saved)
        self.assertEqual(retry["status"], "received")
        self.assertTrue(retry["receipt"]["duplicate"])
        self.assertEqual(len(list((self.store.directory / "attempts").rglob("*.request.json"))), 2)
        self.assertEqual(len(list((self.store.directory / "attempts").rglob("*.result.json"))), 1)


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        parent = Path.home() / "sec-rule-refine-data"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="saved-delivery-demo-", dir=parent)).resolve()
        store = delivery.LocalAnalysisStore(root / "artifacts")
        data = fixtures.fixtures.materials()
        analysis = fixtures.analyze(data)
        saved = store.save(data, analysis)
        repeat = store.save(data, analysis)
        sink = Sink(root / "receipts")

        def lost_reply(payload):
            sink.submit(payload)
            raise TimeoutError("人工模拟：接收已完成，但回执丢失")

        first = delivery.deliver_saved(
            store, saved["record_id"], fixtures.loader(data), Mock(submit=Mock(side_effect=lost_reply))
        )
        retry = delivery.deliver_saved(store, saved["record_id"], fixtures.loader(data), sink)
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": retry["status"],
                    "saved_record_reused": repeat["duplicate"] and saved["record_id"] == repeat["record_id"],
                    "first_delivery": first["status"],
                    "retry_duplicate": retry["receipt"]["duplicate"],
                    "recommendation_id_preserved": retry["receipt"]["recommendation_id"]
                    == analysis["recommendation"]["recommendation_id"],
                    "automatic_apply": False,
                    "model_calls": 0,
                    "database_calls": 0,
                    "redis_calls": 0,
                    "output_dir": str(root),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
''',
}
expected = {'my_workflows/zeus/rule_refine/saved_delivery.py': '82dd2169020258b003651915bfd9909794b26d45ae91897b75fbb47f766189e8', 'test/rule_refine/test_saved_delivery.py': 'a83251f7609a096c5eb2d4d9f0c556e0ebadce8288a409fe2951523d6ae9afce'}
for name, content in files.items():
    raw = content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected[name]:
        raise SystemExit("停止：粘贴内容校验失败，未修改：" + name)
    compile(content, name, "exec")
    target = root / name
    if target.exists() and target.read_bytes() != raw:
        raise SystemExit("停止：已有不同版本，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("新增：" + name)
    else:
        print("已存在且一致：" + name)
PYWRITE
.venv/bin/python test/rule_refine/test_saved_delivery.py
.venv/bin/python test/rule_refine/test_saved_delivery.py --demo
SOC_STEP06A
