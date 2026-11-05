"""Durable two-context advice with synthetic originals and no external calls."""

import copy
import fcntl
import importlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_isolated_surge_processor"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
module = importlib.import_module(PACKAGE + ".surge_processor")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget


def rule():
    return {
        "company_code": "test-company",
        "topic": "synthetic-topic",
        "rule_code": "test-rule",
        "rule_name": "人工通用规则",
        "rule_config_version": "v1",
        "configuration": {"terms": []},
        "source": {"kind": "synthetic_fixture"},
    }


def sample(identity, *, history=False):
    return {
        "alert_id": identity,
        "occurred_at": "2026-11-03T08:00:00Z" if history else "2026-11-04T08:00:00Z",
        "raw_logs": [
            {"event_content": "scheduled task", "detail_process_name": "probe.exe"},
            {"event_content": "different activity", "detail_process_name": "other.exe"},
        ],
    }


def case():
    return {
        "rule": rule(),
        "original_rule": rule(),
        "current_samples": [sample("new-" + str(i)) for i in range(6)],
        "history_samples": [sample("old-1", history=True)],
        "coverage": {
            "current_alert_count": 6,
            "historical_available_unique_alerts": 1,
            "history_normal_labels_assumed": False,
        },
    }


def signal():
    return {
        "schema_version": "sec_know.rule_burst_signal.v1",
        "signal_id": "signal-1",
        "mode": "demo",
        "namespace": "test-isolated",
        "source_app": "test-app",
        "company_code": "test-company",
        "topic": "synthetic-topic",
        "rule_code": "test-rule",
        "sample_alert_ids": ["new-" + str(i) for i in range(6)],
        "current_count": 6,
        "previous_count": 1,
    }


def answer(request, *, status="proposal", field="event_content"):
    context = request["context"]
    return {
        "status": status,
        "issue": "样本存在主要重复场景，另有不同活动。",
        "recommendation": "待运营确认后新增排除场景。",
        "certainty": "仅限展示样本。",
        "filter": {
            "topic": "synthetic-topic",
            "A": {"name": field, "operator": "Contains", "value": "scheduled task"},
            "expression": "A",
        }
        if status == "proposal"
        else None,
        "sample_alert_ids": [context["current_samples"][0]["alert_id"]]
        if status == "proposal"
        else [],
        "review_notes": "其他活动不能因同属一条告警而被排除。",
        "sample_request": None,
    }


def transport(content):
    return {
        "content": content,
        "transport": {
            "sdk_request_calls": 1,
            "response_received": True,
            "finish_reason": "stop",
            "error_code": None,
            "requested_model": "synthetic-model",
        },
    }


class SurgeProcessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case = case()
        self.signal = signal()
        self.load_case = Mock(side_effect=lambda _: copy.deepcopy(self.case))
        self.rules = Mock(get_rule=Mock(side_effect=lambda *a, **k: rule()))
        self.generate = Mock(
            side_effect=lambda request: transport(
                json.dumps(answer(request), ensure_ascii=False)
            )
        )
        self.processor = module.SurgeProcessor(
            self.root,
            load_case=self.load_case,
            load_rules=lambda: self.rules,
            generate=self.generate,
            model="synthetic-model",
            budget=Budget(),
        )

    def process(self):
        return self.processor.process(self.signal)

    def folder(self):
        _, _, job_id = module._identity(self.signal)
        return self.root / "jobs" / job_id

    def read(self, name):
        return json.loads((self.folder() / name).read_text(encoding="utf-8"))

    def test_two_calls_then_delivery_retry_reuses_exact_result(self):
        first = self.process()
        second = self.process()
        self.assertEqual(first["status"], "received")
        self.assertEqual(second["status"], "received")
        self.assertEqual(
            (first["model_calls_this_attempt"], second["model_calls_this_attempt"]),
            (2, 0),
        )
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertFalse(first["analysis_reused"])
        self.assertTrue(second["analysis_reused"])
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.load_case.call_count, 1)
        self.assertTrue(self.processor.verify_durable_result(self.signal, second))
        recommendation = self.read("recommendation.json")
        self.assertTrue(recommendation["ai_review_completed"])
        self.assertFalse(recommendation["automatic_apply"])
        self.assertFalse(recommendation["filter_replay_performed"])
        self.assertFalse(recommendation["filter_coverage_measured"])
        self.assertFalse(recommendation["semantic_quality_verified"])
        self.assertEqual(recommendation["delivery_scope"], "local_record_only")

    def test_initial_request_independent_then_review_has_new_and_cited_originals(self):
        self.process()
        first, second = [
            call.args[0]["context"] for call in self.generate.call_args_list
        ]
        self.assertNotIn("draft", first)
        self.assertEqual(first["assessment_mode"], "independent_initial_generation")
        self.assertIn("draft", second)
        first_ids = {item["alert_id"] for item in first["current_samples"]}
        second_ids = {item["alert_id"] for item in second["current_samples"]}
        self.assertTrue(second_ids - first_ids)
        self.assertTrue(set(second["draft"]["sample_alert_ids"]).issubset(second_ids))
        self.assertEqual(first["history_samples"], second["history_samples"])
        for context in (first, second):
            for item in context["current_samples"]:
                self.assertEqual(item["raw_logs"], sample(item["alert_id"])["raw_logs"])

    def test_current_and_history_are_separate_and_old_model_answers_not_prompted(self):
        self.case["current_samples"][0]["agent_response"] = "SECRET-OLD-ANSWER"
        self.case["history_samples"][0]["agent_response"] = "SECRET-OLD-ANSWER"
        self.process()
        for call in self.generate.call_args_list:
            request = call.args[0]
            self.assertNotIn("SECRET-OLD-ANSWER", json.dumps(request["messages"]))
            self.assertEqual(
                request["context"]["history_samples"][0]["alert_id"], "old-1"
            )

    def test_crash_after_saved_generation_response_resumes_without_regeneration(self):
        with patch.object(
            module.ai,
            "parse_reply",
            side_effect=RuntimeError("simulated process crash"),
        ):
            with self.assertRaises(RuntimeError):
                self.process()
        self.assertTrue((self.folder() / "generate/response.json").exists())
        self.assertFalse((self.folder() / "generate/parsed.json").exists())
        outcome = self.process()
        self.assertEqual(outcome["status"], "received")
        self.assertTrue(outcome["analysis_reused"])
        self.assertEqual(outcome["model_calls_this_attempt"], 1)
        self.assertEqual(self.generate.call_count, 2)

    def test_lost_transport_response_is_parked_without_repeat(self):
        self.generate.side_effect = TimeoutError("private endpoint should not leak")
        first, second = self.process(), self.process()
        self.assertEqual(first["status"], "analysis_unconfirmed")
        self.assertEqual(second["status"], "analysis_unconfirmed")
        self.assertEqual(self.generate.call_count, 1)
        self.assertNotIn("private endpoint", json.dumps(first))
        self.assertEqual(second["model_calls_this_attempt"], 0)

    def test_start_marker_only_prevents_request_repeat(self):
        real_write = module._write
        with patch.object(module, "_write", wraps=module._write) as writer:

            def crash_after_start(path, value, **kwargs):
                result = real_write(path, value, **kwargs)
                if path.name == "started.json":
                    raise KeyboardInterrupt()
                return result

            writer.side_effect = crash_after_start
            with self.assertRaises(KeyboardInterrupt):
                self.process()
        outcome = self.process()
        self.assertEqual(outcome["status"], "analysis_unconfirmed")
        self.generate.assert_not_called()

    def test_invalid_json_saved_and_blocked_no_model_retry(self):
        self.generate.side_effect = lambda request: transport('{"bad":')
        first, second = self.process(), self.process()
        self.assertEqual((first["status"], second["status"]), ("blocked", "blocked"))
        self.assertEqual(self.read("generate/response.json")["content"], '{"bad":')
        self.assertEqual(self.generate.call_count, 1)
        self.assertFalse(first["ai_review_completed"])

    def test_confirmed_provider_error_is_saved_and_parked(self):
        self.generate.side_effect = lambda request: {
            "content": None,
            "transport": {
                "error_code": "request_failed",
                "response_received": False,
                "sdk_request_calls": 1,
            },
        }
        result = self.process()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "model_request_failed")
        self.assertEqual(self.generate.call_count, 1)

    def test_truncated_response_even_valid_json_is_not_accepted(self):
        def generate(request):
            result = transport(json.dumps(answer(request)))
            result["transport"]["finish_reason"] = "length"
            return result

        self.generate.side_effect = generate
        result = self.process()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "model_response_not_complete")

    def test_unknown_final_field_retains_manual_draft_without_guessing_alias(self):
        self.generate.side_effect = lambda request: transport(
            json.dumps(answer(request, field="detail.process.name"))
        )
        result = self.process()
        self.assertEqual(result["status"], "manual_review")
        self.assertTrue(result["ai_review_completed"])
        self.assertFalse(result["recommendation_ready"])
        saved = self.read("recommendation.json")
        self.assertEqual(saved["filter"]["A"]["name"], "detail.process.name")
        self.assertEqual(saved["validation_issues"][0]["code"], "field_not_visible")
        self.assertTrue(self.processor.verify_durable_result(self.signal, result))

    def test_generation_unknown_field_can_be_corrected_by_review(self):
        def generate(request):
            field = (
                "missing"
                if request["context"]["phase"] == "generate"
                else "event_content"
            )
            return transport(json.dumps(answer(request, field=field)))

        self.generate.side_effect = generate
        result = self.process()
        self.assertEqual(result["status"], "received")
        self.assertTrue(
            self.generate.call_args_list[1].args[0]["context"][
                "draft_validation_issues"
            ]
        )

    def test_no_history_is_valid_and_reported(self):
        self.case["history_samples"] = []
        outcome = self.process()
        self.assertEqual(outcome["status"], "received")
        for phase in ("generate", "review"):
            self.assertEqual(
                self.read("recommendation.json")["evidence"][phase]["history_samples"],
                [],
            )

    def test_no_change_and_insufficient_evidence_still_get_second_review(self):
        for status in ("no_change", "insufficient_evidence"):
            with self.subTest(status=status):
                self.signal["signal_id"] = "signal-" + status
                before = self.generate.call_count
                self.generate.side_effect = lambda request, status=status: transport(
                    json.dumps(answer(request, status=status))
                )
                outcome = self.process()
                self.assertEqual(outcome["status"], "not_applicable")
                self.assertTrue(outcome["ai_review_completed"])
                self.assertEqual(self.generate.call_count - before, 2)
                self.assertFalse((self.folder() / "recommendation.json").exists())

    def test_rule_configuration_changed_before_publication_requires_manual_review(self):
        fresh = rule()
        fresh["configuration"]["terms"] = [{"changed": True}]
        self.rules.get_rule.side_effect = lambda *a, **kw: fresh
        outcome = self.process()
        self.assertEqual(outcome["status"], "manual_review")
        self.assertEqual(outcome["reason"], "configuration_changed")
        self.assertFalse(self.read("recommendation.json")["recommendation_ready"])
        self.assertEqual(self.generate.call_count, 2)

    def test_current_rule_provider_failure_does_not_use_old_configuration(self):
        self.rules.get_rule.side_effect = ConnectionError("private source")
        outcome = self.process()
        self.assertEqual(outcome["status"], "manual_review")
        self.assertEqual(outcome["reason"], "configuration_unavailable")
        self.assertNotIn("private source", json.dumps(outcome))

    def test_same_scope_signal_id_different_content_rejected(self):
        original = self.process()
        altered = {**self.signal, "current_count": 999}
        with self.assertRaises(module.DurableSurgeError):
            self.processor.process(altered)
        with self.assertRaises(module.DurableSurgeError):
            self.processor.verify_durable_result(altered, original)
        self.assertEqual(self.generate.call_count, 2)

    def test_forged_outcome_and_tampered_artifacts_rejected(self):
        outcome = self.process()
        with self.assertRaises(module.DurableSurgeError):
            self.processor.verify_durable_result(
                self.signal, {**outcome, "status": "not_applicable"}
            )
        path = self.folder() / "review/response.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(module.DurableSurgeError):
            self.processor.verify_durable_result(self.signal, outcome)
        with self.assertRaises(module.DurableSurgeError):
            self.process()
        self.assertEqual(self.generate.call_count, 2)

    def test_tampered_final_result_rejected(self):
        outcome = self.process()
        record = self.read("result.json")
        record["outcome"]["recommendation_ready"] = False
        (self.folder() / "result.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(module.DurableSurgeError):
            self.processor.verify_durable_result(self.signal, outcome)

    def test_recommendation_has_stable_id_on_delivery_retry(self):
        self.process()
        first = self.read("recommendation.json")
        self.process()
        self.assertEqual(first, self.read("recommendation.json"))

    def test_overlapping_current_history_id_is_rejected_before_call(self):
        self.case["history_samples"] = [sample("new-0", history=True)]
        with self.assertRaises(module.DurableSurgeError):
            self.process()
        self.generate.assert_not_called()

    def test_busy_job_does_not_call_model(self):
        folder = self.folder()
        folder.mkdir(parents=True)
        with (folder / "process.lock").open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.process()
        self.assertEqual(result["status"], "busy")
        self.generate.assert_not_called()

    def test_empty_current_materials_retry_without_calls(self):
        self.case["current_samples"] = []
        self.assertEqual(self.process()["status"], "materials_pending")
        self.case = case()
        self.assertEqual(self.process()["status"], "received")
        self.assertEqual(self.generate.call_count, 2)

    def test_only_materials_pending_error_is_deferred(self):
        self.load_case.side_effect = module.MaterialsPendingError(
            {"missing_current_alerts": 1}
        )
        first = self.process()
        self.assertEqual(first["status"], "materials_pending")
        self.assertEqual(first["reason"], "current_alert_materials_pending")
        self.load_case.side_effect = ValueError(
            "current_receipt_window_no_longer_complete"
        )
        second = self.process()
        self.assertEqual(second["status"], "blocked")
        self.generate.assert_not_called()

    def test_changed_processor_configuration_is_parked_before_retry(self):
        self.case["current_samples"] = []
        self.assertEqual(self.process()["status"], "materials_pending")
        self.processor.model = "other-model"
        self.case = case()
        result = self.process()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "processor_configuration_changed")
        self.generate.assert_not_called()

    def test_crash_after_review_response_recovers_with_no_new_requests(self):
        parse_reply = module.ai.parse_reply

        def crash_review(*args, **kwargs):
            if kwargs.get("phase") == "review":
                raise RuntimeError("review parse interrupted")
            return parse_reply(*args, **kwargs)

        with patch.object(module.ai, "parse_reply", side_effect=crash_review):
            with self.assertRaises(RuntimeError):
                self.process()
        self.assertEqual(self.generate.call_count, 2)
        result = self.process()
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["model_calls_this_attempt"], 0)
        self.assertTrue(result["analysis_reused"])
        self.assertEqual(self.generate.call_count, 2)

    def test_local_report_publication_interruption_does_not_repeat_models(self):
        real_write = module._write

        def crash_before_record(path, value, **kwargs):
            if path.name == "result.json":
                raise KeyboardInterrupt()
            return real_write(path, value, **kwargs)

        with patch.object(module, "_write", side_effect=crash_before_record):
            with self.assertRaises(KeyboardInterrupt):
                self.process()
        self.assertTrue((self.folder() / "recommendation.json").exists())
        result = self.process()
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["model_calls_this_attempt"], 0)
        self.assertEqual(self.generate.call_count, 2)

    def test_single_alert_review_does_not_claim_new_samples(self):
        self.case["current_samples"] = [sample("new-0")]
        self.assertEqual(self.process()["status"], "received")
        selection = self.read("review/selection.json")["selection"]
        self.assertEqual(selection["new_current_count"], 0)
        self.assertEqual(selection["cited_current_reused"], 1)

    def test_source_has_no_program_filter_evaluator_dependency(self):
        source = (ROOT / "surge_processor.py").read_text(encoding="utf-8")
        self.assertNotIn("import filter_evaluation", source)
        self.assertNotIn("evaluate_filter(", source)
        self.assertNotIn("LocalRecommendationSink", source)


if __name__ == "__main__":
    unittest.main()
