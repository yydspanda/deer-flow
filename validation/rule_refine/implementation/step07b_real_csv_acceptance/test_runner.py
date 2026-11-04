"""Offline command boundary tests; no provider calls or operational writes."""

import io
import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_file("surge_materials", HERE.parent / "step06z_surge_recommendation/materials.py")
import runner  # noqa: E402


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prepared = {
            "analysis": {"model": "configured-model"},
            "budget": SimpleNamespace(context_tokens=262144, input_token_budget=209715),
            "case": {
                "coverage": {
                    "current_alert_count": 2205,
                    "history_selected_unique_alerts": 11,
                }
            },
            "preview": {
                "selection": {"current_count": 30, "history_count": 11},
                "request": {"input_bytes": 123, "estimated_input_tokens": 120},
            },
            "audit": {"source": "real_exports"},
        }

    def call(self, args=(), answer="LIVE"):
        output = io.StringIO()
        with (
            patch.object(runner, "prepare", return_value=self.prepared),
            patch.object(runner, "ask", return_value=answer),
            patch.object(
                runner, "execute", return_value={"status": "received"}
            ) as execute,
            patch("sys.stdout", output),
        ):
            code = runner.main(["--project-root", str(self.root), *args])
        return code, output.getvalue(), execute

    def test_preview_does_not_execute_or_ask_for_live(self):
        code, text, execute = self.call()
        self.assertEqual(code, 0)
        execute.assert_not_called()
        self.assertEqual(json.loads(text)["history_selected"], 11)

    def test_cancel_does_not_execute(self):
        code, _, execute = self.call(["--live"], answer="")
        self.assertEqual(code, 0)
        execute.assert_not_called()

    def test_live_runs_prepared_real_case_once(self):
        code, _, execute = self.call(["--live"])
        self.assertEqual(code, 0)
        execute.assert_called_once_with(self.prepared)

    def test_preparation_failure_never_runs_or_prints_private_error(self):
        with (
            patch.object(runner, "prepare", side_effect=ValueError("private payload")),
            patch.object(runner, "execute") as execute,
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            self.assertEqual(runner.main(["--live"]), 1)
        execute.assert_not_called()
        self.assertNotIn("private payload", output.getvalue())

    def test_terminal_uses_distinct_real_pty_streams_not_stdin(self):
        import os
        import pty
        import threading

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        slave_name = os.ttyname(slave)
        original_open = open

        def tty_open(path, *args, **kwargs):
            if str(path) == "/dev/tty":
                kwargs["opener"] = lambda name, flags: os.open(
                    name, flags | os.O_NOCTTY
                )
                path = slave_name
            return original_open(path, *args, **kwargs)

        sender = threading.Thread(target=lambda: os.write(master, b"LIVE\n"))
        with (
            patch("builtins.open", side_effect=tty_open),
            patch("sys.stdin", io.StringIO("NO\n")),
        ):
            sender.start()
            self.assertEqual(runner.ask("confirm: "), "LIVE")
            sender.join(timeout=2)


class InstalledWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        stage = load_file(
            "real_csv_stage", HERE.parent / "step07_surge_runtime/run_payload_tests.py"
        )
        stage.assemble(self.folder, stage.builder.contents())
        fixtures = load_file("real_csv_material_fixtures", HERE / "test_materials.py")
        self.fixture = fixtures.MaterialsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        from datetime import timedelta

        self.fixture.write_current(
            [
                self.fixture.payload("current-0", self.fixture.cutoff),
                self.fixture.payload(
                    "current-1", self.fixture.cutoff + timedelta(minutes=1)
                ),
            ]
        )
        for i in range(22):
            self.fixture.add_db(
                self.fixture.payload(
                    "h-" + str(i), self.fixture.cutoff - timedelta(days=i + 1)
                )
            )
        load = runner.modules(self.folder)
        case, signal, audit = runner.prepare_case(
            self.fixture.root, self.fixture.current, None, load, self.fixture.rules
        )
        budget = load("surge_budget").ContextBudget()
        self.prepared = {
            "load": load,
            "case": case,
            "signal": signal,
            "audit": audit,
            "analysis": {"model": "offline-model"},
            "budget": budget,
            "rules_factory": lambda: self.fixture.rules,
        }

    def test_full_preflight_uses_configured_budget_without_service_imports(self):
        import shutil

        config = self.folder / "config"
        config.mkdir(exist_ok=True)
        for name, value in (
            ("analysis", {"enabled": True, "model": "configured-model"}),
            (
                "surge",
                {"enabled": True, "context_tokens": 1000000, "input_fraction": 0.8},
            ),
        ):
            (config / f"rule_refine.{name}.local.json").write_text(
                json.dumps(value), encoding="utf-8"
            )
        database = self.folder / "nas/data/sec_know_model.sqlite"
        database.parent.mkdir(parents=True)
        shutil.copyfile(self.fixture.db, database)
        rules = self.folder / "rules.csv"
        rules.write_text("test provider handled separately", encoding="utf-8")
        actual_load = runner.modules(self.folder)

        def load(name):
            if name == "csv_config_source":
                return SimpleNamespace(
                    CsvRuleConfigSource=lambda path: self.fixture.rules
                )
            return actual_load(name)

        original_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name in {"httpx", "openai", "redis", "util.root_config", "nas.config"}:
                raise AssertionError("service_import_before_LIVE")
            return original_import(name, *args, **kwargs)

        before = database.read_bytes()
        with (
            patch.object(runner, "modules", return_value=load),
            patch("builtins.__import__", side_effect=guarded_import),
        ):
            prepared = runner.prepare(
                self.folder,
                {"env_profile": "LOCAL"},
                current_path=self.fixture.current,
                history_path="-",
                rules_path=rules,
            )
        self.assertEqual(prepared["analysis"]["model"], "configured-model")
        self.assertEqual(prepared["budget"].input_token_budget, 800000)
        self.assertEqual(len(prepared["case"]["history_samples"]), 20)
        self.assertIsNone(prepared["preview"]["request"]["model_output_token_limit"])
        self.assertEqual(database.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "explicit_LOCAL_profile_required"):
            runner.prepare(self.folder, {"env_profile": "STG"})

    def test_actual_two_phase_processor_uses_original_history_and_records_usage(self):
        seen = []

        def generate(request):
            seen.append(request)
            context = request["context"]
            self.assertEqual(len(context["history_samples"]), 20)
            self.assertFalse(context["program_filter_evaluation_performed"])
            self.assertNotIn("net1 accounts", json.dumps(context["rule"]))
            self.assertNotIn("PRIVATE_OLD_MODEL_ANSWER", json.dumps(context))
            return {
                "content": json.dumps(
                    {
                        "status": "no_change",
                        "issue": "Offline transport fixture only",
                        "recommendation": "No changes",
                        "certainty": "test",
                        "filter": None,
                        "sample_alert_ids": [],
                        "review_notes": "test",
                        "sample_request": None,
                    }
                ),
                "transport": {
                    "finish_reason": "stop",
                    "error_code": None,
                    "usage": {
                        "prompt_tokens": 123,
                        "completion_tokens": 45,
                        "total_tokens": 168,
                    },
                },
            }

        directory = self.folder / "results"
        outcome = runner.process(self.prepared, directory, generate)
        self.assertEqual(outcome["status"], "not_applicable", outcome)
        self.assertTrue(outcome["ai_review_completed"])
        self.assertEqual([request["phase"] for request in seen], ["generate", "review"])
        self.assertNotIn("draft", seen[0]["context"])
        summary = runner.result_summary(self.prepared, outcome)
        self.assertEqual(
            summary["phases"][0]["transport"]["usage"]["prompt_tokens"], 123
        )
        self.assertEqual(summary["history_count"], 20)
        self.assertFalse(summary["celery_dispatch_tested"])
        runner.process(self.prepared, directory, generate)
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
