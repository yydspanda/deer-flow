"""Offline topic configuration/production adapter checks with fake services."""

import hashlib
import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_topic_background_fixtures", Path(__file__).with_name("test_background_service.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
package = fixtures.package
settings = importlib.import_module(package + ".topic_settings")
background = importlib.import_module(package + ".topic_background")
legacy = importlib.import_module(package + ".background_service")
monitor = importlib.import_module(package + ".local_settings")


class TopicRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / settings.CONFIG
        self.path.parent.mkdir()
        self.config = {
            "enabled": True,
            "model": "Pub_DeepSeek_V4_Flash",
            "csv_path": "rules.csv",
            "namespace": "topic-test",
        }

    def write(self, value=None):
        self.path.write_text(
            json.dumps(self.config if value is None else value), encoding="utf-8"
        )

    def test_disabled_missing_and_stg_preserve_legacy(self):
        self.assertIsNone(settings.load_settings(self.root, {}))
        self.write({"enabled": False})
        self.assertIsNone(settings.load_settings(self.root, {"env_profile": "PRD"}))
        self.path.write_text("broken", encoding="utf-8")
        self.assertIsNone(settings.load_settings(self.root, {"env_profile": "STG"}))
        self.assertFalse(settings.topic_enabled(self.root, {"env_profile": "STG"}))
        execute = Mock()
        self.assertEqual(
            legacy.run_background(self.root, {"env_profile": "STG"}, execute=execute)[
                "reason"
            ],
            "local_only",
        )
        execute.assert_not_called()

    def test_explicit_off_works_even_when_topic_file_is_invalid(self):
        self.path.write_text("broken", encoding="utf-8")
        env = {"env_profile": "PRD", "RULE_REFINE_MONITOR_ENABLED": "0"}
        self.assertFalse(settings.topic_enabled(self.root, env))
        self.assertFalse(monitor.configure_rule_monitor(self.root, env)["enabled"])
        self.assertEqual(
            background.run_background(self.root, env)["reason"], "monitor_disabled"
        )

    def test_prd_and_local_use_topic_config_and_keep_env_overrides(self):
        self.write()
        for profile in ("PRD", "LOCAL"):
            env = {"env_profile": profile, "RULE_REFINE_NAMESPACE": "override"}
            execute = Mock(return_value={"status": "idle"})
            result = legacy.run_background(self.root, env, execute=execute)
            self.assertEqual(result["schema_version"], "sec_know.topic_background.v1")
            _, configured, effective = execute.call_args.args
            self.assertEqual(configured["model"], "Pub_DeepSeek_V4_Flash")
            self.assertEqual(effective["RULE_REFINE_NAMESPACE"], "override")
            self.assertEqual(
                effective["RULE_REFINE_CSV_PATH"], str(self.root / "rules.csv")
            )
            self.assertNotIn("RULE_REFINE_MONITOR_ENABLED", env)

    def test_import_time_check_swallows_bad_configuration(self):
        for raw in (
            "{",
            '{"enabled": true,"enabled":false}',
            '{"enabled":1}',
            '{"enabled":true,"unknown":1}',
        ):
            self.path.write_text(raw, encoding="utf-8")
            self.assertFalse(settings.topic_enabled(self.root, {"env_profile": "PRD"}))
            self.assertEqual(
                legacy.run_background(self.root, {"env_profile": "PRD"})["status"],
                "failed",
            )

    def test_conflicting_profiles_never_enable(self):
        self.write()
        self.assertFalse(
            settings.topic_enabled(
                self.root, {"env_profile": "PRD", "ENV_PROFILE": "STG"}
            )
        )

    def test_enabled_import_check_has_no_service_imports(self):
        self.write()
        original = __import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {
                "redis",
                "openai",
                "httpx",
                "sqlalchemy",
                "model",
                "util",
                "nas",
            }:
                raise AssertionError("service dependency imported")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            self.assertTrue(settings.topic_enabled(self.root, {"env_profile": "PRD"}))

    def test_production_wiring_nas_shared_capacity_single_flat_submission(self):
        self.write()
        configured = settings.load_settings(self.root, {"env_profile": "PRD"})
        with fixtures.dependencies(self.root) as d:
            d.rc.ENV, d.rc.DATA_DIR = "PRD", self.root / "nas/data"
            d.env["env_profile"] = "PRD"
            captured = {}
            fake_processor = ModuleType(package + ".topic_processor")

            def factory(directory, **options):
                captured.update(directory=directory, **options)

                def process(signal):
                    options["generate"]({"synthetic": True})
                    return options["submit"](
                        {
                            "topic": "synthetic",
                            "A": {
                                "name": "command",
                                "operator": "Eq",
                                "value": "inventory",
                            },
                            "expression": "A",
                            "executeType": [3],
                        }
                    )

                return Mock(process=process)

            fake_processor.TopicProcessor = factory
            fake_sync = ModuleType(
                "my_workflows.zeus.flows.disposition_tools.small_rule_sync_client"
            )
            sync = Mock(
                sync_small_rule=Mock(
                    return_value={"status": "received", "rule_id": 123}
                )
            )
            fake_sync.SmallRuleSyncClient = Mock(return_value=sync)
            runtime = importlib.import_module(package + ".surge_runtime")
            answer = {
                "content": "{}",
                "transport": {"sdk_request_calls": 1, "error_code": None},
            }
            with (
                patch.dict(
                    sys.modules,
                    {
                        fake_processor.__name__: fake_processor,
                        fake_sync.__name__: fake_sync,
                    },
                ),
                patch.object(runtime, "call_model", return_value=answer),
            ):
                outcome = background._execute(self.root, configured, d.env)
            self.assertEqual(outcome["rule_id"], 123)
            expected = (
                d.rc.DATA_DIR
                / "rule_refine/topic_filters"
                / hashlib.sha256(b"scope").hexdigest()[:16]
            )
            self.assertEqual(captured["directory"], expected)
            d.semaphore.acquire.assert_called_once_with(
                "shared-model", 6, wait_timeout=0
            )
            sent = sync.sync_small_rule.call_args.args[0]
            self.assertEqual(sent[0]["executeType"], [3])
            self.assertNotIn("items", sent[0])
            self.assertNotIn("terms", sent[0])
            sync.sync_small_rule.assert_called_once()
            d.client.close.assert_called_once()
            d.redis_client.close.assert_called_once()
            d.rc.DATA_DIR = self.root / "synthetic-nas"
            d.rc.ENV = "STG"
            with self.assertRaises(ValueError):
                background._execute(self.root, configured, d.env)


if __name__ == "__main__":
    unittest.main()
