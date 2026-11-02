"""Offline replay uses real validation and local delivery without changing source files."""

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock
from uuid import uuid4
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
IMPLEMENTATION = HERE.parent
REPOSITORY = HERE.parents[3]


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "saved_fenced_reply_replay", HERE / "replay.py"
        )
        cls.replay_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.replay_module)
        cls.old_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        cls.stage = tempfile.TemporaryDirectory(prefix="replay-stage-")
        cls.root = Path(cls.stage.name) / "sec_know_model"
        cls.root.mkdir()
        for payload in sorted(IMPLEMENTATION.glob("*/payload")):
            for path in (payload / "my_workflows/zeus/rule_refine").glob("*.py"):
                target = cls.root / path.relative_to(payload)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        relative = "my_workflows/zeus/rule_refine/analysis.py"
        shutil.copyfile(
            IMPLEMENTATION / "step04b_analysis/payload" / relative, cls.root / relative
        )
        cls.pins_bytes = (
            IMPLEMENTATION / "step06j_synthetic_chain/pins.json"
        ).read_bytes()
        pins = json.loads(cls.pins_bytes)
        with ZipFile(REPOSITORY / "validation/sec_know_model.zip") as archive:
            for name in pins:
                target = cls.root / name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        for name, digest in pins.items():
            assert (
                hashlib.sha256((cls.root / name).read_bytes()).hexdigest() == digest
            ), name
        package = ModuleType("_offline_replay_fixture_" + uuid4().hex)
        package.__path__ = [str(cls.root / "my_workflows/zeus/rule_refine")]
        sys.modules[package.__name__] = package
        cls.analysis = importlib.import_module(package.__name__ + ".analysis")
        cls.materials = importlib.import_module(package.__name__ + ".materials")
        cls.delivery = importlib.import_module(package.__name__ + ".saved_delivery")

    @classmethod
    def tearDownClass(cls):
        sys.dont_write_bytecode = cls.old_bytecode
        cls.stage.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="replay-artifacts-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "prior-diagnostic"
        (self.source / "result").mkdir(parents=True)

    @staticmethod
    def snapshot(directory):
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file()
        }

    def prepare(self, *, missing_certainty=False):
        rule = {
            "company_code": "demo-company",
            "topic": "demo-http",
            "rule_code": "DEMO_RULE",
            "rule_name": "人工健康探测规则",
            "rule_config_version": "synthetic-v1",
            "source": {"kind": "synthetic_fixture"},
            "configuration": {"expression": "A"},
        }
        signal = {
            "schema_version": "sec_know.rule_burst_signal.v1",
            "mode": "demo",
            "signal_id": "synthetic-signal",
            "namespace": "synthetic-replay-test",
            "source_app": "zeus",
            "company_code": "demo-company",
            "topic": "demo-http",
            "rule_code": "DEMO_RULE",
            "current_count": 3,
            "window_start": "2026-01-01T00:00:00+00:00",
            "window_end": "2026-01-01T01:00:00+00:00",
            "time_basis": "provided_received_at",
            "sample_alert_ids": ["demo-1"],
        }
        rows = [
            {
                "id": "task-demo-1",
                "app_code": "zeus",
                "flow_id": "alert_agent",
                "alert_id": "demo-1",
                "alert_data": {
                    "alert": {
                        "alertId": "demo-1",
                        "hitLog": [
                            {
                                "topic": "demo-http",
                                "ruleCode": "DEMO_RULE",
                                "zeusRawLogs": [{"host": "synthetic.invalid"}],
                            }
                        ],
                    }
                },
                "agent_response": None,
            }
        ]
        materials = self.materials.build_rule_materials(
            signal,
            Mock(get_rule=Mock(return_value=rule)),
            rows,
            source_kind="synthetic_fixture",
        )
        draft = {
            "status": "proposal",
            "issue": "人工健康探测集中触发",
            "recommendation": "运营确认后增加精确排除",
            "certainty": "人工材料仅验证功能",
            "sample_alert_ids": ["demo-1"],
            "filter": {
                "topic": "demo-http",
                "expression": "A",
                "A": {
                    "name": "host",
                    "value": "synthetic.invalid",
                    "operator": "equal",
                },
            },
        }
        if missing_certainty:
            draft.pop("certainty")
        answer = "```json\n" + json.dumps(draft, ensure_ascii=False) + "\n```"
        answer_bytes = answer.encode("utf-8")
        previous = self.analysis.analyze_materials(
            materials, lambda messages: answer, response_source="synthetic_reply"
        )
        self.assertEqual(previous["error_code"], "invalid_model_output")
        store = self.delivery.LocalAnalysisStore(
            self.source / "result/processing/artifacts"
        )
        saved = store.save(materials, previous)
        (self.source / "assistant-content.txt").write_bytes(answer_bytes)
        (self.source / "pins.json").write_bytes(self.pins_bytes)
        diagnostic = (
            IMPLEMENTATION / "step06k_output_diagnostic/diagnostics.py"
        ).read_bytes()
        (self.source / "diagnostics.py").write_bytes(diagnostic)
        (self.source / "diagnostic-manifest.json").write_text(
            json.dumps(
                {"diagnostic_helper_sha256": hashlib.sha256(diagnostic).hexdigest()}
            ),
            encoding="utf-8",
        )
        (self.source / "result/summary.json").write_text(
            json.dumps({"record_id": saved["record_id"]}), encoding="utf-8"
        )
        (self.source / "result/rule.json").write_text(
            json.dumps(rule, ensure_ascii=False), encoding="utf-8"
        )
        return hashlib.sha256(answer_bytes).hexdigest()

    def test_saved_fenced_reply_revalidates_and_delivers_without_source_changes(self):
        body_sha = self.prepare()
        original_project = self.snapshot(self.root)
        original_evidence = self.snapshot(self.source)
        result = self.replay_module.replay(
            self.root, self.source, expected_body=body_sha
        )
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["analysis_status"], "proposal")
        self.assertEqual(result["delivery_status"], "received")
        self.assertEqual(
            (result["model_calls"], result["redis_calls"], result["database_calls"]),
            (0, 0, 0),
        )
        self.assertFalse(result["automatic_apply"])
        self.assertFalse(result["application_code_updated"])
        self.assertEqual(self.snapshot(self.root), original_project)
        self.assertEqual(self.snapshot(self.source), original_evidence)
        output = Path(result["output_dir"])
        self.assertNotEqual(output, self.source)
        self.assertEqual(json.loads((output / "summary.json").read_text()), result)
        receipts = list((output / "receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual((output / "summary.json").stat().st_mode & 0o777, 0o600)

    def test_missing_field_remains_blocked_and_does_not_misreport_success(self):
        body_sha = self.prepare(missing_certainty=True)
        original_evidence = self.snapshot(self.source)
        result = self.replay_module.replay(
            self.root, self.source, expected_body=body_sha
        )
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["analysis_status"], "analysis_failed")
        self.assertEqual(result["delivery_status"], "blocked")
        self.assertEqual(result["check_reason"], "analysis_failed")
        self.assertIn(
            {"type": "missing", "loc": ["certainty"]},
            result["validation_error"]["errors"],
        )
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(self.snapshot(self.source), original_evidence)
        self.assertFalse((Path(result["output_dir"]) / "receipts").exists())

    def test_wrong_answer_hash_stops_before_creating_replay_output(self):
        self.prepare()
        original_evidence = self.snapshot(self.source)
        with self.assertRaisesRegex(ValueError, "已保存回答摘要不匹配"):
            self.replay_module.replay(self.root, self.source, expected_body="0" * 64)
        self.assertEqual(self.snapshot(self.source), original_evidence)
        self.assertEqual(list(self.directory.iterdir()), [self.source])


if __name__ == "__main__":
    unittest.main(verbosity=2)
