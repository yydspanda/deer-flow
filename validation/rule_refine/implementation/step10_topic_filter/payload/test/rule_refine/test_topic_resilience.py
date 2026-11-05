"""Independent regression checks for interrupted compact topic jobs."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "_topic_resilience_fixtures", Path(__file__).with_name("test_topic_processor.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
module = fixtures.module


class TopicResilienceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.signal = fixtures.signal()
        self.case = fixtures.case()
        self.generate = Mock(side_effect=fixtures.response)
        self.submit = Mock(return_value={"success": True, "code": 200, "rule_id": 123})
        self.processor = module.TopicProcessor(
            self.root,
            load_case=lambda _: copy.deepcopy(self.case),
            load_rules=lambda: Mock(get_rule=Mock(return_value=fixtures.rule())),
            generate=self.generate,
            model="synthetic-model",
            budget=fixtures.Budget(),
            submit=self.submit,
        )

    def prepare_pending_delivery(self):
        with patch.object(
            self.processor.delivery, "deliver", return_value={"status": "busy"}
        ):
            outcome = self.processor.process(self.signal)
        self.assertEqual(outcome["status"], "busy")
        self.assertEqual(self.generate.call_count, 2)
        self.submit.assert_not_called()
        return Path(outcome["output_dir"])

    def assert_submission_prevented(self):
        try:
            outcome = self.processor.process(self.signal)
        except (ValueError, OSError):
            pass
        else:
            self.assertNotEqual(outcome["status"], "received")
        self.submit.assert_not_called()
        self.assertEqual(self.generate.call_count, 2)

    def test_changed_candidate_cannot_resume_submission_without_review(self):
        folder = self.prepare_pending_delivery()
        path = folder / "recommendation.json"
        candidate = json.loads(path.read_text(encoding="utf-8"))
        candidate["filter"]["A"]["value"] = "different-unreviewed-operation"
        candidate["executeType"] = [0]
        path.write_text(json.dumps(candidate), encoding="utf-8")
        self.assert_submission_prevented()

    def test_missing_review_evidence_cannot_resume_submission(self):
        folder = self.prepare_pending_delivery()
        (folder / "review.summary.json").unlink()
        self.assert_submission_prevented()

    def test_review_exception_does_not_claim_generation_reply_hash(self):
        def generate(request):
            if request["phase"] == "review":
                raise TimeoutError("synthetic provider error")
            return fixtures.response(request)

        self.generate.side_effect = generate
        outcome = self.processor.process(self.signal)
        self.assertEqual(outcome["status"], "blocked")
        failed = json.loads(
            (Path(outcome["output_dir"]) / "review.failed.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIsNone(failed["reply_sha256"])
        self.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
