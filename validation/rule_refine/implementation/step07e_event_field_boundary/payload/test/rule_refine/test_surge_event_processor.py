"""Offline two-stage correction and durable rejection of post-hit metadata."""

import copy
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
PACKAGE = "_surge_event_processor_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
processor = importlib.import_module(PACKAGE + ".surge_processor")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget
spec = importlib.util.spec_from_file_location(
    "_surge_event_fixtures", Path(__file__).with_name("test_surge_processor.py")
)
fixtures = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixtures
spec.loader.exec_module(fixtures)
POLICY = "event-fields-not-detection-results-v1"


class EventFieldProcessorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case = fixtures.case()
        for key in ("rule", "original_rule"):
            self.case[key]["topic"] = "security_qthids"
        for sample in self.case["current_samples"] + self.case["history_samples"]:
            for log in sample["raw_logs"]:
                log["detail_hit_rule_name"] = "Synthetic detector result"
        self.signal = fixtures.signal()
        self.signal["topic"] = "security_qthids"
        self.requests, self.responses = [], []
        self.review_field = "event_content"
        self.review_status = "proposal"
        self.generate = Mock(side_effect=self.respond)
        self.service = processor.SurgeProcessor(
            self.root,
            load_case=Mock(side_effect=lambda _: copy.deepcopy(self.case)),
            load_rules=lambda: types.SimpleNamespace(
                get_rule=lambda *a, **kw: copy.deepcopy(self.case["original_rule"])
            ),
            generate=self.generate,
            model="synthetic-offline-transport",
            budget=Budget(),
        )

    def respond(self, request):
        self.requests.append(copy.deepcopy(request))
        phase = request["phase"]
        reply = fixtures.answer(
            request,
            status=self.review_status if phase == "review" else "proposal",
            field=self.review_field if phase == "review" else "detail_hit_rule_name",
        )
        if reply["filter"] is not None:
            reply["filter"]["topic"] = "security_qthids"
        if phase == "generate":
            reply["filter"]["A"]["value"] = "Synthetic detector result"
        self.responses.append(copy.deepcopy(reply))
        return fixtures.transport(json.dumps(reply, ensure_ascii=False))

    @staticmethod
    def read(outcome, name):
        return json.loads(
            (Path(outcome["output_dir"]) / name).read_text(encoding="utf-8")
        )

    def test_generation_metadata_is_preserved_and_corrected_in_existing_review_call(
        self,
    ):
        result = self.service.process(self.signal)
        self.assertEqual(result["status"], "received")
        self.assertTrue(result["recommendation_ready"])
        self.assertEqual(result["model_calls_this_attempt"], 2)
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(result["field_usage_policy_version"], POLICY)
        self.assertEqual(
            result["recommendation_ready_scope"],
            "format_fields_references_and_reviewed_field_roles_only",
        )
        generation = self.read(result, "generate/parsed.json")["reply"]
        self.assertFalse(generation["recommendation_ready"])
        self.assertEqual(generation["filter"], self.responses[0]["filter"])
        self.assertEqual(
            generation["validation_issues"][0]["code"],
            "condition_uses_detection_metadata",
        )
        review_context = self.requests[1]["context"]
        self.assertEqual(review_context["draft"]["filter"], self.responses[0]["filter"])
        self.assertEqual(
            review_context["draft_validation_issues"], generation["validation_issues"]
        )
        saved = self.read(result, "generate/response.json")
        self.assertEqual(json.loads(saved["content"]), self.responses[0])
        recommendation = self.read(result, "recommendation.json")
        self.assertEqual(recommendation["filter"], self.responses[1]["filter"])
        self.assertFalse(recommendation["semantic_quality_verified"])
        self.assertFalse(recommendation["filter_replay_performed"])
        self.assertEqual(recommendation["engine_compatibility"], "not_verified")
        self.assertTrue(self.service.verify_durable_result(self.signal, result))

    def test_review_metadata_remains_manual_revision_with_exact_issue_in_report(self):
        self.review_field = "detail_hit_rule_name"
        result = self.service.process(self.signal)
        self.assertEqual(result["status"], "manual_review")
        self.assertFalse(result["recommendation_ready"])
        self.assertTrue(result["ai_review_completed"])
        self.assertEqual(result["model_calls_this_attempt"], 2)
        final = self.read(result, "recommendation.json")
        self.assertEqual(final["filter"], self.responses[1]["filter"])
        self.assertEqual(
            final["validation_issues"][0]["code"], "condition_uses_detection_metadata"
        )
        report = (Path(result["output_dir"]) / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("filter.A.name", report)
        self.assertIn("检测结果信息，不能作为新增排除条件", report)
        self.assertIn("detail_hit_rule_name", report)
        self.assertIn(self.responses[1]["recommendation"], report)
        self.assertTrue(self.service.verify_durable_result(self.signal, result))

    def test_review_can_withdraw_without_forcing_a_replacement_condition(self):
        self.review_status = "insufficient_evidence"
        result = self.service.process(self.signal)
        self.assertEqual(result["status"], "not_applicable")
        self.assertFalse(result["recommendation_ready"])
        self.assertEqual(self.generate.call_count, 2)
        self.assertFalse((Path(result["output_dir"]) / "recommendation.json").exists())

    def test_completed_rejection_reuses_exact_artifacts_without_retrying_model(self):
        self.review_field = "detail_hit_rule_name"
        first = self.service.process(self.signal)
        folder = Path(first["output_dir"])
        before = {
            p.relative_to(folder): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file()
        }
        second = self.service.process(self.signal)
        self.assertEqual(second["status"], "manual_review")
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(second["model_calls_this_attempt"], 0)
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(
            {
                p.relative_to(folder): p.read_bytes()
                for p in folder.rglob("*")
                if p.is_file()
            },
            before,
        )

    def test_saved_generation_response_is_rechecked_without_regeneration(self):
        with patch.object(
            processor.ai, "parse_reply", side_effect=RuntimeError("crash")
        ):
            with self.assertRaises(RuntimeError):
                self.service.process(self.signal)
        self.assertEqual(self.generate.call_count, 1)
        result = self.service.process(self.signal)
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["model_calls_this_attempt"], 1)
        self.assertEqual(self.generate.call_count, 2)
        generation = self.read(result, "generate/parsed.json")["reply"]
        self.assertEqual(
            generation["validation_issues"][0]["code"],
            "condition_uses_detection_metadata",
        )
        self.assertTrue(self.service.verify_durable_result(self.signal, result))


if __name__ == "__main__":
    unittest.main()
