"""Durable retry/release regressions using synthetic files and counters only."""

import copy
import hashlib
import importlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_recovery_fixtures", Path(__file__).with_name("test_pending_signal.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
base = fixtures.base
pending = fixtures.module
processing = fixtures.fixtures.module
control = importlib.import_module(base.package.__name__ + ".retry_control")
limits = importlib.import_module(base.package.__name__ + ".limits")


class RetryCounter(fixtures.Counter):
    def __init__(self, signal):
        super().__init__(signal)
        self.client = self
        self.set_calls = 0

    def _keys(self, observation):
        return ["events", "state", "pending"]

    def set(self, key, value, nx):
        self.set_calls += 1
        if self.signal is not None:
            return False
        self.signal = json.loads(value)
        return True


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.signal, _, self.observation, self.options, self.processor = fixtures.setup(
            self.root
        )
        self.counter = RetryCounter(self.signal)
        self.scheduler = self.root / "scheduler"

    def failed(self, data):
        return fixtures.fixtures.fixtures.fixtures.analysis.analyze_materials(
            data, lambda _: "invalid"
        )

    def consume(self):
        return pending.consume_pending(self.counter, self.observation, self.processor)

    def test_known_failure_gets_new_bounded_attempt_and_keeps_old_result(self):
        original = self.options["analyze"].side_effect
        # Generate bound failure from the actual incoming materials, then succeed.
        calls = []

        def analyze(data):
            calls.append(1)
            return self.failed(data) if len(calls) == 1 else original(data)

        self.options["analyze"].side_effect = analyze
        first = self.consume()
        self.assertEqual(first["processing"]["status"], "analysis_retryable")
        path = self.processor.analysis_path(first["processing"]["job_id"], 1)
        old = path.read_bytes()
        second = self.consume()
        self.assertEqual(second["status"], "acknowledged")
        self.assertEqual(second["processing"]["attempt_number"], 2)
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(len(calls), 2)

    def test_exhausted_failures_are_archived_before_ack_and_can_be_explicitly_requeued(
        self,
    ):
        self.options["analyze"].side_effect = self.failed
        for _ in range(2):
            self.assertEqual(self.consume()["status"], "retained")
        last = self.consume()
        self.assertEqual(last["processing"]["status"], "failed_finalized")
        self.assertEqual(last["status"], "acknowledged")
        self.assertIsNone(self.counter.signal)
        before = [
            p.read_bytes()
            for p in sorted(self.processor.directory.rglob("analysis.json"))
        ]
        result = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="retry",
            rule=self.signal["rule_code"],
            counter=self.counter,
        )
        self.assertEqual(result["items"][0]["action_status"], "scheduled")
        self.assertEqual(result["items"][0]["new_attempt"], 4)
        self.assertEqual(self.counter.signal, self.signal)
        self.assertEqual(
            before,
            [
                p.read_bytes()
                for p in sorted(self.processor.directory.rglob("analysis.json"))
            ],
        )
        self.assertEqual(self.options["analyze"].call_count, 3)

    def test_result_unknown_never_automatically_repeats_and_requires_explicit_flag(
        self,
    ):
        self.options["analyze"].side_effect = TimeoutError("synthetic unknown")
        first = self.consume()
        self.assertEqual(first["processing"]["status"], "analysis_unconfirmed")
        self.consume()
        self.options["analyze"].assert_called_once()
        with self.assertRaisesRegex(ValueError, "requires_explicit_authorization"):
            self.processor.request_retry(self.signal)
        scheduled = self.processor.request_retry(self.signal, allow_unconfirmed=True)
        self.assertEqual(scheduled["status"], "scheduled")
        self.assertEqual(scheduled["attempt_number"], 2)
        self.options["analyze"].assert_called_once()

    def test_legacy_failure_is_not_automatically_recalled_but_retry_is_effective(self):
        self.options["analyze"].side_effect = self.failed
        first = self.processor.process(self.signal)
        folder = self.processor.directory / "jobs" / first["job_id"]
        current = folder / "attempts/0001"
        for name in ("analysis_started.json", "analysis.json"):
            (folder / name).write_bytes((current / name).read_bytes())
        shutil.rmtree(folder / "attempts")
        old = (folder / "analysis.json").read_bytes()
        self.assertEqual(self.processor.process(self.signal)["status"], "blocked")
        self.options["analyze"].assert_called_once()
        self.processor.request_retry(self.signal)
        self.options["analyze"].side_effect = fixtures.fixtures.fixtures.analyze
        out = self.consume()
        self.assertEqual(out["status"], "acknowledged")
        self.assertEqual((folder / "analysis.json").read_bytes(), old)
        self.assertEqual(self.options["analyze"].call_count, 2)

    def test_partial_materials_can_finish_and_report_missing_count(self):
        self.signal["sample_alert_ids"].append("102")
        self.counter.signal = copy.deepcopy(self.signal)
        out = self.consume()
        self.assertEqual(out["status"], "acknowledged")
        self.assertEqual(out["processing"]["missing_task_count"], 1)

    def test_release_unknown_requires_explicit_flag_then_durable_ack(self):
        self.options["analyze"].side_effect = TimeoutError()
        self.consume()
        with self.assertRaisesRegex(ValueError, "requires_explicit_authorization"):
            self.processor.release(self.signal)
        self.processor.release(self.signal, allow_unconfirmed=True)
        result = self.consume()
        self.assertEqual(result["processing"]["status"], "released")
        self.assertEqual(result["status"], "acknowledged")
        self.options["analyze"].assert_called_once()

    def test_failed_finalization_write_error_does_not_ack(self):
        self.options["analyze"].side_effect = self.failed
        self.consume()
        self.consume()
        publish = processing._publish

        def fail_final(path, content):
            if path.name == "finalized.json":
                raise OSError("synthetic disk full")
            return publish(path, content)

        with patch.object(processing, "_publish", side_effect=fail_final):
            out = self.consume()
        self.assertEqual(out["status"], "processing_failed")
        self.assertEqual(self.counter.acks, 0)
        self.assertIsNotNone(self.counter.signal)
        self.assertEqual(self.consume()["status"], "acknowledged")
        self.assertEqual(self.options["analyze"].call_count, 3)

    def test_list_does_not_contact_counter_or_model(self):
        self.options["analyze"].side_effect = self.failed
        self.processor.process(self.signal)
        counter = Mock()
        out = control.maintain(
            self.processor.directory, self.scheduler, counter=counter
        )
        self.assertEqual(out["status"], "listed")
        self.assertEqual(out["items"][0]["status"], "failed")
        self.assertEqual(out["redis_calls"], 0)
        self.assertEqual(counter.mock_calls, [])
        self.options["analyze"].assert_called_once()

    def test_retry_does_not_replace_newer_pending_signal(self):
        self.options["analyze"].side_effect = self.failed
        self.processor.process(self.signal)
        other = {**self.signal, "signal_id": "newer"}
        self.counter.signal = other
        out = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="retry",
            rule=self.signal["rule_code"],
            counter=self.counter,
        )
        self.assertEqual(out["items"][0]["action_status"], "newer_pending_preserved")
        self.assertEqual(self.counter.signal, other)
        self.assertEqual(self.counter.set_calls, 0)
        self.options["analyze"].assert_called_once()

    def test_manual_review_is_terminal_and_never_submitted_as_recommendation(self):
        source = fixtures.fixtures.fixtures
        draft = source.fixtures.draft()
        draft["filter"]["A"]["operator"] = "regex"
        self.options["analyze"].side_effect = lambda data: source.analyze(data, draft)
        out = self.consume()
        self.assertEqual(out["processing"]["status"], "manual_review")
        self.assertEqual(out["status"], "acknowledged")
        self.assertFalse(self.options["sink"].directory.exists())

    def test_empty_attempt_directory_recovers_but_nonempty_damaged_attempt_does_not(
        self,
    ):
        self.options["load_task_rows"].return_value = []
        first = self.processor.process(self.signal)
        directory = (
            self.processor.directory / "jobs" / first["job_id"] / "attempts/0001"
        )
        (directory / "attempt.json").unlink()
        self.options["load_task_rows"].return_value = [base.row()]
        self.assertEqual(self.consume()["status"], "acknowledged")
        self.assertTrue((directory / "attempt.json").is_file())
        damaged = directory.with_name("0002")
        damaged.mkdir()
        (damaged / "unpublished.tmp").write_text("incomplete", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.processor.process(self.signal)
        self.options["analyze"].assert_called_once()

    def test_partial_publication_wrapper_over_depth_is_rejected_before_model(self):
        value = base_material()
        nested = {}
        # Find a material that is valid standalone but invalid in the start wrapper.
        for _ in range(80):
            nested = {"child": nested}
            value["synthetic_depth_probe"] = nested
            try:
                processing._encode(value, processing.LIMIT)
            except ValueError:
                self.fail("did not detect wrapper depth boundary")
            try:
                processing._encode({"materials": value}, processing.LIMIT)
            except ValueError:
                break
        else:
            self.fail("depth guard missing")
        with (
            patch.object(processing, "build_rule_materials", return_value=value),
            patch.object(processing, "build_analysis_request", return_value={}),
        ):
            outcome = self.processor.process(self.signal)
        self.assertEqual(outcome["status"], "materials_failed")
        self.options["analyze"].assert_not_called()
        self.assertFalse(list(self.processor.directory.rglob("analysis_started.json")))

    def test_materials_wait_retry_clears_only_selected_parked_scope(self):
        self.options["load_task_rows"].return_value = []
        self.processor.process(self.signal)
        tick = importlib.import_module(base.package.__name__ + ".background_tick")
        scope_key = hashlib.sha256(
            processing._encode(
                {
                    name: self.signal[name]
                    for name in ("company_code", "topic", "rule_code")
                },
                8192,
            )
        ).hexdigest()
        state = {
            "schema_version": "sec_know.rule_background_tick.v1",
            "mode": "live",
            "namespace": self.signal["namespace"],
            "after": "",
            "retry": {
                scope_key: {
                    "signal_id": self.signal["signal_id"],
                    "attempts": 3,
                    "next_at": 0,
                    "parked": True,
                },
                "a" * 64: {
                    "signal_id": "other",
                    "attempts": 3,
                    "next_at": 0,
                    "parked": True,
                },
            },
        }
        self.scheduler.mkdir()
        tick._replace(self.scheduler / "cursor.json", state)
        out = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="retry",
            rule=self.signal["rule_code"],
            counter=self.counter,
        )
        self.assertEqual(out["items"][0]["action_status"], "already_scheduled")
        self.assertEqual(
            set(tick._state(self.scheduler / "cursor.json")["retry"]), {"a" * 64}
        )
        self.options["analyze"].assert_not_called()

    def test_delivery_retry_reuses_saved_analysis_without_new_model_attempt(self):
        with patch.object(self.processor.sink, "submit", side_effect=TimeoutError()):
            first = self.consume()
        self.assertEqual(first["processing"]["status"], "delivery_unconfirmed")
        out = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="retry",
            rule=self.signal["rule_code"],
            counter=self.counter,
        )
        self.assertEqual(out["items"][0]["action_status"], "delivery_retry_scheduled")
        self.assertFalse(out["items"][0]["next_background_tick_may_call_model"])
        self.assertEqual(self.consume()["status"], "acknowledged")
        self.options["analyze"].assert_called_once()

    def test_later_success_hides_older_failure_from_bulk_retry(self):
        original = self.options["analyze"].side_effect
        self.options["analyze"].side_effect = self.failed
        self.processor.process(self.signal)
        self.options["analyze"].side_effect = original
        newer = {
            **self.signal,
            "signal_id": "later",
            "window_end": "2026-10-28T11:00:00+00:00",
        }
        self.processor.process(newer)
        listed = control.maintain(self.processor.directory, self.scheduler)
        self.assertEqual(listed["items"], [])

    def test_maintenance_errors_explain_controlled_reason_without_leaking_input(self):
        spec = importlib.util.spec_from_file_location(
            "_maintenance_cli", Path(__file__).with_name("retry_rule_refine.py")
        )
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        self.assertEqual(
            cli.failure_result(ValueError("ambiguous_rule_use_topic_and_company"))[
                "reason"
            ],
            "ambiguous_rule_use_topic_and_company",
        )
        result = cli.failure_result(ValueError("SECRET credentials"))
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["reason"], "configuration_or_saved_record_invalid")

    def test_maintenance_and_tick_share_lock_before_scanning_or_restoring(self):
        self.options["analyze"].side_effect = self.failed
        self.processor.process(self.signal)
        tick = importlib.import_module(base.package.__name__ + ".background_tick")
        with tick._locked(self.scheduler), patch.object(control, "_inspect") as inspect:
            outcome = control.maintain(
                self.processor.directory,
                self.scheduler,
                action="retry",
                rule=self.signal["rule_code"],
                counter=self.counter,
            )
        self.assertEqual(outcome["status"], "busy")
        inspect.assert_not_called()
        self.assertEqual(self.counter.set_calls, 0)
        original = self.counter.pending

        def pending_under_lock(observation):
            with tick._locked(self.scheduler) as path:
                self.assertIsNone(path)
            return original(observation)

        with patch.object(self.counter, "pending", side_effect=pending_under_lock):
            self.assertEqual(
                control.maintain(
                    self.processor.directory,
                    self.scheduler,
                    action="retry",
                    rule=self.signal["rule_code"],
                    counter=self.counter,
                )["status"],
                "completed",
            )

    def test_explicit_retry_after_releasing_materials_wait_creates_unreleased_attempt(
        self,
    ):
        self.options["load_task_rows"].return_value = []
        self.consume()
        released = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="release",
            rule=self.signal["rule_code"],
            counter=self.counter,
        )
        self.assertEqual(released["items"][0]["action_status"], "released")
        retried = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="retry",
            rule=self.signal["rule_code"],
            counter=self.counter,
        )
        self.assertEqual(retried["items"][0]["new_attempt"], 2)
        self.options["load_task_rows"].return_value = [base.row()]
        self.assertEqual(self.consume()["processing"]["status"], "received")
        self.options["analyze"].assert_called_once()

    def test_saved_transport_unknown_is_not_automatically_recalled(self):
        def uncertain(data):
            answer = self.failed(data)
            answer["model_transport"] = {
                "error_code": "request_failed",
                "sdk_request_calls": 1,
                "response_received": False,
            }
            return answer

        self.options["analyze"].side_effect = uncertain
        self.assertEqual(self.consume()["processing"]["status"], "analysis_unconfirmed")
        self.assertEqual(self.consume()["processing"]["status"], "analysis_unconfirmed")
        self.options["analyze"].assert_called_once()
        result = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="retry",
            rule=self.signal["rule_code"],
            counter=self.counter,
        )
        self.assertEqual(
            result["items"][0]["action_status"], "requires_allow_unconfirmed"
        )
        result = control.maintain(
            self.processor.directory,
            self.scheduler,
            action="retry",
            rule=self.signal["rule_code"],
            counter=self.counter,
            allow_unconfirmed=True,
        )
        self.assertEqual(result["items"][0]["action_status"], "scheduled")
        self.options["analyze"].assert_called_once()


def base_material():
    return fixtures.fixtures.fixtures.fixtures.materials()


if __name__ == "__main__":
    unittest.main(verbosity=2)
