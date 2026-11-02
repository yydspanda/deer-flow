import base64
import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import build_command


def load_tools():
    packet = build_command.contents()
    result = {}
    for name in (
        "inventory",
        "historical_review",
        "historical_materials",
        "historical_runner",
    ):
        mod = types.ModuleType(name)
        mod.__file__ = str(Path(__file__).parent / (name + ".py"))
        sys.modules[name] = mod
        exec(compile(packet["files"][name], name, "exec"), mod.__dict__)
        result[name] = mod
    return NS(**result)


class CommandTests(unittest.TestCase):
    def test_embedded_sources_and_digest_match_current_files(self):
        command = build_command.build()
        payload = command.split('payload = """', 1)[1].split('"""', 1)[0]
        raw = zlib.decompress(base64.b64decode(payload))
        self.assertIn(hashlib.sha256(raw).hexdigest(), command)
        self.assertEqual(json.loads(raw), build_command.contents())
        self.assertEqual(len(json.loads(raw)["pins"]), 13)
        for source in json.loads(raw)["files"].values():
            ast.parse(source, feature_version=(3, 11))
        syntax = subprocess.run(
            ["bash", "-n"], input=command, text=True, capture_output=True
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_complete_command_stops_non_local_before_loading_application(self):
        command = build_command.build()
        source = command.split("<<'PY_HISTORICAL_REVIEW'\n", 1)[1].rsplit(
            "\nPY_HISTORICAL_REVIEW", 1
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            out = subprocess.run(
                [sys.executable, "-B", "-c", source],
                cwd=directory,
                env={**os.environ, "env_profile": "PRD", "HOME": directory},
                text=True,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(out.returncode, 1, out.stderr)
            result = json.loads(out.stdout)
            self.assertEqual(result["error_code"], "local_only")
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_empty_context_reports_coverage_without_confirming_or_model_calls(self):
        tools = load_tools()
        runner = tools.historical_runner
        with (
            patch.object(
                runner,
                "prepare",
                side_effect=runner.NoUsableSamples({"included_alerts": 0}),
            ),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(runner.main({}), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["coverage"]["included_alerts"], 0)
        self.assertEqual(result["model_calls"], 0)

    def test_cancel_does_not_execute_or_create_artifacts(self):
        runner = load_tools().historical_runner
        from test_review import limits_module

        p = NS(
            context={"rule": {"rule_code": "demo"}, "coverage": {}},
            model="demo",
            limits=limits_module.Limits(),
            request={"input_bytes": 1234},
        )
        with (
            patch.object(runner, "prepare", return_value=p),
            patch.object(runner, "execute") as execute,
            patch("builtins.open", return_value=io.StringIO("NO\n")),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(runner.main({}), 0)
        execute.assert_not_called()

    def execute_fixture(self, fresh_version="v1", fail_capacity=False):
        runner = load_tools().historical_runner
        rule = {
            "rule_code": "RULE",
            "rule_name": "人工测试",
            "topic": "demo",
            "rule_config_version": "v1",
            "company_code": "demo-company",
        }
        result = {
            "schema_version": "sec_know.historical_rule_review.v1",
            "status": "no_change",
            "format_valid": True,
            "issue": "无具体问题",
            "advice": "不调整",
            "certainty": "只限人工材料",
            "model_transport": {
                "sdk_request_calls": 1,
                "error_code": None,
                "requested_model": "demo",
                "returned_model": "demo",
                "finish_reason": "stop",
                "usage": {},
                "elapsed_ms": 1,
                "upstream_attempts": None,
            },
        }
        adapter = NS(analyze_with_model=Mock(return_value=result))
        loaded = NS(
            analysis=object(),
            filter_evaluation=NS(evaluate_filter=Mock()),
            model_adapter=adapter,
            csv_config_source=NS(
                CsvRuleConfigSource=lambda _: NS(
                    get_rule=lambda *a, **kw: {
                        **rule,
                        "rule_config_version": fresh_version,
                    }
                )
            ),
        )
        from test_review import limits_module

        p = NS(
            rc=NS(REDIS_URL="redis://mock"),
            policy=NS(
                lease_ms=300000, heartbeat_ms=1000, semaphore_key="mock", concurrency=1
            ),
            model="demo",
            key="SECRET-KEY",
            endpoint="http://127.0.0.1:1/v1",
            loaded=loaded,
            csv="unused",
            context={"rule": rule, "samples": [], "coverage": {}},
            request={"prompt_sha256": "test", "input_bytes": 1234},
            limits=limits_module.Limits(
                input_kib=256, output_kib=128, timeout_seconds=300
            ),
        )
        lease = Mock()
        lease.assert_valid = Mock()
        lease.__enter__ = Mock(return_value=lease)
        lease.__exit__ = Mock(return_value=False)
        semaphore = Mock()
        if fail_capacity:
            semaphore.acquire.side_effect = TimeoutError("secret-error")
        else:
            semaphore.acquire.return_value = lease
        breaker = Mock()
        breaker.assert_closed = Mock()
        fake_modules = {}
        for name, attrs in {
            "config.circuit_breaker": {
                "RedisCircuitBreaker": Mock(return_value=breaker)
            },
            "util.model_semaphore": {
                "RedisModelSemaphore": Mock(return_value=semaphore)
            },
            "openai": {"OpenAI": Mock(return_value=Mock())},
        }.items():
            mod = types.ModuleType(name)
            mod.__dict__.update(attrs)
            fake_modules[name] = mod
        redis_client = Mock()
        redis_client.connection_pool.connection_kwargs = {}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, fake_modules),
            patch("redis.Redis.from_url", return_value=redis_client),
        ):
            folder = Path(directory)
            out = runner.execute(p, folder)
            files = {path.name: path.read_bytes() for path in folder.iterdir()}
            self.assertNotIn("SECRET-KEY", json.dumps(out))
            self.assertEqual(json.loads(files["summary.json"]), out)
        return out, files, adapter, breaker, redis_client

    def test_runner_makes_one_adapter_call_and_checks_fresh_configuration(self):
        out, files, adapter, breaker, redis_client = self.execute_fixture()
        self.assertEqual(out["status"], "passed")
        self.assertEqual(out["sdk_request_calls"], 1)
        self.assertEqual(adapter.analyze_with_model.call_count, 1)
        self.assertEqual(
            adapter.analyze_with_model.call_args.kwargs["timeout_seconds"], 300
        )
        self.assertEqual(adapter.get_limits().output_kib, 128)
        self.assertEqual(out["limits"]["input_kib"], 256)
        self.assertIn("request-started.json", files)
        self.assertFalse(out["live_counter_modified"])
        self.assertEqual(out["zeus_callback_calls"], 0)
        breaker.record_success.assert_called_once_with("demo")
        redis_client.eval.assert_not_called()
        redis_client.close.assert_called_once()

    def test_configuration_drift_blocks_report(self):
        out, files, *_ = self.execute_fixture(fresh_version="v2")
        self.assertEqual(out["status"], "blocked")
        self.assertFalse(out["configuration_unchanged"])
        self.assertIn("请勿", files["REPORT.md"].decode("utf-8"))

    def test_capacity_failure_does_not_call_model(self):
        out, files, adapter, *_ = self.execute_fixture(fail_capacity=True)
        self.assertEqual(out["sdk_request_calls"], 0)
        self.assertEqual(out["status"], "failed")
        self.assertNotIn("request-started.json", files)
        adapter.analyze_with_model.assert_not_called()
        self.assertNotIn("secret-error", json.dumps(out))

    def test_preparation_reports_changed_file_without_disclosing_source(self):
        runner = load_tools().historical_runner
        with (
            patch.object(
                runner,
                "prepare",
                side_effect=runner.ModuleChanged("analysis.py", "a" * 64),
            ),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(runner.main({}), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["error_code"], "module_changed")
        self.assertEqual(result["file"], "analysis.py")
        self.assertEqual(result["model_calls"], 0)

    def test_preparation_reports_policy_error_before_model_call(self):
        runner = load_tools().historical_runner
        with (
            patch.object(
                runner,
                "prepare",
                side_effect=ValueError("invalid_rule_refine_limit:input_kib"),
            ),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(runner.main({}), 1)
        self.assertEqual(
            json.loads(out.getvalue())["error_code"], "invalid_limits_configuration"
        )

    def test_pinned_modules_load_from_actual_6w_sources_and_use_local_policy(self):
        packet = build_command.contents()
        pins = {
            name: digest
            for name, digest in packet["pins"].items()
            if name.startswith("my_workflows/")
        }
        implementation = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, digest in pins.items():
                source = next(
                    p
                    for p in sorted(implementation.glob("step*/payload/" + name))
                    if hashlib.sha256(p.read_bytes()).hexdigest() == digest
                )
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            config = root / "config/rule_refine.limits.local.json"
            config.parent.mkdir()
            config.write_text('{"input_kib":256,"output_kib":128}', encoding="utf-8")
            loaded = load_tools().historical_runner.modules(root, pins)
            self.assertEqual(loaded.limits.get_limits(root).input_kib, 256)
            self.assertEqual(loaded.model_adapter.get_limits().output_kib, 128)


if __name__ == "__main__":
    unittest.main(verbosity=2)
