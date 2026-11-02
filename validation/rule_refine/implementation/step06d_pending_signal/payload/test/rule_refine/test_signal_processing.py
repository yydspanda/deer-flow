"""人工信号完整串联与恢复测试；不连接模型、数据库或 Redis。"""

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

    def test_nonproposal_and_failed_analysis_are_saved_and_reused(self):
        for case in ("no_change", "analysis_failed"):
            with self.subTest(case=case):
                options = dependencies(self.root / case)
                if case == "no_change":
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
