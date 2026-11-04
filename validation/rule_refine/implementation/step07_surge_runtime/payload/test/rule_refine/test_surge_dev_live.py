"""LIVE tool preflight tests; no network, real model calls, or business databases."""

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch


HERE = Path(__file__).resolve().parent
STAGE = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "isolated_surge_dev_live", HERE / "check_surge_dev_live.py"
)
LIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIVE)
SPEC = importlib.util.spec_from_file_location(
    "isolated_surge_seed_for_live", HERE / "check_surge_dev_history.py"
)
SEED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEED)


class DevLiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.database = self.root / "nas/data/sec_know_model.sqlite"
        self.database.parent.mkdir(parents=True)
        with sqlite3.connect(self.database) as db:
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status (id TEXT PRIMARY KEY, app_code TEXT NOT NULL, flow_id TEXT NOT NULL, alert_id TEXT NOT NULL, alert_data TEXT NOT NULL, status TEXT NOT NULL, failed_count INTEGER NOT NULL DEFAULT 0, agent_response TEXT, created_by TEXT, updated_by TEXT, created_date TEXT, updated_date TEXT)"
            )
        shutil.copytree(
            STAGE / "my_workflows/zeus/rule_refine",
            self.root / "my_workflows/zeus/rule_refine",
        )
        tool = self.root / "test/rule_refine/check_surge_dev_history.py"
        tool.parent.mkdir(parents=True)
        shutil.copyfile(HERE / tool.name, tool)
        self.config = self.root / "config"
        self.config.mkdir()
        self.write_settings()
        SEED.run(self.root, environ={"env_profile": "LOCAL"})
        self.old_modules = set(sys.modules)
        self.addCleanup(self.clean_modules)

    def clean_modules(self):
        for name in set(sys.modules) - self.old_modules:
            if name.startswith(("_installed_surge_acceptance", "_surge_seed")):
                sys.modules.pop(name, None)

    def write_settings(self, *, model="Pub_DeepSeek_V4_Flash", **surge):
        (self.config / "rule_refine.analysis.local.json").write_text(
            json.dumps({"enabled": True, "model": model}), encoding="utf-8"
        )
        (self.config / "rule_refine.surge.local.json").write_text(
            json.dumps({"enabled": True, **surge}), encoding="utf-8"
        )

    def prepare(self):
        return LIVE.prepare(self.root, {"env_profile": "LOCAL"})

    def run_main(self, *args):
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"env_profile": "LOCAL"}, clear=True),
            patch("sys.stdout", output),
        ):
            code = LIVE.main(["--project-root", str(self.root), *args])
        return code, output.getvalue()

    def test_no_live_preflight_does_not_execute_or_write_database(self):
        before = self.database.read_bytes()
        with patch.object(LIVE, "execute") as execute:
            code, text = self.run_main()
        self.assertEqual(code, 0)
        execute.assert_not_called()
        report = json.loads(text)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["model_calls_this_command"], 0)
        self.assertEqual(report["material_source"], "synthetic_dev_fixture")
        self.assertEqual(report["history_available"], 20)
        self.assertEqual(self.database.read_bytes(), before)

    def test_prepare_does_not_import_business_configuration_or_service_clients(self):
        original_import = __import__
        forbidden = {
            "httpx",
            "openai",
            "redis",
            "util.root_config",
            "nas.config",
            "config.model_limits",
            "config.circuit_breaker",
            "util.model_semaphore",
        }

        def checked_import(name, *args, **kwargs):
            if name in forbidden or any(
                name.startswith(prefix + ".") for prefix in forbidden
            ):
                raise AssertionError("business_import_before_live")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=checked_import):
            prepared = self.prepare()
        self.assertEqual(len(prepared["case"]["history_samples"]), 20)

    def test_actual_recalled_history_and_current_logs_enter_model_messages(self):
        prepared = self.prepare()
        context = json.loads(prepared["preview"]["request"]["messages"][1]["content"])
        history = context["history_samples"]
        self.assertEqual(len(history), 20)
        self.assertEqual(
            [row["alert_id"] for row in history],
            [SEED.ALERT_PREFIX + f"history-{index:02}" for index in range(20)],
        )
        self.assertEqual(
            {row["alert_id"] for row in context["current_samples"]},
            {SEED.ALERT_PREFIX + f"current-{index:02}" for index in (0, 2, 4)},
        )
        self.assertEqual(history, prepared["preview"]["history_samples"])
        self.assertEqual(
            {
                log["event_content"]
                for row in context["current_samples"]
                for log in row["raw_logs"]
            },
            {"inventory-probe --collect versions"},
        )
        self.assertEqual(
            len({log["event_content"] for row in history for log in row["raw_logs"]}), 4
        )
        self.assertEqual(context["assessment_mode"], "independent_initial_generation")
        self.assertNotIn("draft", context)
        self.assertFalse(context["program_filter_evaluation_performed"])
        self.assertIn("人工DEV", context["operator_context"])

    def test_prior_model_responses_are_not_copied_to_messages(self):
        with sqlite3.connect(self.database) as db:
            db.execute(
                "UPDATE uas_app_workflow_task_status SET agent_response='PRIVATE_OLD_MODEL_ANSWER'"
            )
        # The source never reads an agent answer. Some preflights reject edited
        # fixtures entirely; either route must not pass that answer to a model.
        try:
            prepared = self.prepare()
        except ValueError:
            return
        text = json.dumps(prepared["preview"]["request"]["messages"])
        self.assertNotIn("PRIVATE_OLD_MODEL_ANSWER", text)

    def test_runtime_model_and_operator_context_budget_are_read_from_settings(self):
        self.write_settings(model="Different_Local_Model", context_tokens=1000000)
        prepared = self.prepare()
        self.assertEqual(prepared["analysis"]["model"], "Different_Local_Model")
        self.assertEqual(prepared["budget"].context_tokens, 1000000)
        self.assertEqual(prepared["budget"].input_token_budget, 800000)
        self.assertIsNone(prepared["preview"]["request"]["model_output_token_limit"])

    def test_live_cancel_does_not_execute(self):
        terminal = mock_open(read_data="NO\n")
        original_open = open

        def open_only_tty(path, *args, **kwargs):
            return (
                terminal(path, *args, **kwargs)
                if str(path) == "/dev/tty"
                else original_open(path, *args, **kwargs)
            )

        with (
            patch("builtins.open", side_effect=open_only_tty),
            patch.object(LIVE, "execute") as execute,
        ):
            code, _ = self.run_main("--live")
        self.assertEqual(code, 0)
        execute.assert_not_called()

    def test_live_confirmation_invokes_execute_once_with_prepared_history(self):
        terminal = mock_open(read_data="LIVE\n")
        original_open = open

        def open_only_tty(path, *args, **kwargs):
            return (
                terminal(path, *args, **kwargs)
                if str(path) == "/dev/tty"
                else original_open(path, *args, **kwargs)
            )

        with (
            patch("builtins.open", side_effect=open_only_tty),
            patch.object(
                LIVE, "execute", return_value={"status": "received"}
            ) as execute,
        ):
            code, _ = self.run_main("--live")
        self.assertEqual(code, 0)
        execute.assert_called_once()
        self.assertEqual(len(execute.call_args.args[0]["case"]["history_samples"]), 20)

    def test_disabled_or_invalid_configuration_never_invokes_execute(self):
        for value in ({"enabled": False}, {"enabled": "yes"}, {"context_tokens": 1}):
            self.write_settings(**value)
            with patch.object(LIVE, "execute") as execute:
                code, _ = self.run_main("--live")
            self.assertEqual(code, 1)
            execute.assert_not_called()

    def test_analysis_disabled_never_invokes_execute(self):
        (self.config / "rule_refine.analysis.local.json").write_text(
            '{"enabled":false}', encoding="utf-8"
        )
        with patch.object(LIVE, "execute") as execute:
            code, _ = self.run_main("--live")
        self.assertEqual(code, 1)
        execute.assert_not_called()

    def test_nonlocal_or_missing_database_never_invokes_execute(self):
        with self.assertRaisesRegex(ValueError, "explicit_LOCAL_profile_required"):
            LIVE.prepare(self.root, {"env_profile": "STG"})
        self.database.unlink()
        with patch.object(LIVE, "execute") as execute:
            code, _ = self.run_main("--live")
        self.assertEqual(code, 1)
        execute.assert_not_called()
        self.assertFalse(self.database.exists())

    def test_missing_seed_stops_before_execute(self):
        with sqlite3.connect(self.database) as db:
            db.execute("DELETE FROM uas_app_workflow_task_status")
        with patch.object(LIVE, "execute") as execute:
            code, _ = self.run_main("--live")
        self.assertEqual(code, 1)
        execute.assert_not_called()

    def test_changed_fixture_content_cannot_keep_the_trusted_synthetic_premise(self):
        row = next(
            row
            for row in SEED.fixture_rows()
            if row["alert_id"] == SEED.ALERT_PREFIX + "current-00"
        )
        payload = json.loads(row["alert_data"])
        payload["alert"]["hitLog"][0]["zeusRawLogs"][0]["event_content"] = (
            "different-real-command"
        )
        with sqlite3.connect(self.database) as db:
            db.execute(
                "UPDATE uas_app_workflow_task_status SET alert_data=? WHERE id=?",
                (json.dumps(payload), row["id"]),
            )
        with patch.object(LIVE, "execute") as execute:
            code, _ = self.run_main("--live")
        self.assertEqual(code, 1)
        execute.assert_not_called()

    def test_module_loader_uses_the_requested_project_each_time(self):
        second = self.root.parent / "second"
        shutil.copytree(self.root, second)
        _, first_load = LIVE.modules(self.root)
        _, second_load = LIVE.modules(second)
        self.assertEqual(
            Path(first_load("surge_settings").__file__).resolve(),
            self.root / "my_workflows/zeus/rule_refine/surge_settings.py",
        )
        self.assertEqual(
            Path(second_load("surge_settings").__file__).resolve(),
            second / "my_workflows/zeus/rule_refine/surge_settings.py",
        )


if __name__ == "__main__":
    unittest.main()
