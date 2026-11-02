"""Offline historical review checks using the staged parser and transport."""

import copy
import functools
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


HERE = Path(__file__).resolve().parent
IMPLEMENTATION = HERE.parent
PACKAGE = "_historical_review_test_staged"
package = ModuleType(PACKAGE)
package.__path__ = []
sys.modules[PACKAGE] = package


def load(name, path):
    qualified = PACKAGE + "." + name
    spec = importlib.util.spec_from_file_location(qualified, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


def staged(name, step):
    return load(
        name,
        IMPLEMENTATION
        / step
        / "payload/my_workflows/zeus/rule_refine"
        / (name + ".py"),
    )


contracts = staged("contracts", "step01_contracts")
staged("rule_config_source", "step03d_preparation")
staged("materials", "step06c_task_source")
analysis = staged("analysis", "step06l_fenced_json")
evaluation = staged("filter_evaluation", "step05a_filter_evaluation")
adapter = staged("model_adapter", "step04c_model_adapter")
review = load("historical_review", HERE / "review.py")


def context():
    return {
        "schema_version": "sec_know.historical_rule_context.v1",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "rule": {"topic": "demo-http", "rule_code": "DEMO", "rule_name": "人工规则"},
        "samples": [
            {
                "alert_id": "original-1",
                "raw_logs": [{"user_agent": "Probe/1.0", "path": "/health"}],
            },
            {
                "alert_id": "original-2",
                "raw_logs": [{"user_agent": "Browser/1.0", "path": "/login"}],
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

    def save(self, name, raw):
        self.assertIsInstance(raw, bytes)
        self.assertNotIn(name, self.saved)
        self.saved[name] = raw

    def analyze(self, value=None, material=None, raw=None):
        content = (
            raw if raw is not None else json.dumps(value or draft(), ensure_ascii=False)
        )
        return review.analyze(
            material or context(),
            lambda messages: content,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=self.save,
        )

    def test_prompt_preserves_historical_basis_without_fabricated_evidence(self):
        material = context()
        before = copy.deepcopy(material)
        request = review.build_request(material, analysis)
        system, user = request["messages"]
        self.assertIn("没有实时突增信号、接收窗口或线上告警速率", system["content"])
        self.assertNotIn(review.SAMPLE_SENTENCE, system["content"])
        self.assertEqual(json.loads(user["content"]), material)
        for fabricated in (
            "window_start",
            "window_end",
            "alert_count",
            "received_unique_alerts",
            "task_id",
        ):
            self.assertNotIn(fabricated, user["content"])
        self.assertEqual(material, before)
        self.assertEqual(len(request["prompt_sha256"]), 64)

    def test_prompt_rejects_live_or_empty_material_before_generation(self):
        for change in (
            {"material_source": "workflow_task_store"},
            {"observed_surge": True},
            {"rate_window": {"seconds": 3600}},
            {"samples": []},
        ):
            with self.subTest(change=change):
                material = {**context(), **change}
                generate = Mock()
                with self.assertRaises(ValueError):
                    review.analyze(
                        material,
                        generate,
                        analysis=analysis,
                        evaluator=evaluation.evaluate_filter,
                        save=self.save,
                    )
                generate.assert_not_called()

    def test_fenced_json_proposal_saves_exact_content_and_matches_combination(self):
        raw = "```json\n" + json.dumps(draft(), ensure_ascii=False) + "\n```"
        result = self.analyze(raw=raw)
        self.assertEqual(result["status"], "proposal")
        self.assertTrue(result["format_valid"])
        self.assertEqual(
            result["evaluation"]["counts"],
            {"matched": 1, "not_matched": 1, "unknown": 0},
        )
        self.assertEqual(result["sample_alert_ids"], ["original-1"])
        self.assertEqual(self.saved, {"assistant-content.txt": raw.encode("utf-8")})
        self.assertEqual(
            result["response_sha256"], hashlib.sha256(raw.encode("utf-8")).hexdigest()
        )

    def test_conditions_cannot_combine_across_distinct_logs(self):
        material = context()
        material["samples"][0]["raw_logs"] = [
            {"user_agent": "Probe/1.0", "path": "/other"},
            {"user_agent": "Browser/1.0", "path": "/health"},
        ]
        self.assertEqual(
            self.analyze(material=material)["error_code"], "invalid_model_output"
        )

    def test_each_cited_alert_must_match_complete_filter(self):
        value = draft()
        value["sample_alert_ids"].append("original-2")
        result = self.analyze(value)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertFalse(result["format_valid"])
        self.assertNotIn("evaluation", result)

    def test_unknown_sample_and_unsupported_field_or_topic_are_rejected(self):
        for variant in ("unknown_id", "missing_field", "wrong_topic"):
            with self.subTest(variant=variant):
                self.saved.clear()
                value = draft()
                if variant == "unknown_id":
                    value["sample_alert_ids"] = ["invented-id"]
                elif variant == "missing_field":
                    value["filter"]["A"] = {
                        "name": "missing",
                        "operator": "notcontain",
                        "value": "attack",
                    }
                else:
                    value["filter"]["topic"] = "other-topic"
                self.assertEqual(
                    self.analyze(value)["error_code"], "invalid_model_output"
                )

    def test_nonproposal_states_are_valid_without_filter_or_evidence(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            with self.subTest(status=status):
                self.saved.clear()
                value = {
                    **draft(),
                    "status": status,
                    "sample_alert_ids": [],
                    "filter": None,
                }
                result = self.analyze(value)
                self.assertEqual(result["status"], status)
                self.assertTrue(result["format_valid"])
                self.assertIsNone(result["filter"])
                self.assertNotIn("evaluation", result)

    def test_malformed_or_ambiguous_json_is_preserved_but_rejected(self):
        for raw in (
            '{"status":"proposal","status":"no_change"}',
            "说明\n```json\n{}\n```",
            "not json",
        ):
            with self.subTest(raw=raw):
                self.saved.clear()
                result = self.analyze(raw=raw)
                self.assertEqual(result["error_code"], "invalid_model_output")
                self.assertEqual(
                    self.saved["assistant-content.txt"], raw.encode("utf-8")
                )

    def test_generation_failure_is_not_a_successful_review(self):
        generate = Mock(side_effect=RuntimeError("offline synthetic failure"))
        result = review.analyze(
            context(),
            generate,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=self.save,
        )
        generate.assert_called_once()
        self.assertEqual(result["error_code"], "generation_failed")
        self.assertEqual(self.saved, {})

    def test_existing_transport_uses_one_mocked_sdk_request_without_live_evidence(self):
        raw = "```json\n" + json.dumps(draft(), ensure_ascii=False) + "\n```"
        message = SimpleNamespace(
            role="assistant",
            content=raw,
            refusal=None,
            tool_calls=None,
            function_call=None,
        )
        response = SimpleNamespace(
            model="default",
            choices=[SimpleNamespace(index=0, finish_reason="stop", message=message)],
            usage=SimpleNamespace(
                prompt_tokens=100, completion_tokens=50, total_tokens=150
            ),
        )
        create = Mock(return_value=response)
        client = SimpleNamespace(
            base_url="http://127.0.0.1:4001/v1/", with_options=Mock()
        )
        client.with_options.return_value = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        collector = functools.partial(
            review.analyze,
            analysis=analysis,
            evaluator=evaluation.evaluate_filter,
            save=self.save,
        )
        with (
            patch.object(adapter, "analyze_materials", collector),
            patch.object(
                analysis,
                "Evidence",
                side_effect=AssertionError("unexpected live evidence"),
            ),
            patch.object(
                analysis,
                "RuleRecommendation",
                side_effect=AssertionError("unexpected formal recommendation"),
            ),
        ):
            result = adapter.analyze_with_model(
                context(), client, model="DeepSeek_V32", transport_mode="mock"
            )
        create.assert_called_once()
        client.with_options.assert_called_once_with(max_retries=0, timeout=240)
        self.assertFalse(create.call_args.kwargs["stream"])
        self.assertEqual(result["schema_version"], "sec_know.historical_rule_review.v1")
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 1)
        self.assertEqual(result["model_calls"], 0)
        self.assertFalse(result["formal_recommendation_submitted"])
        self.assertFalse(result["automatic_apply"])
        self.assertFalse(result["observed_surge"])
        self.assertIsNone(result["rate_window"])
        for forbidden in (
            "recommendation_id",
            "evidence",
            "window_start",
            "received_unique_alerts",
        ):
            self.assertNotIn(forbidden, review.encoded(result).decode("utf-8"))
        self.assertEqual(self.saved["assistant-content.txt"], raw.encode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
