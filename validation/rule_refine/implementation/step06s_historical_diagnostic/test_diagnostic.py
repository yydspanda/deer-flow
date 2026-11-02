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


def tools():
    packet = build_command.contents()
    loaded = {}
    for name in (
        "inventory",
        "historical_materials",
        "historical_review",
        "historical_checks",
        "log_audit",
        "diagnostic_runner",
    ):
        mod = types.ModuleType(name)
        sys.modules[name] = mod
        exec(compile(packet["files"][name], name, "exec"), mod.__dict__)
        loaded[name] = mod
    pins = {
        "my_workflows/zeus/rule_refine/" + name + ".py": hashlib.sha256(
            source.encode()
        ).hexdigest()
        for name, source in packet["sources"].items()
    }
    pure = loaded["diagnostic_runner"].load_pure(packet["sources"], pins)
    return types.SimpleNamespace(**loaded, pure=pure, packet=packet, pins=pins)


def context():
    return {
        "material_source": "historical_export",
        "observed_surge": False,
        "rate_window": None,
        "rule": {"topic": "demo"},
        "samples": [
            {
                "alert_id": "private-id-one",
                "raw_logs": [{"ua": "probe/1", "path": "/health"}],
            },
            {
                "alert_id": "private-id-two",
                "raw_logs": [{"ua": "browser/1", "path": "/login"}],
            },
        ],
    }


