"""Exercise real transport adapter with an in-memory SDK double; no network."""

import copy
import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch


HERE = Path(__file__).resolve().parent
BATCH = HERE.parent / "step06y_historical_batches"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ai_review = load("ai_review", HERE / "ai_review.py")
sampling = load("sampling", HERE / "sampling.py")
context_budget = load("context_budget", HERE / "context_budget.py")
historical_adapter = load("historical_adapter", BATCH / "adapter.py")
load("surge_materials", HERE / "materials.py")
runner = load("surge_runner_under_test", HERE / "runner.py")


def source_case():
    return {
        "rule": {
            "topic": "synthetic-vendor",
            "rule_code": "SYNTHETIC",
            "rule_name": "人工规则",
            "company_code": "DEMO-COMPANY",
            "rule_config_version": "synthetic-1",
            "configuration": {"terms": []},
        },
        "current_samples": [
            {
                "alert_id": f"now-{index:03d}",
                "occurred_at": f"2026-11-02T10:00:{index:02d}",
                "raw_logs": [
                    {
                        "message": "routine" if index % 2 else "other",
                        "payload": "X" * 1000,
                    }
                ],
            }
            for index in range(30)
        ],
        "history_samples": [
            {
                "alert_id": f"old-{index}",
                "occurred_at": "2026-10-31T10:00:00",
                "raw_logs": [{"message": "earlier", "payload": "previous"}],
            }
            for index in range(2)
        ],
        "coverage": {"current_alert_count": 30, "history_selected_unique_alerts": 2},
    }


def prepared(root):
    case = source_case()
    limits = context_budget.ContextBudget(context_tokens=20000)
    return NS(
        root=root,
        case=case,
        model="Pub_DeepSeek_V4_Flash",
        limits=limits,
        generation=sampling.select(case, limits),
        snapshot={},
    )


def model_reply(kwargs, *, status="proposal"):
    payload = json.loads(kwargs["messages"][1]["content"])
    return {
        "status": status,
        "issue": "人工重复场景",
        "recommendation": "运营确认后处理",
        "sample_alert_ids": [payload["current_samples"][0]["alert_id"]]
        if status == "proposal"
        else [],
        "filter": {
            "topic": "synthetic-vendor",
            "A": {"name": "message", "operator": "contain", "value": "routine"},
            "expression": "A",
        }
        if status == "proposal"
        else None,
        "review_notes": "根据本轮原文进行复核，没有执行过滤条件。",
        "sample_request": {"field": "message", "text": "routine"}
        if payload["phase"] == "generate"
        else None,
    }


