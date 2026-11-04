"""Repair-task contracts using synthetic originals, not assertions of AI quality."""

import copy
import hashlib
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_surge_repair_first_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
ai = importlib.import_module(PACKAGE + ".surge_ai_review")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget
POLICY = "event-fields-not-detection-results-v1"


def answer(field="command_text", *, status="proposal"):
    return {
        "status": status,
        "issue": "Synthetic repeated operation",
        "recommendation": "Synthetic candidate; not an approved deployment",
        "certainty": "Actual legitimacy and engine compatibility are unverified",
        "filter": {
            "topic": "security_qthids",
            "A": {"name": field, "operator": "contain", "value": "inventory-read"},
            "expression": "A",
        }
        if status == "proposal"
        else None,
        "sample_alert_ids": ["current-one"] if status == "proposal" else [],
        "review_notes": "Scripted structural control, not model reasoning evidence",
        "sample_request": None,
    }


class RepairFirstReviewTests(unittest.TestCase):
    def setUp(self):
        self.rule = {
            "company_code": "synthetic-company",
            "topic": "security_qthids",
            "rule_code": "synthetic-operation-rule",
            "rule_name": "Synthetic broad detector",
            "rule_config_version": "fixture-v1",
            "configuration": {
                "relation": "allMatch",
                "terms": [
                    {
                        "alias": "A",
                        "config": {
                            "matchType": "and",
                            "conditions": [
                                {
                                    "leftFieldName": "category",
                                    "operator": "Contains",
                                    "rightFieldValue": "service_event",
                                }
                            ],
                        },
                    }
                ],
            },
        }
        self.current = [
            {
                "alert_id": identity,
                "raw_logs": [
                    {
                        "category": "service_event",
                        "detail_hit_rule_name": "Synthetic detector label",
                        "detail_process_name": "collector.exe",
                        "command_text": "collector.exe inventory-read --packages",
                    }
                ],
            }
            for identity in ("current-one", "current-two")
        ]
        self.history = []
        self.budget = Budget()

    def request(self, *, draft=None, independent=True):
        return ai.build_request(
            self.rule,
            self.current,
            self.history,
            {"current_alert_count": 2, "history_selected_unique_alerts": 0},
            phase="review" if draft is not None else "generate",
            draft=draft,
            independent=independent if draft is None else False,
            limits=self.budget,
        )

    def parse(self, value, *, phase="review"):
        return ai.parse_reply(
            json.dumps(value, ensure_ascii=False),
            rule=self.rule,
            visible_samples=self.current,
            phase=phase,
        )

    def test_new_task_versions_keep_the_existing_field_usage_policy(self):
        generation = self.request()
        review = self.request(draft=answer("detail_process_name"))
        self.assertEqual(
            generation["prompt_version"],
            "rule-surge-runtime-independent-generation-v4",
        )
        self.assertEqual(review["prompt_version"], "rule-surge-runtime-review-v5")
        for request in (generation, review):
            self.assertEqual(ai.request_field_usage_version(request), POLICY)
            self.assertEqual(request["context"]["field_usage_policy_version"], POLICY)
            self.assertFalse(
                request["context"]["field_usage"][
                    "candidate_engine_availability_verified"
                ]
            )

    def test_process_attribute_is_not_reclassified_as_detection_metadata(self):
        request = self.request(draft=answer("detail_process_name"))
        self.assertIn(
            "detail_process_name", request["context"]["candidate_field_names"]
        )
        ordinary = self.parse(answer("detail_process_name"))
        metadata = self.parse(answer("detail_hit_rule_name"))
        # Ordinary-field acceptance is a structural check only; the review task
        # must still assess whether a process-only candidate is too broad.
        self.assertEqual(ordinary["validation_issues"], [])
        self.assertEqual(ordinary["engine_compatibility"], "not_verified")
        self.assertFalse(metadata["recommendation_ready"])
        self.assertEqual(
            metadata["validation_issues"][0]["code"],
            "condition_uses_detection_metadata",
        )

    def test_repair_order_is_recorded_in_review_without_claiming_model_success(self):
        generation = self.request()
        review = self.request(draft=answer("detail_process_name"))
        self.assertNotIn("review_decision_order", generation["context"])
        self.assertEqual(
            review["context"]["review_decision_order"],
            "inspect_draft_try_supported_revision_then_decide",
        )
        self.assertFalse(review["context"]["draft_explanation_included"])
        self.assertNotIn("semantic_quality_verified", review["context"])
        self.assertEqual(
            set(review["context"]["draft"]),
            {"status", "filter", "sample_alert_ids"},
        )

    def test_uniform_samples_without_history_do_not_force_parser_withdrawal(self):
        value = answer()
        request = self.request(draft=answer("detail_process_name"))
        self.assertEqual(request["context"]["history_samples"], [])
        self.assertEqual(len(value), 8)
        parsed = self.parse(value)
        self.assertTrue(parsed["format_valid"])
        self.assertEqual(parsed["status"], "proposal")
        self.assertEqual(parsed["filter"], value["filter"])
        self.assertFalse(parsed["program_filter_evaluation_performed"])
        self.assertEqual(parsed["engine_compatibility"], "not_verified")

    def test_review_can_change_the_condition_without_changing_original_draft(self):
        draft = answer("detail_process_name")
        original = copy.deepcopy((draft, self.rule, self.current, self.history))
        request = self.request(draft=draft)
        repaired = self.parse(answer())
        self.assertEqual(request["context"]["draft"]["filter"], draft["filter"])
        self.assertEqual(repaired["filter"]["A"]["name"], "command_text")
        self.assertEqual((draft, self.rule, self.current, self.history), original)
        self.assertEqual(request["context"]["current_samples"], self.current)
        self.assertEqual(
            request["context"]["rule"]["configuration"], self.rule["configuration"]
        )
        self.assertEqual(request["context"]["history_samples"], self.history)
        self.assertNotIn("recommendation", request["context"]["draft"])
        self.assertNotIn("review_notes", request["context"]["draft"])

    def test_withdrawal_remains_valid_without_forcing_a_specific_condition(self):
        for status in ("insufficient_evidence", "no_change"):
            with self.subTest(status=status):
                value = answer(status=status)
                parsed = self.parse(value)
                self.assertEqual(parsed["status"], status)
                self.assertIsNone(parsed["filter"])
                self.assertFalse(parsed["recommendation_produced"])
                self.assertTrue(parsed["format_valid"])

    def test_previous_e_request_versions_remain_valid_under_the_same_role_contract(
        self,
    ):
        # This tests dispatch by the frozen version; full saved-request/report
        # byte preservation is separately covered by the installer upgrade tests.
        for current, previous in (
            (self.request(), "rule-surge-runtime-independent-generation-v3"),
            (self.request(draft=answer()), "rule-surge-runtime-review-v4"),
        ):
            with self.subTest(previous=previous):
                current["prompt_version"] = previous
                self.assertEqual(ai.request_field_usage_version(current), POLICY)

    def test_new_and_previous_e_requests_cannot_drop_or_forge_the_policy(self):
        for version in (
            "rule-surge-runtime-review-v5",
            "rule-surge-runtime-review-v4",
        ):
            with self.subTest(version=version):
                request = self.request(draft=answer())
                request["prompt_version"] = version
                missing = copy.deepcopy(request)
                missing["context"].pop("field_usage_policy_version")
                with self.assertRaises(ValueError):
                    ai.request_field_usage_version(missing)
                forged = copy.deepcopy(request)
                forged["context"]["candidate_field_names"].append(
                    "detail_hit_rule_name"
                )
                with self.assertRaises(ValueError):
                    ai.request_field_usage_version(forged)
        unknown = self.request(draft=answer())
        unknown["prompt_version"] = "unreviewed-request-version"
        with self.assertRaises(ValueError):
            ai.request_field_usage_version(unknown)

    def test_prompts_do_not_inject_this_real_cases_expected_filter(self):
        for request in (self.request(), self.request(draft=answer())):
            system = request["messages"][0]["content"]
            for case_detail in (
                "net1 accounts",
                "net1.exe",
                "RPAADM_000598",
                "Windows使用net查询密码策略",
                "2846821",
            ):
                self.assertNotIn(case_detail, system)
            self.assertNotIn("expected_filter", request["context"])
            self.assertEqual(
                request["prompt_sha256"],
                hashlib.sha256(ai.encoded(request["messages"])).hexdigest(),
            )

    def test_complete_repair_request_keeps_existing_input_and_output_controls(self):
        for request in (self.request(), self.request(draft=answer())):
            self.assertLessEqual(
                request["estimated_input_tokens"], self.budget.input_token_budget
            )
            self.assertEqual(
                request["input_token_budget"], self.budget.input_token_budget
            )
            self.assertIsNone(request["model_output_token_limit"])
            self.assertFalse({"max_tokens", "max_completion_tokens"} & set(request))
            self.assertFalse(request["context"]["program_filter_evaluation_performed"])


if __name__ == "__main__":
    unittest.main()
