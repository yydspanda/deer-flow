bash <<'SOC_STEP06D'
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
 'my_workflows/zeus/rule_refine/materials.py': '5d06b99874b667f8c9edd1d8a2e2840fa25c1f663eca55e20d24f967239f0e33',
 'my_workflows/zeus/rule_refine/task_source.py': 'ee225d85ee000eba1600698a54d65cb10e840a32179838b77fe651289e25cb8e',
 'my_workflows/zeus/rule_refine/counter.py': '2b05b81ee7f28034989b4866f5eed5dc2dc5de8a010b9152792d9715e02b95c2'}
updates = {'my_workflows/zeus/rule_refine/signal_processing.py': {'old': 'df465f57c528d17f3fdfdf3c1dff8ae55739a05d27ffd414cc40954ed37efac0',
                                                        'changes': [('from .analysis import _invalid_constant, '
                                                                     '_unique_object\n'
                                                                     'from .materials import _encode, _text, '
                                                                     'build_rule_materials\n',
                                                                     'from .analysis import _invalid_constant, '
                                                                     '_unique_object, build_analysis_request\n'
                                                                     'from .materials import _alert_id, _encode, '
                                                                     '_text, build_rule_materials\n'),
                                                                    ('\n'
                                                                     '\n'
                                                                     'class SignalProcessor:\n'
                                                                     '    """注入配置工厂、任务读取、分析和接收端；本类不启动后台线程。\n',
                                                                     '\n'
                                                                     '\n'
                                                                     'def _missing_tasks(signal, rows):\n'
                                                                     '    wanted = {_text(value) for value in '
                                                                     'signal["sample_alert_ids"]}\n'
                                                                     '    present = set()\n'
                                                                     '    for row in rows:\n'
                                                                     '        if (\n'
                                                                     '            not isinstance(row, dict)\n'
                                                                     '            or row.get("app_code") != '
                                                                     'signal["source_app"]\n'
                                                                     '            or row.get("flow_id") != '
                                                                     '"alert_agent"\n'
                                                                     '        ):\n'
                                                                     '            continue\n'
                                                                     '        try:\n'
                                                                     '            '
                                                                     'present.add(_alert_id(row.get("alert_id")))\n'
                                                                     '        except ValueError:\n'
                                                                     '            continue\n'
                                                                     '    return len(wanted - present)\n'
                                                                     '\n'
                                                                     '\n'
                                                                     'class SignalProcessor:\n'
                                                                     '    """注入配置工厂、任务读取、分析和接收端；本类不启动后台线程。\n'),
                                                                    ('            except Exception as error:\n'
                                                                     '                return '
                                                                     '{**self._summary("materials_failed", job_id, '
                                                                     'False, 0), "error_type": type(error).__name__}\n'
                                                                     '            _publish(\n'
                                                                     '                started,\n',
                                                                     '                missing = _missing_tasks(signal, '
                                                                     'rows)\n'
                                                                     '                if missing:\n'
                                                                     '                    return '
                                                                     '{**self._summary("materials_pending", job_id, '
                                                                     'False, 0), "missing_task_count": missing}\n'
                                                                     '                if '
                                                                     'build_analysis_request(materials) is None:\n'
                                                                     '                    return '
                                                                     'self._summary("materials_unavailable", job_id, '
                                                                     'False, 0)\n'
                                                                     '            except Exception as error:\n'
                                                                     '                return '
                                                                     '{**self._summary("materials_failed", job_id, '
                                                                     'False, 0), "error_type": type(error).__name__}\n'
                                                                     '            _publish(\n'
                                                                     '                started,\n')]},
 'test/rule_refine/test_signal_processing.py': {'old': '746d0498bec4b08763766c232bba32881535891b20f0ab5d66df5f4256914428',
                                                'changes': [('    def '
                                                             'test_empty_materials_nonproposal_and_failed_analysis_are_saved_and_reused(self):\n'
                                                             '        for case in ("empty", "no_change", '
                                                             '"analysis_failed"):\n',
                                                             '    def '
                                                             'test_nonproposal_and_failed_analysis_are_saved_and_reused(self):\n'
                                                             '        for case in ("no_change", "analysis_failed"):\n'),
                                                            ('                if case == "empty":\n'
                                                             '                    '
                                                             'options["load_task_rows"].return_value = []\n'
                                                             '                elif case == "no_change":\n',
                                                             '                if case == "no_change":\n')]}}