def response(content, *, finish="stop"):
    return NS(
        model="internal-provider-label",
        usage=NS(prompt_tokens=4000, completion_tokens=300, total_tokens=4300),
        choices=[
            NS(
                index=0,
                finish_reason=finish,
                message=NS(
                    role="assistant",
                    content=content,
                    refusal=None,
                    tool_calls=None,
                    function_call=None,
                ),
            )
        ],
    )


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.p = prepared(self.directory)
        self.client = Mock()
        self.client.base_url = "http://127.0.0.1:4001/v1/"
        self.client.with_options.return_value = self.client
        self.client.chat.completions.create.side_effect = self.normal_answer
        self.requests = []

    def normal_answer(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return response(json.dumps(model_reply(kwargs), ensure_ascii=False))

    def invoke(self, prepared, selection, child):
        return runner.call_model(
            prepared, selection, child, self.client, transport_mode="mock"
        )

    def run_chain(self, *, invoke=None, check=None):
        with redirect_stdout(io.StringIO()):
            return runner.run_chain(
                self.p, self.directory, invoke or self.invoke, check=check
            )

    def test_readable_revision_cli_exits_normally_and_prints_report(self):
        self.p.case.update(audit={}, original_rule=self.p.case["rule"])

        def local_chain(prepared, directory):
            (directory / "REPORT.md").write_text(
                "建议待调整：原文保留", encoding="utf-8"
            )
            return {"status": "needs_revision", "recommendation_ready": False}

        output = io.StringIO()
        with (
            patch.object(Path, "home", return_value=self.directory),
            patch.object(runner, "prepare", return_value=self.p),
            patch.object(runner, "ask", side_effect=["", "", "LIVE"]),
            patch.object(runner, "live_chain", side_effect=local_chain) as chain,
            redirect_stdout(output),
        ):
            self.assertEqual(runner.main(), 0)
        chain.assert_called_once()
        self.assertIn("建议待调整：原文保留", output.getvalue())

    def test_two_real_adapter_sdk_invocations_fresh_contexts_no_output_cap_or_retries(
        self,
    ):
        summary = self.run_chain()
        self.assertEqual(summary["status"], "reviewed", summary)
        self.assertEqual(summary["sdk_request_calls"], 2)
        self.assertEqual(self.client.chat.completions.create.call_count, 2)
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assertEqual(request["model"], "Pub_DeepSeek_V4_Flash")
            self.assertEqual(len(request["messages"]), 2)
            self.assertEqual(
                [m["role"] for m in request["messages"]], ["system", "user"]
            )
            self.assertFalse(
                {"max_tokens", "max_completion_tokens", "max_output_tokens"}
                & set(request)
            )
            self.assertEqual(request["extra_body"]["timeout"], 600)
        self.assertTrue(
            all(
                call.kwargs == {"max_retries": 0, "timeout": 600}
                for call in self.client.with_options.call_args_list
            )
        )
        first, second = [json.loads(r["messages"][1]["content"]) for r in self.requests]
        self.assertEqual(first["phase"], "generate")
        self.assertEqual(second["phase"], "review")
        self.assertNotIn("draft", first)
        self.assertIn("draft", second)
        self.assertEqual(first["history_samples"], second["history_samples"])
        first_ids = {s["alert_id"] for s in first["current_samples"]}
        second_ids = {s["alert_id"] for s in second["current_samples"]}
        self.assertTrue(second_ids - first_ids)
        self.assertTrue(
            set(second["draft"]["sample_alert_ids"]).issubset(first_ids | second_ids)
        )

    def test_raw_answers_saved_with_transport_usage_and_private_permissions(self):
        summary = self.run_chain()
        for phase in ("generate", "review"):
            child = self.directory / phase
            raw = (child / "assistant-content.txt").read_text(encoding="utf-8")
            result = json.loads((child / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["draft"]["filter"], json.loads(raw)["filter"])
            self.assertEqual(result["model_transport"]["usage"]["prompt_tokens"], 4000)
            self.assertEqual(
                result["model_transport"]["returned_model"], "internal-provider-label"
            )
            self.assertEqual(
                stat.S_IMODE((child / "assistant-content.txt").stat().st_mode), 0o600
            )
        self.assertTrue((self.directory / "recommendation.json").is_file())
        self.assertFalse(summary["automatic_apply"])

    def test_second_invalid_reply_saved_but_never_delivered_as_final_recommendation(
        self,
    ):
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            if len(self.requests) == 2:
                return response("```json\nnot valid json\n```")
            return response(json.dumps(model_reply(kwargs)))

        self.client.chat.completions.create.side_effect = create
        summary = self.run_chain()
        self.assertNotEqual(summary["status"], "reviewed")
        self.assertEqual(summary["sdk_request_calls"], 2)
        self.assertFalse((self.directory / "recommendation.json").exists())
        self.assertIn(
            "not valid json",
            (self.directory / "review/assistant-content.txt").read_text(),
        )
        self.assertEqual(self.client.chat.completions.create.call_count, 2)
        report = (self.directory / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("invalid_json", report)
        self.assertIn("not valid json", report)

    def test_generation_field_issue_is_repaired_by_existing_review_call(self):
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            reply = model_reply(kwargs)
            if len(self.requests) == 1:
                reply["filter"]["A"]["name"] = "detail.message"
            else:
                context = json.loads(kwargs["messages"][1]["content"])
                self.assertEqual(
                    context["draft"]["filter"]["A"]["name"], "detail.message"
                )
                self.assertEqual(
                    context["draft_validation_issues"][0]["field"], "detail.message"
                )
                self.assertIn("message", context["visible_field_names"])
            return response(json.dumps(reply))

        self.client.chat.completions.create.side_effect = create
        summary = self.run_chain()
        self.assertEqual(summary["status"], "reviewed", summary)
        self.assertEqual(summary["phases"][0]["status"], "needs_revision")
        self.assertEqual(summary["sdk_request_calls"], 2)
        self.assertTrue(summary["recommendation_ready"])
        self.assertTrue((self.directory / "recommendation.json").exists())
        first = json.loads((self.directory / "generate/result.json").read_text())
        self.assertEqual(first["draft"]["filter"]["A"]["name"], "detail.message")

    def test_final_missing_fields_remain_readable_draft_without_ready_artifact(self):
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            reply = model_reply(kwargs)
            if len(self.requests) == 2:
                reply["filter"]["A"]["name"] = "detail.message"
                reply["filter"]["B"] = {
                    "name": "invented.command",
                    "operator": "contain",
                    "value": "routine",
                }
                reply["filter"]["expression"] = "A && B"
                reply["review_notes"] = "保留原分析，但字段还需修改。"
            return response(json.dumps(reply, ensure_ascii=False))

        self.client.chat.completions.create.side_effect = create
        summary = self.run_chain()
        self.assertEqual(summary["status"], "needs_revision", summary)
        self.assertEqual(summary["analysis_status"], "proposal")
        self.assertEqual(summary["sdk_request_calls"], 2)
        self.assertFalse(summary["recommendation_ready"])
        self.assertEqual(len(summary["validation_issues"]), 2)
        self.assertFalse((self.directory / "recommendation.json").exists())
        draft = json.loads((self.directory / "draft-recommendation.json").read_text())
        self.assertEqual(draft["filter"]["A"]["name"], "detail.message")
        self.assertEqual(draft["filter"]["B"]["name"], "invented.command")
        report = (self.directory / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("待修改的字段", report)
        self.assertIn("filter.A.name", report)
        self.assertIn("filter.B.name", report)
        self.assertIn("message", report)
        self.assertIn("保留原分析，但字段还需修改。", report)
        self.assertIn("人工重复场景", report)
        self.assertNotIn("本次未完成两步AI复核", report)

    def test_true_structure_failure_shows_error_path_and_original_answer(self):
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            reply = model_reply(kwargs)
            if len(self.requests) == 2:
                reply["filter"]["expression"] = "A &&"
            return response(json.dumps(reply, ensure_ascii=False))

        self.client.chat.completions.create.side_effect = create
        summary = self.run_chain()
        self.assertEqual(summary["status"], "failed", summary)
        self.assertEqual(summary["phases"][1]["error_path"], ["filter", "expression"])
        report = (self.directory / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("incomplete_expression", report)
        self.assertIn('"expression": "A &&"', report)
        self.assertIn("filter", report)
        self.assertFalse((self.directory / "recommendation.json").exists())

    def test_final_optional_sample_request_does_not_block_core_proposal(self):
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            reply = model_reply(kwargs)
            if len(self.requests) == 2:
                reply["sample_request"] = {
                    "field": "nonexistent.extra",
                    "text": "unneeded",
                }
            return response(json.dumps(reply))

        self.client.chat.completions.create.side_effect = create
        summary = self.run_chain()
        self.assertEqual(summary["status"], "reviewed", summary)
        self.assertEqual(summary["sdk_request_calls"], 2)
        self.assertIn(
            "review_sample_request_ignored", summary["phases"][1]["output_warnings"]
        )
        self.assertTrue(summary["recommendation_ready"])
        original = json.loads(
            (self.directory / "review/assistant-content.txt").read_text()
        )
        self.assertEqual(original["sample_request"]["field"], "nonexistent.extra")

    def test_second_transport_failure_does_not_retry_or_leak_exception_text(self):
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            if len(self.requests) == 2:
                raise RuntimeError("synthetic-secret-credential")
            return response(json.dumps(model_reply(kwargs)))

        self.client.chat.completions.create.side_effect = create
        summary = self.run_chain()
        self.assertNotEqual(summary["status"], "reviewed")
        self.assertEqual(summary["sdk_request_calls"], 2)
        self.assertEqual(self.client.chat.completions.create.call_count, 2)
        self.assertFalse((self.directory / "recommendation.json").exists())
        for path in self.directory.rglob("*"):
            if path.is_file():
                self.assertNotIn("synthetic-secret-credential", path.read_text())

    def test_first_failure_stops_before_second_call(self):
        self.client.chat.completions.create.return_value = None
        self.client.chat.completions.create.side_effect = lambda **kwargs: response(
            "{}"
        )
        summary = self.run_chain()
        self.assertNotEqual(summary["status"], "reviewed")
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertFalse((self.directory / "review").exists())
        self.assertFalse((self.directory / "recommendation.json").exists())

    def test_http_error_body_saved_privately_without_leaking_into_summary(self):
        class BadRequestError(Exception):
            status_code = 400
            body = {
                "error": {
                    "message": "synthetic provider detail",
                    "code": "test_rejection",
                }
            }

        self.client.chat.completions.create.side_effect = BadRequestError("not printed")
        summary = self.run_chain()
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertEqual(summary["phases"][0]["http_status"], 400)
        self.assertTrue(summary["phases"][0]["http_error_response_received"])
        self.assertEqual(
            summary["phases"][0]["provider_error_save_status"], "saved_locally"
        )
        saved = self.directory / "generate/provider-error.json"
        self.assertEqual(json.loads(saved.read_text())["body"], BadRequestError.body)
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
        self.assertNotIn("synthetic provider detail", json.dumps(summary))
        self.assertFalse((self.directory / "review").exists())

    def test_provider_truncation_rejected_even_if_json_looks_complete(self):
        self.client.chat.completions.create.side_effect = lambda **kwargs: response(
            json.dumps(model_reply(kwargs)), finish="length"
        )
        summary = self.run_chain()
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertNotEqual(summary["status"], "reviewed")
        self.assertEqual(
            summary["phases"][0]["transport"]["error_code"], "incomplete_response"
        )

    def test_reviewer_can_withdraw_first_proposal(self):
        def create(**kwargs):
            self.requests.append(copy.deepcopy(kwargs))
            status = "proposal" if len(self.requests) == 1 else "no_change"
            return response(json.dumps(model_reply(kwargs, status=status)))

        self.client.chat.completions.create.side_effect = create
        summary = self.run_chain()
        self.assertEqual(summary["status"], "reviewed")
        self.assertEqual(summary["analysis_status"], "no_change")
        final = json.loads((self.directory / "recommendation.json").read_text())
        self.assertFalse(final["recommendation_produced"])
        self.assertIsNone(final["filter"])
        self.assertIsNone(final["change_type"])

    def test_configuration_change_after_generation_prevents_review_and_final(self):
        configuration = self.directory / "frozen-config.json"
        configuration.write_text("{}", encoding="utf-8")
        self.p.snapshot = {str(configuration): runner.file_hash(configuration)}

        def invoke(prepared, selection, child):
            result = self.invoke(prepared, selection, child)
            configuration.write_text('{"changed":true}', encoding="utf-8")
            return result

        summary = self.run_chain(invoke=invoke, check=lambda: runner.verify(self.p))
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertNotEqual(summary["status"], "reviewed")
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertFalse((self.directory / "recommendation.json").exists())

    def test_output_save_failure_cannot_report_reviewed(self):
        original = runner.save

        def save(directory, name, value):
            if name == "recommendation.json":
                raise OSError("synthetic write failure")
            return original(directory, name, value)

        with patch.object(runner, "save", side_effect=save):
            summary = self.run_chain()
        self.assertNotEqual(summary["status"], "reviewed")
        self.assertEqual(summary["sdk_request_calls"], 2)
        self.assertFalse((self.directory / "recommendation.json").exists())

    def test_exception_after_sdk_cannot_claim_zero_requests(self):
        def invoke(prepared, selection, child):
            self.invoke(prepared, selection, child)
            raise RuntimeError("synthetic post-call lease failure")

        summary = self.run_chain(invoke=invoke)
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertNotEqual(summary["sdk_request_calls"], 0)
        self.assertNotEqual(summary["status"], "reviewed")
        self.assertFalse((self.directory / "recommendation.json").exists())

    def test_no_program_filter_evaluation_or_claimed_coverage(self):
        evaluator = ModuleType("filter_evaluation")
        evaluator.evaluate_filter = Mock(
            side_effect=AssertionError("must not execute filter")
        )
        with patch.dict(sys.modules, {"filter_evaluation": evaluator}):
            summary = self.run_chain()
        evaluator.evaluate_filter.assert_not_called()
        self.assertFalse(summary["filter_replay_performed"])
        self.assertFalse(summary["filter_coverage_measured"])
        self.assertNotIn("sample_match_counts", summary)
        self.assertNotIn("reduction_percent", summary)

    def test_history_available_and_each_phase_visible_counts_remain_distinct(self):
        generation_case = copy.deepcopy(self.p.case)
        generation_case["history_samples"] = generation_case["history_samples"][:1]
        self.p.generation = sampling.select(generation_case, self.p.limits)
        real_select = sampling.select

        def review_with_no_history(source, limits, **kwargs):
            review_case = copy.deepcopy(source)
            review_case["history_samples"] = []
            return real_select(review_case, limits, **kwargs)

        with patch.object(sampling, "select", side_effect=review_with_no_history):
            summary = self.run_chain()
        self.assertEqual(summary["status"], "reviewed", summary)
        self.assertEqual(summary["history_available_count"], 2)
        self.assertEqual(summary["history_count"], 0)
        self.assertEqual(
            [phase["selection"]["history_count"] for phase in summary["phases"]],
            [1, 0],
        )
        report = (self.directory / "REPORT.md").read_text(encoding="utf-8")
        self.assertRegex(report, r"可用[^\n]*2")
        self.assertRegex(report, r"生成[^\n]*1")
        self.assertRegex(report, r"复核[^\n]*0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
