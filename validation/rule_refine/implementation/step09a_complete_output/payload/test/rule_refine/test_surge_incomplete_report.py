"""Incomplete final answers stay blocked and diagnosable without a model retry."""

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "_incomplete_report_fixtures", HERE / "test_surge_processor.py"
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
module, ai = fixtures.module, fixtures.module.ai


class IncompleteReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.signal = fixtures.signal()
        self.case = fixtures.case()
        self.generate = Mock(
            side_effect=lambda request: fixtures.transport(
                json.dumps(fixtures.answer(request), ensure_ascii=False)
            )
        )
        self.processor = module.SurgeProcessor(
            self.root,
            load_case=lambda _: copy.deepcopy(self.case),
            load_rules=lambda: Mock(get_rule=Mock(return_value=fixtures.rule())),
            generate=self.generate,
            model="synthetic-model",
            budget=fixtures.Budget(),
        )

    def folder(self):
        return self.root / "jobs" / module._identity(self.signal)[2]

    def read(self, name):
        return json.loads((self.folder() / name).read_text(encoding="utf-8"))

    def report(self):
        return (self.folder() / "REPORT.md").read_text(encoding="utf-8")

    def snapshot(self):
        return {
            path.relative_to(self.folder()).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in self.folder().rglob("*")
            if path.is_file()
        }

    def test_stop_with_missing_filter_and_references_blocks_after_one_call(self):
        content = json.dumps(
            {
                "status": "proposal",
                "issue": "Synthetic repeated inventory operation.",
                "recommendation": "A prose-only suggestion is incomplete.",
                "certainty": "Evidence is limited because",
            }
        )
        self.generate.side_effect = lambda request: fixtures.transport(content)
        first = self.processor.process(self.signal)
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["reason"], "missing_reply_field")
        self.assertFalse(first["ai_review_completed"])
        self.assertFalse(first["recommendation_ready"])
        self.assertEqual(first["model_calls_this_attempt"], 1)
        self.assertFalse((self.folder() / "review").exists())
        self.assertFalse((self.folder() / "recommendation.json").exists())
        parsed = self.read("generate/parsed.json")
        self.assertEqual(parsed["error_path"], ["filter"])
        self.assertEqual(
            set(parsed["missing_reply_fields"]), {"filter", "sample_alert_ids"}
        )
        self.assertNotIn("reply", parsed)
        self.assertEqual(
            set(parsed["missing_template_fields"]),
            {"filter", "sample_alert_ids", "review_notes", "sample_request"},
        )
        self.assertEqual(first["validation_errors"][0]["path"], ["filter"])
        self.assertIn("不是可采纳建议", self.report())
        self.assertIn("missing_reply_field", self.report())
        self.assertIn("A prose-only suggestion is incomplete.", self.report())
        self.assertEqual(self.read("generate/response.json")["content"], content)
        original = self.snapshot()
        second = self.processor.process(self.signal)
        self.assertEqual(second["model_calls_this_attempt"], 0)
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(self.generate.call_count, 1)
        self.assertEqual(self.snapshot(), original)

    def test_present_filter_without_references_is_not_repaired_or_accepted(self):
        def generate(request):
            answer = fixtures.answer(request)
            del answer["sample_alert_ids"]
            return fixtures.transport(json.dumps(answer))

        self.generate.side_effect = generate
        outcome = self.processor.process(self.signal)
        self.assertEqual(outcome["reason"], "proposal_requires_sample_references")
        self.assertEqual(
            outcome["validation_errors"][0]["missing_reply_fields"],
            ["sample_alert_ids"],
        )
        self.assertEqual(self.generate.call_count, 1)
        self.assertNotIn("reply", self.read("generate/parsed.json"))

    def test_review_missing_filter_keeps_raw_reply_without_ready_recommendation(self):
        def generate(request):
            answer = fixtures.answer(request)
            if request["phase"] == "review":
                del answer["filter"]
            return fixtures.transport(json.dumps(answer))

        self.generate.side_effect = generate
        outcome = self.processor.process(self.signal)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["model_calls_this_attempt"], 2)
        self.assertFalse(outcome["recommendation_ready"])
        self.assertFalse(outcome["ai_review_completed"])
        self.assertEqual(outcome["validation_errors"][0]["phase"], "review")
        self.assertIn("阶段：review", self.report())
        self.assertFalse((self.folder() / "recommendation.json").exists())
        self.processor.process(self.signal)
        self.assertEqual(self.generate.call_count, 2)

    def test_valid_two_phase_reply_still_uses_two_calls_and_no_error_section(self):
        outcome = self.processor.process(self.signal)
        self.assertEqual(outcome["status"], "received")
        self.assertTrue(outcome["ai_review_completed"])
        self.assertEqual(outcome["model_calls_this_attempt"], 2)
        self.assertNotIn("validation_errors", outcome)
        self.assertNotIn("模型回答未通过校验", self.report())

    def test_invalid_json_is_shown_inertly_without_inline_reasoning(self):
        content = (
            "<think>HIDDEN_REASONING</think>\n"
            '<script>alert("unsafe")</script>\n# Fake report\n'
            '<analysis>HIDDEN_ANALYSIS</analysis>\n{"incomplete":'
        )
        self.generate.side_effect = lambda request: fixtures.transport(content)
        outcome = self.processor.process(self.signal)
        self.assertEqual(outcome["status"], "blocked")
        report = self.report()
        self.assertNotIn("HIDDEN_REASONING", report)
        self.assertNotIn("HIDDEN_ANALYSIS", report)
        self.assertNotIn("<script>", report)
        self.assertIn("    &lt;script&gt;", report)
        self.assertIn("    # Fake report", report)
        self.assertEqual(self.read("generate/response.json")["content"], content)

    def test_display_is_bounded_and_full_raw_answer_stays_saved(self):
        content = "x" * (module.FAILED_REPLY_DISPLAY_LIMIT + 1000)
        self.generate.side_effect = lambda request: fixtures.transport(content)
        self.processor.process(self.signal)
        self.assertIn("展示限24000字符", self.report())
        self.assertNotIn(content, self.report())
        self.assertEqual(self.read("generate/response.json")["content"], content)

    def test_unclosed_inline_reasoning_is_hidden_to_end_of_display(self):
        shown = "\n".join(module._failed_reply_display("visible<THINK>PRIVATE"))
        self.assertIn("visible", shown)
        self.assertNotIn("PRIVATE", shown)

    def test_old_contract_failure_report_and_saved_result_are_unchanged(self):
        # A deliberately synthetic prior-version request exercises the report
        # gate. It is not a captured provider answer or model-quality evidence.
        real_select = module.sampling.select

        def select_old(*args, **kwargs):
            selection = real_select(*args, **kwargs)
            request = selection["request"]
            request["prompt_version"] = "rule-surge-runtime-independent-generation-v5"
            request["context"].pop("output_contract_version")
            request["messages"] = request["messages"][:2]
            request["messages"][0]["content"] = (
                "Synthetic frozen prior output contract."
            )
            request["messages"][1]["content"] = ai.encoded(request["context"]).decode()
            request["prompt_sha256"] = hashlib.sha256(
                ai.encoded(request["messages"])
            ).hexdigest()
            return selection

        self.generate.side_effect = lambda request: fixtures.transport(
            json.dumps({"status": "proposal", "issue": "old", "recommendation": "old"})
        )
        with patch.object(module.sampling, "select", side_effect=select_old):
            outcome = self.processor.process(self.signal)
        self.assertNotIn("validation_errors", outcome)
        parsed = self.read("generate/parsed.json")
        self.assertNotIn("missing_reply_fields", parsed)
        self.assertNotIn("missing_template_fields", parsed)
        count = self.read("generate/selection.json")["selection"]["current_count"]
        expected = (
            "# 规则告警突增过滤建议（本地记录）\n\n"
            "规则：test-rule；状态：blocked。\n"
            "生成与复核使用分别保存的原始样本；历史对照不是正常业务标签。\n"
            "未执行过滤条件，未测量全量覆盖率或线上降量率，未提交或应用规则。\n\n"
            f"generate：当前 {count} 条、历史 1 条；状态 analysis_failed。\n\n"
            "处理说明：missing_reply_field\n"
        )
        self.assertEqual(self.report(), expected)
        before = self.snapshot()
        again = self.processor.process(self.signal)
        self.assertEqual(again["record_id"], outcome["record_id"])
        self.assertEqual(again["model_calls_this_attempt"], 0)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
