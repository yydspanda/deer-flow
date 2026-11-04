"""Keep detector results as context, never turn them into exclusion predicates."""

import copy
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_surge_event_field_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
ai = importlib.import_module(PACKAGE + ".surge_ai_review")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget
POLICY = "event-fields-not-detection-results-v1"
SCOPE = "contract_references_expression_visible_fields_and_reviewed_field_roles_only"


def rule(topic="security_qthids"):
    return {
        "company_code": "synthetic-company",
        "topic": topic,
        "rule_code": "synthetic-detector",
        "rule_name": "Synthetic detector result",
        "rule_config_version": "fixture-v1",
        "configuration": {"terms": []},
    }


def sample(identity, logs):
    return {"alert_id": identity, "raw_logs": logs}


def answer(field="event_content", topic="security_qthids"):
    return {
        "status": "proposal",
        "issue": "Synthetic repeated inventory operation",
        "recommendation": "Only the explicit operation is proposed for review",
        "certainty": "Synthetic transport fixture, not model accuracy evidence",
        "filter": {
            "topic": topic,
            "A": {"name": field, "operator": "contain", "value": "inventory-read"},
            "expression": "A",
        },
        "sample_alert_ids": ["current-one"],
        "review_notes": "Other activities must retain their detection",
        "sample_request": None,
    }


class EventFieldBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.rule = rule()
        self.current = [
            sample(
                "current-one",
                [
                    {
                        "detail_hit_rule_name": "Synthetic detector result",
                        "detail_rule": {"id": "derived-detector-id"},
                        "event_content": "inventory-read --packages",
                        "detail_process_name": "inventory.exe",
                    }
                ],
            ),
            sample("current-two", [{"vendor_operation": "interactive-change"}]),
        ]
        self.history = [sample("history-one", [{"history_only_field": "manual"}])]

    def request(self, **kwargs):
        return ai.build_request(
            self.rule,
            self.current,
            self.history,
            {"current_alert_count": 2},
            limits=Budget(),
            **kwargs,
        )

    def parse(self, value, **kwargs):
        return ai.parse_reply(
            json.dumps(value, ensure_ascii=False),
            rule=self.rule,
            visible_samples=self.current + self.history,
            **kwargs,
        )

    def test_catalog_separates_context_metadata_without_erasing_original_evidence(self):
        originals = copy.deepcopy((self.current, self.history))
        for kwargs in ({"independent": True}, {"phase": "review", "draft": answer()}):
            with self.subTest(phase=kwargs):
                request = self.request(**kwargs)
                context = request["context"]
                self.assertEqual(context["field_usage_policy_version"], POLICY)
                self.assertEqual(context["current_samples"], self.current)
                self.assertEqual(context["history_samples"], self.history)
                self.assertIn("detail_hit_rule_name", context["visible_field_names"])
                self.assertNotIn(
                    "detail_hit_rule_name", context["candidate_field_names"]
                )
                self.assertNotIn("detail_rule", context["candidate_field_names"])
                self.assertNotIn("detail_rule.id", context["candidate_field_names"])
                for field in (
                    "event_content",
                    "vendor_operation",
                    "history_only_field",
                ):
                    self.assertIn(field, context["candidate_field_names"])
                self.assertFalse(
                    context["field_usage"]["candidate_engine_availability_verified"]
                )
                self.assertEqual(ai.request_field_usage_version(request), POLICY)
                self.assertLessEqual(
                    request["estimated_input_tokens"], Budget().input_token_budget
                )
                self.assertIsNone(request["model_output_token_limit"])
        self.assertEqual((self.current, self.history), originals)

    def test_detection_metadata_cannot_hide_in_and_or_or_unused_conditions(self):
        for expression in ("A", "A && B", "A || B", "B"):
            with self.subTest(expression=expression):
                value = answer("detail_hit_rule_name")
                value["filter"]["B"] = {
                    "name": "event_content",
                    "operator": "contain",
                    "value": "inventory-read",
                }
                value["filter"]["expression"] = expression
                original = copy.deepcopy(value)
                parsed = self.parse(value, phase="review")
                self.assertTrue(parsed["format_valid"])
                self.assertFalse(parsed["recommendation_ready"])
                issue = parsed["validation_issues"][0]
                self.assertEqual(issue["code"], "condition_uses_detection_metadata")
                self.assertEqual(issue["path"], ["filter", "A", "name"])
                self.assertEqual(issue["field"], "detail_hit_rule_name")
                self.assertEqual(parsed["filter"], original["filter"])
                self.assertEqual(value, original)

    def test_rule_configuration_does_not_whitelist_detection_results(self):
        self.rule["configuration"] = {
            "terms": [{"conditions": [{"leftFieldName": "detail_hit_rule_name"}]}]
        }
        parsed = self.parse(answer("detail_hit_rule_name"))
        self.assertFalse(parsed["recommendation_ready"])
        self.assertEqual(
            parsed["validation_issues"][0]["code"], "condition_uses_detection_metadata"
        )

    def test_single_actual_event_field_is_not_forced_into_multiple_conditions(self):
        value = answer()
        self.assertEqual(len(value), 8)
        parsed = self.parse(value)
        self.assertTrue(parsed["recommendation_ready"])
        self.assertEqual(parsed["filter"], value["filter"])
        self.assertEqual(parsed["validation_scope"], SCOPE)
        self.assertEqual(parsed["engine_compatibility"], "not_verified")
        self.assertFalse(parsed["program_filter_evaluation_performed"])

    def test_unreviewed_topic_does_not_get_semantics_guessed_from_field_name(self):
        self.rule = rule("synthetic-build-events")
        value = answer("detail_hit_rule_name", topic=self.rule["topic"])
        request = self.request(independent=True)
        self.assertIn(
            "detail_hit_rule_name", request["context"]["candidate_field_names"]
        )
        parsed = self.parse(value)
        self.assertEqual(parsed["validation_issues"], [])
        self.assertEqual(parsed["engine_compatibility"], "not_verified")

    def test_unknown_field_remains_a_separate_error_without_alias_guessing(self):
        parsed = self.parse(answer("detail.process_name"))
        self.assertFalse(parsed["recommendation_ready"])
        self.assertEqual(parsed["validation_issues"][0]["code"], "field_not_visible")
        self.assertEqual(parsed["filter"]["A"]["name"], "detail.process_name")

    def test_known_metadata_and_missing_fields_are_both_reported(self):
        value = answer("detail_hit_rule_name")
        value["filter"]["B"] = {
            "name": "unknown_command_path",
            "operator": "contain",
            "value": "inventory-read",
        }
        value["filter"]["expression"] = "A && B"
        issues = self.parse(value)["validation_issues"]
        self.assertEqual(
            {issue["code"] for issue in issues},
            {"condition_uses_detection_metadata", "field_not_visible"},
        )

    def test_review_receives_rejected_field_issue_and_unchanged_candidate(self):
        parsed = self.parse(answer("detail_hit_rule_name"))
        context = self.request(phase="review", draft=parsed)["context"]
        self.assertEqual(context["draft"]["filter"], parsed["filter"])
        self.assertEqual(
            context["draft_validation_issues"], parsed["validation_issues"]
        )
        self.assertNotIn("recommendation", context["draft"])
        self.assertNotIn("certainty", context["draft"])

    def test_frozen_catalog_cannot_relax_roles_even_if_prompt_hash_is_updated(self):
        request = self.request(independent=True)
        request["context"]["candidate_field_names"].append("detail_hit_rule_name")
        request["context"]["field_usage"]["candidate_field_names"].append(
            "detail_hit_rule_name"
        )
        request["messages"][1]["content"] = ai.encoded(request["context"]).decode()
        request["prompt_sha256"] = ai.hashlib.sha256(
            ai.encoded(request["messages"])
        ).hexdigest()
        with self.assertRaises(ValueError):
            ai.request_field_usage_version(request)

    def test_new_prompt_cannot_silently_drop_policy_marker(self):
        request = self.request(independent=True)
        request["context"].pop("field_usage_policy_version")
        with self.assertRaises(ValueError):
            ai.request_field_usage_version(request)

    def test_explicit_legacy_replay_preserves_old_validation_contract(self):
        value = answer("detail_hit_rule_name")
        parsed = self.parse(value, field_usage_policy_version=None)
        self.assertTrue(parsed["recommendation_ready"])
        self.assertEqual(parsed["validation_issues"], [])
        self.assertEqual(
            parsed["validation_scope"],
            "contract_references_expression_and_visible_field_names_only",
        )
        self.assertNotIn("field_usage_policy_version", parsed)


if __name__ == "__main__":
    unittest.main()
