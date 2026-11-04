"""Missing certainty remains an explicit omission, not invented model confidence."""

import copy
import hashlib
import importlib
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import context_budget
import review

HERE = Path(__file__).resolve().parent
PACKAGE = "_missing_certainty_installed_checks"
package = ModuleType(PACKAGE)
package.__path__ = [
    str(HERE.parent / "step06w_unified_limits/payload/my_workflows/zeus/rule_refine")
]
sys.modules[PACKAGE] = package
analysis = importlib.import_module(PACKAGE + ".analysis")
evaluation = importlib.import_module(PACKAGE + ".filter_evaluation")
old_draft = analysis.AnalysisDraft
budget = context_budget.ContextBudget()


def context():
    return {
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "rule": {"topic": "demo", "rule_code": "DEMO", "rule_name": "人工规则"},
        "samples": [
            {
                "alert_id": "synthetic-1",
                "raw_logs": [{"host": "normal.test", "port": 443}],
            }
        ],
    }


original_request = review.build_request(context(), analysis, limits=budget)
context_budget.install_compatibility(
    SimpleNamespace(analysis=analysis, filter_evaluation=evaluation)
)


def draft(status="proposal"):
    return {
        "status": status,
        "issue": "人工场景待核对",
        "recommendation": "确认业务属性后再排除",
        "sample_alert_ids": ["synthetic-1"],
        "filter": {
            "topic": "demo",
            "expression": "A && B",
            "A": {"name": "host", "operator": "equal", "value": "normal.test"},
            "B": {"name": "port", "operator": "equal", "value": "443"},
        }
        if status == "proposal"
        else None,
    }


class CertaintyTests(unittest.TestCase):
    def check_answer(self, answer, *, material=None, source="saved_model_response"):
        material = context() if material is None else material
        before = copy.deepcopy(answer)
        raw = json.dumps(answer, ensure_ascii=False)
        reader = Mock(return_value=raw)
        saved = {}
        result = review.analyze(
            material,
            reader,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=lambda name, value: saved.__setitem__(name, value),
            limits=budget,
            response_source=source,
        )
        reader.assert_called_once()
        self.assertEqual(answer, before)
        self.assertEqual(saved["assistant-content.txt"], raw.encode("utf-8"))
        self.assertEqual(
            result["response_sha256"], hashlib.sha256(raw.encode()).hexdigest()
        )
        return result

    def test_missing_certainty_retains_nonproposal_status_and_explicit_omission(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            with self.subTest(status=status):
                result = self.check_answer(draft(status))
                self.assertEqual(result["status"], status)
                self.assertTrue(result["format_valid"])
                self.assertEqual(
                    result["certainty"], "未提供（模型未返回判断把握说明）"
                )
                self.assertEqual(result["certainty_source"], "missing")
                self.assertEqual(result["output_warnings"], ["missing_certainty"])
                self.assertFalse(result["automatic_apply"])

    def test_missing_certainty_proposal_still_runs_local_evidence_checks(self):
        result = self.check_answer(draft())
        self.assertEqual(result["status"], "proposal")
        self.assertTrue(result["local_evaluation_completed"])
        self.assertEqual(result["evaluation"]["counts"]["matched"], 1)
        self.assertEqual(result["certainty_source"], "missing")
        self.assertEqual(result["output_warnings"], ["missing_certainty"])

    def test_provided_model_certainty_is_preserved_without_warning(self):
        answer = {**draft(), "certainty": "仅依据提供的人工样本"}
        result = self.check_answer(answer)
        self.assertEqual(result["certainty"], answer["certainty"])
        self.assertEqual(result["certainty_source"], "model")
        self.assertEqual(result["output_warnings"], [])

    def test_null_blank_wrong_type_and_overlong_certainty_stay_invalid(self):
        for value in (None, "", " \n ", 1, False, [], {}, "中" * 4097):
            with self.subTest(value_type=type(value).__name__):
                result = self.check_answer({**draft(), "certainty": value})
                self.assertFalse(result["format_valid"])
                self.assertEqual(result["error_code"], "invalid_output_contract")

    def test_core_required_fields_still_required(self):
        for field in ("status", "issue", "recommendation"):
            answer = draft()
            del answer[field]
            with self.subTest(field=field):
                result = self.check_answer(answer)
                self.assertFalse(result["format_valid"])
                self.assertEqual(result["error_code"], "invalid_output_contract")

    def test_proposal_without_filter_or_references_stays_invalid(self):
        for answer in (
            {**draft(), "filter": None},
            {**draft(), "sample_alert_ids": []},
        ):
            result = self.check_answer(answer)
            self.assertFalse(result["format_valid"])
            self.assertEqual(result["error_code"], "invalid_output_contract")

    def test_wrong_topic_is_rejected_despite_missing_certainty(self):
        answer = draft()
        answer["filter"]["topic"] = "other"
        result = self.check_answer(answer)
        self.assertFalse(result["format_valid"])
        self.assertEqual(result["error_code"], "wrong_topic")

    def test_unprovided_sample_and_unmatched_combination_remain_rejected(self):
        answer = draft()
        answer["sample_alert_ids"] = ["not-provided"]
        self.assertEqual(self.check_answer(answer)["error_code"], "uncited_sample")
        material = context()
        material["samples"][0]["raw_logs"] = [
            {"host": "normal.test", "port": 80},
            {"host": "other.test", "port": 443},
        ]
        result = self.check_answer(draft(), material=material)
        self.assertEqual(result["error_code"], "cited_sample_not_matched")
        self.assertFalse(result["format_valid"])

    def test_offline_replay_reads_response_without_counting_generation(self):
        result = self.check_answer(draft("no_change"))
        self.assertEqual(result["response_source"], "saved_model_response")
        self.assertEqual(result["generation_calls"], 0)
        self.assertEqual(result["saved_response_reads"], 1)
        generated = self.check_answer(draft("no_change"), source="synthetic_reply")
        self.assertEqual(generated["generation_calls"], 1)

    def test_private_default_does_not_change_prompt_or_original_contract(self):
        with self.assertRaises(ValueError):
            old_draft.model_validate(draft())
        self.assertEqual(
            original_request,
            review.build_request(context(), analysis, limits=budget),
        )
        schema = analysis.AnalysisDraft.model_json_schema()
        self.assertNotIn("certainty", schema["required"])
        self.assertEqual(
            schema["properties"]["certainty"]["default"],
            "未提供（模型未返回判断把握说明）",
        )


if __name__ == "__main__":
    unittest.main()
