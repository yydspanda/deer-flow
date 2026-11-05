"""Behavior-first contexts without field-role lists or model-effect claims."""

import copy
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_surge_behavior_scope_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
ai = importlib.import_module(PACKAGE + ".surge_ai_review")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget


def rule(topic="synthetic-vendor"):
    return {
        "company_code": "synthetic-company",
        "topic": topic,
        "rule_code": "synthetic-rule",
        "rule_name": "synthetic detector",
        "rule_config_version": "test-v1",
        "configuration": {"terms": []},
    }


def sample(identity, logs):
    return {"alert_id": identity, "raw_logs": logs}


def answer(refs, *, topic="synthetic-vendor", field="vendor_activity"):
    return {
        "status": "proposal",
        "issue": "FIRST_STAGE_ISSUE_MUST_NOT_ANCHOR_REVIEW",
        "recommendation": "FIRST_STAGE_ARGUMENT_MUST_NOT_ANCHOR_REVIEW",
        "certainty": "FIRST_STAGE_CERTAINTY_MUST_NOT_ANCHOR_REVIEW",
        "filter": {
            "topic": topic,
            "A": {"name": field, "operator": "Contains", "value": "inventory-read"},
            "expression": "A",
        },
        "sample_alert_ids": list(refs),
        "review_notes": "FIRST_STAGE_REVIEW_NOTES_MUST_NOT_ANCHOR_REVIEW",
        "sample_request": None,
    }


