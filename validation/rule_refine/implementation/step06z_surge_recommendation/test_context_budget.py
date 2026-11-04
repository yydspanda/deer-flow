"""The copied 6Z tool must use the observed route capacity in both phases."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

import ai_review
import build_command
import sampling

SPEC = importlib.util.spec_from_file_location(
    "observed_route_budget", Path(__file__).with_name("context_budget.py")
)
budget_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = budget_module
SPEC.loader.exec_module(budget_module)


class ContextBudgetTests(unittest.TestCase):
    def test_budget_uses_service_capacity_and_operator_eighty_percent(self):
        budget = budget_module.ContextBudget.for_model("Pub_DeepSeek_V4_Flash")
        self.assertEqual(budget.input_token_budget, 209715)
        audit = budget.audit()
        self.assertEqual(audit["reported_context_tokens"], 262144)
        self.assertEqual(audit["reported_rejected_input_tokens"], 336423)
        self.assertIsNone(audit["model_output_token_limit"])
        self.assertNotIn("assumed_context_tokens", audit)
        self.assertFalse(audit["token_count_exact"])
        self.assertFalse(audit["token_estimate_is_verified_upper_bound"])

    def test_capacity_not_silently_applied_as_verified_for_other_model(self):
        with self.assertRaises(ValueError):
            budget_module.ContextBudget.for_model("different-model")

    def test_packaged_budget_is_new_source_without_changing_old_tool(self):
        here = Path(__file__).parent
        self.assertEqual(
            build_command.contents()["files"]["context_budget"],
            (here / "context_budget.py").read_text(),
        )
        older = (
            here.parent / "step06y_historical_batches/context_budget.py"
        ).read_text()
        self.assertIn("assumed_context_tokens: int = 1_000_000", older)

    def test_generation_and_review_fit_new_budget_and_do_not_trim_logs(self):
        budget = budget_module.ContextBudget()
        case = {
            "rule": {"topic": "demo", "rule_code": "RULE", "configuration": {}},
            "current_samples": [
                {"alert_id": str(i), "raw_logs": [{"event": "synthetic" * 800}]}
                for i in range(160)
            ],
            "history_samples": [],
            "coverage": {},
        }
        first = sampling.select(case, budget)
        draft = {
            "status": "no_change",
            "issue": "人工回复",
            "recommendation": "仅测试预算",
            "filter": None,
            "sample_alert_ids": [],
        }
        second = sampling.select(case, budget, draft=draft, generation=first)
        for chosen in (first, second):
            self.assertLessEqual(chosen["request"]["estimated_input_tokens"], 209715)
            self.assertIsNone(chosen["request"]["model_output_token_limit"])
            self.assertEqual(
                chosen["request"]["context_budget"]["reported_context_tokens"], 262144
            )
            for sample in chosen["current_samples"]:
                self.assertEqual(
                    sample["raw_logs"],
                    case["current_samples"][int(sample["alert_id"])]["raw_logs"],
                )
        self.assertGreater(second["selection"]["new_current_count"], 0)
        self.assertNotIn("1_000_000", json.dumps(first["request"]))

    def test_request_without_explicit_limits_no_longer_defaults_to_eight_hundred_k(
        self,
    ):
        request = ai_review.build_request(
            {"topic": "demo", "rule_code": "R"}, [], [], {}
        )
        self.assertEqual(request["input_token_budget"], 209715)


if __name__ == "__main__":
    unittest.main()
