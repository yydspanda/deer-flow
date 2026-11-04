"""Context-budget and expanded historical checks; artificial data, no services."""

import copy
import hashlib
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import context_budget

HERE = Path(__file__).resolve().parent
PACKAGE = "_context_budget_installed_checks"
package = ModuleType(PACKAGE)
package.__path__ = [
    str(HERE.parent / "step06w_unified_limits/payload/my_workflows/zeus/rule_refine")
]
sys.modules[PACKAGE] = package
analysis = importlib.import_module(PACKAGE + ".analysis")
evaluation = importlib.import_module(PACKAGE + ".filter_evaluation")
old_limits = importlib.import_module(PACKAGE + ".limits")
original_draft = analysis.AnalysisDraft
original_evaluator = evaluation.evaluate_filter
loaded = SimpleNamespace(analysis=analysis, filter_evaluation=evaluation)
context_budget.install_compatibility(loaded)
spec = importlib.util.spec_from_file_location(
    "_context_budget_review", HERE / "review.py"
)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def context(count=1):
    return {
        "schema_version": "sec_know.historical_rule_context.v4",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "rule": {"topic": "demo", "rule_code": "DEMO", "rule_name": "人工规则"},
        "samples": [
            {
                "alert_id": f"original-{i}",
                "raw_logs": [{"host": "normal.test", "port": 443}],
            }
            for i in range(count)
        ],
        "historical_model_answers_included": False,
    }


def proposal(count=1):
    return {
        "status": "proposal",
        "issue": "人工场景待核对",
        "recommendation": "确认业务属性后再排除",
        "certainty": "只依据提供的日志",
        "sample_alert_ids": [f"original-{i}" for i in range(count)],
        "filter": {
            "topic": "demo",
            "expression": "A && B",
            "A": {"name": "host", "operator": "equal", "value": "normal.test"},
            "B": {"name": "port", "operator": "equal", "value": "443"},
        },
    }


