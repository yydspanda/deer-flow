"""Synthetic history lookup tests; no business database, Redis or model calls."""

import copy
import importlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
history = load("surge_history", HERE / "history.py")
counter_module = load(
    "surge_history_counter",
    HERE.parent
    / "step03d_preparation/payload/my_workflows/zeus/rule_refine/counter.py",
)


class RedisReads:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def zrevrangebyscore(self, key, maximum, minimum, *, start, num, withscores):
        self.calls.append((key, maximum, minimum, start, num, withscores))
        exclusive = isinstance(maximum, str) and maximum.startswith("(")
        bound = float(maximum[1:] if exclusive else maximum)
        rows = [
            item
            for item in self.records
            if item[1] < bound or (not exclusive and item[1] == bound)
        ]
        rows.sort(key=lambda item: (item[1], item[0]), reverse=True)
        return rows[start : start + num]


class TaskSource:
    def __init__(self, rows):
        self.rows = rows
        self.signals = []

    def load_task_rows(self, signal):
        self.signals.append(copy.deepcopy(signal))
        return [
            copy.deepcopy(row)
            for row in self.rows
            if row["alert_id"] in signal["sample_alert_ids"]
        ]


def row(identity, **changes):
    return {
        "id": "task-" + identity,
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": identity,
        "status": "failed",
        "alert_data": {"provider_owned_payload": identity},
        "agent_response": {"claim": "OLD-MODEL-ANSWER-IS-NOT-A-LABEL"},
        **changes,
    }


class RecentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.observation = {
            "mode": "live",
            "source_app": "zeus",
            "company_code": "SYNTHETIC",
            "topic": "topic-a",
            "rule_code": "rule-a",
            "company_scope_source": "explicit",
        }

    def run_lookup(self, records, rows, **kwargs):
        redis = RedisReads(records)
        counter = counter_module.RedisRuleCounter(
            redis, mode="live", namespace="same-running-counter"
        )
        source = TaskSource(rows)
        result = history.load_recent_history(
            counter, self.observation, 10_000_000, source, **kwargs
        )
        self.assertTrue(
            all(call[0] == counter._keys(self.observation)[0] for call in redis.calls)
        )
        self.assertTrue(all(call[2] == "-inf" for call in redis.calls))
        self.assertNotIn("OLD-MODEL-ANSWER-IS-NOT-A-LABEL", str(result))
        self.assertFalse(result["coverage"]["raw_log_scope_verified"])
        return result, redis, source

    def test_recent_twenty_not_previous_hour_and_strict_boundary(self):
        records = [(f"a-{i:02}".encode(), 9_999_999 - i * 400_000) for i in range(25)]
        records += [(b"at-boundary", 10_000_000), (b"future", 10_000_001)]
        rows = [row(identity.decode()) for identity, _ in records]
        original = copy.deepcopy(rows)
        result, _, source = self.run_lookup(records, rows)
        self.assertEqual(result["sample_alert_ids"], [f"a-{i:02}" for i in range(20)])
        self.assertEqual(result["coverage"]["stop_reason"], "requested_count_reached")
        self.assertEqual(
            result["coverage"]["oldest_selected_received_at_ms"], 2_399_999
        )
        self.assertEqual(rows, original)
        self.assertEqual(source.signals[0]["mode"], "live")
        self.assertEqual(source.signals[0]["namespace"], "same-running-counter")
        self.assertEqual(source.signals[0]["company_code"], "SYNTHETIC")

    def test_pages_across_missing_excluded_and_same_timestamp_references(self):
        records = [(f"a-{i:02}".encode(), 9_000_000) for i in range(45)]
        rows = [row(f"a-{i:02}") for i in range(40)]
        rows += [row("a-40", alert_data=None)]
        result, redis, _ = self.run_lookup(records, rows, exclude_ids=["a-39"])
        self.assertEqual(
            result["sample_alert_ids"], [f"a-{i:02}" for i in range(38, 18, -1)]
        )
        self.assertGreater(len(redis.calls), 1)
        self.assertEqual(result["coverage"]["excluded_reference_count"], 1)
        self.assertEqual(result["coverage"]["unavailable_reference_count"], 5)

    def test_duplicate_tasks_count_as_one_alert_and_remain_for_material_validation(
        self,
    ):
        records = [(b"same", 9_000_000), (b"older", 8_000_000)]
        rows = [row("same"), row("same", id="other-task"), row("older")]
        result, _, _ = self.run_lookup(records, rows, limit=2)
        self.assertEqual(result["sample_alert_ids"], ["same", "older"])
        self.assertEqual(len(result["task_rows"]), 3)
        self.assertEqual(result["coverage"]["additional_task_row_count"], 1)
        self.assertTrue(all(item["status"] == "failed" for item in result["task_rows"]))

    def test_invalid_variant_is_unavailable_even_with_another_decoded_task(self):
        rows = [row("invalid"), row("invalid", id="bad", alert_data=None), row("older")]
        result, _, _ = self.run_lookup([(b"invalid", 9), (b"older", 8)], rows, limit=1)
        self.assertEqual(result["sample_alert_ids"], ["older"])
        self.assertEqual(result["coverage"]["unavailable_reference_count"], 1)

    def test_empty_retained_index_does_not_claim_no_history(self):
        result, _, source = self.run_lookup([], [])
        coverage = result["coverage"]
        self.assertEqual(result["task_rows"], [])
        self.assertEqual(source.signals, [])
        self.assertEqual(coverage["stop_reason"], "retained_index_exhausted")
        self.assertEqual(coverage["retention_seconds"], 86400)
        self.assertEqual(coverage["history_outside_retained_index"], "unknown")
        self.assertFalse(coverage["complete_historical_coverage"])
        self.assertFalse(coverage["atomic_snapshot"])

    def test_repeated_references_are_skipped_and_scan_budget_is_explicit(self):
        records = [(b"same", 9), (b"same", 9), (b"older", 8), (b"last", 7)]
        result, _, _ = self.run_lookup(records, [row("same"), row("older")], limit=2)
        self.assertEqual(result["sample_alert_ids"], ["same", "older"])
        self.assertEqual(result["coverage"]["duplicate_reference_count"], 1)
        with patch.object(history, "MAX_SCANNED_REFERENCES", 3):
            result, _, _ = self.run_lookup(records, [])
        self.assertEqual(result["coverage"]["scanned_reference_count"], 3)
        self.assertEqual(result["coverage"]["stop_reason"], "scan_reference_limit")

    def test_return_material_budget_does_not_silently_drop_task_variants(self):
        rows = [row("newer"), row("newer", id="other-task"), row("older")]
        with patch.object(history, "MAX_TASK_BYTES", 1):
            result, _, _ = self.run_lookup([(b"newer", 9), (b"older", 8)], rows)
        self.assertEqual(result["task_rows"], [])
        self.assertEqual(
            result["coverage"]["stop_reason"], "returned_task_material_limit"
        )

    def test_existing_local_task_reader_accepts_history_query_without_invented_schema(
        self,
    ):
        package = types.ModuleType("_surge_history_existing_task_source")
        package.__path__ = [
            str(
                HERE.parent
                / "step06w_unified_limits/payload/my_workflows/zeus/rule_refine"
            )
        ]
        sys.modules[package.__name__] = package
        task_source = importlib.import_module(package.__name__ + ".task_source")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE uas_app_workflow_task_status (
                    id TEXT PRIMARY KEY, app_code TEXT, flow_id TEXT, alert_id TEXT,
                    status TEXT, created_date TEXT, alert_data TEXT, agent_response TEXT)""")
                db.execute(
                    "INSERT INTO uas_app_workflow_task_status VALUES (?,?,?,?,?,?,?,?)",
                    (
                        "task",
                        "zeus",
                        "alert_agent",
                        "a",
                        "running",
                        "2030-01-01",
                        json.dumps({"opaque_original": {"field": "value"}}),
                        json.dumps({"old_answer": "normal"}),
                    ),
                )
            before = path.read_bytes()
            counter = counter_module.RedisRuleCounter(
                RedisReads([(b"a", 99)]), mode="live", namespace="active"
            )
            result = history.load_recent_history(
                counter,
                self.observation,
                100,
                task_source.LocalWorkflowTaskSource(path),
            )
            self.assertEqual(result["sample_alert_ids"], ["a"])
            self.assertEqual(
                result["task_rows"][0]["alert_data"],
                {"opaque_original": {"field": "value"}},
            )
            self.assertNotIn("agent_response", result["task_rows"][0])
            self.assertEqual(result["coverage"]["oldest_selected_received_at_ms"], 99)
            self.assertEqual(path.read_bytes(), before)

    def test_bad_limits_and_cross_namespace_or_mode_fail_before_read(self):
        redis = RedisReads([])
        counter = counter_module.RedisRuleCounter(
            redis, mode="live", namespace="active"
        )
        for limit in (0, 21, True, 1.5):
            with self.assertRaises(ValueError):
                history.load_recent_history(
                    counter, self.observation, 100, TaskSource([]), limit=limit
                )
        for before in (True, float("nan"), 100.5):
            with self.assertRaises(ValueError):
                history.load_recent_history(
                    counter, self.observation, before, TaskSource([])
                )
        for change in ({"namespace": "other"}, {"mode": "replay"}):
            with self.assertRaises(ValueError):
                history.load_recent_history(
                    counter, {**self.observation, **change}, 100, TaskSource([])
                )
        self.assertEqual(redis.calls, [])

    def test_provider_failures_propagate_instead_of_appearing_as_empty_history(self):
        redis = RedisReads([(b"a", 99)])
        counter = counter_module.RedisRuleCounter(
            redis, mode="live", namespace="active"
        )

        class BrokenSource:
            def load_task_rows(self, signal):
                raise RuntimeError("synthetic_read_failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic_read_failure"):
            history.load_recent_history(counter, self.observation, 100, BrokenSource())


if __name__ == "__main__":
    unittest.main()
