"""Assembled-payload consumer and background wiring, with no external services."""

import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def fixture(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


background = fixture("_surge_background_fixtures", "test_background_service.py")
surge = fixture("_surge_processor_fixtures", "test_surge_processor.py")
pending = importlib.import_module(background.package + ".pending_signal")
runtime = importlib.import_module(background.package + ".surge_runtime")


class Counter:
    def __init__(self, signal):
        self.mode, self.namespace = signal["mode"], signal["namespace"]
        self.signal = copy.deepcopy(signal)
        self.acknowledgements = 0

    def pending(self, observation):
        return copy.deepcopy(self.signal)

    def acknowledge(self, observation, signal_id):
        self.acknowledgements += 1
        if self.signal is None or self.signal["signal_id"] != signal_id:
            return False
        self.signal = None
        return True


class SurgeIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        self.signal = surge.signal()
        self.counter = Counter(self.signal)
        self.observation = {
            key: self.signal[key]
            for key in ("mode", "source_app", "company_code", "topic", "rule_code")
        }
        self.generate = Mock(side_effect=self.reply)
        self.processor = surge.module.SurgeProcessor(
            self.root / "advice",
            load_case=lambda signal: surge.case(),
            load_rules=lambda: Mock(get_rule=Mock(return_value=surge.rule())),
            generate=self.generate,
            model="synthetic-model",
            budget=surge.Budget(),
        )

    @staticmethod
    def reply(request):
        return surge.transport(json.dumps(surge.answer(request), ensure_ascii=False))

    def consume(self):
        return pending.consume_pending(self.counter, self.observation, self.processor)

    def enable_surge(self):
        (self.root / "config/rule_refine.surge.local.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "context_tokens": 262144,
                    "input_fraction": 0.8,
                    "timeout_seconds": 600,
                    "operator_context": "人工测试材料",
                }
            ),
            encoding="utf-8",
        )

    def test_consumer_acknowledges_only_after_durable_two_phase_result(self):
        acknowledged = self.counter.acknowledge

        def checked_ack(observation, signal_id):
            folder = (
                self.processor.directory
                / "jobs"
                / surge.module._identity(self.signal)[2]
            )
            record = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(record["outcome"]["status"], "received")
            self.assertTrue((folder / "recommendation.json").is_file())
            self.assertEqual(self.generate.call_count, 2)
            return acknowledged(observation, signal_id)

        with patch.object(self.counter, "acknowledge", side_effect=checked_ack):
            result = self.consume()
        self.assertEqual(result["status"], "acknowledged")
        self.assertTrue(result["acknowledged"])
        self.assertEqual(self.counter.acknowledgements, 1)
        self.assertEqual(self.consume()["status"], "idle")
        self.assertEqual(self.generate.call_count, 2)

    def test_ack_timeout_retry_only_reuses_saved_result_without_model_calls(self):
        with patch.object(
            self.counter,
            "acknowledge",
            side_effect=TimeoutError("synthetic Redis timeout"),
        ):
            first = self.consume()
        self.assertEqual(first["status"], "ack_unconfirmed")
        self.assertIsNone(first["acknowledged"])
        self.assertEqual(self.generate.call_count, 2)
        second = self.consume()
        self.assertEqual(second["status"], "acknowledged")
        self.assertEqual(second["processing"]["model_calls_this_attempt"], 0)
        self.assertTrue(second["processing"]["analysis_reused"])
        self.assertEqual(self.generate.call_count, 2)

    def test_tamper_between_processing_and_ack_is_result_unverified(self):
        process = self.processor.process

        def corrupt_after_processing(signal):
            result = process(signal)
            (Path(result["output_dir"]) / "REPORT.md").write_text(
                "tampered", encoding="utf-8"
            )
            return result

        with patch.object(
            self.processor, "process", side_effect=corrupt_after_processing
        ):
            result = self.consume()
        self.assertEqual(result["status"], "result_unverified")
        self.assertFalse(result["acknowledged"])
        self.assertEqual(self.counter.acknowledgements, 0)
        self.assertIsNotNone(self.counter.signal)
        self.assertEqual(self.generate.call_count, 2)

    def test_enabled_background_wires_new_processor_600_seconds_and_closes_resources(
        self,
    ):
        self.enable_surge()
        captured = {}
        with background.dependencies(self.root) as deps:
            deps.settings["model"] = "Pub_DeepSeek_V4_Flash"
            deps.rules.get_rule.return_value = surge.rule()

            def build(directory, **options):
                captured.update(directory=directory, options=options)
                return surge.module.SurgeProcessor(
                    directory,
                    load_case=lambda signal: surge.case(),
                    load_rules=options["load_rules"],
                    generate=options["generate"],
                    model=options["model"],
                    budget=runtime.budget_from_settings(options["settings"]),
                    operator_context=options["settings"]["operator_context"],
                )

            def tick(directory, **options):
                captured["guard"] = options["processor"]
                return options["processor"].process(self.signal)

            deps.tick_module.run_tick.side_effect = tick
            with (
                patch.object(runtime, "build_processor", side_effect=build) as factory,
                patch.object(
                    runtime,
                    "call_model",
                    side_effect=lambda client, request, **options: self.reply(request),
                ) as call_model,
            ):
                result = background.module._execute(self.root, deps.settings, deps.env)
            self.assertEqual(result["status"], "received")
            self.assertEqual(call_model.call_count, 2)
            factory.assert_called_once()
            self.assertEqual(captured["directory"].name, "processing_surge_v1")
            self.assertEqual(captured["options"]["database"], deps.database)
            self.assertIs(captured["options"]["counter"], deps.counter)
            self.assertIs(captured["options"]["redis_client"], deps.redis_client)
            deps.adapter.analyze_with_model.assert_not_called()
            deps.fake[
                background.package + ".signal_processing"
            ].SignalProcessor.assert_not_called()
            deps.fake[
                background.package + ".local_sink"
            ].LocalRecommendationSink.assert_not_called()
            deps.fake["httpx"].Client.assert_called_once_with(
                trust_env=False, timeout=600
            )
            client_options = deps.fake["openai"].OpenAI.call_args.kwargs
            self.assertEqual(
                (client_options["timeout"], client_options["max_retries"]), (600, 0)
            )
            for call in call_model.call_args_list:
                self.assertIs(call.args[0], deps.client)
                self.assertEqual(call.kwargs["budget"].timeout_seconds, 600)
                self.assertEqual(call.kwargs["model"], "Pub_DeepSeek_V4_Flash")
            self.assertEqual(deps.breaker.record_success.call_count, 2)
            deps.semaphore.acquire.assert_called_once_with(
                "shared-model", 6, wait_timeout=0
            )
            self.assertGreaterEqual(deps.lease.assert_valid.call_count, 4)
            deps.lease.__exit__.assert_called_once()
            self.assertIsNone(captured["guard"].lease)
            deps.client.close.assert_called_once()
            deps.redis_client.close.assert_called_once()
            deps.http.__exit__.assert_called_once()

    def test_background_guard_failure_closes_clients_without_ack_or_old_analysis(self):
        self.enable_surge()
        captured = {}
        with background.dependencies(self.root) as deps:
            processor = Mock(
                process=Mock(side_effect=OSError("synthetic local disk failure"))
            )

            def tick(directory, **options):
                captured["guard"] = options["processor"]
                return options["processor"].process(self.signal)

            deps.tick_module.run_tick.side_effect = tick
            with (
                patch.object(runtime, "build_processor", return_value=processor),
                patch.object(runtime, "call_model") as call_model,
            ):
                with self.assertRaises(OSError):
                    background.module._execute(self.root, deps.settings, deps.env)
            call_model.assert_not_called()
            deps.adapter.analyze_with_model.assert_not_called()
            deps.counter.acknowledge.assert_not_called()
            deps.lease.__exit__.assert_called_once()
            self.assertIsNone(captured["guard"].lease)
            deps.client.close.assert_called_once()
            deps.redis_client.close.assert_called_once()
            deps.http.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
