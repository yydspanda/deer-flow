"""Background/material integration using real pipeline/SQLite and fake Redis/model."""

import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit
from uuid import uuid4


HERE = Path(__file__).resolve().parent
IMPLEMENTATION = HERE.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("base_probe", IMPLEMENTATION / "step06j_synthetic_chain/probe.py")
probe = load("background_material_probe", HERE / "probe.py")


class Pipeline:
    def __init__(self, client):
        self.client, self.keys = client, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, key):
        self.keys.append(key)
        return self

    def execute(self):
        self.client.pipeline_calls.append(list(self.keys))
        if self.client.pipeline_failure:
            raise ConnectionError("private connection detail")
        return [self.client.values.get(key) for key in self.keys]


class Redis:
    def __init__(self):
        self.values = {"production:do-not-touch": "preserve"}
        self.observe_failure = False
        self.pipeline_failure = False
        self.delete_failure = False
        self.bad_duplicate = False
        self.pipeline_calls = []

    def pipeline(self, *, transaction):
        assert transaction is False
        return Pipeline(self)

    def delete(self, key):
        if self.delete_failure:
            raise ConnectionError("private endpoint")
        return int(self.values.pop(key, None) is not None)

    def exists(self, key):
        return int(key in self.values)


class Counter:
    def __init__(self, client, *, mode, namespace, minimum_count, transaction_retries):
        assert mode == "live" and minimum_count == 3 and transaction_retries == 3
        assert namespace.startswith("synthetic-background-")
        self.client, self.mode, self.namespace = client, mode, namespace
        self.events = []

    def _keys(self, observation):
        assert observation["mode"] == "live"
        return [
            self.namespace + ":" + suffix for suffix in ("events", "state", "pending")
        ]

    @staticmethod
    def _signal(signal):
        return copy.deepcopy(signal)

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
            self.client.values[keys[2]] = json.dumps(
                {
                    "schema_version": "sec_know.rule_burst_signal.v1",
                    "mode": "live",
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
                    "time_basis": "redis_observed_at",
                }
            )
        return {
            "added": added,
            "triggered": triggered,
            "current_count": len(self.events),
            "pending_signal": self.pending(observation),
        }

    def pending(self, observation):
        value = self.client.values.get(self._keys(observation)[2])
        return json.loads(value) if value else None

    def acknowledge(self, observation, signal_id):
        pending = self.pending(observation)
        if not pending or pending["signal_id"] != signal_id:
            return False
        return bool(self.client.delete(self._keys(observation)[2]))


