"""Saved-chain, same-sample and one-call checks using scripted transports only."""

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


tool = load("_surge_saved_review_tool", HERE / "check_surge_review_repair.py")
fixtures = load("_surge_saved_review_fixtures", HERE / "test_surge_processor.py")


class SavedReviewRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.root = self.temp / "project"
        shutil.copytree(ROOT / tool.MODULE, self.root / tool.MODULE)
        config = self.root / "config"
        config.mkdir()
        self.analysis = config / "rule_refine.analysis.local.json"
        self.analysis.write_text(
            json.dumps({"enabled": True, "model": "synthetic-model"}), encoding="utf-8"
        )
        self.settings = config / "rule_refine.surge.local.json"
        self.settings.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        self.load = tool.modules(self.root)
        self.case = fixtures.case()
        self.signal = fixtures.signal()
        self.signal["topic"] = "security_qthids"
        for key in ("rule", "original_rule"):
            self.case[key]["topic"] = "security_qthids"
        for sample in self.case["current_samples"] + self.case["history_samples"]:
            for log in sample["raw_logs"]:
                log["detail_hit_rule_name"] = "Synthetic detector"
        self.source = self.temp / "source"
        self.original_calls = []

        def original_transport(request):
            self.original_calls.append(copy.deepcopy(request))
            status = (
                "proposal"
                if request["phase"] == "generate"
                else "insufficient_evidence"
            )
            reply = fixtures.answer(request, status=status, field="detail_process_name")
            if reply["filter"]:
                reply["filter"]["topic"] = "security_qthids"
                reply["filter"]["A"]["value"] = "probe.exe"
            marker = (
                "GENERATION_EXPLANATION_NOT_INPUT"
                if request["phase"] == "generate"
                else "OLD_REVIEW_ANSWER_NOT_INPUT"
            )
            for key in ("issue", "recommendation", "certainty", "review_notes"):
                reply[key] = marker
            response = fixtures.transport(json.dumps(reply))
            response["transport"].update(
                sdk_request_calls=0, transport_mode="scripted_offline"
            )
            return response

        processor = self.load("surge_processor").SurgeProcessor(
            self.source,
            load_case=lambda _: copy.deepcopy(self.case),
            load_rules=lambda: types.SimpleNamespace(
                get_rule=lambda *a, **kw: self.case["original_rule"]
            ),
            generate=original_transport,
            model="synthetic-model",
            budget=self.load("surge_budget").ContextBudget(),
            operator_context="synthetic operator context",
        )
        result = processor.process(self.signal)
        self.assertEqual(result["status"], "not_applicable")
        self.folder = Path(result["output_dir"])
        self.prepared = tool.prepare(self.root, self.source, {"env_profile": "LOCAL"})

    def frozen(self):
        return {
            str(path.relative_to(self.source)): path.read_bytes()
            for path in self.source.rglob("*")
            if path.is_file()
        }

    def response(self, request, *, field="event_content", status="proposal"):
        reply = fixtures.answer(request, status=status, field=field)
        if reply["filter"]:
            reply["filter"]["topic"] = "security_qthids"
        response = fixtures.transport(json.dumps(reply, ensure_ascii=False))
        response["transport"].update(
            sdk_request_calls=0, transport_mode="scripted_offline"
        )
        return response

    def test_same_review_samples_and_draft_reused_without_old_review_explanation(self):
        before = self.frozen()
        context = self.prepared["request"]["context"]
        previous = json.loads(
            (self.folder / "review/request.json").read_text(encoding="utf-8")
        )["context"]
        for key in (
            "current_samples",
            "history_samples",
            "coverage",
            "operator_context",
            "draft",
        ):
            self.assertEqual(context[key], previous[key])
        messages = json.dumps(self.prepared["request"]["messages"])
        self.assertNotIn("OLD_REVIEW_ANSWER_NOT_INPUT", messages)
        self.assertNotIn("GENERATION_EXPLANATION_NOT_INPUT", messages)
        self.assertEqual(len(self.original_calls), 2)
        self.assertEqual(before, self.frozen())
        preview = tool.preview(self.prepared)
        self.assertEqual(preview["max_sdk_requests"], 1)
        self.assertEqual(preview["generation_model_calls_this_command"], 0)
        self.assertIsNone(preview["model_output_token_limit"])

    def test_one_review_call_saves_provenance_and_preserves_source_bytes(self):
        before = self.frozen()
        generate = Mock(side_effect=self.response)
        out = self.temp / "review-only"
        result = tool.run(self.prepared, out, generate)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(generate.call_args.args[0]["phase"], "review")
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["sdk_request_calls"], 0)
        self.assertTrue(result["generation_reused"])
        self.assertEqual(before, self.frozen())
        self.assertTrue((out / "response.json").is_file())
        self.assertTrue((out / "parsed.json").is_file())
        report = (out / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("没有重新生成或补样", report)
        self.assertIn("没有查询当前线上配置", report)
        self.assertFalse(result["filter_replay_performed"])
        self.assertFalse(result["automatic_apply"])
        source = json.loads((out / "source.json").read_text(encoding="utf-8"))
        self.assertTrue(source["saved_chain_verified"])
        self.assertEqual(
            source["source_record_id"], self.prepared["source"]["source_record_id"]
        )

    def test_known_metadata_in_new_review_remains_manual_and_original_answer_kept(self):
        generate = Mock(
            side_effect=lambda request: self.response(
                request, field="detail_hit_rule_name"
            )
        )
        result = tool.run(self.prepared, self.temp / "metadata", generate)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result["status"], "manual_review")
        self.assertFalse(result["recommendation_ready"])
        self.assertEqual(
            result["validation_issues"][0]["code"], "condition_uses_detection_metadata"
        )
        saved = json.loads(
            (Path(result["output_dir"]) / "response.json").read_text(encoding="utf-8")
        )
        self.assertIn("detail_hit_rule_name", saved["content"])

    def test_valid_withdrawal_is_not_a_transport_failure_or_forced_proposal(self):
        result = tool.run(
            self.prepared,
            self.temp / "withdraw",
            lambda request: self.response(request, status="insufficient_evidence"),
        )
        self.assertEqual(result["status"], "not_applicable")
        self.assertEqual(result["analysis_status"], "insufficient_evidence")
        self.assertIsNone(result["error_code"])
        self.assertTrue(result["ai_review_completed"])
        self.assertFalse(result["recommendation_ready"])

    def test_invalid_json_is_saved_before_parse_and_never_retried(self):
        generate = Mock(
            return_value={
                "content": "{bad-json",
                "transport": {
                    "sdk_request_calls": 0,
                    "finish_reason": "stop",
                    "error_code": None,
                },
            }
        )
        result = tool.run(self.prepared, self.temp / "invalid", generate)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(generate.call_count, 1)
        response = json.loads(
            (Path(result["output_dir"]) / "response.json").read_text(encoding="utf-8")
        )
        self.assertEqual(response["content"], "{bad-json")

    def test_truncated_response_is_not_accepted_even_if_json_parses(self):
        response = self.response(self.prepared["request"])
        response["transport"]["finish_reason"] = "length"
        result = tool.run(
            self.prepared, self.temp / "truncated", Mock(return_value=response)
        )
        self.assertEqual(result["error_code"], "model_response_not_complete")
        self.assertFalse(result["recommendation_ready"])

    def test_transport_exception_records_unknown_request_without_resubmission(self):
        generate = Mock(side_effect=RuntimeError("opaque transport error"))
        result = tool.run(self.prepared, self.temp / "exception", generate)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["sdk_request_calls"])
        self.assertEqual(generate.call_count, 1)
        self.assertTrue((Path(result["output_dir"]) / "started.json").is_file())

    def test_tampered_source_is_rejected_before_preparation_or_service_call(self):
        path = self.folder / "generate/response.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["content"] += "modified"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            tool.prepare(self.root, self.source, {"env_profile": "LOCAL"})

    def test_source_or_config_change_after_preview_stops_before_new_call(self):
        self.settings.write_text(
            json.dumps({"enabled": True, "context_tokens": 1000000}), encoding="utf-8"
        )
        generate = Mock()
        out = self.temp / "should-not-exist"
        with self.assertRaisesRegex(
            ValueError, "prepared_source_or_configuration_changed"
        ):
            tool.run(self.prepared, out, generate)
        generate.assert_not_called()
        self.assertFalse(out.exists())

    def test_too_small_budget_stops_without_trimming_saved_samples(self):
        before = self.frozen()
        self.settings.write_text(
            json.dumps({"enabled": True, "context_tokens": 100, "input_fraction": 0.8}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
            tool.prepare(self.root, self.source, {"env_profile": "LOCAL"})
        self.assertEqual(before, self.frozen())

    def test_existing_output_cannot_be_used_to_resubmit_or_overwrite(self):
        out = self.temp / "same-output"
        generate = Mock(side_effect=self.response)
        tool.run(self.prepared, out, generate)
        with self.assertRaises(FileExistsError):
            tool.run(self.prepared, out, generate)
        self.assertEqual(generate.call_count, 1)

    def test_source_job_output_path_and_ambiguous_parent_are_rejected(self):
        generate = Mock()
        with self.assertRaisesRegex(ValueError, "output_must_be_separate"):
            tool.run(self.prepared, self.folder / "child", generate)
        generate.assert_not_called()
        other = self.source / "jobs/other"
        other.mkdir()
        (other / "result.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source_requires_one_completed_job"):
            tool.prepare(self.root, self.source, {"env_profile": "LOCAL"})
        exact = tool.prepare(self.root, self.folder, {"env_profile": "LOCAL"})
        self.assertEqual(exact["source_folder"], self.folder)

    def test_nonlocal_profile_fails_before_runtime_loading(self):
        with patch.object(tool, "modules") as loader:
            with self.assertRaisesRegex(ValueError, "explicit_LOCAL_profile_required"):
                tool.prepare(self.root, self.source, {"env_profile": "PRD"})
            loader.assert_not_called()

    def test_live_cancel_does_not_execute_and_default_source_is_visible(self):
        with (
            patch.object(tool, "ask", return_value="NO") as ask,
            patch.object(tool, "execute") as execute,
            patch.dict(
                tool.os.environ, {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}
            ),
        ):
            result = tool.main(
                [
                    "--project-root",
                    str(self.root),
                    "--source",
                    str(self.source),
                    "--live",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(ask.call_count, 1)
        execute.assert_not_called()

    def test_post_request_change_keeps_response_and_marks_manual_review(self):
        def generate(request):
            response = self.response(request)
            self.settings.write_text(
                json.dumps({"enabled": True, "timeout_seconds": 300}), encoding="utf-8"
            )
            return response

        result = tool.run(self.prepared, self.temp / "post-change", generate)
        self.assertEqual(result["status"], "manual_review")
        self.assertFalse(result["recommendation_ready"])
        self.assertFalse(result["source_snapshot_unchanged_after_request"])
        self.assertIsNone(result["original_report_unchanged"])
        self.assertTrue((Path(result["output_dir"]) / "response.json").is_file())

    def test_main_reports_safe_budget_reason_without_service_execution(self):
        with (
            patch.object(
                tool, "prepare", side_effect=ValueError("input_budget_exceeded")
            ),
            patch.object(tool, "execute") as execute,
            patch("builtins.print") as output,
        ):
            self.assertEqual(tool.main(["--source", str(self.source)]), 1)
        self.assertIn("input_budget_exceeded", output.call_args.args[0])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
