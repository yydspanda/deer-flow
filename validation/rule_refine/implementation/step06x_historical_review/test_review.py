"""Artificial historical materials and replies; no model or external service calls."""

import copy
import functools
import hashlib
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
PACKAGE = "_historical_unified_review_tests"
package = ModuleType(PACKAGE)
package.__path__ = [
    str(HERE.parent / "step06w_unified_limits/payload/my_workflows/zeus/rule_refine")
]
sys.modules[PACKAGE] = package
analysis = importlib.import_module(PACKAGE + ".analysis")
evaluation = importlib.import_module(PACKAGE + ".filter_evaluation")
adapter = importlib.import_module(PACKAGE + ".model_adapter")
limits_module = importlib.import_module(PACKAGE + ".limits")
Limits = limits_module.Limits
spec = importlib.util.spec_from_file_location(
    "_historical_unified_review", HERE / "review.py"
)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def context():
    return {
        "schema_version": "sec_know.historical_rule_context.v3",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "rule": {"topic": "demo-http", "rule_code": "DEMO", "rule_name": "人工规则"},
        "samples": [
            {
                "alert_id": "original-1",
                "raw_logs": [
                    {"user_agent": "Probe/1.0", "path": "/health", "port": 443}
                ],
            },
            {
                "alert_id": "original-2",
                "raw_logs": [
                    {"user_agent": "Browser/1.0", "path": "/login", "port": 80}
                ],
            },
        ],
        "historical_model_answers_included": False,
    }


def draft():
    return {
        "status": "proposal",
        "issue": "人工示例：健康探测触发规则，业务性质尚待确认。",
        "recommendation": "运营确认后新增该探测场景的排除条件。",
        "certainty": "只依据当前展示的历史样本，不能证明线上降量效果。",
        "sample_alert_ids": ["original-1"],
        "filter": {
            "topic": "demo-http",
            "expression": "A && B",
            "A": {"name": "user_agent", "operator": "contain", "value": "Probe/"},
            "B": {"name": "path", "operator": "equal", "value": "/health"},
        },
    }


class HistoricalReviewTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.limits = Limits()

    def save(self, name, raw):
        self.assertIsInstance(raw, bytes)
        self.assertNotIn(name, self.saved)
        self.saved[name] = raw

    def analyze(self, value=None, material=None, raw=None, **options):
        content = (
            raw
            if raw is not None
            else json.dumps(draft() if value is None else value, ensure_ascii=False)
        )
        generate = Mock(return_value=content)
        result = review.analyze(
            context() if material is None else material,
            generate,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=self.save,
            limits=self.limits,
            response_source="synthetic_reply",
            **options,
        )
        self.generate = generate
        return result

    def test_prompt_preserves_origin_and_exact_materials(self):
        material = context()
        before = copy.deepcopy(material)
        request = review.build_request(material, analysis, limits=self.limits)
        system, user = request["messages"]
        self.assertIn("没有实时突增信号、接收窗口或线上告警速率", system["content"])
        self.assertNotIn(review.SAMPLE_SENTENCE, system["content"])
        self.assertEqual(json.loads(user["content"]), material)
        self.assertEqual(material, before)
        self.assertEqual(
            request["prompt_sha256"],
            hashlib.sha256(review.encoded(request["messages"])).hexdigest(),
        )
        self.assertEqual(
            request["input_bytes"], len(review.encoded(request["messages"]))
        )
        self.assertEqual(request["max_output_bytes"], 65536)
        for fabricated in (
            "window_start",
            "window_end",
            "alert_count",
            "received_unique_alerts",
            "task_id",
        ):
            self.assertNotIn(fabricated, user["content"])

    def test_empty_context_allowed_only_for_budget_preview(self):
        material = {**context(), "samples": []}
        with self.assertRaisesRegex(ValueError, "no_usable_samples"):
            review.build_request(material, analysis, limits=self.limits)
        self.assertGreater(
            review.build_request(
                material, analysis, limits=self.limits, require_samples=False
            )["input_bytes"],
            0,
        )

    def test_invalid_origin_is_not_budget_failure_and_never_generates(self):
        for change in (
            {"material_source": "workflow_task_store"},
            {"observed_surge": True},
            {"rate_window": {}},
            {"trigger": "live"},
        ):
            with self.subTest(change=change):
                generate = Mock()
                with self.assertRaisesRegex(ValueError, "historical_context_required"):
                    review.analyze(
                        {**context(), **change},
                        generate,
                        analysis=analysis,
                        evaluator=evaluation.evaluate_filter,
                        save=self.save,
                        limits=self.limits,
                    )
                generate.assert_not_called()

    def test_full_message_escaping_is_counted_and_budget_is_configurable(self):
        material = context()
        material["samples"][0]["raw_logs"][0]["body"] = '\\"' * 18000
        self.assertLess(len(review.encoded(material)), 128 * 1024)
        with self.assertRaisesRegex(ValueError, "^historical_request_over_budget$"):
            review.build_request(material, analysis, limits=Limits(input_kib=128))
        request = review.build_request(material, analysis, limits=Limits(input_kib=256))
        self.assertLessEqual(request["input_bytes"], 256 * 1024)

    def test_invalid_json_material_is_not_misreported_as_budget(self):
        material = context()
        material["samples"][0]["raw_logs"][0]["broken"] = float("nan")
        with self.assertRaises(ValueError) as caught:
            review.build_request(material, analysis, limits=self.limits)
        self.assertNotEqual(str(caught.exception), "historical_request_over_budget")

    def test_prompt_change_stops_before_generation(self):
        result = self.analyze(expected_prompt_sha256="0" * 64)
        self.generate.assert_not_called()
        self.assertEqual(result["generation_calls"], 0)
        self.assertEqual(result["error_code"], "prompt_changed")

    def test_fenced_json_saves_original_and_evaluates_combination(self):
        raw = "```json\n" + json.dumps(draft(), ensure_ascii=False) + "\n```"
        result = self.analyze(raw=raw)
        self.generate.assert_called_once()
        self.assertEqual(result["status"], "proposal")
        self.assertTrue(result["format_valid"])
        self.assertEqual(
            result["evaluation"]["counts"],
            {"matched": 1, "not_matched": 1, "unknown": 0},
        )
        self.assertEqual(self.saved["assistant-content.txt"], raw.encode("utf-8"))
        self.assertEqual(
            result["response_sha256"], hashlib.sha256(raw.encode("utf-8")).hexdigest()
        )

    def test_long_valid_chinese_reply_above_old_16k_is_accepted(self):
        value = draft()
        for key in ("issue", "recommendation", "certainty"):
            value[key] = "测" * 4096
        raw = json.dumps(value, ensure_ascii=False)
        self.assertGreater(len(raw.encode("utf-8")), 16384)
        self.assertEqual(self.analyze(raw=raw)["status"], "proposal")

    def test_output_budget_failure_is_explicit(self):
        result = self.analyze(raw="测" * 23000)
        self.assertEqual(result["error_code"], "response_over_budget")
        self.assertEqual(result["validation_stage"], "response_budget")
        self.assertFalse(result["assistant_content_saved"])
        self.assertEqual(self.saved, {})

    def test_numeric_equal_uses_installed_comparison(self):
        value = draft()
        value["filter"]["B"] = {"name": "port", "operator": "equal", "value": "443.0"}
        result = self.analyze(value)
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["evaluation"]["counts"]["matched"], 1)

    def test_unused_condition_is_not_evidence_gate_or_executable_filter(self):
        value = draft()
        value["filter"]["expression"] = "A"
        value["filter"]["B"]["name"] = "missing"
        result = self.analyze(value)
        self.assertEqual(result["status"], "proposal")
        self.assertNotIn("B", result["filter"])

    def test_supported_and_cannot_mix_across_logs_or_cited_alerts(self):
        material = context()
        material["samples"][0]["raw_logs"] = [
            {"user_agent": "Probe/1.0", "path": "/other"},
            {"user_agent": "Browser/1.0", "path": "/health"},
        ]
        result = self.analyze(material=material)
        self.assertEqual(result["error_code"], "cited_sample_not_matched")
        self.assertEqual(result["validation_stage"], "combination_evaluation")
        self.saved.clear()
        value = draft()
        value["sample_alert_ids"].append("original-2")
        self.assertEqual(self.analyze(value)["error_code"], "cited_sample_not_matched")

    def test_wrong_reference_field_topic_remain_specific_failures(self):
        for variant, reason in (
            ("reference", "uncited_sample"),
            ("field", "condition_field_missing"),
            ("topic", "wrong_topic"),
        ):
            with self.subTest(variant=variant):
                self.saved.clear()
                value = draft()
                if variant == "reference":
                    value["sample_alert_ids"] = ["invented"]
                elif variant == "field":
                    value["filter"]["A"]["name"] = "absent"
                else:
                    value["filter"]["topic"] = "other"
                result = self.analyze(value)
                self.assertEqual(result["error_code"], reason)
                self.assertTrue(result["assistant_content_saved"])

    def test_unsupported_operator_preserves_manual_advice_without_evaluation(self):
        value = draft()
        value["filter"]["B"] = {
            "name": "port",
            "operator": "greater_than",
            "value": "100",
        }
        with patch.object(
            analysis,
            "RuleRecommendation",
            side_effect=AssertionError("formal forbidden"),
        ):
            result = self.analyze(value)
        self.assertEqual(result["status"], "manual_review")
        self.assertTrue(result["format_valid"])
        self.assertFalse(result["local_evaluation_completed"])
        self.assertNotIn("evaluation", result)
        self.assertEqual(result["filter"]["B"]["operator"], "greater_than")
        self.assertEqual(result["advice"], value["recommendation"])
        self.assertFalse(result["formal_recommendation_submitted"])

    def test_unknown_operator_with_missing_field_is_still_failure(self):
        value = draft()
        value["filter"]["B"] = {"name": "missing", "operator": "regex", "value": ".*"}
        self.assertEqual(self.analyze(value)["error_code"], "condition_field_missing")

    def test_numeric_contains_is_manual_not_string_coercion(self):
        value = draft()
        value["filter"]["B"] = {"name": "port", "operator": "contain", "value": "44"}
        self.assertEqual(self.analyze(value)["status"], "manual_review")

    def test_nonproposal_states_are_valid(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            self.saved.clear()
            result = self.analyze(
                {**draft(), "status": status, "filter": None, "sample_alert_ids": []}
            )
            self.assertEqual(result["status"], status)
            self.assertTrue(result["format_valid"])
            self.assertIsNone(result["filter"])

    def test_invalid_json_diagnostics_do_not_expose_response(self):
        result = self.analyze(raw="SECRET RESPONSE")
        self.assertEqual(result["error_code"], "invalid_json")
        self.assertEqual(result["validation_error"]["line"], 1)
        self.assertEqual(result["validation_error"]["position"], 0)
        self.assertNotIn("SECRET RESPONSE", json.dumps(result))
        self.assertEqual(self.saved["assistant-content.txt"], b"SECRET RESPONSE")

    def test_duplicate_json_and_contract_errors_have_specific_stages(self):
        result = self.analyze(raw='{"status":"proposal","status":"no_change"}')
        self.assertEqual(result["error_code"], "invalid_json")
        self.saved.clear()
        result = self.analyze(value={"secret_key": "private input"})
        self.assertEqual(result["error_code"], "invalid_output_contract")
        self.assertNotIn("private input", json.dumps(result))

    def test_generation_and_persistence_failures_do_not_become_success(self):
        generate = Mock(side_effect=TimeoutError("secret endpoint"))
        result = review.analyze(
            context(),
            generate,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=self.save,
            limits=self.limits,
        )
        generate.assert_called_once()
        self.assertEqual(result["error_code"], "generation_failed")
        self.assertNotIn("secret endpoint", json.dumps(result))
        with patch.object(self, "save", side_effect=OSError("private path")):
            result = self.analyze()
        self.assertEqual(result["error_code"], "response_save_failed")
        self.assertFalse(result["format_valid"])

    def test_manual_report_distinguishes_unverified_local_condition(self):
        value = draft()
        value["filter"]["B"]["operator"] = "regex"
        result = self.analyze(value)
        report = review.report_text(
            context(),
            result,
            {
                "status": "manual_review",
                "analysis_status": result["status"],
                "sdk_request_calls": 1,
            },
        )
        self.assertIn("尚未完成匹配验证", report)
        self.assertIn("不是误报率、线上降量率", report)

    def test_installed_adapter_uses_one_stubbed_sdk_request_without_fake_evidence(self):
        message = NS(
            role="assistant",
            content=json.dumps(draft(), ensure_ascii=False),
            refusal=None,
            tool_calls=None,
            function_call=None,
        )
        response = NS(
            model="default",
            choices=[NS(index=0, finish_reason="stop", message=message)],
            usage=NS(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        create = Mock(return_value=response)
        client = NS(base_url="http://127.0.0.1:4001/v1/", with_options=Mock())
        client.with_options.return_value = NS(chat=NS(completions=NS(create=create)))
        collector = functools.partial(
            review.analyze,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=self.save,
            limits=self.limits,
        )
        with (
            patch.object(adapter, "get_limits", return_value=self.limits),
            patch.object(adapter, "analyze_materials", collector),
            patch.object(
                analysis,
                "RuleRecommendation",
                side_effect=AssertionError("formal forbidden"),
            ),
            patch.object(
                analysis, "Evidence", side_effect=AssertionError("window forbidden")
            ),
        ):
            result = adapter.analyze_with_model(
                context(), client, model="DeepSeek_V32", transport_mode="mock"
            )
        create.assert_called_once()
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 1)
        self.assertEqual(result["model_calls"], 0)
        self.assertFalse(result["formal_recommendation_submitted"])
        self.assertIsNone(result["rate_window"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
