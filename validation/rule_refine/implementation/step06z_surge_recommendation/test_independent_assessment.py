"""Independent frozen-evidence assessment; synthetic fixtures and SDK doubles only."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_resume_review import resume
from test_runner import HERE, ai_review, load, model_reply, prepared, response

independent = load("independent_assessment", HERE / "independent_assessment.py")
runner = independent.runner


class IndependentAssessmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "new"
        self.output.mkdir()
        self.p = prepared(self.root)
        self.p.case["audit"] = {"source": "synthetic"}
        draft = ai_review.parse_reply(
            json.dumps(model_reply(self.p.generation["request"])),
            rule=self.p.case["rule"],
            visible_samples=self.p.generation["current_samples"],
        )
        draft["issue"] = "OLD_DRAFT_MUST_NOT_ENTER_NEW_REQUEST"
        request = ai_review.build_request(
            self.p.case["rule"],
            self.p.case["current_samples"][:8],
            self.p.case["history_samples"],
            {**self.p.case["coverage"], "new_current_samples": 7},
            draft=draft,
            phase="review",
            operator_context="synthetic operator context",
            limits=self.p.limits,
        )
        self.saved_materials = copy.deepcopy(request["context"])
        self.original = self.root / "original-review.txt"
        self.original.write_text(
            "OLD_REVIEW_MUST_NOT_ENTER_NEW_REQUEST", encoding="utf-8"
        )
        self.original_bytes = self.original.read_bytes()
        self.p.snapshot[str(self.original)] = runner.file_hash(self.original)
        recovered = {
            "review_selection": {"request": request},
            "provenance": {
                "original_report": str(self.root),
                "original_response_sha256": runner.file_hash(self.original),
            },
        }
        self.prepare_saved = Mock(return_value=(self.p, recovered))
        self.p, self.provenance = independent.prepare(
            self.root, self.root, prepare_saved=self.prepare_saved
        )
        self.client = Mock()
        self.client.base_url = "http://127.0.0.1:4001/v1/"
        self.client.with_options.return_value = self.client
        self.client.chat.completions.create.side_effect = lambda **kwargs: response(
            json.dumps(model_reply(kwargs), ensure_ascii=False)
        )

    def invoke(self, p, selection, child):
        return runner.call_model(
            p,
            selection,
            child,
            self.client,
            transport_mode="mock",
            response_format=independent.RESPONSE_FORMAT,
        )

    def run_once(self, *, invoke=None, check=None):
        with redirect_stdout(io.StringIO()):
            return independent.single_chain(
                self.p, self.output, invoke or self.invoke, check=check
            )

    def test_prepare_retains_exact_raw_samples_and_omits_both_old_answers(self):
        self.assertIs(independent.resume_review, resume)
        self.prepare_saved.assert_called_once_with(
            self.root, self.root, reuse_review_samples=True
        )
        request = self.p.independent_selection["request"]
        context = request["context"]
        self.assertEqual(
            context["current_samples"], self.saved_materials["current_samples"]
        )
        self.assertEqual(
            context["history_samples"], self.saved_materials["history_samples"]
        )
        self.assertEqual(
            context["operator_context"], self.saved_materials["operator_context"]
        )
        self.assertNotIn("draft", context)
        self.assertNotIn("OLD_DRAFT_MUST_NOT_ENTER_NEW_REQUEST", json.dumps(request))
        self.assertNotIn("OLD_REVIEW_MUST_NOT_ENTER_NEW_REQUEST", json.dumps(request))
        self.assertEqual(context["coverage"]["new_current_samples"], 0)
        self.assertEqual(context["coverage"]["reused_current_samples"], 8)
        self.assertEqual(
            self.p.independent_selection["selection"]["new_current_count"], 0
        )
        self.assertTrue(self.provenance["original_proposal_not_provided"])

    def test_one_json_mode_request_no_output_cap_preserves_originals(self):
        check = Mock(side_effect=lambda: runner.verify(self.p))
        summary = self.run_once(check=check)
        self.assertEqual(summary["status"], "proposal", summary)
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertEqual(check.call_count, 2)
        self.client.chat.completions.create.assert_called_once()
        request = self.client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)
        self.assertEqual(
            summary["transport"]["requested_response_format"], {"type": "json_object"}
        )
        self.assertFalse(summary["transport"]["response_format_enforcement_verified"])
        self.assertFalse(summary["ai_review_completed"])
        self.assertFalse(summary["semantic_quality_verified"])
        self.assertTrue(summary["original_proposal_not_provided"])
        self.assertTrue((self.output / "recommendation.json").exists())
        self.assertEqual(self.original.read_bytes(), self.original_bytes)

    def test_invalid_json_saved_raw_and_never_published(self):
        raw = '{"review_notes": "synthetic", "unkeyed text"}'
        self.client.chat.completions.create.side_effect = lambda **kwargs: response(raw)
        summary = self.run_once()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertEqual(summary["error_code"], "invalid_json")
        self.assertFalse((self.output / "recommendation.json").exists())
        self.assertFalse((self.output / "draft-recommendation.json").exists())
        self.assertEqual(
            (self.output / "assessment/assistant-content.txt").read_text(
                encoding="utf-8"
            ),
            raw,
        )
        self.assertIn(raw, (self.output / "REPORT.md").read_text(encoding="utf-8"))

    def test_invisible_field_is_draft_never_ready(self):
        def answer(**kwargs):
            draft = model_reply(kwargs)
            draft["filter"]["A"]["name"] = "invented.nested.field"
            return response(json.dumps(draft))

        self.client.chat.completions.create.side_effect = answer
        summary = self.run_once()
        self.assertEqual(summary["status"], "needs_revision", summary)
        self.assertFalse(summary["recommendation_ready"])
        self.assertEqual(summary["validation_issues"][0]["code"], "field_not_visible")
        self.assertFalse((self.output / "recommendation.json").exists())
        self.assertTrue((self.output / "draft-recommendation.json").exists())

    def test_invented_sample_reference_fails_without_published_recommendation(self):
        def answer(**kwargs):
            draft = model_reply(kwargs)
            draft["sample_alert_ids"] = ["never-shown-synthetic-alert"]
            return response(json.dumps(draft))

        self.client.chat.completions.create.side_effect = answer
        summary = self.run_once()
        self.assertEqual(summary["status"], "failed", summary)
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertFalse((self.output / "recommendation.json").exists())
        self.assertFalse((self.output / "draft-recommendation.json").exists())
        self.assertTrue((self.output / "assessment/assistant-content.txt").exists())

    def test_valid_no_change_is_not_chain_completion(self):
        self.client.chat.completions.create.side_effect = lambda **kwargs: response(
            json.dumps(model_reply(kwargs, status="no_change"))
        )
        summary = self.run_once()
        self.assertEqual(summary["status"], "no_change", summary)
        self.assertFalse(summary["ai_review_completed"])
        self.assertFalse(summary["filter_coverage_measured"])
        self.assertEqual(summary["review_method"], "independent_reassessment")

    def test_missing_or_null_optional_review_notes_still_produces_report(self):
        for omitted in (True, False):
            with self.subTest(omitted=omitted):
                output = self.root / ("missing-notes" if omitted else "null-notes")
                output.mkdir()

                def answer(**kwargs):
                    draft = model_reply(kwargs)
                    if omitted:
                        draft.pop("review_notes")
                    else:
                        draft["review_notes"] = None
                    return response(json.dumps(draft))

                self.client.chat.completions.create.side_effect = answer
                with redirect_stdout(io.StringIO()):
                    summary = independent.single_chain(self.p, output, self.invoke)
                self.assertEqual(summary["status"], "proposal", summary)
                self.assertEqual(
                    summary["validation_scope"], ai_review.VALIDATION_SCOPE
                )
                self.assertTrue((output / "summary.json").exists())
                self.assertTrue((output / "recommendation.json").exists())
                report = (output / "REPORT.md").read_text(encoding="utf-8")
                self.assertIn("## 分析说明\n\n未提供", report)

    def test_source_changes_after_response_prevent_publication(self):
        count = 0

        def verify():
            nonlocal count
            count += 1
            if count == 2:
                self.original.write_text("changed", encoding="utf-8")
            runner.verify(self.p)

        summary = self.run_once(check=verify)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertFalse(summary["original_report_unchanged"])
        self.assertFalse((self.output / "recommendation.json").exists())

    def test_started_request_with_lost_result_reports_unknown_call_count(self):
        def lost_result(p, selection, child):
            runner.save(child, "request-started.json", {"max_sdk_calls": 1})
            raise OSError("synthetic persistence failure")

        summary = self.run_once(invoke=lost_result)
        self.assertEqual(summary["status"], "failed")
        self.assertIsNone(summary["sdk_request_calls"])
        self.assertFalse((self.output / "recommendation.json").exists())

    def test_decline_makes_no_call_or_output_directory(self):
        output = io.StringIO()
        with (
            patch.object(Path, "home", return_value=self.root),
            patch.object(
                independent, "prepare", return_value=(self.p, self.provenance)
            ),
            patch.object(runner, "ask", side_effect=["", "NO"]),
            patch.object(runner, "live_chain") as chain,
            redirect_stdout(output),
        ):
            self.assertEqual(independent.main(), 0)
        chain.assert_not_called()
        self.assertFalse((self.root / "sec-rule-refine-data").exists())
        self.assertIn(
            '"response_format_enforcement_verified": false', output.getvalue()
        )

    def test_main_wires_one_processor_with_json_mode(self):
        def chain(p, directory, **kwargs):
            self.assertIs(kwargs["processor"], independent.single_chain)
            self.assertEqual(kwargs["response_format"], {"type": "json_object"})
            self.assertNotIn("recovered_generation", kwargs)
            return kwargs["processor"](p, directory, self.invoke)

        with (
            patch.object(Path, "home", return_value=self.root),
            patch.object(
                independent, "prepare", return_value=(self.p, self.provenance)
            ),
            patch.object(runner, "ask", side_effect=["", "LIVE"]),
            patch.object(runner, "live_chain", side_effect=chain) as live,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(independent.main(tool_hashes={"synthetic": "digest"}), 0)
        live.assert_called_once()
        self.client.chat.completions.create.assert_called_once()
        self.assertEqual(
            len(list((self.root / "sec-rule-refine-data").glob("surge-independent-*"))),
            1,
        )
        self.assertEqual(self.original.read_bytes(), self.original_bytes)


if __name__ == "__main__":
    unittest.main()
