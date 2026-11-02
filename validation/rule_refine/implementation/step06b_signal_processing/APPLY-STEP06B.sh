bash <<'SOC_STEP06B'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
from pathlib import Path

root = Path.cwd()
dependencies = {'my_workflows/zeus/rule_refine/contracts.py': '59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a',
 'my_workflows/zeus/rule_refine/local_sink.py': '9120cfd91079acb79b17e8951af11f6d1b38f6aebf82b8d6ea9b2578581f47b0',
 'my_workflows/zeus/rule_refine/rule_config_source.py': 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941',
 'my_workflows/zeus/rule_refine/materials.py': '75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9',
 'test/rule_refine/test_materials.py': '3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76',
 'my_workflows/zeus/rule_refine/analysis.py': '1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117',
 'test/rule_refine/test_analysis.py': 'a956151f1d3b2615cf745f1c879566af469fb18d8e8d1418c024fd2cf01d5f5f',
 'my_workflows/zeus/rule_refine/filter_evaluation.py': 'e47ec1171bb6ca5948f5b15fc0a8eaa158782f1d3910dbcad08d262eb8a615fe',
 'my_workflows/zeus/rule_refine/proposal_check.py': 'd6ca2c833edceab255c91798cef93d9687c93a641df4913f41c06db13dbb7338',
 'test/rule_refine/test_proposal_check.py': '97ad1061ffe1e77d8e6b1d90527529cf82062c305078ff1f72a371adc2da47b4',
 'my_workflows/zeus/rule_refine/saved_delivery.py': '82dd2169020258b003651915bfd9909794b26d45ae91897b75fbb47f766189e8'}
for name, digest in dependencies.items():
    target = root / name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("停止：前序文件不存在或版本不同，未修改：" + name)
