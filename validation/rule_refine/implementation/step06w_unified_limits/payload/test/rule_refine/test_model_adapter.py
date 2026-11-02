"""模型适配离线验证：只使用注入的 Mock，不导入应用或 OpenAI SDK。"""

import copy
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_rule_refine_adapter_fixtures", Path(__file__).with_name("test_analysis.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
adapter = importlib.import_module(fixtures.fixtures.package.__name__ + ".model_adapter")


def response():
    return NS(
        model="demo-model-returned",
        choices=[
            NS(
                index=0,
                finish_reason="stop",
                message=NS(
                    role="assistant",
                    content=json.dumps(fixtures.draft(), ensure_ascii=False),
                    refusal=None,
                    tool_calls=[],
                    function_call=None,
                ),
            )
        ],
        usage=NS(prompt_tokens=100, completion_tokens=200, total_tokens=300),
    )


def client_for(reply=None):
    client = Mock(base_url="http://127.0.0.1:4001/v1")
    client.with_options.return_value.chat.completions.create.return_value = (
        response() if reply is None else reply
    )
    return client


class ModelAdapterTests(unittest.TestCase):
    def call(self, reply=None, data=None, client=None, **options):
        client = client_for(reply) if client is None else client
        options = {"model": "demo-model", "transport_mode": "mock", **options}
        result = adapter.analyze_with_model(
            fixtures.materials() if data is None else data, client, **options
        )
        return result, client

    def assert_failure(self, reply, code):
        result, client = self.call(reply)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertIsNone(result["recommendation"])
        self.assertEqual(result["model_transport"]["error_code"], code)
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 1)
        self.assertEqual(result["model_calls"], 0)
        client.with_options.return_value.chat.completions.create.assert_called_once()

    def test_normal_request_is_bounded_projected_once_and_audited(self):
        with patch.object(
            fixtures.analysis,
            "build_analysis_request",
            wraps=fixtures.analysis.build_analysis_request,
        ) as p:
            result, client = self.call()
            p.assert_called_once()
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(
            result["model_call_basis"], "sdk_create_invocations_not_upstream_attempts"
        )
        client.with_options.assert_called_once_with(max_retries=0, timeout=240)
        client.with_options.return_value.chat.completions.create.assert_called_once_with(
            model="demo-model",
            messages=fixtures.analysis.build_analysis_request(fixtures.materials())[
                "messages"
            ],
            stream=False,
            extra_body={"timeout": 240, "need_trans": True},
        )
        audit = result["model_transport"]
        for key, value in {
            "sdk_request_calls": 1,
            "upstream_attempts": None,
            "sdk_max_retries": 0,
            "timeout_seconds": 240,
            "requested_model": "demo-model",
            "returned_model": "demo-model-returned",
            "finish_reason": "stop",
            "error_code": None,
        }.items():
            self.assertEqual(audit[key], value)
        self.assertEqual(
            audit["usage"],
            {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        )
        self.assertGreaterEqual(audit["elapsed_ms"], 0)
        for field in ("endpoint_sha256", "request_config_sha256"):
            self.assertRegex(audit[field], r"^[0-9a-f]{64}$")

    def test_invalid_choices(self):
        good = response().choices[0]
        for choices in (
            [],
            None,
            [good, good],
            [NS(index=1, message=good.message, finish_reason="stop")],
        ):
            with self.subTest(choices=choices):
                reply = response()
                reply.choices = choices
                self.assert_failure(reply, "invalid_choices")

    def test_incomplete_response(self):
        for reason in (None, "length", "content_filter", "tool_calls", "unknown"):
            with self.subTest(reason=reason):
                reply = response()
                reply.choices[0].finish_reason = reason
                self.assert_failure(reply, "incomplete_response")

    def test_invalid_message(self):
        for message in (None, NS(role="user", content="{}"), NS(content="{}")):
            reply = response()
            reply.choices[0].message = message
            self.assert_failure(reply, "invalid_message")

    def test_refusal_and_tool_responses_are_rejected(self):
        for key, value in (
            ("refusal", "refused"),
            ("refusal", ""),
            ("tool_calls", [NS(id="tool")]),
            ("function_call", {}),
        ):
            with self.subTest(field=key):
                reply = response()
                setattr(reply.choices[0].message, key, value)
                self.assert_failure(reply, "unsupported_response")

    def test_invalid_or_oversized_content(self):
        for content in (None, "", " \n", 123, [], "\ud800"):
            reply = response()
            reply.choices[0].message.content = content
            self.assert_failure(reply, "invalid_content")
        for content in ("x" * (64 * 1024 + 1), "文" * 22000):
            reply = response()
            reply.choices[0].message.content = content
            self.assert_failure(reply, "response_over_budget")

    def test_sdk_failure_is_not_retried_or_leaked(self):
        client = client_for()
        client.with_options.return_value.chat.completions.create.side_effect = (
            RuntimeError("SECRET synthetic payload")
        )
        result, _ = self.call(client=client)
        self.assertEqual(result["model_transport"]["error_code"], "request_failed")
        self.assertEqual(result["model_transport"]["error_type"], "RuntimeError")
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertFalse(result["model_transport"]["response_received"])
        client.with_options.return_value.chat.completions.create.assert_called_once()

    def test_configured_timeout_and_output_budget_are_used(self):
        limits = importlib.import_module(fixtures.fixtures.package.__name__ + ".limits")
        with patch.object(
            adapter,
            "get_limits",
            return_value=limits.Limits(timeout_seconds=310, output_kib=128),
        ):
            result, client = self.call()
        self.assertEqual(result["status"], "proposal")
        self.assertTrue(result["model_transport"]["response_received"])
        client.with_options.assert_called_once_with(max_retries=0, timeout=310)

    def test_setup_failure_makes_no_request(self):
        client = client_for()
        client.with_options.side_effect = RuntimeError("SECRET setup failure")
        result, _ = self.call(client=client)
        self.assertEqual(result["model_transport"]["error_code"], "client_setup_failed")
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_invalid_options_are_rejected_before_using_client(self):
        cases = [
            {"model": x}
            for x in ("", " x", "x ", "x\ny", "x\x00y", "x\x7fy", "x" * 129, 1)
        ]
        cases += [{"transport_mode": x} for x in (None, "demo", "unknown")]
        cases += [
            {"timeout_seconds": x}
            for x in (True, False, 0, 601, float("nan"), float("inf"), "240")
        ]
        for options in cases:
            with self.subTest(options=options):
                client = client_for()
                with self.assertRaises(ValueError):
                    self.call(client=client, **options)
                self.assertEqual(client.mock_calls, [])

    def test_unusable_materials_do_not_use_client(self):
        for data in (
            fixtures.materials([]),
            {},
            [],
            {**fixtures.materials(), "samples": [1]},
        ):
            result, client = self.call(data=data)
            self.assertIn(
                result["status"], ("insufficient_evidence", "analysis_failed")
            )
            self.assertEqual(result["model_transport"]["sdk_request_calls"], 0)
            self.assertEqual(client.mock_calls, [])

    def test_independent_calls_preserve_input_and_audit(self):
        data = fixtures.materials()
        before = copy.deepcopy(data)
        first, _ = self.call(data=data)
        saved = copy.deepcopy(first)
        second, _ = self.call(data=data, model="other-model", timeout_seconds=12.5)
        self.assertEqual(data, before)
        self.assertEqual(first, saved)
        self.assertIsNot(first["model_transport"], second["model_transport"])
        self.assertNotEqual(
            first["model_transport"]["request_config_sha256"],
            second["model_transport"]["request_config_sha256"],
        )

    def test_invalid_json_still_fails_analysis_after_valid_transport(self):
        reply = response()
        reply.choices[0].message.content = '{"unknown": true}'
        result, _ = self.call(reply)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "invalid_output_contract")
        self.assertIsNone(result["model_transport"]["error_code"])
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 1)

    def test_missing_or_invalid_usage_is_unknown_and_reasoning_is_not_used(self):
        reply = response()
        reply.model = None
        reply.usage = None
        reply.choices[
            0
        ].message.reasoning_content = "SECRET reasoning must not be stored"
        result, _ = self.call(reply)
        self.assertEqual(result["status"], "proposal")
        self.assertIsNone(result["model_transport"]["returned_model"])
        self.assertTrue(
            all(value is None for value in result["model_transport"]["usage"].values())
        )
        self.assertNotIn("SECRET", json.dumps(result))
        reply.usage = NS(prompt_tokens=True, completion_tokens=-1, total_tokens="300")
        result, _ = self.call(reply)
        self.assertTrue(
            all(value is None for value in result["model_transport"]["usage"].values())
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        outcome = adapter.analyze_with_model(
            fixtures.materials(),
            client_for(),
            model="demo-model",
            transport_mode="mock",
        )
        print(
            json.dumps(
                {
                    "status": outcome["status"],
                    "adapter_ready": outcome["status"] == "proposal",
                    "sdk_request_calls": outcome["model_transport"][
                        "sdk_request_calls"
                    ],
                    "model_calls": outcome["model_calls"],
                    "database_calls": 0,
                    "redis_calls": 0,
                },
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
