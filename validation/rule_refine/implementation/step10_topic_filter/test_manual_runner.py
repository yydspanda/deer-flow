"""Manual command checks: default preview, explicit creation consent, real type recall."""

import copy
import importlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step07b_real_csv_acceptance"


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_file("surge_materials", HERE.parent / "step06z_surge_recommendation/materials.py")
load_file("materials", PREVIOUS / "materials.py")
runner = load_file("topic_manual_runner_tested", HERE / "manual_runner.py")


class ManualCommandTests(unittest.TestCase):
    def setUp(self):
        self.prepared = {
            "model": "synthetic-model",
            "profile": "LOCAL",
            "zeus_target": {
                "base_url": "https://synthetic.invalid",
                "endpoint": "https://synthetic.invalid/public/syncSmallRule",
                "source": "static_root_config",
            },
            "namespace": "stable",
            "signal": {"mode": "historical_replay", "signal_id": "manual-unique"},
            "operator_context": "synthetic",
            "rules_factory": Mock(),
            "snapshot": {},
            "budget": types.SimpleNamespace(input_token_budget=209715),
            "case": {
                "current_samples": [{"alert_id": "a"}],
                "history_samples": [],
                "execute_type": {"status": "resolved", "values": [3]},
            },
            "preview": {
                "selection": {"current_count": 1},
                "request": {"input_bytes": 100, "estimated_input_tokens": 90},
            },
        }

    def call(self, argv=(), answer="LIVE"):
        output = io.StringIO()
        with (
            patch.object(runner, "prepare", return_value=self.prepared),
            patch.object(runner, "ask", return_value=answer) as ask,
            patch.object(
                runner,
                "execute",
                return_value={
                    "status": "received",
                    "recommendation": {"filter": {"topic": "synthetic"}},
                },
            ) as execute,
            patch("sys.stdout", output),
        ):
            code = runner.main(list(argv))
        return code, output.getvalue(), ask, execute

    def test_default_is_preview_without_prompt_model_or_post(self):
        code, output, ask, execute = self.call()
        self.assertEqual(code, 0)
        ask.assert_not_called()
        execute.assert_not_called()
        preview = json.loads(output)
        self.assertEqual(preview["max_sdk_requests"], 2)
        self.assertEqual(preview["max_zeus_posts"], 1)
        self.assertEqual(preview["model_calls_this_command"], 0)
        self.assertEqual(preview["execute_type"]["values"], [3])
        self.assertEqual(
            preview["zeus_endpoint"], "https://synthetic.invalid/public/syncSmallRule"
        )
        self.assertEqual(preview["zeus_target_source"], "static_root_config")

    def test_live_requires_literal_live_and_discloses_creation(self):
        code, output, ask, execute = self.call(["--live"])
        self.assertEqual(code, 0)
        self.assertIn("实际创建小规则", output)
        self.assertIn("不能保证默认禁用", output)
        self.assertIn("创建ZEUS小规则", ask.call_args.args[0])
        execute.assert_called_once_with(self.prepared)
        self.assertIn('"recommendation"', output)

    def test_cancel_never_executes(self):
        code, _, _, execute = self.call(["--live"], answer="")
        self.assertEqual(code, 0)
        execute.assert_not_called()

    def test_unresolved_type_stops_before_confirmation_or_calls(self):
        self.prepared["case"]["execute_type"] = {
            "status": "needs_confirmation",
            "values": [1, 3],
        }
        code, _, ask, execute = self.call(["--live"])
        self.assertEqual(code, 1)
        ask.assert_not_called()
        execute.assert_not_called()

    def test_private_exception_text_is_not_printed(self):
        with (
            patch.object(
                runner, "prepare", side_effect=ValueError("SYNTHETIC_PRIVATE_VALUE")
            ),
            patch.object(runner, "execute") as execute,
            patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            self.assertEqual(runner.main(["--live"]), 1)
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", out.getvalue())
        execute.assert_not_called()

    def test_process_preserves_replay_scope_and_shares_delivery_ledger(self):
        captured = {}

        class Processor:
            def __init__(self, directory, **options):
                captured.update(directory=directory, **options)

            def process(self, signal):
                captured["signal"] = signal
                return {"status": "received"}

        self.prepared["load"] = lambda name: types.SimpleNamespace(
            TopicProcessor=Processor
        )
        generate, submit = Mock(), Mock()
        result = runner.process(
            self.prepared,
            Path("manual/unique"),
            generate,
            submit,
            delivery_directory=Path("stable/deliveries"),
        )
        self.assertEqual(result["status"], "received")
        self.assertEqual(captured["signal"]["mode"], "historical_replay")
        self.assertEqual(captured["delivery_scope"], "live:stable")
        self.assertEqual(captured["delivery_directory"], Path("stable/deliveries"))
        self.assertIs(captured["generate"], generate)
        self.assertIs(captured["submit"], submit)
        case = captured["load_case"]({})
        case["current_samples"].clear()
        self.assertEqual(len(self.prepared["case"]["current_samples"]), 1)

    def test_terminal_confirmation_uses_separate_real_tty_streams(self):
        import os
        import pty
        import threading

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        original_open = open

        def tty_open(path, *args, **kwargs):
            if str(path) == "/dev/tty":
                path = os.ttyname(slave)
                kwargs["opener"] = lambda name, flags: os.open(
                    name, flags | os.O_NOCTTY
                )
            return original_open(path, *args, **kwargs)

        writer = threading.Thread(target=lambda: os.write(master, b"LIVE\n"))
        with (
            patch("builtins.open", side_effect=tty_open),
            patch("sys.stdin", io.StringIO("NO\n")),
        ):
            writer.start()
            self.assertEqual(runner.ask("confirm: "), "LIVE")
            writer.join(timeout=2)


class RecallAdapterTests(unittest.TestCase):
    def setUp(self):
        fixtures = load_file(
            "_topic_manual_material_fixtures", PREVIOUS / "test_materials.py"
        )
        self.fixture = fixtures.MaterialsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        package = types.ModuleType("_topic_manual_source_test")
        package.__path__ = [
            str(HERE / "payload/my_workflows/zeus/rule_refine"),
            str(
                HERE.parent
                / "step07_surge_runtime/payload/my_workflows/zeus/rule_refine"
            ),
        ]
        sys.modules[package.__name__] = package
        self.load = lambda name: importlib.import_module(package.__name__ + "." + name)

    def test_real_type_is_recalled_and_counterfactual_does_not_mutate_original(self):
        data = self.fixture.payload("current", self.fixture.cutoff)
        data["alert"]["executeType"] = 3
        self.fixture.write_current([data])
        original = copy.deepcopy(self.fixture.rules.rule)
        database_hash = runner.fingerprint(self.fixture.db)
        case, signal, audit = runner.prepare_case(
            self.fixture.root,
            self.fixture.current,
            None,
            runner.material_loader(self.load),
            self.fixture.rules,
        )
        self.assertEqual(case["execute_type"]["values"], [3])
        self.assertEqual(case["execute_type"]["status"], "resolved")
        self.assertEqual(case["original_rule"], original)
        self.assertEqual(self.fixture.rules.rule, original)
        self.assertNotEqual(case["rule"]["configuration"], original["configuration"])
        self.assertEqual(runner.fingerprint(self.fixture.db), database_hash)
        self.assertTrue(audit["temporary_database_removed"])
        self.assertFalse(audit["removed_conditions_provided_to_model"])
        self.assertEqual(signal["mode"], "historical_replay")

    def test_mixed_type_is_not_replaced_with_production_type(self):
        current = self.fixture.payload("a", self.fixture.cutoff)
        other = self.fixture.payload("b", self.fixture.cutoff)
        current["alert"]["executeType"], other["alert"]["executeType"] = 1, 3
        self.fixture.write_current([current, other])
        case, _, _ = runner.prepare_case(
            self.fixture.root,
            self.fixture.current,
            None,
            runner.material_loader(self.load),
            self.fixture.rules,
        )
        self.assertEqual(case["execute_type"]["values"], [1, 3])
        self.assertEqual(case["execute_type"]["status"], "needs_confirmation")


class StaticTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "util/root_config.py"
        self.path.parent.mkdir()
        self.source = """
ENV = "LOCAL"
APP_KEY = "SYNTHETIC_SECRET_MUST_NOT_PRINT"
if ENV == "LOCAL":
    ZEUS_SYSTEM_URL = "https://local-gateway.invalid"
elif ENV == "STG":
    ZEUS_SYSTEM_URL = "https://stg-gateway.invalid"
else:
    ZEUS_SYSTEM_URL = "https://prd-gateway.invalid"
raise RuntimeError("configuration must never execute during preview")
"""
        self.path.write_text(self.source, encoding="utf-8")

    def test_selected_literal_branch_without_import_or_key_disclosure(self):
        for profile, endpoint in (("LOCAL", "local"), ("PRD", "prd")):
            result = runner.static_zeus_target(self.root, profile)
            self.assertEqual(
                result["endpoint"],
                f"https://{endpoint}-gateway.invalid/public/syncSmallRule",
            )
            self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_dynamic_values_and_later_target_or_env_override_are_rejected(self):
        sources = (
            self.source.replace(
                '"https://local-gateway.invalid"', 'os.environ["PRIVATE_URL"]'
            ),
            self.source.replace('ENV == "LOCAL"', 'runtime_profile() == "LOCAL"'),
            self.source + '\nZEUS_SYSTEM_URL = "https://other.invalid"\n',
            self.source + '\nENV = "PRD"\n',
        )
        for source in sources:
            with self.subTest(source=source[-20:]):
                self.path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "static_zeus_target_dynamic"):
                    runner.static_zeus_target(self.root, "LOCAL")

    def test_missing_or_credential_bearing_target_is_rejected(self):
        for value in (
            '"https://user:secret@synthetic.invalid"',
            '"https://synthetic.invalid?key=secret"',
        ):
            self.path.write_text(
                self.source.replace('"https://local-gateway.invalid"', value),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "static_zeus_target_invalid"):
                runner.static_zeus_target(self.root, "LOCAL")
        self.path.write_text("ENV='LOCAL'\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "static_zeus_target_unavailable"):
            runner.static_zeus_target(self.root, "LOCAL")

    def test_runtime_target_mismatch_stops_before_model_or_redis_construction(self):
        fake = {}

        def add(name, **values):
            module = types.ModuleType(name)
            module.__path__ = []
            module.__dict__.update(values)
            fake[name] = module
            if "." in name:
                parent, child = name.rsplit(".", 1)
                if parent in fake:
                    setattr(fake[parent], child, module)
            return module

        add("httpx", Client=Mock())
        add("openai", OpenAI=Mock())
        add("redis", Redis=Mock())
        add("redis.backoff", NoBackoff=Mock())
        add("redis.retry", Retry=Mock())
        add("util")
        rc = add(
            "util.root_config", ENV="LOCAL", ZEUS_SYSTEM_URL="https://changed.invalid"
        )
        add("util.model_semaphore", RedisModelSemaphore=Mock())
        add("config")
        add("config.circuit_breaker", RedisCircuitBreaker=Mock())
        add("config.model_limits", get_model_policy=Mock())
        add("nas")
        add("nas.config", APP_KEY_DICT={"litellm_key": "synthetic-secret"})
        prepared = {
            "root": self.root,
            "snapshot": {},
            "profile": "LOCAL",
            "zeus_target": runner.static_zeus_target(self.root, "LOCAL"),
        }
        with (
            patch.dict(sys.modules, fake),
            self.assertRaisesRegex(ValueError, "loaded_zeus_target_changed"),
        ):
            runner.execute(prepared)
        fake["openai"].OpenAI.assert_not_called()
        fake["redis"].Redis.from_url.assert_not_called()
        fake["config.model_limits"].get_model_policy.assert_not_called()
        rc.ZEUS_SYSTEM_URL = prepared["zeus_target"]["base_url"]
        self.assertIsNone(runner.verify_runtime_target(rc, prepared))


if __name__ == "__main__":
    unittest.main()
