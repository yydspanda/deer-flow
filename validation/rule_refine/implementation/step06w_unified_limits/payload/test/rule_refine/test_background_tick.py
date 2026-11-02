"""后台轮转测试；人工 Redis 计数器与消费回调，不连接外部服务。"""

import fcntl
import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_tick_fixtures", Path(__file__).with_name("test_pending_signal.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
module = importlib.import_module(fixtures.base.package.__name__ + ".background_tick")


class Pipeline:
    def __init__(self, client):
        self.client, self.keys = client, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, key):
        self.keys.append(key)

    def execute(self):
        self.client.calls.append(list(self.keys))
        if self.client.fail:
            raise ConnectionError("private endpoint")
        return [
            json.dumps(
                {
                    "schema_version": "sec_know.rule_burst_signal.v1",
                    "signal_id": self.client.identities.get(key, "signal-" + key),
                }
            )
            if key in self.client.present
            else None
            for key in self.keys
        ]


class Counter:
    mode, namespace = "live", "test-background"

    def __init__(self):
        self.client = self
        self.present, self.calls, self.fail, self.identities = set(), [], False, {}

    def _keys(self, observation):
        return ["events", "state", observation["rule_code"]]

    def pipeline(self, *, transaction):
        if transaction is not False:
            raise AssertionError("不应使用事务查询")
        return Pipeline(self)

    @staticmethod
    def _signal(signal):
        return signal


def rule(code):
    return {"company_code": "demo-company", "topic": "demo-topic", "rule_code": code}


class BackgroundTickTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.rules = Mock(
            list_rules=Mock(return_value=[rule("A"), rule("B"), rule("C")])
        )
        self.counter, self.processor = Counter(), object()
        self.consume = Mock(
            return_value={
                "status": "retained",
                "acknowledged": False,
                "processing": {
                    "status": "materials_pending",
                    "model_calls_this_attempt": 0,
                },
            }
        )
        self.patch = patch.object(module, "consume_pending", self.consume)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def run_tick(self, **options):
        return module.run_tick(
            self.root,
            rules=self.rules,
            counter=self.counter,
            processor=self.processor,
            **options,
        )

    def test_rotates_past_retained_signal_and_processes_at_most_one(self):
        self.counter.present.update(("A", "B", "C"))
        results = [self.run_tick(now=0) for _ in range(3)]
        self.assertEqual({r["scope"]["rule_code"] for r in results}, {"A", "B", "C"})
        self.assertTrue(all(r["processed"] == 1 for r in results))
        self.assertEqual(self.consume.call_count, 3)
        self.assertEqual(self.run_tick(now=0)["processed"], 0)

    def test_cooldown_survives_next_tick_and_expiry_allows_retry(self):
        self.rules.list_rules.return_value = [rule("A")]
        self.counter.present.add("A")
        self.assertEqual(self.run_tick(now=1)["processed"], 1)
        self.assertEqual(self.run_tick(now=60)["deferred"], 1)
        self.assertEqual(self.run_tick(now=61)["processed"], 1)
        self.assertEqual(self.consume.call_count, 2)

    def test_bounded_batches_eventually_reach_all_scopes_without_full_scan(self):
        for _ in range(3):
            self.assertEqual(self.run_tick(batch_size=1, now=0)["scanned"], 1)
        self.assertEqual({call[0] for call in self.counter.calls}, {"A", "B", "C"})
        self.consume.assert_not_called()

    def test_duplicate_catalog_scope_is_only_queried_once(self):
        self.rules.list_rules.return_value = [rule("A"), rule("A")]
        self.assertEqual(self.run_tick(now=0)["catalog_scopes"], 1)
        self.assertEqual(self.counter.calls, [["A"]])

    def test_cursor_and_retry_metadata_are_saved_before_consuming(self):
        self.counter.present.add("A")

        def consume(*args):
            data = json.loads((self.root / "cursor.json").read_text())
            self.assertIn(data["after"], data["retry"])
            raise OSError("interrupted")

        self.consume.side_effect = consume
        with self.assertRaises(OSError):
            self.run_tick(now=0)
        self.assertEqual(self.run_tick(now=1)["processed"], 0)

    def test_state_write_failure_prevents_consumption(self):
        self.counter.present.add("A")
        with (
            patch.object(module, "_replace", side_effect=OSError("disk")),
            self.assertRaises(OSError),
        ):
            self.run_tick(now=0)
        self.consume.assert_not_called()

    def test_corrupt_state_and_wrong_namespace_are_not_reset(self):
        self.run_tick(now=0)
        path = self.root / "cursor.json"
        original = path.read_bytes()
        path.write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.run_tick(now=0)
        self.assertEqual(path.read_bytes(), b"{}")
        path.write_bytes(original)
        self.counter.namespace = "another"
        with self.assertRaises(ValueError):
            self.run_tick(now=0)
        self.assertEqual(path.read_bytes(), original)

    def test_symlink_state_is_rejected(self):
        target = self.root / "other.json"
        target.write_bytes(b"{}")
        (self.root / "cursor.json").symlink_to(target)
        with self.assertRaises(ValueError):
            self.run_tick(now=0)
        self.assertEqual(target.read_bytes(), b"{}")

    def test_busy_lock_does_not_query_or_consume(self):
        with (self.root / "tick.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_tick(now=0)["status"], "busy")
        self.assertFalse(self.counter.calls)
        self.consume.assert_not_called()

    def test_query_error_does_not_advance_cursor_or_consume(self):
        self.counter.fail = True
        with self.assertRaises(ConnectionError):
            self.run_tick(now=0)
        self.assertFalse((self.root / "cursor.json").exists())
        self.consume.assert_not_called()

    def test_removed_scopes_are_pruned_and_empty_catalog_makes_no_redis_call(self):
        self.counter.present.add("A")
        self.run_tick(now=0)
        self.rules.list_rules.return_value = []
        before = len(self.counter.calls)
        self.assertEqual(self.run_tick(now=1)["catalog_scopes"], 0)
        self.assertEqual(len(self.counter.calls), before)
        self.assertEqual(
            json.loads((self.root / "cursor.json").read_text())["retry"], {}
        )

    def test_invalid_limits_and_excessive_catalog_fail_before_redis(self):
        for options in (
            {"batch_size": 0},
            {"batch_size": 257},
            {"retry_delay_seconds": 0},
            {"now": float("nan")},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_tick(**options)
        self.rules.list_rules.return_value = [rule("A")] * 5001
        with self.assertRaises(ValueError):
            self.run_tick(now=0)
        self.assertFalse(self.counter.calls)

    def test_three_failed_attempts_park_and_explicit_reset_preserves_cursor(self):
        self.rules.list_rules.return_value = [rule("A")]
        self.counter.present.add("A")
        self.consume.return_value["processing"]["status"] = "materials_failed"
        for now in (0, 60, 120):
            result = self.run_tick(now=now)
        self.assertEqual((result["status"], result["attempts"]), ("needs_attention", 3))
        self.assertEqual(self.run_tick(now=180)["processed"], 0)
        path = self.root / "cursor.json"
        previous = json.loads(path.read_text())["after"]
        self.assertEqual(module.reset_background_retry(self.root)["reset_scopes"], 1)
        self.assertEqual(json.loads(path.read_text())["after"], previous)
        self.assertEqual(self.run_tick(now=180)["attempts"], 1)

    def test_terminal_result_parks_immediately_and_new_signal_resets_attempts(self):
        self.rules.list_rules.return_value = [rule("A")]
        self.counter.present.add("A")
        self.consume.return_value["processing"]["status"] = "analysis_unconfirmed"
        self.assertEqual(self.run_tick(now=0)["status"], "needs_attention")
        self.assertEqual(self.run_tick(now=1000)["processed"], 0)
        self.counter.identities["A"] = "another-signal"
        self.assertEqual(self.run_tick(now=1001)["attempts"], 1)
        self.assertEqual(self.consume.call_count, 2)

    def test_capacity_wait_does_not_consume_attempt_budget(self):
        self.rules.list_rules.return_value = [rule("A")]
        self.counter.present.add("A")
        self.consume.return_value["processing"]["status"] = "capacity_wait"
        for now in (0, 60, 120, 180):
            result = self.run_tick(now=now)
            self.assertEqual((result["status"], result["attempts"]), ("retained", 0))
        self.assertEqual(self.consume.call_count, 4)

    def test_materials_wait_does_not_exhaust_model_attempt_budget(self):
        self.rules.list_rules.return_value = [rule("A")]
        self.counter.present.add("A")
        for now in (0, 60, 120, 180, 240):
            result = self.run_tick(now=now)
            self.assertEqual((result["status"], result["attempts"]), ("retained", 0))
        self.assertEqual(self.consume.call_count, 5)

    def test_prefetched_signal_is_frozen_even_if_live_counter_changes(self):
        self.rules.list_rules.return_value = [rule("A")]
        self.counter.present.add("A")

        def consume(counter, observation, processor):
            self.counter.identities["A"] = "new-signal"
            first = counter.pending(observation)
            first["signal_id"] = "mutated"
            self.assertEqual(counter.pending(observation)["signal_id"], "signal-A")
            return {"status": "ack_not_applied", "acknowledged": False}

        self.consume.side_effect = consume
        self.assertEqual(self.run_tick(now=0)["status"], "ack_not_applied")

    def test_malformed_signal_parks_without_consuming(self):
        self.rules.list_rules.return_value = [rule("A")]
        with patch.object(Pipeline, "execute", return_value=["not-json"]):
            result = self.run_tick(now=0)
        self.assertEqual(
            (result["status"], result["processing_status"]),
            ("needs_attention", "invalid_signal"),
        )
        self.consume.assert_not_called()

    def test_state_supports_the_full_catalog_bound_without_material_node_limit(self):
        path = self.root / "cursor.json"
        state = module._state(path, self.counter.namespace)
        state["retry"] = {
            f"{index:064x}": {
                "signal_id": "signal-" + str(index),
                "attempts": 3,
                "next_at": 60,
                "parked": True,
            }
            for index in range(5000)
        }
        module._replace(path, state)
        self.assertEqual(
            len(module._state(path, self.counter.namespace)["retry"]), 5000
        )

    def test_noncanonical_catalog_scope_is_rejected_without_query(self):
        self.rules.list_rules.return_value = [
            {**rule("A"), "company_code": " demo-company "}
        ]
        with self.assertRaises(ValueError):
            self.run_tick(now=0)
        self.assertFalse(self.counter.calls)

    def test_parked_state_and_static_error_type_are_visible(self):
        self.rules.list_rules.return_value = [rule("A")]
        self.counter.present.add("A")
        self.consume.return_value = {
            "status": "result_unverified",
            "error_type": "SavedAnalysisError",
        }
        first = self.run_tick(now=0)
        self.assertEqual(
            (first["status"], first["error_type"]),
            ("needs_attention", "SavedAnalysisError"),
        )
        second = self.run_tick(now=1000)
        self.assertEqual(
            (second["status"], second["needs_attention"]), ("needs_attention", 1)
        )

    def test_existing_redis_counter_keys_and_millisecond_snapshot_are_compatible(self):
        counter_module = importlib.import_module(
            fixtures.base.package.__name__ + ".counter"
        )
        counter = counter_module.RedisRuleCounter(
            self.counter, mode="live", namespace=self.counter.namespace
        )
        counter.acknowledge = Mock()
        signal = {
            **fixtures.base.signal(),
            "mode": "live",
            "namespace": counter.namespace,
        }
        for field in ("window_start", "window_end"):
            signal[field + "_ms"] = int(
                datetime.fromisoformat(signal.pop(field)).timestamp() * 1000
            )
        self.rules.list_rules.return_value = [rule(signal["rule_code"])]
        processor = Mock(
            process=Mock(
                return_value={
                    "schema_version": "sec_know.rule_signal_processing.v1",
                    "status": "capacity_wait",
                }
            )
        )
        with (
            patch.object(Pipeline, "execute", return_value=[json.dumps(signal)]),
            patch.object(module, "consume_pending", fixtures.module.consume_pending),
        ):
            result = module.run_tick(
                self.root, rules=self.rules, counter=counter, processor=processor, now=0
            )
        self.assertEqual(
            (result["processing_status"], result["attempts"]), ("capacity_wait", 0)
        )
        self.assertEqual(
            processor.process.call_args.args[0]["window_start"],
            "2026-01-01T00:00:00+00:00",
        )
        counter.acknowledge.assert_not_called()


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            counter = Counter()
            counter.present.update(("A", "B"))
            rules = Mock(list_rules=Mock(return_value=[rule("A"), rule("B")]))
            consume = Mock(
                return_value={
                    "status": "retained",
                    "processing": {"status": "materials_pending"},
                }
            )
            with patch.object(module, "consume_pending", consume):
                rows = [
                    module.run_tick(
                        root, rules=rules, counter=counter, processor=object(), now=0
                    )
                    for _ in range(3)
                ]
            processed = [row["processed"] for row in rows]
            distinct = len(
                {row["scope"]["rule_code"] for row in rows if row["processed"]}
            )
            assert processed == [1, 1, 0] and distinct == 2 and consume.call_count == 2
            report = {
                "mode": "demo",
                "status": "passed",
                "processed_per_tick": processed,
                "distinct_rules_processed": distinct,
                "consume_calls": consume.call_count,
                "model_calls": 0,
                "database_calls": 0,
                "redis_calls": 0,
                "automatic_apply": False,
                "real_ingress_connected": False,
            }
        report["cleanup_ok"] = not root.exists()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        unittest.main(verbosity=2)
