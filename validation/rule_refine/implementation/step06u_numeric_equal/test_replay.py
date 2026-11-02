import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

import build_command


def load_tools():
    packet = build_command.contents()
    loaded = {}
    for name in (
        "inventory",
        "historical_materials",
        "historical_review",
        "historical_checks",
        "log_audit",
        "diagnostic_runner",
        "numeric_changes",
        "numeric_runner",
    ):
        module = types.ModuleType(name)
        sys.modules[name] = module
        exec(compile(packet["files"][name], name, "exec"), module.__dict__)
        loaded[name] = module
    pins = {
        "my_workflows/zeus/rule_refine/" + name + ".py": hashlib.sha256(
            source.encode()
        ).hexdigest()
        for name, source in packet["sources"].items()
    }
    old = loaded["diagnostic_runner"].load_pure(packet["sources"], pins)
    updated = loaded["numeric_changes"].patched_sources(
        packet["sources"], pins, packet["comparator_source"]
    )
    new = loaded["numeric_runner"].load_updated(updated)
    return types.SimpleNamespace(
        **loaded, packet=packet, pins=pins, old=old, new=new, updated=updated
    )


def material():
    return {
        "material_source": "historical_export",
        "observed_surge": False,
        "rate_window": None,
        "rule": {"topic": "demo", "rule_code": "DEMO", "rule_name": "人工规则"},
        "samples": [
            {"alert_id": "private-one", "raw_logs": [{"kind": "probe", "port": 443}]},
            {"alert_id": "private-two", "raw_logs": [{"kind": "browser", "port": 80}]},
        ],
    }


def answer():
    return {
        "status": "proposal",
        "issue": "人工测试问题",
        "recommendation": "运营确认后调整",
        "certainty": "尚待确认",
        "sample_alert_ids": ["private-one"],
        "filter": {
            "topic": "demo",
            "expression": "A && B",
            "A": {"name": "kind", "operator": "equal", "value": "probe"},
            "B": {"name": "port", "operator": "equal", "value": "443"},
        },
    }


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = load_tools()

    def analyze(self, modules, context=None, value=None):
        return self.t.historical_review.analyze(
            context or material(),
            lambda _: json.dumps(value or answer()),
            analysis=modules.analysis,
            evaluator=modules.filter_evaluation.evaluate_filter,
            save=lambda *args: None,
            response_source="saved_model_response",
        )

    def test_single_condition_and_filter_share_one_comparator(self):
        self.assertIs(
            self.t.new.analysis.evaluate_condition,
            self.t.new.filter_evaluation.evaluate_condition,
        )
        self.assertIs(
            self.t.new.analysis.evaluate_condition,
            self.t.new.condition_values.evaluate_condition,
        )

    def test_numeric_equal_passes_only_new_local_semantics(self):
        old = self.analyze(self.t.old)
        new = self.analyze(self.t.new)
        self.assertFalse(old["format_valid"])
        self.assertTrue(new["format_valid"])
        self.assertEqual(
            new["evaluation"]["counts"], {"matched": 1, "not_matched": 1, "unknown": 0}
        )
        self.assertEqual(old["prompt_sha256"], new["prompt_sha256"])
        self.assertEqual(self.t.old.analysis.SYSTEM, self.t.new.analysis.SYSTEM)
        self.assertEqual(new["engine_compatibility"], "not_verified")

    def test_value_mismatch_is_still_blocked(self):
        value = answer()
        value["filter"]["B"]["value"] = "444"
        result = self.analyze(self.t.new, value=value)
        self.assertFalse(result["format_valid"])
        self.assertEqual(result["error_code"], "invalid_model_output")

    def test_combination_cannot_match_across_logs(self):
        context = material()
        context["samples"][0]["raw_logs"] = [
            {"kind": "probe", "port": 80},
            {"kind": "browser", "port": 443},
        ]
        result = self.analyze(self.t.new, context=context)
        self.assertFalse(result["format_valid"])
        draft = self.t.new.analysis.AnalysisDraft.model_validate(answer())
        for condition in draft.filter.__pydantic_extra__.values():
            self.assertTrue(
                self.t.new.analysis._supported(
                    condition, context["samples"][0]["raw_logs"]
                )
            )

    def test_every_cited_alert_must_match_not_merely_one(self):
        value = answer()
        value["sample_alert_ids"].append("private-two")
        self.assertFalse(self.analyze(self.t.new, value=value)["format_valid"])

    def test_string_wire_contract_and_missing_negative_stay_strict(self):
        value = answer()
        value["filter"]["B"]["value"] = 443
        self.assertFalse(self.analyze(self.t.new, value=value)["format_valid"])
        value = answer()
        value["filter"]["B"] = {
            "name": "missing",
            "operator": "notcontain",
            "value": "attack",
        }
        self.assertFalse(self.analyze(self.t.new, value=value)["format_valid"])

    def test_original_sources_unchanged_and_unknown_version_rejected(self):
        sources = copy.deepcopy(self.t.packet["sources"])
        snapshot = copy.deepcopy(sources)
        self.t.numeric_changes.patched_sources(
            sources, self.t.pins, self.t.packet["comparator_source"]
        )
        self.assertEqual(sources, snapshot)
        sources["filter_evaluation"] += "\nchanged = True\n"
        with self.assertRaisesRegex(ValueError, "recorded_source_version_mismatch"):
            self.t.numeric_changes.patched_sources(
                sources, self.t.pins, self.t.packet["comparator_source"]
            )

    def fixture(self, folder):
        values = {
            "source-pins.json": self.t.pins,
            "context.json": material(),
            "analysis.json": self.analyze(self.t.old),
            "request.json": self.t.historical_review.build_request(
                material(), self.t.old.analysis
            ),
        }
        for name, value in values.items():
            (folder / name).write_text(json.dumps(value), encoding="utf-8")
        (folder / "assistant-content.txt").write_text(
            json.dumps(answer()), encoding="utf-8"
        )

    def test_complete_copy_command_offline_private_files_and_preserved_old_report(self):
        command = build_command.build()
        source = command.split("<<'PY_NUMERIC_EQUAL'\n", 1)[1].rsplit(
            "\nPY_NUMERIC_EQUAL", 1
        )[0]
        guard = """import sys
def block(event, args):
    if event.startswith("socket.connect"):
        raise AssertionError("network forbidden")
    if event == "import" and args[0].split(".")[0] in {"redis", "openai", "sqlalchemy", "my_workflows", "util", "tasks"}:
        raise AssertionError("business import forbidden")
sys.addaudithook(block)
"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "old"
            folder.mkdir()
            self.fixture(folder)
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            out = subprocess.run(
                [sys.executable, "-B", "-c", guard + source, str(folder)],
                cwd=temp,
                env={**os.environ, "HOME": temp},
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
            result = json.loads(out.stdout)
            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["old_format_valid"])
            self.assertTrue(result["new_format_valid"])
            self.assertFalse(result["preview_materials_used"])
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["conditions"][1]["matching_logs"], 1)
            self.assertFalse(result["conditions"][1]["old_supported"])
            self.assertTrue(result["conditions"][1]["new_supported"])
            self.assertNotIn("private-one", out.stdout)
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            output = Path(result["output_dir"])
            self.assertTrue((output / "REPORT.md").is_file())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertTrue(
                all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
            )

    def test_changed_saved_answer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            (folder / "assistant-content.txt").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "saved_response_hash_mismatch"):
                self.t.numeric_runner.replay(folder, self.t.packet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
