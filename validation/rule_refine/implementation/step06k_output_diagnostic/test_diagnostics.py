"""Diagnostic-only instrumentation preserves the original bounded analysis contract."""

import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock
from uuid import uuid4


HERE = Path(__file__).resolve().parent
IMPLEMENTATION = HERE.parent
ANALYSIS_PATH = "my_workflows/zeus/rule_refine/analysis.py"


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "rule_output_diagnostic_test_module", HERE / "diagnostics.py"
        )
        cls.diagnostic = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.diagnostic)
        cls.stage = tempfile.TemporaryDirectory()
        cls.root = Path(cls.stage.name)
        owned = cls.root / "my_workflows/zeus/rule_refine"
        owned.mkdir(parents=True)
        for step in (
            "step01_contracts",
            "step03d_preparation",
            "step06c_task_source",
            "step04b_analysis",
            "step04c_model_adapter",
        ):
            source = IMPLEMENTATION / step / "payload/my_workflows/zeus/rule_refine"
            for path in source.glob("*.py"):
                shutil.copyfile(path, owned / path.name)
        package = ModuleType("_rule_output_diagnostic_" + uuid4().hex)
        package.__path__ = [str(owned)]
        sys.modules[package.__name__] = package
        cls.modules = SimpleNamespace(
            **{
                name: importlib.import_module(package.__name__ + "." + name)
                for name in ("analysis", "model_adapter", "materials")
            }
        )
        cls.original = staticmethod(cls.modules.analysis.analyze_materials)
        cls.analysis_bytes = (cls.root / ANALYSIS_PATH).read_bytes()
        cls.pins = {ANALYSIS_PATH: hashlib.sha256(cls.analysis_bytes).hexdigest()}

    @classmethod
    def tearDownClass(cls):
        cls.stage.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.modules.model_adapter.analyze_materials = self.original
        self.addCleanup(
            setattr, self.modules.model_adapter, "analyze_materials", self.original
        )
        self.prepared = SimpleNamespace(
            root=self.root, pins=dict(self.pins), modules=self.modules
        )

    def install(self, folder=None):
        folder = self.directory if folder is None else folder
        self.diagnostic.install(self.prepared, folder)
        self.assertIs(self.modules.analysis.analyze_materials, self.original)
        self.assertEqual((self.root / ANALYSIS_PATH).read_bytes(), self.analysis_bytes)

    def materials(self):
        signal = {
            "schema_version": "sec_know.rule_burst_signal.v1",
            "mode": "demo",
            "signal_id": "synthetic-signal",
            "namespace": "synthetic-diagnostic",
            "source_app": "zeus",
            "company_code": "demo-company",
            "topic": "demo-topic",
            "rule_code": "DEMO_RULE",
            "current_count": 3,
            "window_start": "2026-01-01T00:00:00+00:00",
            "window_end": "2026-01-01T01:00:00+00:00",
            "time_basis": "provided_received_at",
            "sample_alert_ids": ["101"],
        }
        rule = {
            "company_code": "demo-company",
            "topic": "demo-topic",
            "rule_code": "DEMO_RULE",
            "rule_name": "人工演示规则",
            "rule_config_version": "synthetic-config-v1",
            "source": {"kind": "synthetic_fixture"},
            "configuration": {"terms": [{"expression": "A"}]},
        }
        row = {
            "id": "task-101",
            "app_code": "zeus",
            "flow_id": "alert_agent",
            "alert_id": "101",
            "alert_data": {
                "alert": {
                    "alertId": "101",
                    "hitLog": [
                        {
                            "topic": "demo-topic",
                            "ruleCode": "DEMO_RULE",
                            "zeusRawLogs": [{"host": "synthetic.invalid"}],
                        }
                    ],
                }
            },
            "agent_response": None,
        }
        return self.modules.materials.build_rule_materials(
            signal,
            Mock(get_rule=Mock(return_value=rule)),
            [row],
            source_kind="synthetic_fixture",
        )

    def draft(self):
        return {
            "status": "proposal",
            "issue": "人工示例：健康探测集中触发告警",
            "recommendation": "运营确认业务正常后增加精确排除条件",
            "certainty": "人工材料仅用于流程测试",
            "sample_alert_ids": ["101"],
            "filter": {
                "topic": "demo-topic",
                "expression": "A",
                "A": {
                    "name": "host",
                    "value": "synthetic.invalid",
                    "operator": "equal",
                },
            },
        }

    def call(self, answer):
        raw = (
            answer
            if isinstance(answer, str)
            else json.dumps(answer, ensure_ascii=False)
        )
        generate = Mock(return_value=raw)
        result = self.modules.model_adapter.analyze_materials(
            self.materials(), generate, response_source="synthetic_reply"
        )
        generate.assert_called_once()
        self.assertEqual(result["generation_calls"], 1)
        self.assertEqual(result["model_calls"], 0)
        return result, raw

    def assert_invalid(self, result):
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "invalid_model_output")
        self.assertIsNone(result["recommendation"])
        self.assertIsInstance(result["validation_error"], dict)
        self.assertTrue(result["validation_error"])

    def test_fenced_and_malformed_json_stay_invalid_with_diagnostics(self):
        for index, answer in enumerate(("```json\n{}\n```", '{"status":')):
            with self.subTest(answer=answer):
                folder = self.directory / str(index)
                folder.mkdir()
                self.install(folder)
                result, raw = self.call(answer)
                self.assert_invalid(result)
                self.assertIn("JSONDecodeError", json.dumps(result["validation_error"]))
                self.assertEqual((folder / "assistant-content.txt").read_text(), raw)

    def test_missing_required_field_and_unknown_extra_are_reported_without_values(self):
        answer = self.draft()
        answer.pop("certainty")
        answer["credential"] = "secret-value-for-redaction-check"
        self.install()
        result, _ = self.call(answer)
        self.assert_invalid(result)
        diagnostic = json.dumps(result["validation_error"])
        self.assertIn("missing", diagnostic)
        self.assertIn("certainty", diagnostic)
        self.assertNotIn("secret-value-for-redaction-check", diagnostic)
        self.assertNotIn('"input"', diagnostic)
        self.assertNotIn('"ctx"', diagnostic)

    def test_unknown_sample_reference_keeps_original_rejection(self):
        answer = self.draft()
        answer["sample_alert_ids"] = ["missing-private-alert"]
        self.install()
        result, _ = self.call(answer)
        self.assert_invalid(result)
        diagnostic = json.dumps(result["validation_error"], ensure_ascii=False)
        self.assertIn("ValueError", diagnostic)
        self.assertNotIn("missing-private-alert", diagnostic)

    def test_unsupported_condition_keeps_original_rejection(self):
        answer = self.draft()
        answer["filter"]["A"]["value"] = "secret-unsupported-value"
        self.install()
        result, _ = self.call(answer)
        self.assert_invalid(result)
        self.assertNotIn(
            "secret-unsupported-value", json.dumps(result["validation_error"])
        )

    def test_valid_proposal_preserves_prompt_and_contract_and_private_body(self):
        answer = self.draft()
        data = self.materials()
        before = copy.deepcopy(data)
        raw = json.dumps(answer, ensure_ascii=False)
        original = self.original(data, lambda messages: raw)
        self.install()
        result, _ = self.call(answer)
        # UUID is freshly created by the existing contract for every accepted proposal.
        original["recommendation"].pop("recommendation_id")
        result["recommendation"].pop("recommendation_id")
        self.assertEqual(result, original)
        self.assertEqual(data, before)
        path = self.directory / "assistant-content.txt"
        self.assertEqual(path.read_text(encoding="utf-8"), raw)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_valid_insufficient_evidence_preserves_original_result(self):
        answer = {
            **self.draft(),
            "status": "insufficient_evidence",
            "sample_alert_ids": [],
            "filter": None,
        }
        raw = json.dumps(answer, ensure_ascii=False)
        original = self.original(self.materials(), lambda messages: raw)
        self.install()
        result, _ = self.call(answer)
        self.assertEqual(result, original)

    def test_invalid_analysis_pin_stops_without_patching_adapter(self):
        self.prepared.pins[ANALYSIS_PATH] = "0" * 64
        with self.assertRaises(ValueError):
            self.diagnostic.install(self.prepared, self.directory)
        self.assertIs(self.modules.model_adapter.analyze_materials, self.original)
        self.assertFalse((self.directory / "assistant-content.txt").exists())

    def test_oversized_body_is_not_saved_and_original_limit_still_applies(self):
        self.install()
        result, _ = self.call("x" * 16385)
        self.assert_invalid(result)
        self.assertFalse((self.directory / "assistant-content.txt").exists())

    def test_existing_body_is_not_overwritten_and_generate_is_not_retried(self):
        path = self.directory / "assistant-content.txt"
        path.write_text("preserve-first-answer", encoding="utf-8")
        self.install()
        result, _ = self.call(self.draft())
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "generation_failed")
        self.assertEqual(result["error_type"], "FileExistsError")
        self.assertEqual(path.read_text(encoding="utf-8"), "preserve-first-answer")

    def test_json_diagnostics_do_not_copy_body_or_exception_message(self):
        secret = "private-string-must-stay-local"
        self.install()
        result, _ = self.call(secret)
        self.assert_invalid(result)
        diagnostic = json.dumps(result["validation_error"])
        self.assertNotIn(secret, diagnostic)
        self.assertNotIn('"doc"', diagnostic)
        self.assertNotIn('"input"', diagnostic)


if __name__ == "__main__":
    unittest.main(verbosity=2)
