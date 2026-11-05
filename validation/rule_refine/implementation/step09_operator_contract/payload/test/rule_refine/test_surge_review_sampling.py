"""Fresh review evidence under the existing budget, with no provider calls."""

import copy
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_review_sampling_regression"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
sampling = importlib.import_module(PACKAGE + ".surge_sampling")
ai = importlib.import_module(PACKAGE + ".surge_ai_review")
processor = importlib.import_module(PACKAGE + ".surge_processor")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget


def sample(index, size=2000, prefix="current"):
    return {
        "alert_id": f"{prefix}-{index}",
        "occurred_at": "2026-09-17 10:00:00"
        if prefix == "current"
        else "2026-04-01 00:00:00",
        "raw_logs": [
            {
                "arbitrary_vendor_field": "routine" if index % 2 else "different",
                "payload": "x" * size,
            }
        ],
    }


def case(count=40, size=2000, history=2):
    rule = {
        "company_code": "test",
        "topic": "vendor",
        "rule_code": "rule",
        "rule_name": "test rule",
        "rule_config_version": "v1",
        "configuration": {"terms": []},
    }
    return {
        "rule": rule,
        "original_rule": copy.deepcopy(rule),
        "current_samples": [sample(i, size) for i in range(count)],
        "history_samples": [sample(i, 100, "history") for i in range(history)],
        "coverage": {
            "current_alert_count": count,
            "history_selected_unique_alerts": history,
        },
    }


def draft(refs, request=None, status="proposal"):
    return {
        "status": status,
        "issue": "Offline fixture",
        "recommendation": "Operator review required",
        "certainty": "test",
        "filter": {
            "topic": "vendor",
            "A": {
                "name": "arbitrary_vendor_field",
                "operator": "Contains",
                "value": "routine",
            },
            "expression": "A",
        }
        if status == "proposal"
        else None,
        "sample_alert_ids": list(refs),
        "review_notes": "offline",
        "sample_request": request,
    }


