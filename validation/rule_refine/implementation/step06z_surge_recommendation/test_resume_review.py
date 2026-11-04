"""Continuations reuse an immutable generation and issue at most one review call."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_runner import HERE, ai_review, load, model_reply, prepared, response, runner

load("saved_generation", HERE / "saved_generation.py")
# The embedded command uses this canonical module name.
resume_runner = load("surge_runner", HERE / "runner.py")
resume = load("resume_review", HERE / "resume_review.py")


class ResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "old"
        (self.source / "generate").mkdir(parents=True)
        self.p = prepared(self.root)
        self.current_file = self.root / "current.csv"
        self.current_file.write_text("synthetic source", encoding="utf-8")
        source_hash = runner.file_hash(self.current_file)
        self.p.snapshot = {str(self.current_file): source_hash}
        self.p.case["audit"] = {
            "sources": [{"scope": "current_file", "file_sha256": source_hash}],
            "coverage": self.p.case["coverage"],
            "source_rule_file_sha256": "synthetic-rule-source",
            "cutoff_basis": "earliest_nonconflicting_current_file_target_alert",
        }
        self.old_request = self.p.generation["request"]
        self.raw = ("```json\n" + json.dumps(model_reply(self.old_request))).encode(
            "utf-8"
        )
        self.old_result = {
            "status": "analysis_failed",
            "format_valid": False,
            "error_code": "invalid_json_fence",
            "response_sha256": resume.saved_generation.sha(self.raw),
            "model_transport": {
                "response_received": True,
                "finish_reason": "stop",
                "sdk_request_calls": 1,
                "requested_model": self.p.model,
                "usage": {"prompt_tokens": 4000},
            },
        }
        for name, value in (
            ("generate/request.json", self.old_request),
            ("generate/result.json", self.old_result),
            ("generate/assistant-content.txt", self.raw),
            ("generate/selection.json", self.p.generation["selection"]),
            ("tool-hashes.json", {"ai_review": "0" * 64}),
            ("case-audit.json", self.p.case["audit"]),
            ("configuration-hashes.json", self.p.snapshot),
        ):
            self.write(name, value)
        self.client = Mock()
        self.client.base_url = "http://127.0.0.1:4001/v1/"
        self.client.with_options.return_value = self.client
        self.calls = []

        def review_answer(**kwargs):
            self.calls.append(copy.deepcopy(kwargs))
            self.assertEqual(
                json.loads(kwargs["messages"][1]["content"])["phase"], "review"
            )
            return response(json.dumps(model_reply(kwargs)))

        self.client.chat.completions.create.side_effect = review_answer

    def write(self, name, value):
        (self.source / name).write_bytes(
            value if isinstance(value, bytes) else ai_review.encoded(value)
        )

    def prepare(self, *, reuse_review_samples=False):
        return resume.prepare(
            self.source,
            self.root,
            prepare_case=Mock(return_value=self.p),
            reuse_review_samples=reuse_review_samples,
        )

    def saved_review(self):
        _, record = self.prepare()
        chosen = copy.deepcopy(record["review_selection"])
        request = chosen["request"]
        request["prompt_version"] = "previous-prompt-version"
        request["messages"][0]["content"] = "Previous independent review prompt."
        request["prompt_sha256"] = resume.saved_generation.sha(
            ai_review.encoded(request["messages"])
        )
        raw = rb'{"sample_alert_ids\u3057\u305f": [], "review_notes": "OLD_CONTRADICTORY_REVIEW"}'
        old_result = copy.deepcopy(self.old_result)
        old_result["error_code"] = "unexpected_reply_field"
        old_result["response_sha256"] = resume.saved_generation.sha(raw)
        (self.source / "review").mkdir()
        for name, value in (
            ("review/request.json", request),
            ("review/selection.json", chosen["selection"]),
            ("review/assistant-content.txt", raw),
            ("review/result.json", old_result),
        ):
            self.write(name, value)
        return request, raw

    def run_review(self, p, record):
        output = self.root / "new-review"
        output.mkdir()

        def invoke(prepared_, selection, child):
            return runner.call_model(
                prepared_, selection, child, self.client, transport_mode="mock"
            )

        with redirect_stdout(io.StringIO()):
            result = runner.run_chain(
                p,
                output,
                invoke,
                check=lambda: runner.verify(p),
                recovered_generation=record,
            )
        return output, result

    def test_only_review_calls_sdk_original_prompt_and_answer_preserved(self):
        before = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            p, record = self.prepare()
            output, summary = self.run_review(p, record)
        self.assertEqual(summary["status"], "reviewed", summary)
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertTrue(summary["generation_reused"])
        self.assertEqual(
            [item["sdk_request_calls_this_command"] for item in summary["phases"]],
            [0, 1],
        )
        self.assertEqual(summary["phases"][0]["transport_origin"], "saved_request")
        self.assertEqual(
            summary["phases"][0]["transport"]["usage"]["prompt_tokens"], 4000
        )
        self.assertIn(
            "missing_closing_json_fence", summary["phases"][0]["output_warnings"]
        )
        self.assertFalse((output / "generate/request-started.json").exists())
        self.assertEqual(
            (output / "generate/assistant-content.txt").read_bytes(), self.raw
        )
        self.assertEqual(
            (output / "generate/request.json").read_bytes(),
            before[self.source / "generate/request.json"],
        )
        self.assertTrue((output / "recommendation.json").is_file())
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertNotIn("max_tokens", self.calls[0])
        review_context = json.loads(self.calls[0]["messages"][1]["content"])
        self.assertGreater(review_context["coverage"]["new_current_samples"], 0)
        by_id = {row["alert_id"]: row for row in review_context["current_samples"]}
        for row in self.old_request["context"]["current_samples"]:
            if row["alert_id"] in review_context["draft"]["sample_alert_ids"]:
                self.assertEqual(row, by_id[row["alert_id"]])

    def test_changed_saved_answer_rejected_before_prepare_case(self):
        self.write("generate/assistant-content.txt", self.raw + b"x")
        loader = Mock()
        with self.assertRaisesRegex(ValueError, "saved_response_hash_mismatch"):
            resume.prepare(self.source, self.root, prepare_case=loader)
        loader.assert_not_called()

    def test_changed_source_file_rejected_before_model_configuration(self):
        self.current_file.write_text("different", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "saved_input_has_changed"):
            self.prepare()

    def test_changed_visible_log_cannot_replace_original_evidence(self):
        identity = self.old_request["context"]["current_samples"][0]["alert_id"]
        row = next(
            row for row in self.p.case["current_samples"] if row["alert_id"] == identity
        )
        row["raw_logs"] = [{"message": "changed"}]
        with self.assertRaisesRegex(ValueError, "saved_visible_samples_changed"):
            self.prepare()

    def test_model_change_requires_new_explicit_setup(self):
        self.p.model = "different-model"
        with self.assertRaisesRegex(ValueError, "configured_model_differs"):
            self.prepare()

    def test_source_mutation_after_preview_stops_without_model_call(self):
        p, record = self.prepare()
        self.write("generate/assistant-content.txt", self.raw + b" ")
        _, summary = self.run_review(p, record)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["sdk_request_calls"], 0)
        self.client.chat.completions.create.assert_not_called()

    def test_review_failure_never_retries_generation_or_publishes_final(self):
        p, record = self.prepare()
        self.client.chat.completions.create.side_effect = lambda **kwargs: response(
            '{"status":', finish="length"
        )
        output, summary = self.run_review(p, record)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.client.chat.completions.create.assert_called_once()
        self.assertFalse((output / "recommendation.json").exists())

    def test_declined_live_preview_has_no_transport_call(self):
        p, record = self.prepare()
        with (
            patch.object(resume, "prepare", return_value=(p, record)),
            patch.object(resume.runner, "ask", side_effect=[str(self.source), "NO"]),
            patch.object(resume.runner, "live_chain") as live,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(resume.main(), 0)
        live.assert_not_called()

    def test_new_prompt_keeps_review_materials_and_only_reissues_review(self):
        old_request, old_answer = self.saved_review()
        before = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        with patch.object(
            resume.sampling, "select", side_effect=AssertionError("no resampling")
        ):
            p, record = self.prepare(reuse_review_samples=True)
        new_request = record["review_selection"]["request"]
        self.assertEqual(new_request["context"], old_request["context"])
        self.assertNotEqual(new_request["prompt_sha256"], old_request["prompt_sha256"])
        self.assertEqual(new_request["prompt_version"], ai_review.PROMPT_VERSION)
        self.assertNotIn("OLD_CONTRADICTORY_REVIEW", json.dumps(new_request))
        self.assertEqual(
            record["provenance"]["previous_review_response_sha256"],
            resume.saved_generation.sha(old_answer),
        )
        self.assertTrue(record["provenance"]["review_samples_reused"])
        self.assertFalse(record["provenance"]["previous_review_answer_used_as_input"])
        output, summary = self.run_review(p, record)
        self.assertEqual(summary["status"], "reviewed", summary)
        self.assertEqual(summary["sdk_request_calls"], 1)
        self.client.chat.completions.create.assert_called_once()
        self.assertEqual(
            (output / "generate/request.json").read_bytes(),
            before[self.source / "generate/request.json"],
        )
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_saved_review_mutation_after_preview_prevents_request(self):
        self.saved_review()
        p, record = self.prepare(reuse_review_samples=True)
        self.write("review/assistant-content.txt", b"changed")
        _, summary = self.run_review(p, record)
        self.assertEqual(summary["sdk_request_calls"], 0)
        self.client.chat.completions.create.assert_not_called()

    def test_old_prompt_without_catalog_reuses_raw_evidence_with_derived_catalog(self):
        request, _ = self.saved_review()
        del request["context"]["visible_field_names"]
        request["messages"][1]["content"] = ai_review.encoded(
            request["context"]
        ).decode()
        request["prompt_sha256"] = resume.saved_generation.sha(
            ai_review.encoded(request["messages"])
        )
        self.write("review/request.json", request)
        with patch.object(
            resume.sampling, "select", side_effect=AssertionError("no resampling")
        ):
            _, record = self.prepare(reuse_review_samples=True)
        new_context = record["review_selection"]["request"]["context"]
        self.assertEqual(
            new_context["current_samples"], request["context"]["current_samples"]
        )
        self.assertEqual(
            new_context["history_samples"], request["context"]["history_samples"]
        )
        self.assertEqual(
            new_context["visible_field_names"],
            sorted(
                ai_review._fields(
                    new_context["current_samples"] + new_context["history_samples"]
                )
            ),
        )
        self.assertTrue(record["provenance"]["derived_field_catalog_added"])

    def test_saved_review_material_cannot_be_replaced_from_source(self):
        request, _ = self.saved_review()
        identity = request["context"]["current_samples"][-1]["alert_id"]
        row = next(
            row for row in self.p.case["current_samples"] if row["alert_id"] == identity
        )
        row["raw_logs"] = [{"message": "altered review evidence"}]
        with self.assertRaisesRegex(ValueError, "saved_.*samples_changed"):
            self.prepare(reuse_review_samples=True)

    def test_review_with_different_draft_cannot_be_reused(self):
        request, _ = self.saved_review()
        request["context"]["draft"]["issue"] = "different issue"
        request["messages"][1]["content"] = ai_review.encoded(
            request["context"]
        ).decode()
        request["prompt_sha256"] = resume.saved_generation.sha(
            ai_review.encoded(request["messages"])
        )
        self.write("review/request.json", request)
        with self.assertRaisesRegex(ValueError, "did_not_receive_original_draft"):
            self.prepare(reuse_review_samples=True)


if __name__ == "__main__":
    unittest.main()
