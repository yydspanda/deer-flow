"""Offline recovery never pays for generation twice or broadens its evidence."""

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_review
import saved_generation


class SavedGenerationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.source = self.parent / "original"
        (self.source / "generate").mkdir(parents=True)
        self.rule = {"topic": "demo", "rule_code": "demo-rule"}
        self.samples = [{"alert_id": "demo-1", "raw_logs": [{"event": "routine"}]}]
        self.request = ai_review.build_request(self.rule, self.samples, [], {})
        self.draft = {
            "status": "proposal",
            "issue": "人工示例",
            "recommendation": "运营核对",
            "filter": {
                "topic": "demo",
                "A": {"name": "event", "operator": "contain", "value": "routine"},
                "expression": "A",
            },
            "sample_alert_ids": ["demo-1"],
        }
        self.raw = ("``` json " + json.dumps(self.draft) + "```").encode("utf-8")
        self.result = {
            "format_valid": False,
            "status": "analysis_failed",
            "error_code": "invalid_json_fence",
            "response_sha256": hashlib.sha256(self.raw).hexdigest(),
            "model_transport": {
                "response_received": True,
                "finish_reason": "stop",
                "sdk_request_calls": 1,
                "usage": {"prompt_tokens": 900},
            },
        }
        self.write("generate/request.json", self.request)
        self.write("generate/result.json", self.result)
        self.write("generate/assistant-content.txt", self.raw)
        self.write("tool-hashes.json", {"ai_review": "0" * 64})

    def write(self, name, value):
        (self.source / name).write_bytes(
            value if isinstance(value, bytes) else ai_review.encoded(value)
        )

    def replay(self):
        return saved_generation.replay(self.source, tool_hashes={"ai_review": "1" * 64})

    def test_replay_uses_original_answer_and_request_without_rewriting_source(self):
        before = {
            p.relative_to(self.source): p.read_bytes()
            for p in self.source.rglob("*")
            if p.is_file()
        }
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = self.replay()
        output = Path(result["output_dir"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["sdk_request_calls"], 0)
        self.assertEqual(result["original_model_transport"]["sdk_request_calls"], 1)
        self.assertFalse(result["ai_review_completed"])
        self.assertEqual(result["next_step"], "ai_review_pending")
        self.assertEqual(result["history_count"], 0)
        self.assertEqual((output / "assistant-content.txt").read_bytes(), self.raw)
        self.assertEqual(
            json.loads((output / "draft.json").read_text())["filter"],
            self.draft["filter"],
        )
        self.assertFalse((output / "recommendation.json").exists())
        for name, raw in before.items():
            self.assertEqual((self.source / name).read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        for path in output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_altered_answer_is_rejected_before_creating_output(self):
        self.write("generate/assistant-content.txt", self.raw + b" ")
        with self.assertRaisesRegex(ValueError, "saved_response_hash_mismatch"):
            self.replay()
        self.assertEqual(list(self.parent.iterdir()), [self.source])

    def test_changed_message_fails_original_prompt_hash(self):
        self.request["messages"][1]["content"] += " "
        self.write("generate/request.json", self.request)
        with self.assertRaisesRegex(ValueError, "saved_prompt_hash_mismatch"):
            self.replay()

    def test_shadow_context_cannot_add_unseen_evidence(self):
        self.request["context"]["current_samples"].append(
            {"alert_id": "invisible", "raw_logs": [{"event": "routine"}]}
        )
        self.write("generate/request.json", self.request)
        with self.assertRaisesRegex(ValueError, "saved_context_mismatch"):
            self.replay()

    def test_unseen_reference_remains_rejected(self):
        self.draft["sample_alert_ids"] = ["invisible"]
        self.raw = json.dumps(self.draft).encode("utf-8")
        self.write("generate/assistant-content.txt", self.raw)
        self.result["response_sha256"] = hashlib.sha256(self.raw).hexdigest()
        self.write("generate/result.json", self.result)
        result = self.replay()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["error_code"], "invisible_or_duplicate_sample_reference"
        )
        self.assertFalse((Path(result["output_dir"]) / "draft.json").exists())

    def test_incomplete_transport_cannot_be_recovered_as_success(self):
        for updates in (
            {"finish_reason": "length"},
            {"error_code": "request_failed"},
            {"response_received": False},
        ):
            value = json.loads(ai_review.encoded(self.result))
            value["model_transport"].update(updates)
            self.write("generate/result.json", value)
            with (
                self.subTest(updates=updates),
                self.assertRaisesRegex(ValueError, "saved_transport_not_complete"),
            ):
                self.replay()

    def test_malformed_wrapper_is_reported_without_modifying_answer(self):
        self.raw = b"```json " + ai_review.encoded(self.draft) + b"%"
        self.write("generate/assistant-content.txt", self.raw)
        self.result["response_sha256"] = hashlib.sha256(self.raw).hexdigest()
        self.write("generate/result.json", self.result)
        result = self.replay()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error_code"], "invalid_json_fence")
        self.assertEqual(result["wrapper_shape"]["closing_marker_length"], 0)
        self.assertNotIn("routine", json.dumps(result))

    def test_missing_close_replays_full_json_and_preserves_saved_bytes(self):
        self.raw = b"```json\n" + ai_review.encoded(self.draft)
        self.write("generate/assistant-content.txt", self.raw)
        self.result["response_sha256"] = hashlib.sha256(self.raw).hexdigest()
        self.write("generate/result.json", self.result)
        result = self.replay()
        self.assertEqual(result["status"], "passed")
        self.assertIn("missing_closing_json_fence", result["output_warnings"])
        self.assertEqual(result["sdk_request_calls"], 0)
        self.assertFalse(result["ai_review_completed"])
        self.assertEqual(
            (self.source / "generate/assistant-content.txt").read_bytes(), self.raw
        )
        output = Path(result["output_dir"])
        self.assertEqual((output / "assistant-content.txt").read_bytes(), self.raw)
        self.assertEqual(
            json.loads((output / "draft.json").read_text())["filter"],
            self.draft["filter"],
        )

    def test_new_results_must_be_outside_original_report(self):
        with self.assertRaisesRegex(
            ValueError, "output_must_be_outside_original_report"
        ):
            saved_generation.replay(self.source, output_parent=self.source / "replay")


if __name__ == "__main__":
    unittest.main()
