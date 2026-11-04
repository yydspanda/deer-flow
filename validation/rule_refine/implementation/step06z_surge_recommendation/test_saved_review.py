"""Recover a paid review response offline, without promoting extra model fields."""

import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_review
import saved_review


class SavedReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.source = self.parent / "source"
        self.source.mkdir()
        self.rule = {"topic": "demo", "rule_code": "demo-rule", "rule_name": "人工规则"}
        self.samples = [{"alert_id": "first", "raw_logs": [{"activity": "routine"}]}]
        self.new = {"alert_id": "new", "raw_logs": [{"activity": "different"}]}
        self.answer = {
            "status": "proposal",
            "issue": "人工示例",
            "recommendation": "确认业务后增加条件",
            "filter": {
                "topic": "demo",
                "A": {"name": "activity", "operator": "contain", "value": "routine"},
                "expression": "A",
            },
            "sample_alert_ids": ["first"],
        }
        self.generation_request = ai_review.build_request(
            self.rule, self.samples, [], {}
        )
        self.generation_text = "```json\n" + json.dumps(self.answer, ensure_ascii=False)
        self.draft = ai_review.parse_reply(
            self.generation_text, rule=self.rule, visible_samples=self.samples
        )
        self.review_request = ai_review.build_request(
            self.rule,
            self.samples + [self.new],
            [],
            {},
            draft=self.draft,
            phase="review",
        )
        self.review_answer = {
            **self.answer,
            "additional_notes": "人工补充说明",
            "automatic_apply": True,
            "review_status": "仍需运营核对",
            "format_valid": False,
        }
        self.record_phase(
            "generate", self.generation_request, self.generation_text, None
        )
        self.record_phase(
            "review",
            self.review_request,
            json.dumps(self.review_answer, ensure_ascii=False),
            "unexpected_reply_field",
        )
        self.write(self.source / "tool-hashes.json", {"ai_review": "0" * 64})

    def write(self, path, value):
        path.write_bytes(
            value if isinstance(value, bytes) else ai_review.encoded(value)
        )

    def record_phase(self, phase, request, text, old_error):
        child = self.source / phase
        child.mkdir(exist_ok=True)
        raw = text.encode("utf-8")
        self.write(child / "request.json", request)
        self.write(child / "assistant-content.txt", raw)
        self.write(
            child / "result.json",
            {
                "response_sha256": saved_review.saved.sha(raw),
                "error_code": old_error,
                "model_transport": {
                    "response_received": True,
                    "finish_reason": "stop",
                    "sdk_request_calls": 1,
                    "usage": {"prompt_tokens": 1000},
                },
            },
        )

    def replay(self):
        return saved_review.replay(self.source, tool_hashes={"ai_review": "1" * 64})

    def test_extra_fields_recover_both_stages_without_calls_or_source_changes(self):
        before = {
            path: path.read_bytes() for path in self.source.rglob("*") if path.is_file()
        }
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            summary = self.replay()
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["model_calls"], 0)
        self.assertEqual(summary["sdk_request_calls"], 0)
        self.assertTrue(summary["ai_review_response_recovered"])
        self.assertTrue(summary["saved_chain_verified"])
        self.assertEqual(
            [phase["sdk_request_calls_this_command"] for phase in summary["phases"]],
            [0, 0],
        )
        output = Path(summary["output_dir"])
        recommendation = json.loads(
            (output / "recommendation.json").read_text(encoding="utf-8")
        )
        self.assertFalse(recommendation["automatic_apply"])
        self.assertTrue(recommendation["format_valid"])
        self.assertTrue(recommendation["model_extra_fields"]["automatic_apply"])
        self.assertIn("extra_reply_fields_preserved", recommendation["output_warnings"])
        report = (output / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("仍需运营核对", report)
        self.assertIn("additional_notes", report)
        self.assertIn("没有再次调用模型", report)
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        for phase in ("generate", "review"):
            self.assertEqual(
                (output / phase / "request.json").read_bytes(),
                before[self.source / phase / "request.json"],
            )
            self.assertEqual(
                (output / phase / "assistant-content.txt").read_bytes(),
                before[self.source / phase / "assistant-content.txt"],
            )
        for path in [output, *output.rglob("*")]:
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600
            )

    def test_missing_core_field_stays_blocked_and_prints_saved_answer(self):
        del self.review_answer["recommendation"]
        self.record_phase(
            "review",
            self.review_request,
            json.dumps(self.review_answer, ensure_ascii=False),
            "unexpected_reply_field",
        )
        summary = self.replay()
        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["phases"][1]["error_path"], ["recommendation"])
        output = Path(summary["output_dir"])
        self.assertFalse((output / "recommendation.json").exists())
        self.assertIn(
            "additional_notes", (output / "REPORT.md").read_text(encoding="utf-8")
        )

    def test_unknown_fields_preserve_advice_and_all_issues_without_ready_export(self):
        self.review_answer["filter"] = {
            "topic": "demo",
            "A": {"name": "detail.activity", "operator": "equal", "value": "routine"},
            "B": {"name": "detail.command", "operator": "contain", "value": "probe"},
            "expression": "A && B",
        }
        self.review_answer["sample_request"] = {
            "field": "detail.command",
            "text": "probe",
        }
        raw = json.dumps(self.review_answer, ensure_ascii=False)
        self.record_phase("review", self.review_request, raw, "field_not_visible")
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            summary = self.replay()
        self.assertEqual(summary["status"], "needs_revision")
        self.assertEqual(summary["analysis_status"], "proposal")
        self.assertTrue(summary["ai_review_response_recovered"])
        self.assertFalse(summary["recommendation_ready"])
        issues = summary["phases"][1]["validation_issues"]
        self.assertEqual(
            [item["field"] for item in issues], ["detail.activity", "detail.command"]
        )
        output = Path(summary["output_dir"])
        self.assertFalse((output / "recommendation.json").exists())
        draft = json.loads((output / "draft-recommendation.json").read_text())
        self.assertEqual(draft["filter"], self.review_answer["filter"])
        self.assertFalse(draft["recommendation_ready"])
        report = (output / "REPORT.md").read_text(encoding="utf-8")
        for value in (
            "待调整",
            "detail.activity",
            "detail.command",
            "activity",
            self.review_answer["recommendation"],
        ):
            self.assertIn(value, report)
        self.assertEqual(
            (self.source / "review/assistant-content.txt").read_text(), raw
        )
        self.assertEqual(summary["model_calls"], 0)

    def test_unused_final_sample_request_does_not_invalidate_valid_advice(self):
        self.review_answer["sample_request"] = {"field": "nonexistent", "text": "probe"}
        self.record_phase(
            "review",
            self.review_request,
            json.dumps(self.review_answer),
            "field_not_visible",
        )
        summary = self.replay()
        self.assertEqual(summary["status"], "passed")
        self.assertTrue(summary["recommendation_ready"])
        self.assertIn(
            "review_sample_request_ignored", summary["phases"][1]["output_warnings"]
        )

    def test_saved_answer_change_detected(self):
        with (self.source / "review/assistant-content.txt").open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(ValueError, "saved_response_hash_mismatch"):
            self.replay()
        self.assertEqual(list(self.parent.iterdir()), [self.source])

    def test_review_prompt_change_detected(self):
        self.review_request["messages"][1]["content"] += " "
        self.write(self.source / "review/request.json", self.review_request)
        with self.assertRaisesRegex(ValueError, "saved_prompt_hash_mismatch"):
            self.replay()

    def test_unrelated_review_of_different_draft_cannot_complete_chain(self):
        changed_draft = {**self.draft, "recommendation": "different draft"}
        request = ai_review.build_request(
            self.rule,
            self.samples + [self.new],
            [],
            {},
            draft=changed_draft,
            phase="review",
        )
        self.record_phase(
            "review", request, json.dumps(self.review_answer), "unexpected_reply_field"
        )
        with self.assertRaisesRegex(
            ValueError, "saved_review_did_not_receive_original_draft"
        ):
            self.replay()

    def test_shared_reference_original_cannot_change_in_review(self):
        rows = copy.deepcopy(self.samples)
        rows[0]["raw_logs"] = [{"activity": "changed"}]
        request = ai_review.build_request(
            self.rule, rows, [], {}, draft=self.draft, phase="review"
        )
        self.record_phase(
            "review", request, json.dumps(self.review_answer), "unexpected_reply_field"
        )
        with self.assertRaisesRegex(ValueError, "saved_review_shared_sample_changed"):
            self.replay()

    def test_truncated_response_not_salvaged_as_final(self):
        path = self.source / "review/result.json"
        value = json.loads(path.read_text())
        value["model_transport"]["finish_reason"] = "length"
        self.write(path, value)
        with self.assertRaisesRegex(ValueError, "saved_transport_not_complete"):
            self.replay()

    def test_valid_no_change_result_is_recovered_without_exclusion(self):
        answer = {
            "status": "no_change",
            "issue": "证据不足",
            "recommendation": "保持现状",
            "filter": None,
            "additional_notes": "撤回原草案",
        }
        self.record_phase(
            "review",
            self.review_request,
            json.dumps(answer, ensure_ascii=False),
            "unexpected_reply_field",
        )
        summary = self.replay()
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["analysis_status"], "no_change")
        result = json.loads(
            (Path(summary["output_dir"]) / "recommendation.json").read_text()
        )
        self.assertIsNone(result["change_type"])
        self.assertIsNone(result["filter"])
        self.assertIn(
            "撤回原草案", (Path(summary["output_dir"]) / "REPORT.md").read_text()
        )

    def test_cannot_save_inside_original_report(self):
        with self.assertRaisesRegex(
            ValueError, "output_must_be_outside_original_report"
        ):
            saved_review.replay(self.source, output_parent=self.source / "replay")

    def test_generation_extra_caveats_must_reach_review_unchanged(self):
        answer = {**self.answer, "caveat": "先确认来源"}
        text = json.dumps(answer, ensure_ascii=False)
        self.record_phase("generate", self.generation_request, text, None)
        draft = ai_review.parse_reply(
            text, rule=self.rule, visible_samples=self.samples
        )
        for extra in (None, {"caveat": "changed"}, {"caveat": "先确认来源"}):
            supplied_draft = copy.deepcopy(draft)
            supplied_draft["model_extra_fields"] = extra or {}
            request = ai_review.build_request(
                self.rule,
                self.samples + [self.new],
                [],
                {},
                draft=supplied_draft,
                phase="review",
            )
            self.record_phase(
                "review",
                request,
                json.dumps(self.review_answer),
                "unexpected_reply_field",
            )
            with self.subTest(extra=extra):
                if extra == draft["model_extra_fields"]:
                    self.assertEqual(self.replay()["status"], "passed")
                else:
                    with self.assertRaisesRegex(
                        ValueError,
                        "saved_review_did_not_receive_generation_extra_fields",
                    ):
                        self.replay()


if __name__ == "__main__":
    unittest.main()