class BudgetTests(unittest.TestCase):
    def test_operator_budget_and_current_non_input_settings(self):
        budget = context_budget.from_limits(
            old_limits.Limits(input_kib=128, output_kib=128)
        )
        self.assertEqual(budget.input_token_budget, 800000)
        self.assertFalse(hasattr(budget, "max_output_tokens"))
        self.assertIsNone(budget.audit()["model_output_token_limit"])
        self.assertEqual(budget.timeout_seconds, 600)
        self.assertFalse(hasattr(budget, "output_kib"))
        self.assertEqual(budget.output_bytes, 40 * 1024 * 1024)
        self.assertEqual(budget.input_bytes, 32 * 1024 * 1024)
        self.assertFalse(budget.audit()["context_capacity_verified"])
        self.assertFalse(budget.audit()["token_count_exact"])
        self.assertFalse(budget.audit()["token_estimate_is_verified_upper_bound"])
        self.assertEqual(
            budget.audit()["context_capacity_basis"], "operator_assumption"
        )

    def test_estimate_counts_exact_content_bytes_but_never_claims_exact_tokens(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": '中文\\"\n😀'},
        ]
        self.assertEqual(
            context_budget.estimate_prompt_tokens(messages),
            sum(len(item["content"].encode("utf-8")) for item in messages) + 128 + 256,
        )
        for value in (
            [],
            {},
            [{"role": "user", "content": []}],
            [{"role": "user", "content": "x", "name": "x"}],
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                context_budget.estimate_prompt_tokens(value)

    def test_exact_estimation_boundary_and_one_over(self):
        budget = context_budget.ContextBudget()
        material = context()
        material["padding"] = ""
        baseline = review.build_request(material, analysis, limits=budget)
        material["padding"] = "x" * (
            budget.input_token_budget - baseline["estimated_input_tokens"]
        )
        full = review.build_request(material, analysis, limits=budget)
        self.assertEqual(full["estimated_input_tokens"], 800000)
        material["padding"] += "x"
        with self.assertRaisesRegex(ValueError, "historical_request_over_token_budget"):
            review.build_request(material, analysis, limits=budget)
        measured = review.build_request(
            material, analysis, limits=budget, enforce_budget=False
        )
        self.assertEqual(measured["estimated_input_tokens"], 800001)

    def test_full_messages_material_and_lineage_are_preserved(self):
        material = context(51)
        original = copy.deepcopy(material)
        request = review.build_request(
            material, analysis, limits=context_budget.ContextBudget()
        )
        self.assertEqual(json.loads(request["messages"][1]["content"]), material)
        self.assertEqual(material, original)
        self.assertEqual(
            request["prompt_sha256"],
            hashlib.sha256(review.encoded(request["messages"])).hexdigest(),
        )
        self.assertEqual(
            request["input_bytes"], len(review.encoded(request["messages"]))
        )
        self.assertEqual(request["input_token_budget"], 800000)
        self.assertNotIn("128KiB", request["messages"][0]["content"])
        self.assertNotIn("max_input_kib", request)
        self.assertNotIn("max_output_tokens", request)
        self.assertNotIn("max_output_kib", request)
        self.assertIsNone(request["model_output_token_limit"])
        self.assertNotIn("回答最多", request["messages"][0]["content"])
        self.assertFalse(request["token_count_exact"])

    def test_empty_materials_only_permitted_for_measurement(self):
        with self.assertRaisesRegex(ValueError, "no_usable_samples"):
            review.build_request(
                context(0), analysis, limits=context_budget.ContextBudget()
            )
        request = review.build_request(
            context(0),
            analysis,
            limits=context_budget.ContextBudget(),
            require_samples=False,
        )
        self.assertGreater(request["estimated_input_tokens"], 0)

    def test_invalid_capacity_allocation_rejected(self):
        for options in (
            {"assumed_context_tokens": True},
            {"input_fraction": 1},
            {"input_fraction": float("nan")},
            {"timeout_seconds": 601},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                context_budget.ContextBudget(**options)


class CompatibilityTests(unittest.TestCase):
    def run_analysis(self, material, draft):
        generate = Mock(return_value=json.dumps(draft, ensure_ascii=False))
        saved = {}
        result = review.analyze(
            material,
            generate,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=lambda name, raw: saved.__setitem__(name, raw),
            limits=context_budget.ContextBudget(),
            response_source="synthetic_reply",
        )
        self.assertEqual(generate.call_count, 1)
        return result

    def test_more_than_twenty_references_and_samples_are_valid(self):
        with self.assertRaises(ValueError):
            original_draft.model_validate(proposal(51))
        result = self.run_analysis(context(51), proposal(51))
        self.assertTrue(result["format_valid"])
        self.assertEqual(result["evaluation"]["input_alert_count"], 51)
        self.assertEqual(result["evaluation"]["counts"]["matched"], 51)
        self.assertEqual(result["cited_sample_count"], 51)
        self.assertFalse(result["token_count_exact"])

    def test_valid_answer_larger_than_old_64kib_receiver_is_accepted(self):
        material = context()
        draft = proposal()
        for key in ("issue", "recommendation", "certainty"):
            draft[key] = "中" * 3500
        draft["filter"] = {"topic": "demo", "expression": "A && B && C"}
        log = {}
        for alias in ("A", "B", "C"):
            value = "文" * 4000
            log[alias] = value
            draft["filter"][alias] = {
                "name": alias,
                "operator": "equal",
                "value": value,
            }
        material["samples"][0]["raw_logs"] = [log]
        self.assertGreater(
            len(json.dumps(draft, ensure_ascii=False).encode("utf-8")), 64 * 1024
        )
        result = self.run_analysis(material, draft)
        self.assertTrue(result["format_valid"], result)
        self.assertEqual(result["evaluation"]["counts"]["matched"], 1)
        self.assertIsNone(result["model_output_token_limit"])

    def test_original_shape_duplicate_and_reference_checks_still_apply(self):
        duplicate = proposal(2)
        duplicate["sample_alert_ids"] *= 2
        no_filter = {**proposal(), "filter": None}
        illegal_filter = {**proposal(), "status": "no_change"}
        for draft in (duplicate, no_filter, illegal_filter):
            result = self.run_analysis(context(2), draft)
            self.assertFalse(result["format_valid"])
            self.assertEqual(result["validation_stage"], "output_contract")
        absent = proposal()
        absent["sample_alert_ids"] = ["not-provided"]
        self.assertEqual(
            self.run_analysis(context(), absent)["error_code"], "uncited_sample"
        )

    def test_many_logs_keep_global_indexes_counts_and_full_hash(self):
        material = context(2)
        material["samples"][0]["raw_logs"] *= 205
        before = copy.deepcopy(material)
        definition = proposal()["filter"]
        evaluated = evaluation.evaluate_filter(definition, material["samples"])
        self.assertEqual(evaluated["input_log_count"], 206)
        self.assertEqual(
            evaluated["counts"], {"matched": 206, "not_matched": 0, "unknown": 0}
        )
        self.assertEqual(
            [row["log_index"] for row in evaluated["results"][:205]], list(range(205))
        )
        raw = evaluation._encode(
            {
                "filter": evaluation.FilterDefinition.model_validate(
                    definition
                ).model_dump(mode="json"),
                "samples": material["samples"],
            },
            evaluation.RECORD_BYTES,
        )
        self.assertEqual(evaluated["input_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(material, before)
        result = self.run_analysis(material, proposal(2))
        self.assertTrue(result["format_valid"])
        self.assertEqual(result["evaluation"]["counts"]["matched"], 206)

    def test_local_chunks_never_join_conditions_across_logs(self):
        material = context()
        material["samples"][0]["raw_logs"] = [
            {"host": "other.test", "port": 80}
        ] * 99 + [
            {"host": "normal.test", "port": 80},
            {"host": "other.test", "port": 443},
        ]
        result = self.run_analysis(material, proposal())
        self.assertFalse(result["format_valid"])
        self.assertEqual(result["error_code"], "cited_sample_not_matched")

    def test_global_duplicate_ids_are_rejected_across_evaluation_chunks(self):
        samples = context(51)["samples"]
        samples[-1]["alert_id"] = samples[0]["alert_id"]
        with self.assertRaisesRegex(ValueError, "告警 ID 不能重复"):
            evaluation.evaluate_filter(proposal()["filter"], samples)

    def test_empty_samples_and_null_fields_preserve_original_semantics(self):
        samples = [
            {"alert_id": "empty", "raw_logs": []},
            {"alert_id": "unknown", "raw_logs": [{"host": None, "port": 443}]},
        ]
        actual = evaluation.evaluate_filter(proposal()["filter"], samples)
        expected = original_evaluator(proposal()["filter"], samples)
        self.assertEqual(actual, expected)

    def test_compatibility_is_idempotent_and_original_model_remains_bounded(self):
        current = analysis.AnalysisDraft
        evaluator = evaluation.evaluate_filter
        context_budget.install_compatibility(loaded)
        self.assertIs(analysis.AnalysisDraft, current)
        self.assertIs(evaluation.evaluate_filter, evaluator)
        self.assertEqual(
            original_draft.model_fields["sample_alert_ids"].metadata[0].max_length, 20
        )


if __name__ == "__main__":
    unittest.main()
