import copy
import hashlib
import io
import json
import os
import pickle
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

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
        "condition_support",
        "budget_preview",
        "support_runner",
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
    pure = loaded["diagnostic_runner"].load_pure(packet["sources"], pins)
    return types.SimpleNamespace(**loaded, packet=packet, pins=pins, pure=pure)


class SupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = load_tools()

    def fixture(
        self, logs, *, field="field", value="value", operator="equal", second=None
    ):
        samples = [{"alert_id": "private-1", "raw_logs": logs}]
        if second is not None:
            samples.append({"alert_id": "private-2", "raw_logs": second})
        context = {
            "material_source": "historical_export",
            "observed_surge": False,
            "rate_window": None,
            "rule": {"topic": "demo"},
            "samples": samples,
        }
        draft = self.t.pure.analysis.AnalysisDraft.model_validate(
            {
                "status": "proposal",
                "issue": "私密描述",
                "recommendation": "私密建议",
                "certainty": "待确认",
                "sample_alert_ids": ["private-1"],
                "filter": {
                    "topic": "demo",
                    "expression": "A",
                    "A": {"name": field, "value": value, "operator": operator},
                },
            }
        )
        return context, draft

    def explain(self, *args, **kwargs):
        context, draft = self.fixture(*args, **kwargs)
        before = copy.deepcopy(context)
        result = self.t.condition_support.explain(context, draft, self.t.pure.analysis)
        self.assertEqual(context, before)
        return result["conditions"][0]

    def test_numeric_field_is_distinguished_without_coercion(self):
        row = self.explain([{"port": 443}], field="port", value="443")
        self.assertEqual(row["reason"], "field_type_not_supported")
        self.assertEqual(row["cited_samples"]["value_types"], {"number": 1})

    def test_missing_field_does_not_support_notcontain(self):
        row = self.explain([{"other": "x"}], operator="notcontain")
        self.assertEqual(row["reason"], "field_missing_at_top_level")
        self.assertFalse(row["supported"])

    def test_string_value_mismatch_is_distinct(self):
        row = self.explain([{"field": "not-equal"}])
        self.assertEqual(row["reason"], "string_value_does_not_match_operator")

    def test_uncited_visible_support_does_not_repair_citations(self):
        row = self.explain([{"field": "no"}], second=[{"field": "value"}])
        self.assertFalse(row["supported"])
        self.assertEqual(row["reason"], "supported_only_in_uncited_visible_samples")

    def test_nested_field_is_only_a_hint(self):
        row = self.explain([{"wrapper": {"field": "value"}}])
        self.assertFalse(row["supported"])
        self.assertEqual(row["structural_hints"]["nested_matching_strings"], 1)
        self.assertFalse(row["structural_hints"]["hints_used_to_accept_condition"])

    def test_case_difference_is_only_a_hint(self):
        row = self.explain([{"FIELD": "value"}])
        self.assertEqual(row["reason"], "field_missing_at_top_level")
        self.assertEqual(row["structural_hints"]["different_case_matching_strings"], 1)

    def test_stringified_json_is_not_flattened_into_usable_fields(self):
        row = self.explain([{"raw": '{"field":"value"}'}])
        self.assertEqual(row["structural_hints"]["nested_matching_strings"], 0)
        self.assertFalse(row["supported"])

    def test_output_contains_no_ids_fields_values_or_advice(self):
        row = self.explain(
            [{"PRIVATE_FIELD": "PRIVATE_VALUE"}],
            field="PRIVATE_FIELD",
            value="PRIVATE_VALUE",
        )
        self.assertTrue(row["supported"])
        text = json.dumps(row, ensure_ascii=False)
        for private in ("PRIVATE_FIELD", "PRIVATE_VALUE", "private-1", "私密"):
            self.assertNotIn(private, text)

    def test_boolean_array_null_are_distinct(self):
        row = self.explain([{"field": True}, {"field": ["value"]}, {"field": None}])
        self.assertEqual(
            row["cited_samples"]["value_types"], {"array": 1, "boolean": 1, "null": 1}
        )
        self.assertFalse(row["supported"])

    def test_reply_change_after_hash_check_is_not_diagnosed_as_original(self):
        material, _ = self.fixture([{"field": "value"}])
        checked = {
            "saved_result_reproduced": True,
            "response_sha256": hashlib.sha256(b"old").hexdigest(),
        }
        with (
            patch.object(sys, "argv", ["tool", "/unused", "128"]),
            patch.object(
                self.t.diagnostic_runner,
                "saved_response_check",
                return_value=(checked, material, self.t.pure),
            ),
            patch.object(self.t.inventory, "bounded_read", return_value=b"changed"),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(self.t.support_runner.main(self.t.packet), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(
            result["failure"]["error_code"], "saved_response_hash_mismatch"
        )
        self.assertNotIn("condition_support", result)

    def test_complete_command_preserves_original_and_makes_no_network_calls(self):
        command = build_command.build()
        source = command.split("<<'PY_HISTORICAL_CONDITIONS'\n", 1)[1].rsplit(
            "\nPY_HISTORICAL_CONDITIONS", 1
        )[0]
        material, draft = self.fixture([{"port": 443}], field="port", value="443")
        answer = draft.model_dump_json()
        result = self.t.historical_review.analyze(
            material,
            lambda _: answer,
            analysis=self.t.pure.analysis,
            evaluator=self.t.pure.filter_evaluation.evaluate_filter,
            save=lambda *args: None,
        )
        guard = """import sys
def block(event, args):
    if event.startswith("socket.connect"):
        raise AssertionError("network forbidden")
    if event == "import" and args[0].split(".")[0] in {"redis", "openai", "sqlalchemy", "my_workflows", "tasks", "util"}:
        raise AssertionError("business import forbidden")
sys.addaudithook(block)
"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "saved"
            folder.mkdir()
            values = {
                "source-pins.json": self.t.pins,
                "context.json": material,
                "analysis.json": result,
                "request.json": self.t.historical_review.build_request(
                    material, self.t.pure.analysis
                ),
            }
            for name, value in values.items():
                (folder / name).write_text(json.dumps(value), encoding="utf-8")
            (folder / "assistant-content.txt").write_text(answer, encoding="utf-8")
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            out = subprocess.run(
                [sys.executable, "-B", "-c", guard + source, str(folder), "128"],
                cwd=temp,
                env={**os.environ, "HOME": temp},
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
            summary = json.loads(out.stdout)
            self.assertEqual(
                summary["condition_support"]["conditions"][0]["reason"],
                "field_type_not_supported",
            )
            self.assertEqual(summary["model_calls"], 0)
            self.assertEqual(summary["budget_preview"]["status"], "unavailable")
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            self.assertNotIn("private-1", out.stdout)
            file = Path(summary["output_dir"]) / "diagnostic.json"
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_full_preview_wrapper_with_restricted_synthetic_pickle(self):
        import pandas as pd

        material = self.t.historical_materials
        rules = types.SimpleNamespace(
            file_sha256=material.CSV_SHA,
            get_rule=lambda *a, **kw: {
                "company_code": "TEST",
                "topic": material.TARGET[0],
                "rule_code": material.TARGET[1],
                "rule_name": "人工规则",
                "rule_config_version": "test",
                "source": {},
                "configuration": {},
            },
        )
        rows = [
            {
                "alert": {
                    "alertId": str(i),
                    "hitLog": [
                        {
                            "topic": material.TARGET[0],
                            "ruleCode": material.TARGET[1],
                            "zeusRawLogs": [{"body": "x" * size}],
                        }
                    ],
                }
            }
            for i, size in enumerate((6000, 10000))
        ]
        frame = pd.DataFrame(
            [[row] for row in rows], columns=pd.Index(["alert_full_data"], dtype=object)
        )
        raw = pickle.dumps(frame, protocol=4)
        original = material.build_context(frame, rules, self.t.inventory)
        real_digest = self.t.diagnostic_runner.digest

        def digest(value):
            if value == raw:
                return material.SAMPLE_SHA
            if value == b"synthetic-csv":
                return material.CSV_SHA
            return real_digest(value)

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(self.t.diagnostic_runner, "digest", side_effect=digest),
            patch.object(
                self.t.pure.csv_config_source, "CsvRuleConfigSource", return_value=rules
            ),
        ):
            root = Path(temp)
            (root / "nas/original").mkdir(parents=True)
            (root / "nas/original" / material.SAMPLE).write_bytes(raw)
            csv = root / "rules.csv"
            csv.write_bytes(b"synthetic-csv")
            new, request, summary = self.t.support_runner.preview(
                root,
                csv,
                original,
                self.t.pure,
                self.t.packet["files"]["historical_materials"],
                128,
            )
            self.assertEqual(summary["old"]["included_alerts"], 1)
            self.assertEqual(summary["new"]["included_alerts"], 2)
            self.assertEqual(summary["new"]["invalid_logs"], 0)
            self.assertEqual(new["samples"][1]["raw_logs"][0]["body"], "x" * 10000)
            self.assertEqual(
                request["input_bytes"], summary["new"]["actual_input_bytes"]
            )
            self.assertLessEqual(request["input_bytes"], 128 * 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)
