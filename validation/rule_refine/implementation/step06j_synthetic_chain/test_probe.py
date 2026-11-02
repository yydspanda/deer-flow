"""Synthetic probe tests: real pipeline modules, artificial counter and model replies."""

import copy
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("synthetic_probe", HERE / "probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Redis:
    def __init__(self):
        self.values = {"production:do-not-touch": "preserve"}
        self.delete_failure = False
        self.observe_failure = False
        self.bad_duplicate = False

    def delete(self, key):
        if self.delete_failure:
            raise ConnectionError("private address")
        return int(self.values.pop(key, None) is not None)

    def exists(self, key):
        return int(key in self.values)


class Counter:
    def __init__(self, client, *, mode, minimum_count):
        assert mode == "demo" and minimum_count == 3
        self.client, self.mode, self.namespace = client, mode, uuid4().hex
        self.events = []

    def _keys(self, observation):
        assert observation["mode"] == "demo"
        return [
            self.namespace + ":" + suffix for suffix in ("events", "state", "pending")
        ]

    def observe(self, observation):
        keys = self._keys(observation)
        added = observation["alert_id"] not in self.events or self.client.bad_duplicate
        if added:
            self.events.append(observation["alert_id"])
        self.client.values[keys[0]] = list(self.events)
        self.client.values[keys[1]] = {"count": len(self.events)}
        if self.client.observe_failure:
            raise ConnectionError("private connection details")
        triggered = len(self.events) >= 3 and keys[2] not in self.client.values
        if triggered:
            self.client.values[keys[2]] = {
                "schema_version": "sec_know.rule_burst_signal.v1",
                "mode": "demo",
                "namespace": self.namespace,
                "signal_id": uuid4().hex,
                **{
                    key: observation[key]
                    for key in ("source_app", "company_code", "topic", "rule_code")
                },
                "current_count": len(self.events),
                "sample_alert_ids": list(self.events),
                "window_start": "2026-01-01T00:00:00+00:00",
                "window_end": "2026-01-01T01:00:00+00:00",
                "time_basis": "provided_received_at",
            }
        return {
            "added": added,
            "triggered": triggered,
            "current_count": len(self.events),
            "pending_signal": self.pending(observation),
        }

    def pending(self, observation):
        return copy.deepcopy(self.client.values.get(self._keys(observation)[2]))

    def acknowledge(self, observation, signal_id):
        pending = self.pending(observation)
        if not pending or pending["signal_id"] != signal_id:
            return False
        return bool(self.client.delete(self._keys(observation)[2]))


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = tempfile.TemporaryDirectory()
        folder = Path(cls.stage.name)
        for step in (
            "step01_contracts",
            "step03d_preparation",
            "step06c_task_source",
            "step04b_analysis",
            "step05a_filter_evaluation",
            "step05b_proposal_check",
            "step06a_saved_delivery",
            "step02b_local_receipt",
            "step06d_pending_signal",
        ):
            source = HERE.parent / step / "payload/my_workflows/zeus/rule_refine"
            for path in source.glob("*.py"):
                shutil.copyfile(path, folder / path.name)
        package = ModuleType("_synthetic_probe_modules")
        package.__path__ = [str(folder)]
        sys.modules[package.__name__] = package
        cls.modules = SimpleNamespace(
            **{
                name: importlib.import_module(package.__name__ + "." + name)
                for name in (
                    "counter",
                    "ingress",
                    "signal_processing",
                    "pending_signal",
                    "local_sink",
                    "analysis",
                )
            }
        )

    @classmethod
    def tearDownClass(cls):
        cls.stage.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "result"
        self.client = Redis()

    def analyzer(self, status="proposal"):
        def analyze(materials):
            answer = {
                "status": status,
                "issue": "人工健康探测集中触发。",
                "recommendation": "确认业务正常后考虑增加组合排除条件。",
                "certainty": "人工材料只用于功能验证，仍需运营确认。",
                "sample_alert_ids": [],
                "filter": None,
            }
            if status == "proposal":
                answer.update(
                    sample_alert_ids=[row["alert_id"] for row in materials["samples"]],
                    filter={
                        "topic": "demo-http",
                        "expression": "A&&B",
                        "A": {
                            "name": "host",
                            "value": "synthetic.invalid",
                            "operator": "equal",
                        },
                        "B": {"name": "path", "value": "/health", "operator": "equal"},
                    },
                )
            raw = (
                "not-json"
                if status == "invalid"
                else json.dumps(answer, ensure_ascii=False)
            )
            result = self.modules.analysis.analyze_materials(materials, lambda _: raw)
            result["model_transport"] = {
                "transport_mode": "mock",
                "sdk_request_calls": 1,
                "requested_model": "fixture-model",
                "returned_model": "fixture-model",
                "error_code": None,
                "finish_reason": "stop",
                "usage": {"total_tokens": 10},
                "elapsed_ms": 1,
                "upstream_attempts": None,
            }
            return result

        return Mock(side_effect=analyze)

    def run_check(self, analyze=None, **options):
        self.analyze = analyze or self.analyzer()
        with patch.object(self.modules.counter, "RedisRuleCounter", Counter):
            return probe.run_check(
                self.directory,
                modules=self.modules,
                redis_client=self.client,
                analyze=self.analyze,
                **options,
            )

    def test_proposal_count_ack_idle_and_private_evidence(self):
        case = probe.synthetic_data("fixed")
        outcome = self.run_check(case=case)
        self.assertEqual(outcome["status"], "passed", outcome)
        self.assertEqual(
            (
                outcome["submitted"],
                outcome["unique_count"],
                outcome["new_signal_count"],
            ),
            (4, 3, 1),
        )
        self.assertFalse(outcome["duplicate_added"])
        self.assertEqual(outcome["next_consume_status"], "idle")
        self.assertEqual(outcome["delivery_status"], "received")
        self.assertEqual(
            outcome["sample_match_counts"],
            {"matched": 3, "not_matched": 0, "unknown": 0},
        )
        self.analyze.assert_called_once()
        self.assertEqual(outcome["model_calls"], 0)
        self.assertEqual(
            json.loads((self.directory / "synthetic-input.json").read_text()), case
        )
        self.assertEqual(
            json.loads((self.directory / "summary.json").read_text()), outcome
        )
        self.assertIn("人工样本", (self.directory / "REPORT.md").read_text())
        self.assertEqual(self.client.values, {"production:do-not-touch": "preserve"})
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertTrue(
            all(
                stat.S_IMODE(p.stat().st_mode) == 0o600
                for p in self.directory.rglob("*")
                if p.is_file()
            )
        )

    def test_nonproposal_is_saved_without_claiming_proposal(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            with self.subTest(status=status):
                self.directory = Path(self.temporary.name) / status
                result = self.run_check(self.analyzer(status))
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["analysis_status"], status)
                self.assertEqual(result["delivery_status"], "not_applicable")
                self.assertFalse((self.directory / "receipts").exists())
                self.analyze.assert_called_once()

    def test_invalid_model_output_is_failed_retained_then_test_keys_cleaned(self):
        result = self.run_check(self.analyzer("invalid"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["consumer_status"], "retained")
        self.assertEqual(result["processing_status"], "blocked")
        self.assertEqual(result["analysis_error_code"], "invalid_model_output")
        self.assertIsNone(result["model_error_code"])
        self.analyze.assert_called_once()
        self.assertTrue(result["cleanup_ok"])
        self.assertEqual(self.client.values, {"production:do-not-touch": "preserve"})

    def test_bad_duplicate_assertion_prevents_analysis(self):
        self.client.bad_duplicate = True
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.analyze.assert_not_called()
        self.assertTrue(result["cleanup_ok"])

    def test_counter_exception_tracks_keys_before_write(self):
        self.client.observe_failure = True
        result = self.run_check()
        self.assertEqual(result["error_type"], "ConnectionError")
        self.assertEqual(result["test_keys_tracked"], 3)
        self.assertTrue(result["cleanup_ok"])
        self.assertNotIn("private", json.dumps(result))
        self.analyze.assert_not_called()

    def test_cleanup_failure_never_reports_success(self):
        self.client.delete_failure = True
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["cleanup_ok"])
        self.assertEqual(len(result["cleanup_failed_keys"]), 3)

    def test_existing_directory_prevents_repeated_generation(self):
        self.run_check()
        original = (self.directory / "summary.json").read_bytes()
        result = self.run_check()
        self.assertEqual(
            result["failure_stage"], "output_directory_not_empty_or_invalid"
        )
        self.analyze.assert_not_called()
        self.assertEqual((self.directory / "summary.json").read_bytes(), original)

    def test_analysis_exception_does_not_repeat_or_leak_details(self):
        analyzer = Mock(side_effect=TimeoutError("private payload"))
        result = self.run_check(analyzer)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["processing_status"], "analysis_unconfirmed")
        self.assertEqual(result["processing_error_type"], "TimeoutError")
        self.assertIsNone(result["sdk_request_calls"])
        self.assertIsNone(result["model_calls"])
        self.assertNotIn("private", json.dumps(result))
        analyzer.assert_called_once()
        self.assertTrue(list(self.directory.rglob("analysis_started.json")))


if __name__ == "__main__":
    unittest.main()
