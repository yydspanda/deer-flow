"""Saved synthetic reply replay; no application services or real materials."""

import copy
import hashlib
import io
import json
import shutil
import socket
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

import diagnose_saved_reply as diagnostic

HERE = Path(__file__).resolve().parent
SOURCES = HERE.parent / "step06w_unified_limits/payload"


def draft(count=1):
    return {
        "status": "proposal",
        "issue": "人工示例问题",
        "recommendation": "待运营确认",
        "certainty": "仅人工材料",
        "sample_alert_ids": [f"SYNTHETIC-{i}" for i in range(count)],
        "filter": {
            "topic": "demo",
            "expression": "A",
            "A": {"name": "host", "value": "synthetic.invalid", "operator": "equal"},
        },
    }


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project, self.report = self.root / "project", self.root / "report"
        module_root = self.project / diagnostic.RELATIVE_ROOT
        module_root.mkdir(parents=True)
        self.child = self.report / "batch-001"
        self.child.mkdir(parents=True)
        self.pins = {}
        for name in diagnostic.MODULES:
            relative = diagnostic.RELATIVE_ROOT / (name + ".py")
            shutil.copyfile(SOURCES / relative, self.project / relative)
            self.pins[relative.as_posix()] = hashlib.sha256(
                (self.project / relative).read_bytes()
            ).hexdigest()
        for parent in (module_root, *list(module_root.parents)[:3]):
            (parent / "__init__.py").write_text(
                "raise AssertionError('project init executed')", encoding="utf-8"
            )
        self.save(self.report / "source-pins.json", self.pins)
        with diagnostic._analysis(self.project, self.pins) as analysis:
            self.schema = analysis.AnalysisDraft.model_json_schema()
        self.save(self.report / "output-schema.json", self.schema)
        self.set_reply(draft())

    def save(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def set_reply(self, value, *, fence=False):
        content = json.dumps(value, ensure_ascii=False)
        if fence:
            content = "```json\n" + content + "\n```"
        raw = content.encode("utf-8")
        (self.child / "assistant-content.txt").write_bytes(raw)
        self.save(
            self.child / "analysis.json",
            {
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "error_code": "invalid_output_contract",
                "error_type": "ValidationError",
                "model_transport": {
                    "finish_reason": "stop",
                    "usage": {"prompt_tokens": 229652, "completion_tokens": 600},
                    "elapsed_ms": 600.25,
                },
            },
        )

    def run_check(self):
        with (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network forbidden"),
            ),
            patch.object(
                socket, "socket", side_effect=AssertionError("network forbidden")
            ),
            patch.object(
                sqlite3, "connect", side_effect=AssertionError("database forbidden")
            ),
        ):
            return diagnostic.diagnose(self.report, self.project)

    def test_more_than_twenty_references_match_saved_expanded_contract(self):
        self.set_reply(draft(50), fence=True)
        result = self.run_check()
        self.assertEqual(result["status"], "contract_valid")
        self.assertTrue(result["saved_schema_verified"])
        self.assertFalse(result["full_proposal_validation_performed"])
        self.assertEqual(result["model_calls"], 0)
        self.assertNotIn("maxItems", self.schema["properties"]["sample_alert_ids"])

    def test_long_text_reports_numeric_bounds_without_payload(self):
        answer = draft()
        secret = "SECRET_ORIGINAL_PAYLOAD"
        answer["issue"] = secret * 300
        self.set_reply(answer)
        result = self.run_check()
        error = result["errors"][0]
        self.assertEqual(error["path"], ["issue"])
        self.assertEqual(error["error_type"], "string_too_long")
        self.assertEqual(error["limit_max_length"], 4096)
        self.assertEqual(error["actual_length"], len(answer["issue"]))
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("input", error)
        self.assertNotIn("ctx", error)

    def test_wrong_value_type_reports_safe_alias_path_and_type(self):
        answer = draft()
        answer["filter"]["A"]["value"] = 443
        self.set_reply(answer)
        error = self.run_check()["errors"][0]
        self.assertEqual(error["path"], ["filter", "A", "value"])
        self.assertEqual(error["actual_type"], "integer")
        self.assertNotIn("443", json.dumps(error))

    def test_unknown_keys_and_custom_errors_do_not_echo_values(self):
        answer = draft()
        answer["SECRET_EXTRA_FIELD"] = "SECRET_VALUE"
        answer["filter"]["SECRET_ALIAS"] = {
            "name": "secret",
            "value": 23,
            "operator": "equal",
        }
        self.set_reply(answer)
        result = self.run_check()
        rendered = json.dumps(result)
        self.assertNotIn("SECRET_", rendered)
        self.assertIn("<unrecognized_field>", rendered)
        answer = draft(2)
        answer["sample_alert_ids"][1] = answer["sample_alert_ids"][0]
        self.set_reply(answer)
        result = self.run_check()
        self.assertEqual(result["errors"][0]["error_type"], "value_error")
        self.assertEqual(result["errors"][0]["reason"], "duplicate_sample_references")
        self.assertNotIn("SYNTHETIC", json.dumps(result))

    def test_missing_filter_reports_fixed_reason_without_sample_ids(self):
        answer = draft()
        answer["filter"] = None
        self.set_reply(answer)
        error = self.run_check()["errors"][0]
        self.assertEqual(error["path"], [])
        self.assertEqual(error["reason"], "proposal_requires_filter_and_samples")
        self.assertNotIn("SYNTHETIC", json.dumps(error))

    def test_incomplete_expression_reports_fixed_reason_without_expression(self):
        answer = draft()
        answer["filter"]["expression"] = "A &&"
        self.set_reply(answer)
        error = self.run_check()["errors"][0]
        self.assertEqual(error["path"], ["filter"])
        self.assertEqual(error["reason"], "incomplete_expression")
        self.assertNotIn("A &&", json.dumps(error))

    def test_custom_reason_whitelist_requires_exact_match(self):
        secret = "SENSITIVE_UNEXPECTED_REASON"
        error = ValidationError.from_exception_data(
            "SyntheticModel",
            [
                {
                    "type": "value_error",
                    "loc": (),
                    "input": {"secret": secret},
                    "ctx": {"error": ValueError("表达式不完整:" + secret)},
                }
            ],
        )
        rows = diagnostic._errors(error, {"secret": secret})
        self.assertNotIn("reason", rows[0])
        self.assertNotIn(secret, json.dumps(rows))
        self.assertEqual(rows[0]["message"], diagnostic.MESSAGES["value_error"])

    def test_source_hash_change_stops_before_import(self):
        path = self.project / diagnostic.RELATIVE_ROOT / "analysis.py"
        path.write_text("raise AssertionError('must not execute')", encoding="utf-8")
        with self.assertRaisesRegex(
            diagnostic.DiagnosticError, "source_hash_mismatch:analysis"
        ):
            self.run_check()

    def test_missing_source_pin_stops(self):
        del self.pins[(diagnostic.RELATIVE_ROOT / "limits.py").as_posix()]
        self.save(self.report / "source-pins.json", self.pins)
        with self.assertRaisesRegex(
            diagnostic.DiagnosticError, "missing_source_pin:limits"
        ):
            self.run_check()

    def test_original_twenty_ref_schema_is_rejected_not_silently_substituted(self):
        schema = copy.deepcopy(self.schema)
        schema["properties"]["sample_alert_ids"]["maxItems"] = 20
        self.save(self.report / "output-schema.json", schema)
        with self.assertRaisesRegex(
            diagnostic.DiagnosticError, "saved_schema_mismatch"
        ):
            self.run_check()

    def test_response_hash_drift_stops(self):
        (self.child / "assistant-content.txt").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
            diagnostic.DiagnosticError, "saved_response_hash_mismatch"
        ):
            self.run_check()

    def test_read_only_no_project_init_or_service_connections(self):
        before = {
            p.relative_to(self.root): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }
        result = self.run_check()
        after = {
            p.relative_to(self.root): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(result["saved_transport"]["usage"]["prompt_tokens"], 229652)

    def test_cli_emits_only_safe_failure_when_artifact_missing(self):
        (self.child / "analysis.json").unlink()
        output = io.StringIO()
        with redirect_stdout(output):
            code = diagnostic.main(
                [str(self.report), "--project-root", str(self.project)]
            )
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "diagnostic_stopped")
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == "__main__":
    unittest.main()
