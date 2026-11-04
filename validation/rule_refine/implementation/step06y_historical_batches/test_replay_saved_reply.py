"""Offline recovery of a saved response; artificial materials and no services."""

import hashlib
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import context_budget
import diagnose_saved_reply as diagnostic
import replay_saved_reply as replay
import review
from test_diagnose_saved_reply import draft

HERE = Path(__file__).resolve().parent
SOURCES = HERE.parent / "step06w_unified_limits/payload"


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.report = self.root / "original"
        self.output = self.root / "new-reports"
        module_root = self.project / diagnostic.RELATIVE_ROOT
        module_root.mkdir(parents=True)
        self.child = self.report / "batch-001"
        self.child.mkdir(parents=True)
        self.pins = {}
        for name in (*diagnostic.MODULES, "filter_evaluation"):
            relative = diagnostic.RELATIVE_ROOT / (name + ".py")
            raw = (SOURCES / relative).read_bytes()
            (self.project / relative).write_bytes(raw)
            self.pins[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
        for parent in (module_root, *list(module_root.parents)[:3]):
            (parent / "__init__.py").write_text(
                "raise AssertionError('project init executed')", encoding="utf-8"
            )
        self.context = {
            "material_source": "historical_export",
            "trigger": "manual_sample_review",
            "observed_surge": False,
            "rate_window": None,
            "rule": {
                "topic": "demo",
                "rule_code": "SYNTHETIC",
                "rule_name": "人工规则",
            },
            "samples": [
                {"alert_id": "SYNTHETIC-0", "raw_logs": [{"host": "synthetic.invalid"}]}
            ],
        }
        self.save(self.report / "source-pins.json", self.pins)
        self.save(self.report / "limits.json", asdict(context_budget.ContextBudget()))
        with diagnostic._analysis(self.project, self.pins) as analysis:
            self.save(
                self.report / "output-schema.json",
                analysis.AnalysisDraft.model_json_schema(),
            )
            self.request = review.build_request(
                self.context, analysis, limits=context_budget.ContextBudget()
            )
        self.save(self.child / "context.json", self.context)
        self.save(self.child / "request.json", self.request)
        self.answer = draft()
        del self.answer["certainty"]
        self.set_reply(self.answer)

    def save(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def set_reply(self, value):
        raw = ("```json\n" + json.dumps(value, ensure_ascii=False) + "\n```").encode(
            "utf-8"
        )
        (self.child / "assistant-content.txt").write_bytes(raw)
        self.record = {
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "prompt_sha256": self.request["prompt_sha256"],
            "validation_stage": "output_contract",
            "error_code": "invalid_output_contract",
            "error_type": "ValidationError",
            "model_transport": {
                "finish_reason": "stop",
                "error_code": None,
                "error_type": None,
                "usage": {"prompt_tokens": 229652, "completion_tokens": 227},
            },
        }
        self.save(self.child / "analysis.json", self.record)
        self.save(
            self.child / "summary.json",
            {"model_error_code": None, "model_error_type": None},
        )

    def run_replay(self, **kwargs):
        with (
            patch.object(
                socket, "socket", side_effect=AssertionError("network forbidden")
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network forbidden"),
            ),
            patch.object(
                sqlite3, "connect", side_effect=AssertionError("database forbidden")
            ),
        ):
            return replay.replay(
                self.report,
                self.project,
                output_parent=kwargs.pop("output_parent", self.output),
                **kwargs,
            )

    def test_proposal_replays_full_matching_without_new_generation(self):
        result = self.run_replay()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["analysis_status"], "proposal")
        self.assertEqual(result["sample_match_counts"]["matched"], 1)
        self.assertEqual(result["certainty_source"], "missing")
        self.assertEqual(result["output_warnings"], ["missing_certainty"])
        self.assertEqual(result["sdk_request_calls"], 0)
        output = Path(result["output_dir"])
        analysis = json.loads((output / "analysis.json").read_bytes())
        self.assertEqual(analysis["generation_calls"], 0)
        self.assertEqual(analysis["saved_response_reads"], 1)
        self.assertEqual(analysis["certainty"], "未提供（模型未返回判断把握说明）")
        self.assertEqual(
            (output / "assistant-content.txt").read_bytes(),
            (self.child / "assistant-content.txt").read_bytes(),
        )
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(
            all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
        )
        self.assertIn(
            "certainty",
            json.loads((output / "original-output-schema.json").read_bytes())[
                "required"
            ],
        )
        self.assertNotIn(
            "certainty",
            json.loads((output / "replay-output-schema.json").read_bytes())["required"],
        )

    def test_valid_non_proposal_keeps_model_status_and_no_fake_filter(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            with self.subTest(status=status):
                self.set_reply({**self.answer, "status": status, "filter": None})
                result = self.run_replay()
                self.assertEqual(result["analysis_status"], status)
                self.assertEqual(result["status"], "passed")
                self.assertIsNone(result["sample_match_counts"])

    def test_copyable_command_runs_embedded_sources_without_local_tool_files(self):
        command = (HERE / "REPLAY-SAVED-REPLY.sh").read_text(encoding="utf-8")
        self.assertTrue(command.startswith("bash <<"))
        source = command.split("<<'PY_REPLAY_CERTAINTY'\n", 1)[1].rsplit(
            "\nPY_REPLAY_CERTAINTY\n", 1
        )[0]
        prohibit_connections = (
            "import socket, sqlite3\n"
            "def forbidden(*args, **kwargs):\n"
            "    raise AssertionError('service connection forbidden')\n"
            "socket.socket.connect = socket.socket.connect_ex = forbidden\n"
            "socket.create_connection = sqlite3.connect = forbidden\n"
        )
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-",
                str(self.report),
                "--project-root",
                str(self.project),
                "--output-parent",
                str(self.output),
            ],
            input=prohibit_connections + source,
            text=True,
            capture_output=True,
            cwd=self.root,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["certainty_source"], "missing")
        provenance = json.loads(
            (Path(result["output_dir"]) / "provenance.json").read_bytes()
        )
        self.assertEqual(len(provenance["replay_tool_source_sha256"]), 4)

    def test_wrong_topic_and_missing_evidence_remain_blocked(self):
        for answer, reason in (
            ({**self.answer, "sample_alert_ids": ["UNKNOWN"]}, "uncited_sample"),
            (
                {**self.answer, "filter": {**self.answer["filter"], "topic": "other"}},
                "wrong_topic",
            ),
            (
                {
                    **self.answer,
                    "filter": {
                        **self.answer["filter"],
                        "A": {
                            "name": "host",
                            "operator": "equal",
                            "value": "not-matching.invalid",
                        },
                    },
                },
                "condition_not_supported",
            ),
        ):
            with self.subTest(reason=reason):
                self.set_reply(answer)
                result = self.run_replay()
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["error_code"], reason)

    def test_other_missing_fields_are_not_silently_repaired(self):
        del self.answer["issue"]
        self.set_reply(self.answer)
        with self.assertRaisesRegex(
            replay.ReplayError, "not_the_missing_certainty_case"
        ):
            self.run_replay()
        self.assertFalse(self.output.exists())

    def test_source_schema_and_response_drift_stop(self):
        for name, mutate, reason in (
            (
                "response",
                lambda: (self.child / "assistant-content.txt").write_bytes(b"{}"),
                "saved_response_hash_mismatch",
            ),
            (
                "schema",
                lambda: self.save(self.report / "output-schema.json", {}),
                "saved_schema_mismatch",
            ),
            (
                "source",
                lambda: (
                    self.project / diagnostic.RELATIVE_ROOT / "analysis.py"
                ).write_bytes(b"raise AssertionError('do not import')"),
                "source_hash_mismatch",
            ),
        ):
            originals = {
                p: p.read_bytes()
                for p in (
                    self.child / "assistant-content.txt",
                    self.report / "output-schema.json",
                    self.project / diagnostic.RELATIVE_ROOT / "analysis.py",
                )
            }
            with self.subTest(name=name):
                mutate()
                with self.assertRaisesRegex(diagnostic.DiagnosticError, reason):
                    self.run_replay()
                for path, raw in originals.items():
                    path.write_bytes(raw)

    def test_evaluator_source_hash_checked_before_execution(self):
        (self.project / diagnostic.RELATIVE_ROOT / "filter_evaluation.py").write_bytes(
            b"raise AssertionError('do not execute')"
        )
        with self.assertRaisesRegex(
            replay.ReplayError, "source_hash_mismatch:filter_evaluation"
        ):
            self.run_replay()

    def test_context_and_request_drift_stop(self):
        original = (self.child / "context.json").read_bytes()
        self.save(self.child / "context.json", {**self.context, "changed": True})
        with self.assertRaisesRegex(
            replay.ReplayError, "saved_context_does_not_match_request"
        ):
            self.run_replay()
        (self.child / "context.json").write_bytes(original)
        self.request["messages"][0]["content"] += "changed"
        self.save(self.child / "request.json", self.request)
        with self.assertRaisesRegex(replay.ReplayError, "saved_prompt_hash_mismatch"):
            self.run_replay()

    def test_truncated_or_transport_failed_answers_are_not_recovered(self):
        for field, value in (
            ("finish_reason", "length"),
            ("error_code", "request_failed"),
        ):
            with self.subTest(field=field):
                self.set_reply(self.answer)
                self.record["model_transport"][field] = value
                self.save(self.child / "analysis.json", self.record)
                with self.assertRaisesRegex(
                    replay.ReplayError, "saved_transport_not_complete"
                ):
                    self.run_replay()

    def test_original_files_unchanged_and_output_cannot_be_inside_original(self):
        before = {
            p: p.read_bytes()
            for base in (self.project, self.report)
            for p in base.rglob("*")
            if p.is_file()
        }
        self.run_replay()
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        with self.assertRaisesRegex(
            replay.ReplayError, "output_must_be_outside_original_report"
        ):
            self.run_replay(output_parent=self.report)


if __name__ == "__main__":
    unittest.main()
