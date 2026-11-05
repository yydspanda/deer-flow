"""Retain manual boundaries and explicitly opt in to the condition-tree contract."""

import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import build_command as builder

legacy = builder.load(
    "topic_tree_original_manual_tests", builder.ORIGINAL / "test_manual_runner.py"
)
runner = legacy.load_file(
    "topic_tree_manual_runner_tested", builder.HERE / "manual_runner.py"
)
legacy.runner = runner
RecallAdapterTests = legacy.RecallAdapterTests
StaticTargetTests = legacy.StaticTargetTests


class ManualCommandTests(legacy.ManualCommandTests):
    def test_preview_explicitly_identifies_tree_contract(self):
        _, output, ask, execute = self.call()
        self.assertEqual(json.loads(output)["ai_contract"], "topic-condition-tree-v1")
        ask.assert_not_called()
        execute.assert_not_called()

    def test_process_explicitly_selects_tree_contract(self):
        captured = {}

        class Processor:
            def __init__(self, directory, **options):
                captured.update(options)

            def process(self, signal):
                return {"status": "received"}

        self.prepared["load"] = lambda _: types.SimpleNamespace(
            TopicProcessor=Processor
        )
        runner.process(
            self.prepared,
            Path("new/manual"),
            Mock(),
            Mock(),
            delivery_directory=Path("stable/deliveries"),
        )
        self.assertEqual(captured["ai_contract"], "topic-condition-tree-v1")

    def test_preview_selection_uses_tree_request_builder(self):
        self.prepared["case"]["coverage"] = {}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "util").mkdir()
            (root / "util/root_config.py").write_text(
                "# synthetic target\n", encoding="utf-8"
            )
            csv = root / "rules.csv"
            csv.write_text("synthetic\n", encoding="utf-8")
            request_builder = Mock()
            select = Mock(return_value=self.prepared["preview"])
            available = {
                "topic_settings": types.SimpleNamespace(
                    CONFIG="config/rule_refine.topic.json",
                    profile=lambda env: "LOCAL",
                    load_settings=lambda *args: None,
                    DEFAULTS={"model": "synthetic-model", "operator_context": ""},
                ),
                "csv_config_source": types.SimpleNamespace(CsvRuleConfigSource=Mock()),
                "surge_runtime": types.SimpleNamespace(
                    budget_from_settings=lambda _: self.prepared["budget"]
                ),
                "surge_sampling": types.SimpleNamespace(select=select),
                "topic_tree_ai": types.SimpleNamespace(build_request=request_builder),
            }
            with (
                patch.object(
                    runner, "modules", return_value=lambda name: available[name]
                ),
                patch.object(
                    runner,
                    "static_zeus_target",
                    return_value=self.prepared["zeus_target"],
                ),
                patch.object(
                    runner,
                    "prepare_case",
                    return_value=(
                        copy.deepcopy(self.prepared["case"]),
                        dict(self.prepared["signal"]),
                        {},
                    ),
                ),
            ):
                runner.prepare(
                    root,
                    {"env_profile": "LOCAL"},
                    current_path=csv,
                    history_path=None,
                    rules_path=csv,
                )
            self.assertIs(select.call_args.kwargs["request_builder"], request_builder)


class TreeManualFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="topic-tree-manual-runtime-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        files = builder.baseline()
        files.update(
            {
                name: entry["content"].encode("utf-8")
                for name, entry in builder.contents()["files"].items()
            }
        )
        for name, raw in files.items():
            path = cls.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        cls.fixtures = legacy.load_file(
            "topic_tree_manual_synthetic_fixtures",
            cls.root / "test/rule_refine/test_topic_processor.py",
        )

    def test_actual_processor_uses_tree_calls_and_flat_submission(self):
        load = runner.modules(self.root)
        fixture = self.fixtures
        calls = []

        def generate(request):
            calls.append(request)
            answer = fixture.reply(request)
            answer["filter"] = {
                "topic": "synthetic-topic",
                "match": {
                    "name": "action",
                    "operator": "Eq",
                    "value": "inventory-versions",
                    "field_role": "event_field",
                    "basis": "Original event action fact",
                },
            }
            del answer["topic_review"]["field_roles"]
            return {
                "content": json.dumps(answer),
                "transport": {
                    "finish_reason": "stop",
                    "error_code": None,
                    "sdk_request_calls": 1,
                },
            }

        submit = Mock(return_value={"success": True, "code": 200, "rule_id": 123})
        prepared = {
            "load": load,
            "case": fixture.case(),
            "signal": fixture.signal(),
            "rules_factory": lambda: Mock(get_rule=Mock(return_value=fixture.rule())),
            "model": "synthetic-model",
            "budget": load("surge_budget").ContextBudget(),
            "operator_context": "Synthetic only",
            "namespace": "manual-tree-fixture",
        }
        before = copy.deepcopy(prepared["case"])
        result = runner.process(
            prepared,
            self.root / "manual-tree-case",
            generate,
            submit,
            delivery_directory=self.root / "deliveries",
        )
        self.assertEqual(result["status"], "received")
        self.assertEqual(len(calls), 2)
        for request in calls:
            self.assertIn("match", request["messages"][0]["content"])
        submit.assert_called_once()
        payload = submit.call_args.args[0]
        self.assertEqual(set(payload), {"topic", "A", "expression", "executeType"})
        self.assertEqual(payload["executeType"], [3])
        self.assertEqual(
            payload["A"],
            {"name": "action", "operator": "Eq", "value": "inventory-versions"},
        )
        self.assertEqual(payload["expression"], "A")
        self.assertEqual(prepared["case"], before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