class ReviewSamplingTests(unittest.TestCase):
    def setUp(self):
        self.case = case()
        self.budget = Budget(context_tokens=40000, input_fraction=0.8)
        self.generation = sampling.select(
            self.case, self.budget, independent_initial=True
        )
        self.old_ids = {row["alert_id"] for row in self.generation["current_samples"]}

    def review(self, output=None, source=None, generation=None, budget=None):
        return sampling.select(
            source or self.case,
            budget or self.budget,
            draft=output or draft(sorted(self.old_ids)),
            generation=generation or self.generation,
        )

    def test_all_generation_references_no_longer_starve_new_evidence(self):
        output = draft(sorted(self.old_ids))
        unchanged = copy.deepcopy((self.case, self.generation, output))
        review = self.review(output)
        self.assertGreater(review["selection"]["new_current_count"], 0)
        self.assertGreater(review["selection"]["cited_current_reused"], 0)
        self.assertGreater(
            review["selection"]["draft_current_references_not_displayed"], 0
        )
        self.assertEqual(
            review["request"]["context"]["draft"]["sample_alert_ids"],
            output["sample_alert_ids"],
        )
        self.assertEqual((self.case, self.generation, output), unchanged)
        self.assertEqual(review["history_samples"], self.case["history_samples"])
        self.assertLessEqual(
            review["request"]["estimated_input_tokens"], self.budget.input_token_budget
        )
        self.assertIsNone(review["request"]["model_output_token_limit"])

    def test_visibility_accounts_for_history_and_final_refs_still_must_be_visible(self):
        output = draft([*sorted(self.old_ids), "history-0"])
        review = self.review(output)
        visibility = review["request"]["context"]["draft_reference_visibility"]
        visible = {
            row["alert_id"]
            for row in review["current_samples"] + review["history_samples"]
        }
        self.assertEqual(
            set(visibility["displayed_alert_ids"]),
            set(output["sample_alert_ids"]) & visible,
        )
        self.assertEqual(
            set(visibility["not_displayed_alert_ids"]),
            set(output["sample_alert_ids"]) - visible,
        )
        self.assertIn("history-0", visibility["displayed_alert_ids"])
        omitted = visibility["not_displayed_alert_ids"][0]
        with self.assertRaises(ai.ReplyValidationError):
            ai.parse_reply(
                json.dumps(draft([omitted])),
                rule=self.case["rule"],
                visible_samples=review["current_samples"] + review["history_samples"],
                phase="review",
            )

    def test_no_unseen_or_no_change_single_alert_still_reviewed(self):
        source = case(count=1, size=10)
        generation = sampling.select(source, self.budget, independent_initial=True)
        for status in ("proposal", "no_change", "insufficient_evidence"):
            output = draft(["current-0"] if status == "proposal" else [], status=status)
            result = self.review(output, source, generation)
            self.assertEqual(len(result["current_samples"]), 1)
            self.assertEqual(result["selection"]["new_current_count"], 0)
            self.assertEqual(
                result["selection"]["new_current_unavailable_reason"],
                "all_current_already_displayed",
            )

    def test_giant_unseen_then_smaller_unseen_retains_complete_smaller_one(self):
        source = case(count=3, size=100, history=0)
        source["current_samples"][1] = sample(1, 80000)
        generation = {
            "current_samples": [source["current_samples"][0]],
            "history_samples": [],
        }
        result = self.review(draft(["current-0"]), source, generation)
        ids = {row["alert_id"] for row in result["current_samples"]}
        self.assertIn("current-2", ids)
        self.assertNotIn("current-1", ids)
        self.assertEqual(
            next(
                row
                for row in result["current_samples"]
                if row["alert_id"] == "current-2"
            ),
            source["current_samples"][2],
        )

    def test_new_alert_larger_than_half_budget_can_still_be_selected(self):
        source = case(count=2, size=10, history=0)
        source["current_samples"][1] = sample(1, 17000)
        generation = {
            "current_samples": [source["current_samples"][0]],
            "history_samples": [],
        }
        result = self.review(draft(["current-0"]), source, generation)
        self.assertEqual(result["selection"]["new_current_count"], 1)
        self.assertGreater(
            len(ai.encoded(source["current_samples"][1])),
            self.budget.input_token_budget // 2,
        )

    def test_failed_first_new_cannot_let_large_old_reference_starve_later_new(self):
        source = case(count=4, size=100, history=0)
        for index, size in enumerate((20000, 10000, 80000, 10000)):
            source["current_samples"][index] = sample(index, size)
        generation = {
            "current_samples": [source["current_samples"][0]],
            "history_samples": [],
        }
        result = self.review(draft(["current-0"]), source, generation)
        self.assertGreater(result["selection"]["new_current_count"], 0)
        self.assertIsNone(result["selection"]["new_current_unavailable_reason"])
        self.assertTrue(
            {row["alert_id"] for row in result["current_samples"]}
            & {"current-1", "current-3"}
        )

    def test_no_complete_new_alert_fits_is_reported_without_truncation(self):
        source = case(count=2, size=100, history=0)
        source["current_samples"][1] = sample(1, 80000)
        generation = {
            "current_samples": [source["current_samples"][0]],
            "history_samples": [],
        }
        result = self.review(draft(["current-0"]), source, generation)
        self.assertEqual(result["selection"]["new_current_count"], 0)
        self.assertEqual(
            result["selection"]["new_current_unavailable_reason"],
            "no_complete_new_alert_fits_with_draft_and_retained_history",
        )
        self.assertEqual(result["current_samples"], generation["current_samples"])

    def test_ai_requested_examples_take_priority_and_keep_all_mixed_logs(self):
        source = case(count=10, size=2000, history=0)
        source["current_samples"][3]["raw_logs"].append(
            {"arbitrary_vendor_field": "different", "extra": "untouched"}
        )
        generation = {
            "current_samples": [source["current_samples"][0]],
            "history_samples": [],
        }
        output = draft(
            ["current-0"], {"field": "arbitrary_vendor_field", "text": "routine"}
        )
        result = self.review(output, source, generation)
        originals = {row["alert_id"]: row for row in source["current_samples"]}
        for row in result["current_samples"]:
            self.assertEqual(row, originals[row["alert_id"]])
        new = [
            row for row in result["current_samples"] if row["alert_id"] != "current-0"
        ]
        self.assertEqual(new[0]["alert_id"], "current-1")
        self.assertTrue(result["selection"]["ai_sample_request_used"])

    def test_no_fixed_reference_count_cap_when_all_evidence_fits(self):
        source = case(count=20, size=20)
        generation = sampling.select(source, self.budget, independent_initial=True)
        refs = [row["alert_id"] for row in generation["current_samples"]]
        result = self.review(draft(refs), source, generation)
        self.assertEqual(result["selection"]["cited_current_reused"], len(refs))
        self.assertEqual(len(result["current_samples"]), 20)

    def test_prompt_versions_identify_current_generation_and_review(self):
        self.assertEqual(
            self.generation["request"]["prompt_version"],
            "rule-surge-runtime-independent-generation-v5",
        )
        result = self.review()
        self.assertEqual(
            result["request"]["prompt_version"], "rule-surge-runtime-review-v6"
        )
        self.assertIn("未展示", result["request"]["messages"][0]["content"])

    def test_processor_two_calls_reports_fresh_counts_and_reuses_saved_result(self):
        source = copy.deepcopy(self.case)
        signal = {
            "schema_version": "sec_know.rule_burst_signal.v1",
            "mode": "demo",
            "namespace": "test",
            "source_app": "test",
            "company_code": "test",
            "topic": "vendor",
            "rule_code": "rule",
            "signal_id": "one",
        }
        calls = []

        def generate(request):
            calls.append(request)
            refs = [row["alert_id"] for row in request["context"]["current_samples"]]
            return {
                "content": json.dumps(draft(refs)),
                "transport": {
                    "error_code": None,
                    "finish_reason": "stop",
                    "sdk_request_calls": 1,
                },
            }

        with tempfile.TemporaryDirectory() as temp:
            service = processor.SurgeProcessor(
                temp,
                load_case=lambda _: source,
                load_rules=lambda: types.SimpleNamespace(
                    get_rule=lambda *a, **kw: source["original_rule"]
                ),
                generate=generate,
                model="mock",
                budget=self.budget,
            )
            result = service.process(signal)
            self.assertEqual(result["status"], "received", result)
            folder = Path(result["output_dir"])
            selection = json.loads(
                (folder / "review/selection.json").read_text(encoding="utf-8")
            )["selection"]
            self.assertGreater(selection["new_current_count"], 0)
            self.assertIn(
                "新增原文", (folder / "REPORT.md").read_text(encoding="utf-8")
            )
            repeated = service.process(signal)
            self.assertEqual(repeated["model_calls_this_attempt"], 0)
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
