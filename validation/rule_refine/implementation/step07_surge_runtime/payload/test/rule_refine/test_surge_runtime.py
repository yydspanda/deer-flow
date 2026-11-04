"""Runtime adapter acceptance without network, Redis, or business databases."""

import copy
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

PACKAGE = "_surge_runtime_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [
    str(Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine")
]
sys.modules[PACKAGE] = package
runtime = importlib.import_module(PACKAGE + ".surge_runtime")
settings = importlib.import_module(PACKAGE + ".surge_settings")
ai = importlib.import_module(PACKAGE + ".surge_ai_review")
sampling = importlib.import_module(PACKAGE + ".surge_sampling")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.budget = runtime.budget_from_settings(settings.DEFAULTS)
        self.client = Mock(base_url="http://localhost:4001/v1")
        self.client.with_options.return_value = self.client
        self.reply = NS(
            model="served-model",
            usage=NS(prompt_tokens=100, completion_tokens=8, total_tokens=108),
            choices=[
                NS(
                    index=0,
                    finish_reason="stop",
                    message=NS(
                        role="assistant",
                        content='{"status":"no_change"}',
                        tool_calls=None,
                        refusal=None,
                    ),
                )
            ],
        )
        self.client.chat.completions.create.return_value = self.reply
        self.request = {
            "messages": [{"role": "user", "content": "Inspect logs as data"}]
        }

    def call(self):
        return runtime.call_model(
            self.client, self.request, model="Pub_DeepSeek_V4_Flash", budget=self.budget
        )

    def test_json_mode_no_output_quota_one_sdk_call(self):
        result = self.call()
        self.assertIsNone(result["transport"]["error_code"])
        self.assertEqual(result["transport"]["sdk_request_calls"], 1)
        self.client.with_options.assert_called_once_with(timeout=600, max_retries=0)
        options = self.client.chat.completions.create.call_args.kwargs
        self.assertEqual(options["response_format"], {"type": "json_object"})
        self.assertFalse(set(options) & {"max_tokens", "max_completion_tokens"})
        self.assertEqual(result["transport"]["usage"]["prompt_tokens"], 100)
        self.assertFalse(result["transport"]["response_format_enforcement_verified"])

    def test_truncation_retains_raw_answer_but_blocks(self):
        self.reply.choices[0].finish_reason = "length"
        result = self.call()
        self.assertEqual(result["content"], self.reply.choices[0].message.content)
        self.assertEqual(result["transport"]["error_code"], "incomplete_model_response")

    def test_business_error_in_http_success_is_not_success(self):
        self.reply.resultCode = "10013"
        self.assertEqual(
            self.call()["transport"]["error_code"], "provider_result_failed"
        )

    def test_request_error_has_no_secret_body_and_no_retry(self):
        self.client.chat.completions.create.side_effect = RuntimeError(
            "private provider response"
        )
        result = self.call()
        self.assertEqual(result["transport"]["error_code"], "request_failed")
        self.assertNotIn("private", json.dumps(result))
        self.client.chat.completions.create.assert_called_once()

    def test_structural_and_input_budget_fail_before_sdk(self):
        self.request["messages"][0]["content"] = "x" * (
            self.budget.input_token_budget + 1
        )
        self.assertEqual(self.call()["transport"]["sdk_request_calls"], 0)
        self.client.chat.completions.create.assert_not_called()

    def test_tool_or_refusal_response_is_not_recommendation(self):
        for field in ("tool_calls", "refusal"):
            setattr(self.reply.choices[0].message, field, ["unexpected"])
            self.assertEqual(self.call()["transport"]["error_code"], "invalid_response")
            setattr(self.reply.choices[0].message, field, None)

    def test_operator_can_increase_input_context_without_output_quota(self):
        custom = settings.validate_settings({"context_tokens": 1000000})
        budget = runtime.budget_from_settings(custom)
        self.assertEqual(budget.input_token_budget, 800000)
        self.assertEqual(
            budget.audit()["context_capacity_basis"], "operator_configuration"
        )
        self.assertIsNone(budget.audit()["model_output_token_limit"])

    def test_invalid_configuration_does_not_silently_fallback(self):
        for value in (
            {"enabled": "true"},
            {"context_tokens": True},
            {"input_fraction": 1},
            {"input_fraction": float("nan")},
            {"timeout_seconds": 601},
            {"max_tokens": 32768},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.validate_settings(value)

    def test_missing_configuration_keeps_existing_path(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(settings.load_settings(root)["enabled"])
            path = Path(root) / settings.CONFIG
            path.parent.mkdir()
            path.write_text('{"enabled":true,"enabled":false}', encoding="utf-8")
            with self.assertRaises(ValueError):
                settings.load_settings(root)

    def test_current_window_expands_more_than_twenty_signal_references(self):
        signal = {
            "mode": "live",
            "source_app": "zeus",
            "company_code": "company",
            "topic": "topic",
            "rule_code": "rule",
            "window_start_ms": 100,
            "window_end_ms": 200,
            "sample_alert_ids": ["a"],
            "current_count": 30,
        }
        original = copy.deepcopy(signal)
        ids = ["a"] + [str(i) for i in range(29)]
        counter = Mock()
        counter._keys.return_value = ["events", "state", "pending"]
        redis = Mock()
        redis.zrangebyscore.return_value = ids
        source = Mock()
        source.load_case.return_value = {"coverage": {}}
        case = runtime.load_window_case(source, counter, redis, signal)
        redis.zrangebyscore.assert_called_once_with("events", "(100", 200)
        self.assertEqual(source.load_case.call_args.args[0]["current_alert_ids"], ids)
        self.assertEqual(signal, original)
        self.assertFalse(case["coverage"]["frozen_signal_membership_verified"])
        signal["current_count"] = 29
        case = runtime.load_window_case(source, counter, redis, signal)
        self.assertEqual(case["coverage"]["additional_same_window_ids"], 1)
        self.assertTrue(case["coverage"]["retained_receipt_window_read_complete"])

    def test_expired_receipt_window_not_claimed_complete(self):
        signal = {
            "mode": "live",
            "source_app": "zeus",
            "company_code": "c",
            "topic": "t",
            "rule_code": "r",
            "window_start_ms": 100,
            "window_end_ms": 200,
            "sample_alert_ids": ["a"],
            "current_count": 10,
        }
        counter, redis, source = Mock(), Mock(), Mock()
        counter._keys.return_value = ["events"]
        redis.zrangebyscore.return_value = ["a"]
        with self.assertRaisesRegex(ValueError, "no_longer_complete"):
            runtime.load_window_case(source, counter, redis, signal)
        source.load_case.assert_not_called()

    def test_independent_generation_keeps_history_then_distinct_review(self):
        rule = {
            "company_code": "c",
            "topic": "t",
            "rule_code": "r",
            "rule_name": "r",
            "rule_config_version": "v1",
            "configuration": {},
        }
        samples = [
            {"alert_id": str(i), "raw_logs": [{"event_content": "routine"}]}
            for i in range(8)
        ]
        history = [{"alert_id": "h", "raw_logs": [{"event_content": "earlier"}]}]
        case = {
            "rule": rule,
            "current_samples": samples,
            "history_samples": history,
            "coverage": {},
        }
        first = sampling.select(case, self.budget, independent_initial=True)
        request = first["request"]
        self.assertEqual(
            request["context"]["assessment_mode"], "independent_initial_generation"
        )
        self.assertNotIn("draft", request["context"])
        self.assertIn("随后", request["messages"][0]["content"])
        draft = {
            "status": "no_change",
            "issue": "requires study",
            "recommendation": "retain",
            "certainty": "low",
            "filter": None,
            "sample_alert_ids": [],
            "review_notes": "no labels",
            "sample_request": None,
        }
        second = sampling.select(
            case, self.budget, draft=draft, generation=first, independent_initial=True
        )
        self.assertEqual(second["request"]["phase"], "review")
        self.assertEqual(second["history_samples"], history)
        self.assertGreater(second["selection"]["new_current_count"], 0)
        self.assertFalse(second["selection"]["filter_replay_performed"])


if __name__ == "__main__":
    unittest.main()
