"""Exercise the new review context/report, using scripted replies, never an LLM."""

import copy
import importlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_surge_behavior_processor_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
processor = importlib.import_module(PACKAGE + ".surge_processor")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget
spec = importlib.util.spec_from_file_location(
    "_surge_behavior_fixtures", Path(__file__).with_name("test_surge_processor.py")
)
fixtures = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixtures
spec.loader.exec_module(fixtures)


class BehaviorProcessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case = fixtures.case()
        # A synthetic detector has multiple behaviors. This is a transport
        # fixture testing orchestration, not a claim about real LLM accuracy.
        for sample in self.case["current_samples"] + self.case["history_samples"]:
            for log in sample["raw_logs"]:
                log["detector_label"] = "synthetic detector"
        self.signal = fixtures.signal()
        self.requests = []
        self.responses = []
        self.review_status = "proposal"
        self.review_field = "event_content"
        self.load_case = Mock(return_value=copy.deepcopy(self.case))
        self.generate = Mock(side_effect=self.respond)
        self.service = processor.SurgeProcessor(
            self.root,
            load_case=self.load_case,
            load_rules=lambda: types.SimpleNamespace(
                get_rule=lambda *a, **kw: fixtures.rule()
            ),
            generate=self.generate,
            model="scripted-offline-transport",
            budget=Budget(),
        )

    def respond(self, request):
        self.requests.append(copy.deepcopy(request))
        reply = fixtures.answer(request)
        if request["phase"] == "generate":
            reply["filter"]["A"] = {
                "name": "detector_label",
                "operator": "Eq",
                "value": "synthetic detector",
            }
            for key in ("issue", "recommendation", "certainty", "review_notes"):
                reply[key] = "FIRST_STAGE_REASON_MUST_NOT_ANCHOR_REVIEW"
            reply["extra_explanation"] = "FIRST_STAGE_EXTRA_MUST_NOT_ANCHOR_REVIEW"
        else:
            reply = fixtures.answer(
                request, status=self.review_status, field=self.review_field
            )
        self.responses.append(copy.deepcopy(reply))
        return fixtures.transport(json.dumps(reply, ensure_ascii=False))

    def read(self, outcome, name):
        return json.loads(
            (Path(outcome["output_dir"]) / name).read_text(encoding="utf-8")
        )

    def test_two_stage_workflow_uses_candidate_only_and_keeps_first_answer(self):
        result = self.service.process(self.signal)
        self.assertEqual(result["model_calls_this_attempt"], 2)
        self.assertEqual(result["status"], "received")
        self.assertEqual(
            result["scope_policy_version"], "behavior-specific-exclusion-v1"
        )
        self.assertEqual(result["review_method"], "filter_scope_and_other_behaviors")
        self.assertFalse(result["draft_explanation_included"])
        first, review = self.requests
        self.assertNotIn("draft", first["context"])
        self.assertEqual(
            review["context"]["draft"],
            {
                key: self.responses[0][key]
                for key in ("status", "filter", "sample_alert_ids")
            },
        )
        self.assertNotIn("MUST_NOT_ANCHOR", json.dumps(review["messages"]))
        saved = self.read(result, "generate/response.json")
        self.assertEqual(json.loads(saved["content"]), self.responses[0])
        recommendation = self.read(result, "recommendation.json")
        self.assertEqual(recommendation["filter"], self.responses[1]["filter"])
        self.assertFalse(recommendation["semantic_quality_verified"])
        self.assertFalse(recommendation["filter_scope_program_verified"])
        self.assertEqual(
            recommendation["recommendation_ready_scope"],
            "format_fields_references_and_reviewed_field_roles_only",
        )
        self.assertFalse(recommendation["filter_replay_performed"])
        self.assertFalse(recommendation["automatic_apply"])
        self.assertTrue(self.service.verify_durable_result(self.signal, result))

    def test_new_report_explains_actual_json_and_readiness_without_rewriting_answer(
        self,
    ):
        result = self.service.process(self.signal)
        report = (Path(result["output_dir"]) / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("未输入第一轮解释", report)
        self.assertIn("可供人工审阅", report)
        self.assertIn("说明文字不会自动增加", report)
        self.assertIn(self.responses[-1]["recommendation"], report)
        self.assertIn("scheduled task", report)
        self.assertNotIn("程序已证明条件安全", report)

    def test_withdrawal_ends_after_two_calls_without_forcing_proposal(self):
        self.review_status = "insufficient_evidence"
        result = self.service.process(self.signal)
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["analysis_status"], "insufficient_evidence")
        self.assertFalse(result["recommendation_ready"])
        self.assertTrue(result["ai_review_completed"])
        self.assertEqual(self.generate.call_count, 2)
        self.assertFalse((Path(result["output_dir"]) / "recommendation.json").exists())

    def test_saved_result_reuses_both_answers_without_more_calls_or_changes(self):
        first = self.service.process(self.signal)
        folder = Path(first["output_dir"])
        frozen = {
            p.relative_to(folder): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file()
        }
        second = self.service.process(self.signal)
        self.assertTrue(second["analysis_reused"])
        self.assertEqual(second["model_calls_this_attempt"], 0)
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(
            frozen,
            {
                p.relative_to(folder): p.read_bytes()
                for p in folder.rglob("*")
                if p.is_file()
            },
        )

    def test_unknown_field_still_requires_revision_and_no_extra_model_calls(self):
        self.review_field = "not_in_original_logs"
        result = self.service.process(self.signal)
        self.assertEqual(result["status"], "manual_review")
        self.assertFalse(result["recommendation_ready"])
        self.assertEqual(self.generate.call_count, 2)
        recommendation = self.read(result, "recommendation.json")
        self.assertEqual(
            recommendation["validation_issues"][0]["code"], "field_not_visible"
        )


if __name__ == "__main__":
    unittest.main()