files = {
    'my_workflows/zeus/rule_refine/pending_signal.py': r'''"""处理一个作用域的待处理信号；完成结果落盘后，按原信号身份确认。"""

import copy
import hashlib
import json
import re

from .analysis import build_analysis_request
from .materials import _encode, _text
from .signal_processing import _load

SCOPE = ("source_app", "company_code", "topic", "rule_code")


def _durable_result(processor, signal, outcome):
    delivery = outcome["delivery"]
    record_id, attempt_id = outcome["record_id"], delivery["attempt_id"]
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("交付尝试身份无效")
    bundle = processor.store.load(record_id)
    processor._check_binding(bundle["materials"], signal)
    if build_analysis_request(bundle["materials"]) is None:
        raise ValueError("原记录没有可分析材料")
    scope = {key: _text(signal.get(key)) for key in ("mode", "namespace", *SCOPE, "signal_id")}
    job_id = hashlib.sha256(_encode(scope, 16 * 1024)).hexdigest()
    folder = processor.directory / "jobs" / job_id
    expected = {
        "schema_version": "sec_know.rule_signal_input.v1",
        "signal": signal,
        "source_kind": processor.source_kind,
    }
    if (
        outcome.get("job_id") != job_id
        or _load(folder / "input.json") != expected
        or _load(folder / "analysis.json") != bundle
    ):
        raise ValueError("完整信号与保存的分析不一致")
    path = processor.store.directory / "attempts" / record_id / (attempt_id + ".result.json")
    if (
        delivery.get("schema_version") != "sec_know.rule_delivery_result.v1"
        or delivery.get("record_id") != record_id
        or delivery.get("status") != outcome["status"]
        or delivery.get("automatic_apply") is not False
        or _load(path) != delivery
    ):
        raise ValueError("交付结果尚未完整保存或不一致")


def consume_pending(counter, observation, processor):
    """只消费已有信号，不增加计数、不启动后台服务；确认失败不重做分析。"""
    report = {"schema_version": "sec_know.rule_pending_processing.v1", "acknowledged": False, "automatic_apply": False}

    def result(status, error=None, **values):
        extra = {} if error is None else {"error_type": type(error).__name__}
        return {**report, "status": status, **extra, **values}

    try:
        observation = json.loads(_encode(observation, 64 * 1024))
        if not isinstance(observation, dict) or observation.get("mode") != counter.mode:
            raise ValueError("观察模式不一致")
        expected = {"mode": counter.mode, "namespace": counter.namespace, **{key: observation[key] for key in SCOPE}}
        if expected["mode"] not in {"demo", "replay", "live"} or any(_text(v) != v for v in expected.values()):
            raise ValueError("作用域不规范")
    except Exception as error:
        return result("invalid_signal", error)
    try:
        signal = counter.pending(copy.deepcopy(observation))
    except Exception as error:
        return result("pending_read_failed", error)
    if signal is None:
        return result("idle")
    try:
        signal = json.loads(_encode(signal, 64 * 1024))
        if (
            not isinstance(signal, dict)
            or signal.get("schema_version") != "sec_know.rule_burst_signal.v1"
            or any(signal.get(key) != value for key, value in expected.items())
            or _text(signal.get("signal_id")) != signal["signal_id"]
        ):
            raise ValueError("信号作用域不一致")
    except Exception as error:
        return result("invalid_signal", error)
    try:
        outcome = processor.process(copy.deepcopy(signal))
        if not isinstance(outcome, dict) or outcome.get("schema_version") != "sec_know.rule_signal_processing.v1":
            raise ValueError("处理结果格式无效")
    except Exception as error:
        return result("processing_failed", error)
    report["processing"] = outcome
    if outcome.get("status") not in {"received", "not_applicable"}:
        return result("retained")
    try:
        _durable_result(processor, signal, outcome)
    except Exception as error:
        return result("result_unverified", error)
    try:
        acknowledged = counter.acknowledge(copy.deepcopy(observation), signal["signal_id"])
        if type(acknowledged) is not bool:
            raise ValueError("确认返回值无效")
    except Exception as error:
        # 删除可能已经成功，不能断言 Redis 中仍有信号。
        return result("ack_unconfirmed", error, acknowledged=None)
    return result("acknowledged" if acknowledged else "ack_not_applied", acknowledged=acknowledged)
''',
    'test/rule_refine/test_pending_signal.py': r'''"""待处理信号确认测试；使用人工计数器和真实本地保存流程。"""

import copy
import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_pending_signal_fixtures", Path(__file__).with_name("test_signal_processing.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
base = fixtures.base
module = importlib.import_module(base.package.__name__ + ".pending_signal")


class Counter:
    def __init__(self, signal):
        self.mode, self.namespace = signal["mode"], signal["namespace"]
        self.signal = copy.deepcopy(signal)
        self.acks = 0

    def pending(self, observation):
        return copy.deepcopy(self.signal)

    def acknowledge(self, observation, signal_id):
        self.acks += 1
        if self.signal is None or self.signal["signal_id"] != signal_id:
            return False
        self.signal = None
        return True


def setup(root):
    signal = base.signal()
    counter = Counter(signal)
    observation = {key: signal[key] for key in ("mode", "source_app", "company_code", "topic", "rule_code")}
    options = fixtures.dependencies(root)
    processor = fixtures.module.SignalProcessor(root / "pipeline", **options)
    return signal, counter, observation, options, processor


class PendingSignalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.signal, self.counter, self.observation, self.options, self.processor = setup(self.root)

    def consume(self):
        return module.consume_pending(self.counter, self.observation, self.processor)

    def assert_retained(self, result, status):
        self.assertEqual(result["status"], "retained")
        self.assertEqual(result["processing"]["status"], status)
        self.assertFalse(result["acknowledged"])
        self.assertEqual(self.counter.acks, 0)
        self.assertIsNotNone(self.counter.signal)

    def test_received_is_saved_before_ack_and_idle_does_not_repeat_analysis(self):
        original = copy.deepcopy(self.observation)
        acknowledge = self.counter.acknowledge

        def checked(observation, signal_id):
            self.assertTrue(list(self.processor.store.directory.rglob("*.result.json")))
            self.assertTrue(list(self.processor.store.directory.glob("records/*.json")))
            return acknowledge(observation, signal_id)

        with patch.object(self.counter, "acknowledge", side_effect=checked):
            result = self.consume()
        self.assertEqual(result["status"], "acknowledged")
        self.assertTrue(result["acknowledged"])
        self.assertEqual(self.consume()["status"], "idle")
        self.options["analyze"].assert_called_once()
        self.assertEqual(self.observation, original)

    def test_missing_tasks_wait_without_marker_then_resume_once(self):
        self.options["load_task_rows"].return_value = []
        self.assert_retained(self.consume(), "materials_pending")
        self.options["analyze"].assert_not_called()
        self.assertFalse(list(self.processor.directory.rglob("analysis_started.json")))
        self.options["load_task_rows"].return_value = [base.row()]
        self.assertEqual(self.consume()["status"], "acknowledged")
        self.options["analyze"].assert_called_once()
        self.assertEqual(self.options["load_task_rows"].call_count, 2)

    def test_partial_missing_or_wrong_scope_tasks_cannot_finalize(self):
        self.counter.signal["sample_alert_ids"] = ["101", "102"]
        self.options["load_task_rows"].return_value = [base.row(), {**base.row("102"), "flow_id": "other"}]
        self.assert_retained(self.consume(), "materials_pending")
        self.options["load_task_rows"].return_value = [base.row(), {**base.row("102"), "app_code": "other"}]
        self.assert_retained(self.consume(), "materials_pending")
        self.options["analyze"].assert_not_called()
        self.options["load_task_rows"].return_value = [base.row(), base.row("102", "second")]
        self.assertEqual(self.consume()["status"], "acknowledged")

    def test_invalid_materials_wait_as_unavailable_but_optional_response_does_not(self):
        self.options["load_task_rows"].return_value = [{**base.row(), "alert_data": None}]
        self.assert_retained(self.consume(), "materials_unavailable")
        self.assertFalse(list(self.processor.directory.rglob("analysis_started.json")))
        self.options["analyze"].assert_not_called()
        self.options["load_task_rows"].return_value = [{**base.row(), "agent_response_unavailable": True}]
        self.assertEqual(self.consume()["status"], "acknowledged")

    def test_missing_rule_is_not_acknowledged(self):
        self.options["load_rules"].return_value.get_rule.side_effect = base.common.RuleNotFoundError("missing")
        self.assert_retained(self.consume(), "materials_unavailable")
        self.options["analyze"].assert_not_called()

    def test_valid_nonproposal_is_terminal(self):
        for status in ("no_change", "insufficient_evidence", "needs_existing_condition_change"):
            with self.subTest(status=status):
                _, counter, observation, options, processor = setup(self.root / status)
                answer = {
                    **fixtures.fixtures.fixtures.draft(),
                    "status": status,
                    "filter": None,
                    "sample_alert_ids": [],
                }
                options["analyze"].side_effect = lambda data: fixtures.fixtures.analyze(data, answer)
                result = module.consume_pending(counter, observation, processor)
                self.assertEqual(result["status"], "acknowledged")
                self.assertEqual(result["processing"]["status"], "not_applicable")
                options["analyze"].assert_called_once()

    def test_delivery_retry_reuses_original_analysis_and_recommendation(self):
        sink = self.processor.sink

        def lost(payload):
            sink.submit(payload)
            raise TimeoutError("private receipt")

        self.processor.sink = Mock(submit=Mock(side_effect=lost))
        first = self.consume()
        self.assert_retained(first, "delivery_unconfirmed")
        self.processor.sink = sink
        retry = self.consume()
        self.assertEqual(retry["status"], "acknowledged")
        self.assertTrue(retry["processing"]["analysis_reused"])
        self.assertTrue(retry["processing"]["delivery"]["receipt"]["duplicate"])
        self.options["analyze"].assert_called_once()

    def test_ack_exception_before_delete_retries_without_model_call(self):
        with patch.object(self.counter, "acknowledge", side_effect=TimeoutError("private redis")):
            result = self.consume()
        self.assertEqual(result["status"], "ack_unconfirmed")
        self.assertIsNone(result["acknowledged"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(self.consume()["status"], "acknowledged")
        self.options["analyze"].assert_called_once()

    def test_ack_exception_after_delete_reports_uncertainty_then_idle(self):
        acknowledge = self.counter.acknowledge

        def lost(observation, identity):
            acknowledge(observation, identity)
            raise TimeoutError("reply lost")

        with patch.object(self.counter, "acknowledge", side_effect=lost):
            result = self.consume()
        self.assertEqual(result["status"], "ack_unconfirmed")
        self.assertIsNone(result["acknowledged"])
        self.assertEqual(self.consume()["status"], "idle")
        self.options["analyze"].assert_called_once()

    def test_conditional_ack_preserves_replacement_signal(self):
        acknowledge = self.counter.acknowledge

        def replaced(observation, identity):
            self.counter.signal["signal_id"] = "next-signal"
            return acknowledge(observation, identity)

        with patch.object(self.counter, "acknowledge", side_effect=replaced):
            result = self.consume()
        self.assertEqual(result["status"], "ack_not_applied")
        self.assertFalse(result["acknowledged"])
        self.assertEqual(self.counter.signal["signal_id"], "next-signal")

    def test_foreign_signal_is_rejected_before_processing(self):
        for key in ("mode", "namespace", "source_app", "company_code", "topic", "rule_code"):
            with self.subTest(key=key):
                self.counter.signal = {**self.signal, key: "other"}
                self.assertEqual(self.consume()["status"], "invalid_signal")
        self.options["load_task_rows"].assert_not_called()
        self.assertEqual(self.counter.acks, 0)

    def test_errors_and_nonterminal_processing_never_ack(self):
        with patch.object(self.counter, "pending", side_effect=ConnectionError("private")):
            self.assertEqual(self.consume()["status"], "pending_read_failed")
        with patch.object(self.processor, "process", side_effect=OSError("private")):
            self.assertEqual(self.consume()["status"], "processing_failed")
        for status in ("busy", "blocked", "analysis_unconfirmed", "materials_failed"):
            with patch.object(
                self.processor,
                "process",
                return_value={"schema_version": "sec_know.rule_signal_processing.v1", "status": status},
            ):
                self.assert_retained(self.consume(), status)
        self.assertEqual(self.counter.acks, 0)

    def test_missing_or_tampered_delivery_record_cannot_ack(self):
        result = self.processor.process(self.signal)
        path = (
            self.processor.store.directory
            / "attempts"
            / result["record_id"]
            / (result["delivery"]["attempt_id"] + ".result.json")
        )
        original = path.read_bytes()
        for raw in (None, b"{}"):
            if raw is None:
                path.unlink()
            else:
                path.write_bytes(raw)
            with patch.object(self.processor, "process", return_value=result):
                self.assertEqual(self.consume()["status"], "result_unverified")
            self.assertEqual(self.counter.acks, 0)
        path.write_bytes(original)
        with patch.object(self.processor, "process", return_value=result):
            self.assertEqual(self.consume()["status"], "acknowledged")

    def test_other_sample_ids_with_same_count_cannot_reuse_saved_result(self):
        result = self.processor.process(self.signal)
        self.counter.signal["sample_alert_ids"] = ["102"]
        with patch.object(self.processor, "process", return_value=result):
            self.assertEqual(self.consume()["status"], "result_unverified")
        self.assertEqual(self.counter.acks, 0)
        self.assertEqual(self.counter.signal["sample_alert_ids"], ["102"])

    def test_missing_job_analysis_cannot_ack_despite_valid_delivery_record(self):
        result = self.processor.process(self.signal)
        path = self.processor.directory / "jobs" / result["job_id"] / "analysis.json"
        path.unlink()
        with patch.object(self.processor, "process", return_value=result):
            self.assertEqual(self.consume()["status"], "result_unverified")
        self.assertEqual(self.counter.acks, 0)

    def test_legacy_empty_saved_analysis_cannot_consume_signal(self):
        materials = base.build(self.signal, self.options["load_rules"](), [], source_kind="synthetic_fixture")
        analysis = fixtures.fixtures.analyze(materials)
        saved = self.processor.store.save(materials, analysis)
        delivery = fixtures.module.deliver_saved(
            self.processor.store, saved["record_id"], self.options["load_rules"], self.processor.sink
        )
        result = {
            "schema_version": "sec_know.rule_signal_processing.v1",
            "status": "not_applicable",
            "record_id": saved["record_id"],
            "delivery": delivery,
        }
        with patch.object(self.processor, "process", return_value=result):
            self.assertEqual(self.consume()["status"], "result_unverified")
        self.assertEqual(self.counter.acks, 0)


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, counter, observation, options, processor = setup(root)
            options["load_task_rows"].return_value = []
            first = module.consume_pending(counter, observation, processor)
            options["load_task_rows"].return_value = [base.row()]
            second = module.consume_pending(counter, observation, processor)
            third = module.consume_pending(counter, observation, processor)
            report = {
                "mode": "demo",
                "status": "passed",
                "first_processing": first["processing"]["status"],
                "after_material_arrival": second["status"],
                "next_check": third["status"],
                "analysis_calls": options["analyze"].call_count,
                "acknowledgements": counter.acks,
                "automatic_apply": False,
                "model_calls": 0,
                "database_calls": 0,
                "redis_calls": 0,
                "real_ingress_connected": False,
            }
        report["cleanup_ok"] = not root.exists()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        unittest.main(verbosity=2)
''',
}
expected = {'my_workflows/zeus/rule_refine/pending_signal.py': '47a3649be7cec89fe364ef0dd2fee69680dcfad8cfb5f5781fe99945af6c4a97',
 'my_workflows/zeus/rule_refine/signal_processing.py': '680a0490031cd9deaff0ff057737266b2f386b5db3206a832ce6a02dfd6fa66a',
 'test/rule_refine/test_pending_signal.py': '6bae636844e08f44ba52bdb44c61e7ac86da172a99307c7afc71ce7a34fd04b9',
 'test/rule_refine/test_signal_processing.py': 'b5836f5a85eb977c04771c0b11bcbae1d5ddf2f53b22f7eb089fbf41198cca68'}
