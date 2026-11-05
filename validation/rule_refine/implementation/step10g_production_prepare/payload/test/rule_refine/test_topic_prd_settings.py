"""PRD release settings stay separate from LOCAL and STG; no service imports."""

import importlib
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "_topic_prd_settings_under_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "my_workflows/zeus/rule_refine")]
sys.modules[PACKAGE] = package
settings = importlib.import_module(PACKAGE + ".topic_settings")
monitor = importlib.import_module(PACKAGE + ".local_settings")
PRD_CONFIG = "config/rule_refine.topic.prd.json"


class TopicPrdSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="topic-prd-settings-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "original-project"
        self.root.mkdir()
        self.local = {
            "enabled": True,
            "model": "Pub_DeepSeek_V4_Flash",
            "csv_path": "local-rule-directory.csv",
            "namespace": "synthetic-local-namespace",
        }
        self.prd = {
            "enabled": True,
            "model": "Pub_DeepSeek_V4_Flash",
            "csv_path": "config/rule_refine.rules.prd.snapshot",
            "namespace": "topic-v1:prd",
            "context_tokens": 262144,
            "input_fraction": 0.8,
            "timeout_seconds": 600,
            "operator_context": "",
        }
        self.write(settings.CONFIG, self.local)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_prd_selects_separate_file_for_each_supported_profile_spelling(self):
        self.write(PRD_CONFIG, self.prd)
        for env in (
            {"env_profile": "prd"},
            {"env_profile": "PRD"},
            {"ENV_PROFILE": "PRD"},
            {"env_profile": "prd", "ENV_PROFILE": "PRD"},
        ):
            with self.subTest(env=env):
                actual = settings.load_settings(self.root, env)
                self.assertEqual(actual["namespace"], self.prd["namespace"])
                self.assertEqual(
                    actual["csv_path"], str(self.root / self.prd["csv_path"])
                )
                self.assertTrue(settings.topic_enabled(self.root, env))

    def test_local_ignores_prd_file_even_when_it_is_broken(self):
        path = self.write(PRD_CONFIG, self.prd)
        for raw in (json.dumps(self.prd), "{"):
            path.write_text(raw, encoding="utf-8")
            for env in ({}, {"env_profile": "LOCAL"}):
                with self.subTest(raw=raw, env=env):
                    actual = settings.load_settings(self.root, env)
                    self.assertEqual(actual["namespace"], self.local["namespace"])
                    self.assertEqual(
                        actual["csv_path"], str(self.root / self.local["csv_path"])
                    )

    def test_stg_ignores_both_topic_configs(self):
        self.write(PRD_CONFIG, self.prd).write_text("{", encoding="utf-8")
        for env in ({"env_profile": "STG"}, {"env_profile": "stg"}):
            self.assertIsNone(settings.load_settings(self.root, env))
            self.assertFalse(settings.topic_enabled(self.root, env))

    def test_prd_without_new_file_retains_existing_installation_behavior(self):
        actual = settings.load_settings(self.root, {"env_profile": "PRD"})
        self.assertEqual(actual["namespace"], self.local["namespace"])
        self.assertEqual(actual["csv_path"], str(self.root / self.local["csv_path"]))

    def test_disabled_prd_never_falls_through_to_enabled_local_topic_file(self):
        self.write(PRD_CONFIG, {**self.prd, "enabled": False})
        self.assertIsNone(settings.load_settings(self.root, {"env_profile": "PRD"}))
        self.assertFalse(settings.topic_enabled(self.root, {"env_profile": "PRD"}))

    def test_invalid_prd_fails_instead_of_using_enabled_local_topic_file(self):
        path = self.write(PRD_CONFIG, self.prd)
        for raw in (
            "{",
            '{"enabled":true,"enabled":false}',
            json.dumps({**self.prd, "namespace": ""}),
            json.dumps({**self.prd, "context_tokens": 0}),
            json.dumps({**self.prd, "unexpected": True}),
        ):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    settings.load_settings(self.root, {"env_profile": "PRD"})
                self.assertFalse(
                    settings.topic_enabled(self.root, {"env_profile": "PRD"})
                )

    def test_prd_symlinks_do_not_select_legacy_fallback(self):
        path = self.root / PRD_CONFIG
        for target in (self.root / settings.CONFIG, self.root / "missing-target"):
            with self.subTest(target=target.name):
                path.symlink_to(target)
                self.assertFalse(
                    settings.topic_enabled(self.root, {"env_profile": "PRD"})
                )
                path.unlink()

    def test_relative_catalog_path_survives_packaging_to_another_root(self):
        self.write(PRD_CONFIG, self.prd)
        source = self.root / self.prd["csv_path"]
        source.write_text("synthetic-rule-directory", encoding="utf-8")
        copied = self.root.parent / "production-release"
        shutil.copytree(self.root, copied)
        actual = settings.load_settings(copied, {"env_profile": "PRD"})
        expected = copied / self.prd["csv_path"]
        self.assertEqual(actual["csv_path"], str(expected))
        self.assertEqual(
            expected.read_text(encoding="utf-8"), "synthetic-rule-directory"
        )
        self.assertNotIn(str(self.root), actual["csv_path"])

    def test_monitor_receives_production_namespace_and_catalog(self):
        self.write(PRD_CONFIG, self.prd)
        env = {"env_profile": "prd"}
        result = monitor.configure_rule_monitor(self.root, env)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["source"], "topic_file")
        self.assertEqual(env["RULE_REFINE_MONITOR_ENABLED"], "1")
        self.assertEqual(env["RULE_REFINE_NAMESPACE"], self.prd["namespace"])
        self.assertEqual(
            env["RULE_REFINE_CSV_PATH"], str(self.root / self.prd["csv_path"])
        )

    def test_explicit_off_wins_over_bad_production_config(self):
        self.write(PRD_CONFIG, self.prd).write_text("{", encoding="utf-8")
        env = {"env_profile": "prd", "RULE_REFINE_MONITOR_ENABLED": "0"}
        before = dict(env)
        result = monitor.configure_rule_monitor(self.root, env)
        self.assertFalse(result["enabled"])
        self.assertEqual(env, before)
        self.assertFalse(settings.topic_enabled(self.root, env))

    def test_conflicting_profiles_do_not_select_any_topic_config(self):
        self.write(PRD_CONFIG, self.prd)
        env = {"env_profile": "PRD", "ENV_PROFILE": "LOCAL"}
        self.assertIsNone(settings.load_settings(self.root, env))
        self.assertFalse(settings.topic_enabled(self.root, env))

    def test_production_settings_do_not_import_business_services(self):
        self.write(PRD_CONFIG, self.prd)
        original = __import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {
                "redis",
                "openai",
                "httpx",
                "sqlalchemy",
                "model",
                "util",
                "root_config",
            }:
                raise AssertionError("service dependency imported")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            self.assertTrue(settings.topic_enabled(self.root, {"env_profile": "PRD"}))


if __name__ == "__main__":
    unittest.main()
