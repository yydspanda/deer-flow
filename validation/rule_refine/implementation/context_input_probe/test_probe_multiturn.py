"""Offline multi-turn capacity evidence checks; every HTTP response is a fixture."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "multiturn_input_probe", ROOT / "probe_multiturn.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

SECRET = "FAKE_MULTI_TURN_TEST_CREDENTIAL"


def route():
    return {
        "type": "OPENAI",
        "base_url": "http://192.0.2.10/v1",
        "upstream_model": "deepseek-v4-flash",
        "api_key": SECRET,
        "source_sha256": "a" * 64,
    }


def completion(tokens, content="OK", **extra):
    payload = {
        "model": "deepseek-v4-flash",
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {
            "prompt_tokens": tokens,
            "completion_tokens": 1,
            "total_tokens": tokens + 1 if isinstance(tokens, int) else None,
        },
    }
    payload.update(extra)
    return json.dumps(payload)


class MultiTurnCapacityTests(unittest.TestCase):
    def test_full_history_preserves_actual_assistant_responses_and_wire_parameters(
        self,
    ):
        sent = []
        replies = ["ACTUAL_FIRST_REPLY", "ACTUAL_SECOND_REPLY", "ACTUAL_THIRD_REPLY"]
        counts = [180031, 360066, 540102]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"

            def send(url, key, body, timeout):
                sent.append(json.loads(body))
                index = len(sent) - 1
                self.assertEqual(url, "http://192.0.2.10/v1/chat/completions")
                self.assertEqual(key, SECRET)
                self.assertEqual(timeout, 600)
                self.assertIs(type(sent[-1]["max_input_tokens"]), int)
                self.assertEqual(sent[-1]["max_input_tokens"], 1048576)
                self.assertNotIn("max_tokens", sent[-1])
                self.assertNotIn("max_completion_tokens", sent[-1])
                self.assertNotIn("extra_body", sent[-1])
                self.assertFalse(sent[-1]["stream"])
                return 200, completion(counts[index], replies[index])

            result = probe.run(route(), directory, units_per_turn=5, send=send)
            self.assertEqual(len(sent), 3)
            self.assertEqual(result["http_post_attempts"], 3)
            self.assertEqual(result["rounds_completed"], 3)
            self.assertTrue(result["input_over_500k_accepted_verified"])
            self.assertEqual(
                [m["role"] for m in sent[0]["messages"]], ["system", "user"]
            )
            for index in (1, 2):
                previous = sent[index - 1]["messages"]
                current = sent[index]["messages"]
                self.assertEqual(current[: len(previous)], previous)
                self.assertEqual(
                    current[len(previous)],
                    {"role": "assistant", "content": replies[index - 1]},
                )
                self.assertEqual(
                    current[-1], probe.make_user_message(index + 1, units=5)
                )
            requests = sorted(directory.rglob("request.json"))
            self.assertEqual(len(requests), 3)
            self.assertEqual(
                [json.loads(path.read_text(encoding="utf-8")) for path in requests],
                sent,
            )
            self.assertEqual(
                json.loads((directory / "summary.json").read_text(encoding="utf-8")),
                result,
            )

    def test_second_round_context_error_stops_without_third_or_fake_assistant(self):
        sent = []

        def send(url, key, body, timeout):
            sent.append(json.loads(body))
            if len(sent) == 1:
                return 200, completion(180031, "FIRST_REAL_REPLY")
            return 400, json.dumps(
                {
                    "error": {
                        "message": "The input (360066 tokens) is longer than the model's context length (262144 tokens)."
                    }
                }
            )

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            result = probe.run(route(), directory, units_per_turn=5, send=send)
            self.assertEqual(len(sent), 2)
            self.assertEqual(result["http_post_attempts"], 2)
            self.assertEqual(result["rounds_completed"], 1)
            self.assertFalse(result["input_over_500k_accepted_verified"])
            self.assertEqual(len(result["round_results"]), 2)
            self.assertEqual(len(list(directory.rglob("request.json"))), 2)
            self.assertEqual(sent[1]["messages"][2]["content"], "FIRST_REAL_REPLY")
            self.assertEqual(sent[1]["messages"][:2], sent[0]["messages"])

    def test_sum_of_request_usage_over_500k_is_not_single_context_proof(self):
        counts = [200001, 200002, 200003]
        calls = []

        def send(*args):
            calls.append(args)
            return 200, completion(counts[len(calls) - 1])

        with tempfile.TemporaryDirectory() as temp:
            result = probe.run(
                route(), Path(temp) / "result", units_per_turn=5, send=send
            )
        self.assertGreater(sum(counts), 512000)
        self.assertLess(max(counts), 512000)
        self.assertEqual(result["rounds_completed"], 3)
        self.assertFalse(result["input_over_500k_accepted_verified"])

    def test_exact_500k_boundary_does_not_count_as_over_500k(self):
        counts = [180031, 360066, 512000]
        calls = []

        def send(*args):
            calls.append(args)
            return 200, completion(counts[len(calls) - 1])

        with tempfile.TemporaryDirectory() as temp:
            result = probe.run(
                route(), Path(temp) / "result", units_per_turn=5, send=send
            )
        self.assertFalse(result["input_over_500k_accepted_verified"])

    def test_missing_usage_does_not_turn_planned_units_into_actual_tokens(self):
        calls = []

        def send(*args):
            calls.append(args)
            payload = json.loads(completion(540102))
            payload.pop("usage")
            return 200, json.dumps(payload)

        with tempfile.TemporaryDirectory() as temp:
            result = probe.run(
                route(), Path(temp) / "result", units_per_turn=5, send=send
            )
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["rounds_completed"], 3)
        self.assertFalse(result["input_over_500k_accepted_verified"])

    def test_timeout_stops_without_retry_or_secret(self):
        calls = []

        def send(*args):
            calls.append(args)
            raise TimeoutError("Authorization: " + SECRET)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            result = probe.run(route(), directory, units_per_turn=5, send=send)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["http_post_attempts"], 1)
            self.assertEqual(result["rounds_completed"], 0)
            self.assertFalse(result["input_over_500k_accepted_verified"])
            self.assertNotIn(SECRET, json.dumps(result))
            for artifact in directory.rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(SECRET, artifact.read_text(encoding="utf-8"))

    def test_http_200_business_error_does_not_complete_round_or_continue(self):
        calls = []

        def send(*args):
            calls.append(args)
            return 200, completion(540102, resultCode="10013", resultMsg="failed")

        with tempfile.TemporaryDirectory() as temp:
            result = probe.run(
                route(), Path(temp) / "result", units_per_turn=5, send=send
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["rounds_completed"], 0)
        self.assertFalse(result["input_over_500k_accepted_verified"])

    def test_empty_response_stops_without_inserting_placeholder_reply(self):
        calls = []

        def send(*args):
            calls.append(args)
            return 200, completion(180031, content="")

        with tempfile.TemporaryDirectory() as temp:
            result = probe.run(
                route(), Path(temp) / "result", units_per_turn=5, send=send
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["rounds_completed"], 0)
        self.assertFalse(result["input_over_500k_accepted_verified"])

    def test_recording_response_failure_stops_before_next_request(self):
        calls = []
        original = Path.write_text

        def write_text(path, *args, **kwargs):
            if path.name == "response.redacted.txt":
                raise OSError("scripted response write failure")
            return original(path, *args, **kwargs)

        def send(*args):
            calls.append(args)
            return 200, completion(180031)

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(Path, "write_text", new=write_text):
                result = probe.run(
                    route(), Path(temp) / "result", units_per_turn=5, send=send
                )
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["input_over_500k_accepted_verified"])

    def test_unreusable_redacted_reply_is_not_a_completed_successful_round(self):
        calls = []

        def send(*args):
            calls.append(args)
            return 200, completion(540102, content="Bearer SYNTHETIC_OTHER_CREDENTIAL")

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            result = probe.run(route(), directory, units_per_turn=5, send=send)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["rounds_completed"], 0)
            self.assertFalse(result["input_over_500k_accepted_verified"])
            self.assertEqual(result["round_results"][0]["status"], "failed")
            for artifact in directory.rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(
                        "SYNTHETIC_OTHER_CREDENTIAL",
                        artifact.read_text(encoding="utf-8"),
                    )

    def test_existing_directory_is_not_overwritten_or_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            directory.mkdir()
            sentinel = directory / "summary.json"
            sentinel.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                probe.run(
                    route(),
                    directory,
                    units_per_turn=5,
                    send=lambda *_: self.fail("must not send"),
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")

    def test_provider_error_is_redacted_and_no_downgrade_retry(self):
        calls = []
        progress = []

        def send(url, key, body, timeout):
            calls.append(json.loads(body))
            return 400, json.dumps(
                {
                    "error": {
                        "message": "Unrecognized max_input_tokens credential=" + SECRET
                    }
                }
            )

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            result = probe.run(
                route(),
                directory,
                units_per_turn=5,
                send=send,
                progress=progress.append,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["max_input_tokens"], 1048576)
            self.assertFalse(result["input_over_500k_accepted_verified"])
            self.assertNotIn(SECRET, json.dumps(result))
            self.assertNotIn(SECRET, json.dumps(progress))
            for artifact in directory.rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(SECRET, artifact.read_text(encoding="utf-8"))

    def test_three_distinct_deterministic_user_messages(self):
        messages = [probe.make_user_message(number, units=8) for number in (1, 2, 3)]
        self.assertEqual(
            messages, [probe.make_user_message(number, units=8) for number in (1, 2, 3)]
        )
        self.assertTrue(all(item["role"] == "user" for item in messages))
        self.assertEqual(len({item["content"] for item in messages}), 3)
        self.assertEqual(probe.ROUNDS, 3)
        self.assertEqual(probe.UNITS_PER_TURN, 180000)
        self.assertEqual(probe.TARGET_INPUT_TOKENS, 512000)
        self.assertEqual(probe.MAX_INPUT_TOKENS, 1048576)


if __name__ == "__main__":
    unittest.main()