class BackgroundMaterialTests(unittest.TestCase):
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
            "step06f_background",
        ):
            source = IMPLEMENTATION / step / "payload/my_workflows/zeus/rule_refine"
            for path in source.glob("*.py"):
                shutil.copyfile(path, folder / path.name)
        parser = load(
            "background_material_parser_changes",
            IMPLEMENTATION / "step06m_apply_parser/changes.py",
        )
        change = parser.PATCHES[0]
        source = folder / "analysis.py"
        content = source.read_text(encoding="utf-8")
        for before, after in change["changes"]:
            assert content.count(before) == 1
            content = content.replace(before, after)
        assert hashlib.sha256(content.encode()).hexdigest() == change["new"]
        source.write_text(content, encoding="utf-8")
        package = ModuleType("_background_material_modules_" + uuid4().hex)
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
                    "background_tick",
                    "task_source",
                )
            }
        )

    @classmethod
    def tearDownClass(cls):
        cls.stage.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "result"
        self.business = Path(self.temporary.name) / "business.sqlite"
        self.business.write_bytes(b"business-data-must-not-change")
        self.client = Redis()
        self.database_calls = []

    def analyzer(self, status="proposal"):
        def analyze(materials):
            self.assertEqual(materials["material_source"], "workflow_task_store")
            self.assertEqual(materials["mode"], "live")
            self.assertEqual(len(materials["samples"]), 3)
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
                else "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```"
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

    def run_check(self, analyzer=None, **options):
        self.analyze = self.analyzer() if analyzer is None else analyzer
        connect = sqlite3.connect

        def isolated_connect(database, *args, **kwargs):
            text = str(database)
            path = (
                Path(unquote(urlsplit(text).path))
                if text.startswith("file:")
                else Path(text)
            )
            self.assertTrue(path.resolve().is_relative_to(self.directory))
            self.database_calls.append((path.resolve(), kwargs.get("uri", False), text))
            return connect(database, *args, **kwargs)

        with (
            patch.object(self.modules.counter, "RedisRuleCounter", Counter),
            patch.object(sqlite3, "connect", side_effect=isolated_connect),
        ):
            return probe.run_check(
                self.directory,
                modules=self.modules,
                redis_client=self.client,
                analyze=self.analyze,
                case=base.synthetic_data("healthcase-v1"),
                **options,
            )

    def assert_clean(self, result):
        self.assertTrue(result["cleanup_ok"], result)
        self.assertTrue(result["temporary_database_removed"], result)
        self.assertEqual(self.client.values, {"production:do-not-touch": "preserve"})
        self.assertEqual(self.business.read_bytes(), b"business-data-must-not-change")
        self.assertFalse(list(self.directory.rglob("*.sqlite")))

    def test_real_background_and_task_reader_ack_once_then_idle(self):
        result = self.run_check()
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(
            (result["submitted"], result["unique_count"], result["new_signal_count"]),
            (4, 3, 1),
        )
        self.assertFalse(result["duplicate_added"])
        self.assertEqual(result["analysis_status"], "proposal")
        self.assertEqual(result["consumer_status"], "acknowledged")
        self.assertEqual(result["processed_per_tick"], [1, 0])
        self.assertEqual(result["next_tick_status"], "idle")
        self.assertEqual(result["database_reads"], 1)
        self.assertEqual(result["business_database_calls"], 0)
        self.assertEqual(result["material_origin"], "synthetic_fixture")
        self.assertFalse(result["real_ingress_connected"])
        self.assertFalse(result["celery_dispatch_tested"])
        self.analyze.assert_called_once()
        self.assertEqual(result["model_calls"], 0)
        self.assertTrue(
            any(uri and "mode=ro" in text for _, uri, text in self.database_calls)
        )
        self.assertGreaterEqual(len(self.client.pipeline_calls), 2)
        self.assertEqual(
            json.loads((self.directory / "summary.json").read_text()), result
        )
        self.assertFalse(result["automatic_apply"])
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assert_clean(result)

    def test_insufficient_evidence_is_saved_without_fabricating_proposal(self):
        result = self.run_check(self.analyzer("insufficient_evidence"))
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["analysis_status"], "insufficient_evidence")
        self.assertFalse((self.directory / "receipts").exists())
        self.analyze.assert_called_once()
        self.assert_clean(result)

    def test_invalid_reply_never_passes_and_is_not_generated_again(self):
        result = self.run_check(self.analyzer("invalid"))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["analysis_status"], "analysis_failed")
        self.assertEqual(result["analysis_error_code"], "invalid_model_output")
        self.assertEqual(result["consumer_status"], "needs_attention")
        self.assertEqual(result["processing_status"], "blocked")
        self.assertEqual(result["processed_per_tick"], [1])
        self.assertFalse(result["acknowledged"])
        self.analyze.assert_called_once()
        self.assert_clean(result)

    def test_counter_exception_still_cleans_exact_test_keys(self):
        self.client.observe_failure = True
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "ConnectionError")
        self.assertEqual(result["test_keys_tracked"], 3)
        self.assertNotIn("private", json.dumps(result))
        self.analyze.assert_not_called()
        self.assert_clean(result)

    def test_background_read_exception_cleans_without_generation(self):
        self.client.pipeline_failure = True
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.analyze.assert_not_called()
        self.assert_clean(result)

    def test_bad_duplicate_prevents_analysis(self):
        self.client.bad_duplicate = True
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.analyze.assert_not_called()
        self.assert_clean(result)

    def test_cleanup_failure_never_reports_success(self):
        self.client.delete_failure = True
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["cleanup_ok"])
        self.assertEqual(len(result["cleanup_failed_keys"]), 3)

    def test_nonempty_output_does_not_repeat_or_replace_prior_evidence(self):
        first = self.run_check()
        self.assertEqual(first["status"], "passed", first)
        saved = (self.directory / "summary.json").read_bytes()
        result = self.run_check()
        self.assertEqual(result["status"], "failed")
        self.analyze.assert_not_called()
        self.assertEqual((self.directory / "summary.json").read_bytes(), saved)

    def test_callback_exception_stays_failed_and_does_not_leak_body(self):
        callback = Mock(side_effect=TimeoutError("private prompt content"))
        result = self.run_check(callback)
        self.assertEqual(result["status"], "failed")
        callback.assert_called_once()
        self.assertIsNone(result["sdk_request_calls"])
        self.assertNotIn("private", json.dumps(result))
        self.assert_clean(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
