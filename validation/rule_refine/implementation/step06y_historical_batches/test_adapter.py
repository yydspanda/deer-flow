"""Check the actual SDK kwargs without making a provider request."""

import hashlib
import json
import unittest
import tempfile
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import adapter
from context_budget import ContextBudget


class AdapterTests(unittest.TestCase):
    def invoke(
        self,
        *,
        content='{"status":"no_change"}',
        finish_reason="stop",
        response_format=None,
    ):
        budget = ContextBudget()
        response = NS(
            model="returned-provider-label",
            usage=NS(prompt_tokens=250000, completion_tokens=100, total_tokens=250100),
            choices=[
                NS(
                    index=0,
                    finish_reason=finish_reason,
                    message=NS(role="assistant", content=content),
                )
            ],
        )
        client = Mock()
        client.base_url = "http://localhost:4001/v1/"
        client.with_options.return_value = client
        client.chat.completions.create.return_value = response
        messages = [{"role": "user", "content": "人工材料"}]

        def collector(materials, generate, **kwargs):
            try:
                text = generate(messages)
                return {"status": "no_change", "content": text}
            except Exception as error:
                return {"status": "analysis_failed", "error_code": str(error)}

        with (
            patch.object(adapter, "get_limits", return_value=budget),
            patch.object(adapter, "analyze_materials", side_effect=collector),
        ):
            result = adapter.analyze_with_model(
                {},
                client,
                model="Pub_DeepSeek_V4_Flash",
                transport_mode="mock",
                response_format=response_format,
            )
        return result, client, budget

    def test_real_sdk_arguments_have_no_output_token_limit_and_frozen_timeout(self):
        result, client, budget = self.invoke()
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "Pub_DeepSeek_V4_Flash")
        self.assertEqual(set(request), {"model", "messages", "stream", "extra_body"})
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)
        self.assertNotIn("max_output_tokens", request)
        self.assertEqual(request["extra_body"]["timeout"], 600)
        self.assertFalse(request["stream"])
        client.with_options.assert_called_once_with(max_retries=0, timeout=600)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        audit = result["model_transport"]
        self.assertIsNone(audit["model_output_token_limit"])
        self.assertEqual(audit["usage"]["prompt_tokens"], 250000)
        expected_config = {
            "model": "Pub_DeepSeek_V4_Flash",
            "stream": False,
            "sdk_max_retries": 0,
            "timeout_seconds": 600,
            "structural_max_response_bytes": budget.output_bytes,
            "model_output_token_limit": None,
            "extra_body": {"timeout": 600, "need_trans": True},
        }
        digest = hashlib.sha256(
            json.dumps(expected_config, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        self.assertEqual(audit["request_config_sha256"], digest)
        self.assertNotIn("requested_response_format", audit)
        self.assertNotIn("response_format_enforcement_verified", audit)

    def test_json_object_mode_is_requested_and_audited_without_enforcement_claim(self):
        result, client, budget = self.invoke(response_format={"type": "json_object"})
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(
            set(request),
            {"model", "messages", "stream", "extra_body", "response_format"},
        )
        client.with_options.assert_called_once_with(max_retries=0, timeout=600)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        audit = result["model_transport"]
        self.assertEqual(audit["requested_response_format"], {"type": "json_object"})
        self.assertIs(audit["response_format_enforcement_verified"], False)
        expected_config = {
            "model": "Pub_DeepSeek_V4_Flash",
            "stream": False,
            "sdk_max_retries": 0,
            "timeout_seconds": 600,
            "structural_max_response_bytes": budget.output_bytes,
            "model_output_token_limit": None,
            "extra_body": {"timeout": 600, "need_trans": True},
            "requested_response_format": {"type": "json_object"},
        }
        self.assertEqual(audit["request_config_sha256"], adapter._hash(expected_config))
        legacy_result, _, _ = self.invoke()
        self.assertNotEqual(
            audit["request_config_sha256"],
            legacy_result["model_transport"]["request_config_sha256"],
        )

    def test_unsupported_response_formats_fail_before_sdk_or_collector(self):
        for response_format in (
            {},
            "json_object",
            [],
            False,
            {"type": "text"},
            {"type": "json_schema"},
            {"type": "json_object", "strict": True},
        ):
            with self.subTest(response_format=response_format):
                client = Mock()
                with (
                    patch.object(adapter, "get_limits", return_value=ContextBudget()),
                    patch.object(adapter, "analyze_materials") as collector,
                    self.assertRaisesRegex(ValueError, "response_format"),
                ):
                    adapter.analyze_with_model(
                        {},
                        client,
                        model="Pub_DeepSeek_V4_Flash",
                        transport_mode="mock",
                        response_format=response_format,
                    )
                collector.assert_not_called()
                client.with_options.assert_not_called()
                client.chat.completions.create.assert_not_called()

    def test_provider_truncation_still_rejected_without_retry(self):
        result, client, _ = self.invoke(finish_reason="length")
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["model_transport"]["error_code"], "incomplete_response")
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_reply_over_old_64kib_is_accepted_by_transport(self):
        result, client, _ = self.invoke(content="测" * 22000)
        self.assertIsNone(result["model_transport"]["error_code"])
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_fifty_references_through_actual_executor_adapter_and_local_evaluation(
        self,
    ):
        from test_runner import (
            load_tools,
            analysis,
            filter_evaluation,
            limits_module,
            context,
        )

        tools = load_tools()
        loaded = NS(
            analysis=analysis,
            filter_evaluation=filter_evaluation,
            limits=limits_module,
            model_adapter=tools.historical_adapter,
        )
        tools.context_budget.install_compatibility(loaded)
        material = context(1)
        material["samples"] = [
            context(1, alert=f"synthetic-{i}")["samples"][0] for i in range(50)
        ]
        rule = material["rule"]
        loaded.csv_config_source = NS(
            CsvRuleConfigSource=lambda _: NS(get_rule=lambda *a, **kw: rule)
        )
        budget = tools.context_budget.ContextBudget()
        request = tools.historical_review.build_request(
            material, analysis, limits=budget
        )
        answer = {
            "status": "proposal",
            "issue": "人工健康探测聚集",
            "recommendation": "确认业务后排除",
            "certainty": "仅人工样本",
            "sample_alert_ids": [s["alert_id"] for s in material["samples"]],
            "filter": {
                "topic": "demo",
                "expression": "A",
                "A": {"name": "path", "operator": "equal", "value": "/health"},
            },
        }
        client = Mock()
        client.base_url = "http://localhost:4001/v1/"
        client.with_options.return_value = client
        client.chat.completions.create.return_value = NS(
            model="demo",
            usage=NS(prompt_tokens=12345, completion_tokens=500, total_tokens=12845),
            choices=[
                NS(
                    index=0,
                    finish_reason="stop",
                    message=NS(
                        role="assistant", content=json.dumps(answer, ensure_ascii=False)
                    ),
                )
            ],
        )
        lease = Mock()
        lease.assert_valid = Mock()
        lease.__enter__ = Mock(return_value=lease)
        lease.__exit__ = Mock(return_value=False)
        semaphore = Mock()
        semaphore.acquire.return_value = lease
        redis = Mock()
        redis.connection_pool.connection_kwargs = {}
        breaker = Mock()
        breaker.assert_closed = Mock()
        fake = {}
        for name, attrs in {
            "openai": {"OpenAI": Mock(return_value=client)},
            "config.circuit_breaker": {
                "RedisCircuitBreaker": Mock(return_value=breaker)
            },
            "util.model_semaphore": {
                "RedisModelSemaphore": Mock(return_value=semaphore)
            },
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            fake[name] = module
        p = NS(
            context=material,
            request=request,
            loaded=loaded,
            limits=budget,
            csv="unused",
            model="Pub_DeepSeek_V4_Flash",
            endpoint="http://localhost:4001/v1/",
            key="synthetic-test-key",
            rc=NS(REDIS_URL="redis://unused"),
            policy=NS(
                lease_ms=300000, heartbeat_ms=30000, semaphore_key="test", concurrency=1
            ),
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(sys.modules, fake),
            patch("redis.Redis.from_url", return_value=redis),
        ):
            result = tools.single_runner.execute(p, Path(temporary))
            saved = json.loads(
                (Path(temporary) / "analysis.json").read_text(encoding="utf-8")
            )
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(saved["cited_sample_count"], 50)
        self.assertEqual(saved["evaluation"]["input_alert_count"], 50)
        self.assertEqual(saved["evaluation"]["counts"]["matched"], 50)
        self.assertEqual(result["usage"]["prompt_tokens"], 12345)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertNotIn("max_tokens", client.chat.completions.create.call_args.kwargs)
        self.assertEqual(result["limits"]["assumed_context_tokens"], 1_000_000)


if __name__ == "__main__":
    unittest.main()