files = {
    'my_workflows/zeus/rule_refine/signal_processing.py': r'''"""单机信号分析串联；输入、分析结果留档，交付失败沿用原分析。"""

import copy
import fcntl
import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path

from .analysis import _invalid_constant, _unique_object
from .materials import _encode, _text, build_rule_materials
from .saved_delivery import LIMIT, LocalAnalysisStore, SavedAnalysisError, _bundle, _publish, _read, deliver_saved


def _load(path):
    raw = _read(path)
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        if not isinstance(value, dict) or _encode(value, LIMIT) != raw:
            raise ValueError("记录不是规范化对象")
        return value
    except (ValueError, TypeError, RecursionError):
        raise SavedAnalysisError("信号处理记录损坏") from None


class SignalProcessor:
    """注入配置工厂、任务读取、分析和接收端；本类不启动后台线程。

    load_task_rows(signal) 返回最多 100 个解码后的任务字典。
    analyze(materials) 返回第 4 步分析结果；实际调用限制由其适配器负责。
    本步只支持同一主机本地目录，不作为跨主机队列锁。
    """

    def __init__(self, directory, *, load_rules, load_task_rows, analyze, sink, source_kind):
        if source_kind not in {"synthetic_fixture", "workflow_task_store"}:
            raise ValueError("未知材料来源")
        if not all(callable(value) for value in (load_rules, load_task_rows, analyze)):
            raise ValueError("配置、任务读取和分析必须可调用")
        self.directory = Path(directory).expanduser().resolve()
        self.store = LocalAnalysisStore(self.directory / "artifacts")
        self.load_rules, self.load_task_rows, self.analyze = load_rules, load_task_rows, analyze
        self.sink, self.source_kind = sink, source_kind

    def process(self, signal):
        # 先复制有界输入；稳定身份定位目录，完整输入检查防止同一身份换内容。
        signal = json.loads(_encode(signal, 64 * 1024))
        if not isinstance(signal, dict) or signal.get("schema_version") != "sec_know.rule_burst_signal.v1":
            raise ValueError("需要 v1 突增信号")
        if (signal.get("mode"), self.source_kind) not in {
            ("demo", "synthetic_fixture"),
            ("live", "workflow_task_store"),
            ("replay", "workflow_task_store"),
        }:
            raise ValueError("信号模式与材料来源不一致")
        scope = {
            key: _text(signal.get(key))
            for key in ("mode", "namespace", "source_app", "company_code", "topic", "rule_code", "signal_id")
        }
        job_id = hashlib.sha256(_encode(scope, 16 * 1024)).hexdigest()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.directory / "jobs").mkdir(mode=0o700, exist_ok=True)
        folder = self.directory / "jobs" / job_id
        folder.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(folder / "process.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise SavedAnalysisError("锁文件类型无效")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self._summary("busy", job_id, False, 0)
            try:
                _publish(
                    folder / "input.json",
                    _encode(
                        {
                            "schema_version": "sec_know.rule_signal_input.v1",
                            "signal": signal,
                            "source_kind": self.source_kind,
                        },
                        LIMIT,
                    ),
                )
                return self._process(folder, job_id, signal)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _summary(status, job_id, reused, calls):
        return {
            "schema_version": "sec_know.rule_signal_processing.v1",
            "status": status,
            "job_id": job_id,
            "analysis_reused": reused,
            "model_calls_this_attempt": calls,
            "automatic_apply": False,
        }

    def _check_binding(self, materials, signal):
        scope = {
            key: _text(signal.get(key))
            for key in ("signal_id", "namespace", "source_app", "company_code", "topic", "rule_code")
        }
        window = {
            "start": datetime.fromisoformat(signal["window_start"]).isoformat(),
            "end": datetime.fromisoformat(signal["window_end"]).isoformat(),
            "count": signal["current_count"],
            "time_basis": signal.get("time_basis", "unknown"),
        }
        if (
            not isinstance(materials, dict)
            or materials.get("mode") != signal["mode"]
            or materials.get("material_source") != self.source_kind
            or materials.get("signal_scope") != scope
            or materials.get("window") != window
            or materials.get("requested_sample_count") != len({_text(value) for value in signal["sample_alert_ids"]})
        ):
            raise SavedAnalysisError("已保存材料与当前信号不一致")

    def _process(self, folder, job_id, signal):
        started, result = folder / "analysis_started.json", folder / "analysis.json"
        reused, calls = False, 0
        if os.path.lexists(started):
            marker = _load(started)
            if (
                set(marker) != {"schema_version", "materials"}
                or marker["schema_version"] != "sec_know.rule_signal_started.v1"
            ):
                raise SavedAnalysisError("分析开始记录无效")
            self._check_binding(marker["materials"], signal)
            if not os.path.lexists(result):
                return self._summary("analysis_unconfirmed", job_id, False, None)
            bundle = _load(result)
            if (
                set(bundle) != {"schema_version", "materials", "analysis"}
                or bundle["schema_version"] != "sec_know.rule_analysis_bundle.v1"
                or bundle["materials"] != marker["materials"]
            ):
                raise SavedAnalysisError("分析结果与原材料不一致")
            materials, analysis, reused = bundle["materials"], bundle["analysis"], True
            _bundle(materials, analysis)
        else:
            if os.path.lexists(result):
                raise SavedAnalysisError("已有分析结果但缺少开始记录")
            try:
                rules = self.load_rules()
                rows = self.load_task_rows(copy.deepcopy(signal))
                materials = build_rule_materials(signal, rules, rows, source_kind=self.source_kind)
            except Exception as error:
                return {**self._summary("materials_failed", job_id, False, 0), "error_type": type(error).__name__}
            _publish(
                started,
                _encode({"schema_version": "sec_know.rule_signal_started.v1", "materials": materials}, LIMIT),
            )
            try:
                analysis = self.analyze(copy.deepcopy(materials))
                content = _bundle(materials, analysis)
            except Exception as error:
                return {
                    **self._summary("analysis_unconfirmed", job_id, False, None),
                    "error_type": type(error).__name__,
                }
            # 先在本信号目录保存完整结果；后续索引或交付失败可从这里恢复。
            _publish(result, content)
            value = analysis.get("model_calls")
            calls = value if type(value) is int and value >= 0 else None
        saved = self.store.save(materials, analysis)
        delivery = deliver_saved(self.store, saved["record_id"], self.load_rules, self.sink)
        return {
            **self._summary(delivery["status"], job_id, reused, calls),
            "analysis_status": analysis.get("status"),
            "record_id": saved["record_id"],
            "delivery": delivery,
        }
''',
    'test/rule_refine/test_signal_processing.py': r'''"""人工信号完整串联与恢复测试；不连接模型、数据库或 Redis。"""

import copy
import importlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_rule_signal_fixtures", Path(__file__).with_name("test_proposal_check.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
base = fixtures.fixtures.fixtures
module = importlib.import_module(base.package.__name__ + ".signal_processing")
Sink = importlib.import_module(base.package.__name__ + ".local_sink").LocalRecommendationSink


def dependencies(root):
    return {
        "load_rules": fixtures.loader(fixtures.fixtures.materials()),
        "load_task_rows": Mock(return_value=[base.row()]),
        "analyze": Mock(side_effect=fixtures.analyze),
        "sink": Sink(root / "receipts"),
        "source_kind": "synthetic_fixture",
    }


class SignalProcessingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.options = dependencies(self.root)
        self.processor = module.SignalProcessor(self.root / "pipeline", **self.options)
        self.signal = base.signal()

    def run_signal(self):
        return self.processor.process(self.signal)

    def test_complete_chain_and_repeat_reuse_analysis_and_original_identity(self):
        original = copy.deepcopy(self.signal)
        first, second = self.run_signal(), self.run_signal()
        self.assertEqual((first["status"], second["status"]), ("received", "received"))
        self.assertFalse(first["analysis_reused"])
        self.assertTrue(second["analysis_reused"])
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertTrue(second["delivery"]["receipt"]["duplicate"])
        self.assertEqual(
            first["delivery"]["receipt"]["recommendation_id"], second["delivery"]["receipt"]["recommendation_id"]
        )
        self.assertEqual((self.options["analyze"].call_count, self.options["load_task_rows"].call_count), (1, 1))
        self.assertEqual(self.signal, original)
        self.assertEqual(second["model_calls_this_attempt"], 0)
        self.assertIs(second["automatic_apply"], False)

    def test_lost_reply_retries_delivery_without_regeneration(self):
        sink = self.options["sink"]

        def lost(payload):
            sink.submit(payload)
            raise TimeoutError("private response")

        self.processor.sink = Mock(submit=Mock(side_effect=lost))
        first = self.run_signal()
        self.processor.sink = sink
        retry = self.run_signal()
        self.assertEqual((first["status"], retry["status"]), ("delivery_unconfirmed", "received"))
        self.assertTrue(retry["delivery"]["receipt"]["duplicate"])
        self.options["analyze"].assert_called_once()
        self.options["load_task_rows"].assert_called_once()

    def test_material_failure_can_retry_before_analysis_started(self):
        self.options["load_task_rows"].side_effect = ConnectionError("private database")
        result = self.run_signal()
        self.assertEqual(result["status"], "materials_failed")
        self.assertNotIn("private", json.dumps(result))
        self.options["analyze"].assert_not_called()
        self.assertFalse(list(self.processor.directory.rglob("analysis_started.json")))
        self.options["load_task_rows"].side_effect = None
        self.assertEqual(self.run_signal()["status"], "received")

    def test_analysis_exception_remains_unconfirmed_and_is_not_automatically_repeated(self):
        self.options["analyze"].side_effect = TimeoutError("private model")
        first, second = self.run_signal(), self.run_signal()
        self.assertEqual((first["status"], second["status"]), ("analysis_unconfirmed", "analysis_unconfirmed"))
        self.assertIsNone(second["model_calls_this_attempt"])
        self.options["analyze"].assert_called_once()
        self.options["load_task_rows"].assert_called_once()
        self.assertFalse(self.options["sink"].directory.exists())
        options = dependencies(self.root / "write-failure")
        processor = module.SignalProcessor(self.root / "write-failure" / "pipeline", **options)
        publish = module._publish

        def fail_result(path, content):
            if path.name == "analysis.json":
                raise OSError("disk")
            return publish(path, content)

        with patch.object(module, "_publish", side_effect=fail_result), self.assertRaises(OSError):
            processor.process(self.signal)
        self.assertEqual(processor.process(self.signal)["status"], "analysis_unconfirmed")
        options["analyze"].assert_called_once()

    def test_saved_bundle_recovers_when_content_index_write_fails(self):
        with patch.object(self.processor.store, "save", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.run_signal()
        self.assertEqual(len(list(self.processor.directory.rglob("analysis.json"))), 1)
        retry = self.run_signal()
        self.assertEqual(retry["status"], "received")
        self.assertTrue(retry["analysis_reused"])
        self.options["analyze"].assert_called_once()

    def test_start_marker_failure_prevents_analysis_and_allows_retry(self):
        publish = module._publish

        def fail_marker(path, content):
            if path.name == "analysis_started.json":
                raise OSError("disk")
            return publish(path, content)

        with patch.object(module, "_publish", side_effect=fail_marker), self.assertRaises(OSError):
            self.run_signal()
        self.options["analyze"].assert_not_called()
        self.assertEqual(self.run_signal()["status"], "received")

    def test_changed_content_under_same_signal_identity_conflicts(self):
        self.run_signal()
        self.signal["current_count"] += 1
        with self.assertRaises(ValueError):
            self.run_signal()
        self.options["analyze"].assert_called_once()

    def test_same_signal_id_in_distinct_namespaces_is_isolated(self):
        first = self.run_signal()
        self.signal["namespace"] = "another-demo-namespace"
        second = self.run_signal()
        self.assertEqual(second["status"], "received")
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertEqual(self.options["analyze"].call_count, 2)

    def test_resume_rechecks_current_configuration_without_regenerating(self):
        self.run_signal()
        self.options["load_rules"].return_value.get_rule.return_value["rule_config_version"] = "changed"
        result = self.run_signal()
        self.assertEqual((result["status"], result["delivery"]["check_reason"]), ("blocked", "configuration_changed"))
        self.options["analyze"].assert_called_once()

    def test_empty_materials_nonproposal_and_failed_analysis_are_saved_and_reused(self):
        for case in ("empty", "no_change", "analysis_failed"):
            with self.subTest(case=case):
                options = dependencies(self.root / case)
                if case == "empty":
                    options["load_task_rows"].return_value = []
                elif case == "no_change":
                    answer = {
                        **fixtures.fixtures.draft(),
                        "status": "no_change",
                        "filter": None,
                        "sample_alert_ids": [],
                    }
                    options["analyze"].side_effect = lambda data: fixtures.analyze(data, answer)
                else:
                    options["analyze"].side_effect = lambda data: fixtures.fixtures.analysis.analyze_materials(
                        data, lambda _: "invalid"
                    )
                processor = module.SignalProcessor(self.root / case / "pipeline", **options)
                first, second = processor.process(self.signal), processor.process(self.signal)
                self.assertEqual(first["status"], "blocked" if case == "analysis_failed" else "not_applicable")
                self.assertTrue(second["analysis_reused"])
                options["analyze"].assert_called_once()
                self.assertFalse(options["sink"].directory.exists())

    def test_concurrent_processors_and_separate_process_observe_same_lock(self):
        entered, release = threading.Event(), threading.Event()

        def slow(data):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test synchronization")
            return fixtures.analyze(data)

        self.options["analyze"].side_effect = slow
        other = module.SignalProcessor(self.processor.directory, **self.options)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.run_signal)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(other.process(self.signal)["status"], "busy")
                lock = next(self.processor.directory.rglob("process.lock"))
                code = "import fcntl,sys\nf=open(sys.argv[1],'r+b')\ntry:\n fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:\n sys.exit(7)\nsys.exit(9)"
                child = subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True, timeout=5)
                self.assertEqual(child.returncode, 7, child.stderr)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)["status"], "received")
        self.assertTrue(other.process(self.signal)["analysis_reused"])
        self.options["analyze"].assert_called_once()

    def test_corrupt_saved_bundle_fails_visibly_without_regeneration(self):
        first = self.run_signal()
        folder = self.processor.directory / "jobs" / first["job_id"]
        path = folder / "analysis.json"
        original = path.read_bytes()
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_signal()
        self.options["analyze"].assert_called_once()
        path.write_bytes(original)
        other = self.processor.process({**self.signal, "namespace": "another-namespace"})
        other_folder = self.processor.directory / "jobs" / other["job_id"]
        for name in ("analysis_started.json", "analysis.json"):
            (folder / name).write_bytes((other_folder / name).read_bytes())
        self.processor.sink = Mock()
        with self.assertRaises(ValueError):
            self.run_signal()
        self.assertEqual(self.options["analyze"].call_count, 2)
        self.processor.sink.submit.assert_not_called()

    def test_callbacks_receive_copies_of_signal_and_materials(self):
        def rows(signal):
            signal["current_count"] = 2
            return [base.row()]

        def analyze(materials):
            result = fixtures.analyze(materials)
            materials["window"]["count"] = 2
            return result

        self.options["load_task_rows"].side_effect = rows
        self.options["analyze"].side_effect = analyze
        outcome = self.run_signal()
        self.assertEqual(outcome["status"], "received")
        saved = self.processor.store.load(outcome["record_id"])
        self.assertEqual((self.signal["current_count"], saved["materials"]["window"]["count"]), (1000, 1000))


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        parent = Path.home() / "sec-rule-refine-data"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="signal-chain-demo-", dir=parent)).resolve()
        options = dependencies(root)
        sink = options["sink"]

        def lost(payload):
            sink.submit(payload)
            raise TimeoutError("人工模拟回执丢失")

        processor = module.SignalProcessor(root / "pipeline", **options)
        processor.sink = Mock(submit=Mock(side_effect=lost))
        first = processor.process(base.signal())
        processor.sink = sink
        retry = processor.process(base.signal())
        saved = processor.store.load(retry["record_id"])
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": retry["status"],
                    "first_delivery": first["status"],
                    "analysis_calls": options["analyze"].call_count,
                    "task_load_calls": options["load_task_rows"].call_count,
                    "analysis_reused_on_retry": retry["analysis_reused"],
                    "retry_duplicate": retry["delivery"]["receipt"]["duplicate"],
                    "recommendation_id_preserved": retry["delivery"]["receipt"]["recommendation_id"]
                    == saved["analysis"]["recommendation"]["recommendation_id"],
                    "automatic_apply": False,
                    "model_calls": 0,
                    "database_calls": 0,
                    "redis_calls": 0,
                    "real_ingress_connected": False,
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
expected = {'my_workflows/zeus/rule_refine/signal_processing.py': 'df465f57c528d17f3fdfdf3c1dff8ae55739a05d27ffd414cc40954ed37efac0',
 'test/rule_refine/test_signal_processing.py': '746d0498bec4b08763766c232bba32881535891b20f0ab5d66df5f4256914428'}
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
.venv/bin/python test/rule_refine/test_signal_processing.py
.venv/bin/python test/rule_refine/test_signal_processing.py --demo
SOC_STEP06B
