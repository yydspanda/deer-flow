"""本机监测配置测试；只操作临时文件，不加载应用或连接外部服务。"""

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_settings_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
module = importlib.import_module(package.__name__ + ".local_settings")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "config/rule_refine.local.json"
        self.path.parent.mkdir()
        self.env = {"env_profile": "STG", "OTHER_SETTING": "keep"}
        self.valid = {"enabled": True, "csv_path": "data/rules.csv", "namespace": "existing-namespace"}

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def configure(self):
        return module.configure_rule_monitor(self.root, self.env)

    def test_missing_file_keeps_default_off_and_other_environment(self):
        result = self.configure()
        self.assertEqual((result["status"], result["enabled"], result["source"]), ("configured", False, "defaults"))
        self.assertEqual((self.env["env_profile"], self.env["OTHER_SETTING"]), ("STG", "keep"))

    def test_file_settings_relative_to_project_and_repeat_namespace_stable(self):
        self.write(self.valid)
        previous = Path.cwd()
        try:
            os.chdir(self.path.parent)
            result = self.configure()
        finally:
            os.chdir(previous)
        self.assertEqual((result["enabled"], result["source"]), (True, "local_file"))
        self.assertEqual(self.env["RULE_REFINE_CSV_PATH"], str(self.root / "data/rules.csv"))
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "existing-namespace")
        self.assertTrue(self.configure()["enabled"])
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "existing-namespace")

    def test_explicit_off_bypasses_broken_file(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.env["RULE_REFINE_MONITOR_ENABLED"] = "0"
        result = self.configure()
        self.assertEqual((result["status"], result["enabled"], result["source"]), ("configured", False, "environment"))

    def test_complete_environment_overrides_broken_file_and_expands_home(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.env.update(
            RULE_REFINE_MONITOR_ENABLED="1",
            RULE_REFINE_CSV_PATH="~/no-read-needed.csv",
            RULE_REFINE_NAMESPACE="env-namespace",
        )
        result = self.configure()
        self.assertEqual((result["enabled"], result["source"]), (True, "environment"))
        self.assertEqual(self.env["RULE_REFINE_CSV_PATH"], str(Path.home() / "no-read-needed.csv"))
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "env-namespace")

    def test_partial_environment_overrides_corresponding_file_field(self):
        self.write(self.valid)
        self.env["RULE_REFINE_NAMESPACE"] = "override-namespace"
        result = self.configure()
        self.assertEqual((result["enabled"], result["source"]), (True, "local_file_and_environment"))
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "override-namespace")
        self.assertEqual(self.env["env_profile"], "STG")

    def test_invalid_json_shape_unknown_and_duplicate_fields_disable_monitor(self):
        for text in ("{broken", "[]", '{"unexpected":1}', '{"enabled":false,"enabled":true}'):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                self.env = {"env_profile": "LOCAL"}
                result = self.configure()
                self.assertEqual((result["status"], result["enabled"]), ("configuration_error", False))
                self.assertEqual(self.env, {"env_profile": "LOCAL", "RULE_REFINE_MONITOR_ENABLED": "0"})

    def test_invalid_enabled_values_or_required_fields_are_not_partially_applied(self):
        changes = [{"enabled": value} for value in (1, "true", None)]
        changes += [{"csv_path": ""}, {"csv_path": []}, {"namespace": " "}, {"namespace": "n" * 513}]
        for change in changes:
            with self.subTest(change=change):
                self.env = {"OTHER_SETTING": "keep"}
                self.write({**self.valid, **change})
                self.assertEqual(self.configure()["status"], "configuration_error")
                self.assertEqual(self.env, {"OTHER_SETTING": "keep", "RULE_REFINE_MONITOR_ENABLED": "0"})

    def test_invalid_environment_switch_or_partial_override_does_not_write_file_values(self):
        self.write(self.valid)
        for changes in ({"RULE_REFINE_MONITOR_ENABLED": "true"}, {"RULE_REFINE_NAMESPACE": ""}):
            self.env = dict(changes)
            self.assertEqual(self.configure()["status"], "configuration_error")
            self.assertEqual(self.env, {**changes, "RULE_REFINE_MONITOR_ENABLED": "0"})

    def test_disabled_file_needs_no_paths_and_loads_no_service_dependencies(self):
        self.write({"enabled": False})
        blocked = {"redis": None, "util.root_config": None, package.__name__ + ".counter": None}
        with patch.dict(sys.modules, blocked):
            self.assertFalse(self.configure()["enabled"])
            self.write(self.valid)
            self.env.clear()
            self.assertTrue(self.configure()["enabled"])

    def test_default_project_root_uses_module_location_not_working_directory(self):
        self.write(self.valid)
        with patch.object(module, "PROJECT_ROOT", self.root):
            result = module.configure_rule_monitor(environ=self.env)
        self.assertEqual((result["status"], result["provider"]), ("configured", "csv_snapshot"))
        self.assertEqual(self.env["RULE_REFINE_CSV_PATH"], str(self.root / "data/rules.csv"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
