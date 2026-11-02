"""待处理信号确认测试；使用人工计数器和真实本地保存流程。"""

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