def draft():
    return {
        "status": "proposal",
        "issue": "人工测试，不要导出这段私密描述",
        "recommendation": "运营确认后再调整",
        "certainty": "尚待确认",
        "sample_alert_ids": ["private-id-one"],
        "filter": {
            "topic": "demo",
            "expression": "A && B",
            "A": {"name": "ua", "operator": "contain", "value": "probe"},
            "B": {"name": "path", "operator": "equal", "value": "/health"},
        },
    }


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = tools()

    def diagnose(self, value=None, material=None, raw=None):
        return self.t.historical_checks.diagnose(
            material or context(),
            raw
            if raw is not None
            else json.dumps(value or draft(), ensure_ascii=False),
            analysis=self.t.pure.analysis,
            evaluator=self.t.pure.filter_evaluation.evaluate_filter,
            review=self.t.historical_review,
        )

    def test_complete_fenced_json_retains_current_parser_semantics(self):
        raw = "```json\n" + json.dumps(draft()) + "\n```"
        result = self.diagnose(raw=raw)
        self.assertTrue(result["format_valid"])
        self.assertTrue(result["original_validator_agrees"])
        self.assertEqual(result["validation_stage"], "complete")
        self.assertNotIn("private-id", json.dumps(result))

    def test_missing_fields_are_named_but_private_values_are_not_exported(self):
        value = draft()
        del value["certainty"]
        value["SECRET-EXTRA-FIELD"] = "SECRET-EXTRA-VALUE"
        result = self.diagnose(value)
        self.assertEqual(result["validation_stage"], "draft_schema")
        self.assertIn(
            {"type": "missing", "loc": ["certainty"]},
            result["validation_error"]["errors"],
        )
        encoded = json.dumps(result)
        self.assertNotIn("SECRET", encoded)
        self.assertNotIn("private-id", encoded)
        self.assertNotIn("私密", encoded)

    def test_json_errors_do_not_echo_reply(self):
        for raw in (
            "SECRET-NOT-JSON",
            '{"private-secret":',
            '{"status":NaN}',
            '{"status":0,"status":1}',
        ):
            with self.subTest(raw=raw):
                result = self.diagnose(raw=raw)
                self.assertEqual(result["validation_stage"], "json_parse")
                self.assertNotIn("SECRET", json.dumps(result))
                self.assertNotIn("private-secret", json.dumps(result))
                self.assertTrue(result["original_validator_agrees"])

    def test_references_topic_and_individual_condition_stages(self):
        for stage in ("sample_references", "topic", "individual_conditions"):
            value = draft()
            if stage == "sample_references":
                value["sample_alert_ids"] = ["SECRET-NOT-SHOWN"]
            elif stage == "topic":
                value["filter"]["topic"] = "SECRET-OTHER-TOPIC"
            else:
                value["filter"]["A"]["value"] = "SECRET-NOT-PRESENT"
            result = self.diagnose(value)
            self.assertEqual(result["validation_stage"], stage)
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertTrue(result["original_validator_agrees"])

    def test_cross_log_and_remains_rejected(self):
        material = context()
        material["samples"][0]["raw_logs"] = [
            {"ua": "probe/1", "path": "/other"},
            {"ua": "browser/1", "path": "/health"},
        ]
        result = self.diagnose(material=material)
        self.assertEqual(result["validation_stage"], "combined_filter")
        self.assertEqual(
            result["validation_error"]["reason"], "cited_sample_not_matched"
        )

    def test_non_proposals_are_valid_and_not_relabelled_as_proposals(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            value = {
                **draft(),
                "status": status,
                "sample_alert_ids": [],
                "filter": None,
            }
            result = self.diagnose(value)
            self.assertTrue(result["format_valid"])
            self.assertEqual(result["analysis_status"], status)

    def fixture(self, folder):
        material = context()
        raw = json.dumps({**draft(), "sample_alert_ids": ["not-shown-secret-id"]})
        original = self.t.historical_review.analyze(
            material,
            lambda _: raw,
            analysis=self.t.pure.analysis,
            evaluator=self.t.pure.filter_evaluation.evaluate_filter,
            save=lambda *args: None,
        )
        values = {
            "context.json": material,
            "request.json": self.t.historical_review.build_request(
                material, self.t.pure.analysis
            ),
            "analysis.json": original,
            "source-pins.json": self.t.pins,
        }
        for name, value in values.items():
            (folder / name).write_text(json.dumps(value), encoding="utf-8")
        (folder / "assistant-content.txt").write_text(raw, encoding="utf-8")

    def test_saved_response_prompt_and_source_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            result, _, _ = self.t.diagnostic_runner.saved_response_check(
                folder, self.t.packet["sources"]
            )
            self.assertTrue(result["saved_result_reproduced"])
            self.assertEqual(result["validation_stage"], "sample_references")
            (folder / "assistant-content.txt").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "saved_response_hash_mismatch"):
                self.t.diagnostic_runner.saved_response_check(
                    folder, self.t.packet["sources"]
                )
            self.fixture(folder)
            request = json.loads((folder / "request.json").read_text())
            request["messages"][0]["content"] += "changed"
            (folder / "request.json").write_text(json.dumps(request))
            with self.assertRaisesRegex(ValueError, "saved_prompt_mismatch"):
                self.t.diagnostic_runner.saved_response_check(
                    folder, self.t.packet["sources"]
                )

    def test_unrecognized_source_pin_is_rejected_before_exec(self):
        pins = copy.deepcopy(self.t.pins)
        pins["my_workflows/zeus/rule_refine/analysis.py"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "recorded_source_version_mismatch"):
            self.t.diagnostic_runner.load_pure(self.t.packet["sources"], pins)

    def test_log_coverage_rebuilds_context_from_restricted_pickle(self):
        import pandas as pd

        materials = self.t.historical_materials
        row = {
            "alert": {
                "alertId": "private-id",
                "hitLog": [
                    {
                        "topic": materials.TARGET[0],
                        "ruleCode": materials.TARGET[1],
                        "zeusRawLogs": [{"path": "/health"}, {"body": "x" * 9000}, {}],
                    }
                ],
            }
        }
        frame = pd.DataFrame(
            [[row]], columns=pd.Index(["alert_full_data"], dtype=object)
        )
        raw = pickle.dumps(frame, protocol=4)
        rule = {
            "company_code": "TEST",
            "topic": materials.TARGET[0],
            "rule_code": materials.TARGET[1],
            "rule_name": "人工规则",
            "rule_config_version": "test",
            "source": {},
            "configuration": {},
        }
        rules = types.SimpleNamespace(
            file_sha256=hashlib.sha256(b"synthetic-csv").hexdigest(),
            get_rule=lambda *args, **kwargs: rule,
        )
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(materials, "SAMPLE_SHA", hashlib.sha256(raw).hexdigest()),
            patch.object(materials, "CSV_SHA", rules.file_sha256),
            patch.object(
                self.t.pure.csv_config_source, "CsvRuleConfigSource", return_value=rules
            ),
        ):
            root = Path(temp)
            original = root / "nas/original"
            original.mkdir(parents=True)
            (original / materials.SAMPLE).write_bytes(raw)
            csv = root / "rules.csv"
            csv.write_bytes(b"synthetic-csv")
            material = materials.build_context(frame, rules, self.t.inventory)
            result = self.t.diagnostic_runner.check_log_coverage(
                root, csv, material, self.t.pure
            )
            self.assertTrue(result["saved_context_reproduced"])
            self.assertEqual(result["log_categories"]["oversized"], 1)
            self.assertEqual(result["log_categories"]["empty_object"], 1)
            self.assertEqual(result["model_visible_logs"], 1)
            self.assertNotIn("private-id", json.dumps(result))
            bad = copy.deepcopy(material)
            bad["samples"][0]["raw_logs"][0]["path"] = "/changed"
            with self.assertRaisesRegex(ValueError, "saved_context_not_reproduced"):
                self.t.diagnostic_runner.check_log_coverage(root, csv, bad, self.t.pure)

    def test_missing_pickle_keeps_useful_diagnosis_and_original_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            folder = home / "saved"
            folder.mkdir()
            self.fixture(folder)
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            with (
                patch.object(sys, "argv", ["tool", str(folder)]),
                patch.dict(os.environ, HOME=str(home)),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(
                    self.t.diagnostic_runner.main(self.t.packet["sources"]), 0
                )
            result = json.loads(output.getvalue())
            self.assertEqual(result["log_coverage_check"]["status"], "unavailable")
            self.assertEqual(
                result["response_check"]["validation_stage"], "sample_references"
            )
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            artifact = Path(result["output_dir"]) / "diagnostic.json"
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
            self.assertEqual(artifact.parent.stat().st_mode & 0o777, 0o700)

    def test_complete_embedded_command_no_application_or_network_imports(self):
        command = build_command.build()
        source = command.split("<<'PY_HISTORICAL_DIAGNOSE'\n", 1)[1].rsplit(
            "\nPY_HISTORICAL_DIAGNOSE", 1
        )[0]
        guard = """import sys
def guard(event, args):
    if event.startswith("socket.connect"):
        raise AssertionError("NETWORK_FORBIDDEN")
    if event == "import" and args[0].split(".")[0] in {"redis", "openai", "sqlalchemy", "my_workflows", "util", "tasks"}:
        raise AssertionError("APP_IMPORT_FORBIDDEN")
sys.addaudithook(guard)
"""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "saved"
            folder.mkdir()
            self.fixture(folder)
            out = subprocess.run(
                [sys.executable, "-B", "-c", guard + source, str(folder)],
                cwd=temp,
                env={**os.environ, "HOME": temp},
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
            result = json.loads(out.stdout)
            self.assertEqual(result["status"], "diagnosed")
            self.assertEqual(result["model_calls"], 0)
            self.assertNotIn("not-shown-secret-id", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
