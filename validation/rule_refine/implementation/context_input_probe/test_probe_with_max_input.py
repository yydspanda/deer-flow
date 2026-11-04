"""Offline proof that the explicit input parameter reaches the outbound JSON."""

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "explicit_input_probe", ROOT / "probe_with_max_input.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

SECRET = "FAKE_PARAMETER_TEST_SECRET"
BASELINE_REQUEST_HASH = (
    "7d020e858b39cb04b283758e28b357c23f2af045b5a18a7a476b915d799f7185"
)


def encode(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def route():
    return {
        "type": "OPENAI",
        "base_url": "http://192.0.2.10/v1",
        "upstream_model": "deepseek-v4-flash",
        "api_key": SECRET,
        "source_sha256": "a" * 64,
    }


def success_body(prompt_tokens=320029):
    return json.dumps(
        {
            "model": "deepseek-v4-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 1,
                "total_tokens": prompt_tokens + 1,
            },
        }
    )


class ExplicitInputParameterTests(unittest.TestCase):
    def test_missing_or_wrong_parameter_stops_before_write_or_post(self):
        payload = probe.make_request("deepseek-v4-flash", 3)
        invalid = [
            {key: value for key, value in payload.items() if key != "max_input_tokens"},
            {**payload, "max_input_tokens": True},
            {**payload, "max_input_tokens": "1048576"},
            {**payload, "max_input_tokens": 262144},
            {**payload, "max_tokens": 8192},
        ]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            for value in invalid:
                with (
                    self.subTest(value=value),
                    self.assertRaises(probe.baseline.ProbeError),
                ):
                    probe.run(
                        route(),
                        encode(value),
                        directory,
                        600,
                        send=lambda *_: self.fail("must not send"),
                    )
                self.assertFalse(directory.exists())

    def test_same_baseline_request_with_only_top_level_parameter_added(self):
        payload = probe.make_request("deepseek-v4-flash", 320000)
        self.assertEqual(
            set(payload), {"model", "messages", "stream", "max_input_tokens"}
        )
        self.assertIs(type(payload["max_input_tokens"]), int)
        self.assertEqual(payload["max_input_tokens"], 1048576)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("extra_body", payload)
        self.assertEqual(len(encode(payload)), 640245)
        payload.pop("max_input_tokens")
        self.assertEqual(
            payload, probe.baseline.make_request("deepseek-v4-flash", 320000)
        )
        self.assertEqual(len(encode(payload)), 640218)
        self.assertEqual(
            hashlib.sha256(encode(payload)).hexdigest(), BASELINE_REQUEST_HASH
        )

    def test_actual_wire_and_saved_request_include_parameter(self):
        body = encode(probe.make_request("deepseek-v4-flash", 320000))
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"

            def send(url, key, sent, timeout):
                calls.append((url, key, sent, timeout))
                outgoing = json.loads(sent)
                self.assertEqual(outgoing["max_input_tokens"], 1048576)
                self.assertNotIn("max_tokens", outgoing)
                started = json.loads(
                    (directory / "summary.json").read_text(encoding="utf-8")
                )
                self.assertTrue(started["max_input_tokens_parameter_sent"])
                self.assertEqual(started["max_input_tokens"], 1048576)
                self.assertFalse(started["parameter_effect_verified"])
                return 200, success_body()

            result = probe.run(route(), body, directory, 600, send=send)
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                calls[0], ("http://192.0.2.10/v1/chat/completions", SECRET, body, 600)
            )
            self.assertEqual((directory / "request.json").read_bytes(), body)
            saved = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, result)
            self.assertTrue(saved["max_input_tokens_parameter_sent"])
            self.assertEqual(saved["max_input_tokens"], 1048576)
            self.assertEqual(saved["baseline_request_sha256"], BASELINE_REQUEST_HASH)
            self.assertTrue(saved["accepted_over_256k"])
            self.assertFalse(saved["parameter_effect_verified"])
            self.assertFalse(saved["max_tokens_parameter_sent"])
            self.assertFalse(saved["application_code_updated"])
            self.assertFalse(saved["configuration_modified"])
            self.assertEqual(saved["redis_calls"], 0)
            self.assertEqual(saved["database_calls"], 0)
            for artifact in directory.iterdir():
                self.assertNotIn(SECRET, artifact.read_text(encoding="utf-8"))

    def test_unsupported_parameter_never_retries_without_it(self):
        calls = []

        def send(url, key, sent, timeout):
            calls.append(json.loads(sent))
            return 400, json.dumps(
                {
                    "error": {
                        "message": "Unrecognized request argument: max_input_tokens"
                    }
                }
            )

        with tempfile.TemporaryDirectory() as temp:
            result = probe.run(
                route(),
                encode(probe.make_request("deepseek-v4-flash", 3)),
                Path(temp) / "result",
                600,
                send=send,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_input_tokens"], 1048576)
        self.assertEqual(result["http_status"], 400)
        self.assertFalse(result["parameter_effect_verified"])
        self.assertIsNone(result["accepted_over_256k"])
        self.assertEqual(result["client_retries"], 0)
        self.assertIsNone(result["upstream_attempts"])

    def test_explicit_parameter_does_not_relabel_provider_capacity(self):
        calls = []

        def send(*args):
            calls.append(args)
            return 400, json.dumps(
                {
                    "error": {
                        "message": "The input (320029 tokens) is longer than the model's context length (262144 tokens)."
                    }
                }
            )

        with tempfile.TemporaryDirectory() as temp:
            result = probe.run(
                route(),
                encode(probe.make_request("deepseek-v4-flash", 3)),
                Path(temp) / "result",
                600,
                send=send,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["max_input_tokens"], 1048576)
        self.assertEqual(result["provider_reported_context_tokens"], 262144)
        self.assertEqual(result["provider_reported_input_tokens"], 320029)
        self.assertFalse(result["accepted_over_256k"])
        self.assertFalse(result["parameter_effect_verified"])

    def test_timeout_preserves_request_parameter_and_never_retries(self):
        calls = []

        def send(*args):
            calls.append(args)
            raise TimeoutError(SECRET)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            result = probe.run(
                route(),
                encode(probe.make_request("deepseek-v4-flash", 3)),
                directory,
                600,
                send=send,
            )
            self.assertEqual(len(calls), 1)
            self.assertTrue(result["max_input_tokens_parameter_sent"])
            self.assertEqual(result["max_input_tokens"], 1048576)
            self.assertFalse(result["parameter_effect_verified"])
            self.assertIsNone(result["accepted_over_256k"])
            self.assertEqual(result["error_type"], "TimeoutError")
            self.assertNotIn(
                SECRET, (directory / "summary.json").read_text(encoding="utf-8")
            )

    def test_cancel_sends_nothing_and_preview_shows_real_parameter(self):
        reader, writer, output = StringIO("NO\n"), StringIO(), StringIO()
        with (
            patch("sys.argv", ["probe_with_max_input.py", "--units", "3"]),
            patch.object(probe.baseline, "load_route", return_value=route()),
            patch("builtins.open", side_effect=[reader, writer]) as terminal,
            patch.object(probe, "run") as run,
            redirect_stdout(output),
        ):
            probe.main()
        run.assert_not_called()
        self.assertEqual(
            [call.args for call in terminal.call_args_list],
            [("/dev/tty", "r"), ("/dev/tty", "w")],
        )
        preview = json.JSONDecoder().raw_decode(output.getvalue())[0]
        self.assertTrue(preview["max_input_tokens_parameter_sent"])
        self.assertEqual(preview["max_input_tokens"], 1048576)
        self.assertNotIn(SECRET, output.getvalue())


if __name__ == "__main__":
    unittest.main()