originals = {}
for name, digest in dependencies.items():
    target = root / name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("停止：前序文件不存在或版本不同，未修改：" + name)
for name, update in updates.items():
    target = root / name
    if not target.is_file() or target.is_symlink():
        raise SystemExit("停止：待更新文件不存在或类型不同：" + name)
    raw = target.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in {update["old"], expected[name]}:
        raise SystemExit("停止：待更新版本不同，未修改：" + name)
    content = raw.decode("utf-8")
    if digest == update["old"]:
        for before, after in update["changes"]:
            if content.count(before) != 1:
                raise SystemExit("停止：更新位置不匹配，未修改：" + name)
            content = content.replace(before, after, 1)
        originals[name] = raw
    files[name] = content
    backup = root / (name + ".before-step06d")
    if os.path.lexists(backup) and (backup.is_symlink() or not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest() != update["old"]):
        raise SystemExit("停止：已有副本内容不同，未覆盖：" + name)
for name, content in files.items():
    raw = content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected[name]:
        raise SystemExit("停止：粘贴内容校验失败，未修改：" + name)
    compile(content, name, "exec")
    target = root / name
    if os.path.lexists(target) and (target.is_symlink() or not target.is_file() or (name not in updates and target.read_bytes() != raw)):
        raise SystemExit("停止：已有不同文件，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if name in originals:
        backup = root / (name + ".before-step06d")
        if not backup.exists():
            with backup.open("xb") as stream:
                stream.write(originals[name])
        fd, temporary = tempfile.mkstemp(prefix=".step06d-", dir=target.parent)
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
.venv/bin/python test/rule_refine/test_signal_processing.py
.venv/bin/python test/rule_refine/test_pending_signal.py
.venv/bin/python test/rule_refine/test_pending_signal.py --demo
SOC_STEP06D