class BehaviorScopeContextTests(unittest.TestCase):
    def setUp(self):
        self.rule = rule()
        self.current = [
            sample("current-one", [{"vendor_activity": "inventory-read --packages"}]),
            sample("current-two", [{"another_shape": "interactive-service-change"}]),
        ]
        self.history = [
            sample("history-one", [{"vendor_activity": "interactive-diagnostic"}])
        ]
        self.coverage = {
            "current_alert_count": 20,
            "history_selected_unique_alerts": 1,
            "new_current_samples": 1,
        }
        self.budget = Budget()

    def request(self, *, draft=None, independent=False):
        return ai.build_request(
            self.rule,
            self.current,
            self.history,
            self.coverage,
            draft=draft,
            phase="review" if draft is not None else "generate",
            independent=independent,
            limits=self.budget,
        )

    def test_generation_records_behavior_first_method_without_previous_answers(self):
        for independent in (False, True):
            with self.subTest(independent=independent):
                self.current[0]["agent_response"] = "OLD_RESPONSE_NOT_EVIDENCE"
                request = self.request(independent=independent)
                context = request["context"]
                self.assertEqual(
                    context["generation_method"], "behavior_first_from_original_logs"
                )
                self.assertEqual(
                    context["scope_policy_version"], "behavior-specific-exclusion-v1"
                )
                self.assertNotIn("draft", context)
                self.assertNotIn("OLD_RESPONSE_NOT_EVIDENCE", json.dumps(request))
                self.assertEqual(
                    request["prompt_version"],
                    "rule-surge-runtime-independent-generation-v6"
                    if independent
                    else "rule-surge-runtime-review-v7",
                )

    def test_review_projects_only_candidate_filter_status_and_original_references(self):
        draft = answer(["current-one", "old-not-displayed", "history-one"])
        draft["model_extra_fields"] = {"rationale": "EXTRA_ARGUMENT_NOT_EVIDENCE"}
        draft["output_warnings"] = ["unrelated_output_warning"]
        original = copy.deepcopy(draft)
        request = self.request(draft=draft)
        context = request["context"]
        self.assertEqual(
            set(context["draft"]), {"status", "filter", "sample_alert_ids"}
        )
        self.assertEqual(
            context["draft"],
            {key: original[key] for key in ("status", "filter", "sample_alert_ids")},
        )
        self.assertEqual(draft, original)
        self.assertEqual(context["review_method"], "filter_scope_and_other_behaviors")
        self.assertFalse(context["draft_explanation_included"])
        self.assertEqual(
            context["scope_policy_version"], "behavior-specific-exclusion-v1"
        )
        self.assertEqual(request["prompt_version"], "rule-surge-runtime-review-v7")
        encoded_request = json.dumps(request["messages"])
        for key in ("issue", "recommendation", "certainty", "review_notes"):
            self.assertNotIn(original[key], encoded_request)
        self.assertNotIn("EXTRA_ARGUMENT_NOT_EVIDENCE", encoded_request)
        self.assertNotIn("draft_model_extra_fields", context)

    def test_candidate_projection_is_a_copy_not_a_mutable_view(self):
        draft = answer(["current-one"])
        original = copy.deepcopy(draft)
        context = self.request(draft=draft)["context"]
        context["draft"]["filter"]["A"]["value"] = "modified-only-in-request"
        context["draft"]["sample_alert_ids"].append("request-only-id")
        self.assertEqual(draft, original)

    def test_original_logs_and_missing_reference_visibility_are_preserved(self):
        self.current[0]["raw_logs"].append({"separate_activity": "different operation"})
        originals = copy.deepcopy((self.current, self.history))
        request = self.request(
            draft=answer(["current-one", "not-displayed", "history-one"])
        )
        context = request["context"]
        self.assertEqual(context["current_samples"], self.current)
        self.assertEqual(context["history_samples"], self.history)
        self.assertEqual((self.current, self.history), originals)
        visibility = context["draft_reference_visibility"]
        self.assertEqual(
            visibility["displayed_alert_ids"], ["current-one", "history-one"]
        )
        self.assertEqual(visibility["not_displayed_alert_ids"], ["not-displayed"])
        self.assertTrue(visibility["draft_reference_list_unchanged"])
        with self.assertRaises(ai.ReplyValidationError):
            ai.parse_reply(
                json.dumps(answer(["not-displayed"])),
                rule=self.rule,
                visible_samples=self.current + self.history,
                phase="review",
            )

    def test_heterogeneous_logs_in_one_topic_do_not_get_a_fixed_field_template(self):
        context = self.request(independent=True)["context"]
        self.assertEqual(
            set(context["visible_field_names"]), {"vendor_activity", "another_shape"}
        )
        self.assertNotIn(
            "vendor_activity", context["current_samples"][1]["raw_logs"][0]
        )
        for field, identity in (
            ("vendor_activity", "current-one"),
            ("another_shape", "current-two"),
        ):
            parsed = ai.parse_reply(
                json.dumps(answer([identity], field=field)),
                rule=self.rule,
                visible_samples=self.current,
                phase="review",
            )
            self.assertEqual(parsed["filter"]["A"]["name"], field)
            self.assertFalse(parsed["validation_issues"])

    def test_other_topics_and_unseen_field_names_remain_supported(self):
        for topic, field in (
            ("synthetic-http-source", "request_note_v6"),
            ("synthetic-host-source", "opTextCustom"),
        ):
            with self.subTest(topic=topic):
                actual_rule = rule(topic)
                samples = [sample("current", [{field: "inventory-read"}])]
                request = ai.build_request(
                    actual_rule, samples, [], {}, independent=True, limits=self.budget
                )
                self.assertEqual(request["context"]["visible_field_names"], [field])
                parsed = ai.parse_reply(
                    json.dumps(answer(["current"], topic=topic, field=field)),
                    rule=actual_rule,
                    visible_samples=samples,
                )
                self.assertFalse(parsed["validation_issues"])
                self.assertEqual(parsed["filter"]["expression"], "A")
                self.assertTrue(parsed["recommendation_ready"])

    def test_no_new_required_reply_fields_or_claim_of_program_scope_verification(self):
        value = answer(["current-one"])
        self.assertEqual(len(value), 8)
        parsed = ai.parse_reply(
            json.dumps(value),
            rule=self.rule,
            visible_samples=self.current,
            phase="review",
        )
        self.assertTrue(parsed["format_valid"])
        self.assertEqual(parsed["filter"], value["filter"])
        self.assertFalse(parsed["program_filter_evaluation_performed"])
        self.assertEqual(parsed["engine_compatibility"], "not_verified")
        self.assertEqual(
            parsed["validation_scope"],
            "contract_references_expression_visible_fields_and_reviewed_field_roles_only",
        )

    def test_scope_instruction_does_not_invent_a_field_blacklist_in_parser(self):
        # Naming a field 'rule_name' does not prove its meaning. These parser
        # assertions only pin structure handling, not semantic approval.
        for field in ("rule_name", "detector_category", "severity_label"):
            samples = [sample("current", [{field: "inventory-read"}])]
            parsed = ai.parse_reply(
                json.dumps(answer(["current"], field=field)),
                rule=self.rule,
                visible_samples=samples,
                phase="review",
            )
            self.assertEqual(parsed["filter"]["A"]["name"], field)
            self.assertFalse(parsed["validation_issues"])
            self.assertEqual(
                parsed["validation_scope"],
                "contract_references_expression_visible_fields_and_reviewed_field_roles_only",
            )

    def test_requests_fit_existing_budget_without_output_token_parameters(self):
        for draft in (None, answer(["current-one"])):
            request = self.request(draft=draft, independent=draft is None)
            self.assertLessEqual(
                request["estimated_input_tokens"], self.budget.input_token_budget
            )
            self.assertEqual(
                request["input_token_budget"], self.budget.input_token_budget
            )
            self.assertIsNone(request["model_output_token_limit"])
            self.assertFalse({"max_tokens", "max_completion_tokens"} & set(request))
            self.assertEqual(len(request["messages"]), 3)
            self.assertEqual(request["messages"][-1]["content"], ai.OUTPUT_REMINDER)
            self.assertFalse(request["context"]["program_filter_evaluation_performed"])

    def test_validation_findings_are_visible_without_generation_arguments(self):
        draft = answer(["current-one"])
        issue = {
            "code": "field_not_visible",
            "path": ["filter", "A", "name"],
            "field": "missing_vendor_field",
        }
        draft["validation_issues"] = [issue]
        context = self.request(draft=draft)["context"]
        self.assertEqual(context["draft_validation_issues"], [issue])
        self.assertNotIn("recommendation", context["draft"])


if __name__ == "__main__":
    unittest.main()
