"""Offline contract tests: synthetic transport only, no model or network calls."""

import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO, StringIO


SPEC = importlib.util.spec_from_file_location(
    "context_input_probe", Path(__file__).with_name("probe.py")
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

SECRET = "FAKE_TEST_CREDENTIAL_123456789"


def route():
    return {
        "type": "OPENAI",
        "base_url": "http://192.0.2.10/v1",
        "upstream_model": "deepseek-v4-flash",
        "api_key": SECRET,
        "source_sha256": "a" * 64,
    }


def completion(prompt_tokens=320021, **extra):
    data = {
        "model": "deepseek-v4-flash",
        "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 1,
            "total_tokens": 320022,
        },
    }
    data.update(extra)
    return data


class ClassificationTests(unittest.TestCase):
    def test_actual_usage_above_threshold_proves_acceptance(self):
        result = probe.assess(200, json.dumps(completion()))
        self.assertEqual(result["verdict"], "accepted_over_256k")
        self.assertTrue(result["accepted_over_256k"])
        self.assertTrue(result["input_over_256k_verified"])
        self.assertEqual(result["usage"]["prompt_tokens"], 320021)

    def test_threshold_or_lower_or_invalid_usage_is_inconclusive(self):
        for value in [262144, 262143, 0, None, "320021", True, -1, 320021.0]:
            with self.subTest(value=value):
                result = probe.assess(200, json.dumps(completion(value)))
                self.assertEqual(
                    result["verdict"], "completed_but_over_256k_not_proven"
                )
                self.assertIsNone(result["accepted_over_256k"])
                self.assertFalse(result["input_over_256k_verified"])

    def test_missing_usage_is_not_filled_from_input_size(self):
        data = completion()
        data.pop("usage")
        result = probe.assess(200, json.dumps(data))
        self.assertIsNone(result["usage"]["prompt_tokens"])
        self.assertEqual(result["verdict"], "completed_but_over_256k_not_proven")

    def test_http_200_business_errors_are_not_success(self):
        for fields in [
            {"resultCode": "10013"},
            {"resultCode": False},
            {"resultCode": True},
            {"success": False},
            {"error": {"message": "failure"}},
        ]:
            with self.subTest(fields=fields):
                result = probe.assess(200, json.dumps(completion(**fields)))
                self.assertFalse(result["valid_completion_received"])
                self.assertIsNone(result["accepted_over_256k"])

    def test_success_business_codes_are_accepted(self):
        for code in ["0", 0]:
            with self.subTest(code=code):
                self.assertTrue(
                    probe.assess(200, json.dumps(completion(resultCode=code)))[
                        "accepted_over_256k"
                    ]
                )

    def test_context_error_extracts_explicit_nested_numbers(self):
        body = json.dumps(
            {
                "error": {
                    "message": 'litellm.BadRequestError: {"message":"The input (336,423 tokens) is longer than the model\'s context length (262,144 tokens)."}'
                }
            }
        )
        result = probe.assess(400, body)
        self.assertEqual(result["verdict"], "context_length_rejected")
        self.assertEqual(result["provider_reported_input_tokens"], 336423)
        self.assertEqual(result["provider_reported_context_tokens"], 262144)
        self.assertTrue(result["input_over_256k_verified"])
        self.assertFalse(result["accepted_over_256k"])

    def test_context_error_does_not_invent_256k_capacity(self):
        body = json.dumps(
            {
                "error": "The input (4097 tokens) is longer than the model's context length (4096 tokens)."
            }
        )
        result = probe.assess(400, body)
        self.assertEqual(result["provider_reported_context_tokens"], 4096)
        self.assertFalse(result["input_over_256k_verified"])

    def test_auth_rate_limit_generic_context_and_html_are_unknown(self):
        for status, body in [
            (401, '{"error":"unauthorized"}'),
            (429, '{"error":"rate limited"}'),
            (400, '{"error":"context exceeded"}'),
            (502, "<html>Bad Gateway</html>"),
        ]:
            with self.subTest(status=status, body=body):
                result = probe.assess(status, body)
                self.assertEqual(result["verdict"], "request_failed_capacity_unknown")
                self.assertIsNone(result["accepted_over_256k"])
                self.assertNotIn("provider_reported_context_tokens", result)

    def test_provider_truncated_output_can_still_prove_input_acceptance(self):
        data = completion()
        data["choices"][0]["finish_reason"] = "length"
        self.assertTrue(probe.assess(200, json.dumps(data))["accepted_over_256k"])

    def test_empty_or_noncompletion_payload_is_not_success(self):
        for data in [
            [],
            {},
            completion(choices=[]),
            completion(choices=[{"finish_reason": "stop", "message": {"content": ""}}]),
        ]:
            with self.subTest(data=data):
                self.assertFalse(
                    probe.assess(200, json.dumps(data))["valid_completion_received"]
                )


class ConfigurationTests(unittest.TestCase):
    def write_source(self, root, text):
        target = root / "llm_service" / "openai_completion.py"
        target.parent.mkdir(parents=True)
        target.write_text(text, encoding="utf-8")
        return target

    def test_ast_read_does_not_execute_source_and_keeps_it_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text = (
                "raise RuntimeError('must never execute')\nPRD_MODEL_CONFIGS = "
                + repr({probe.ALIAS: route()})
            )
            source = self.write_source(root, text)
            before = source.read_bytes()
            loaded = probe.load_route(root)
            self.assertEqual(loaded["api_key"], SECRET)
            self.assertEqual(source.read_bytes(), before)

    def test_computed_credential_is_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            value = repr({probe.ALIAS: route()}).replace(
                repr(SECRET), "dangerous_function()"
            )
            self.write_source(root, "PRD_MODEL_CONFIGS = " + value)
            with self.assertRaisesRegex(
                probe.ProbeError, "literal_OPENAI_route_required"
            ):
                probe.load_route(root)

    def test_duplicate_and_credential_bearing_urls_are_rejected(self):
        for override in [
            "http://user:password@192.0.2.10/v1",
            "http://192.0.2.10/v1?secret=value",
            "http://192.0.2.10/v1#fragment",
        ]:
            with self.subTest(url=override), tempfile.TemporaryDirectory() as temp:
                selected = route()
                selected["base_url"] = override
                self.write_source(
                    Path(temp), "PRD_MODEL_CONFIGS = " + repr({probe.ALIAS: selected})
                )
                with self.assertRaises(probe.ProbeError):
                    probe.load_route(Path(temp))
        with tempfile.TemporaryDirectory() as temp:
            self.write_source(
                Path(temp), "PRD_MODEL_CONFIGS = {}\nPRD_MODEL_CONFIGS = {}"
            )
            with self.assertRaisesRegex(
                probe.ProbeError, "unique_PRD_MODEL_CONFIGS_required"
            ):
                probe.load_route(Path(temp))


class RequestAndTransportTests(unittest.TestCase):
    def test_artificial_request_has_no_input_or_output_token_parameter(self):
        payload = probe.make_request("deepseek-v4-flash", 320000)
        self.assertEqual(set(payload), {"model", "stream", "messages"})
        self.assertFalse(payload["stream"])
        self.assertEqual(payload, probe.make_request("deepseek-v4-flash", 320000))
        filler = payload["messages"][1]["content"].split("\n")[1]
        self.assertEqual(len(filler.split(" ")), 320000)
        self.assertTrue(
            all(len(unit) == 1 and "a" <= unit <= "z" for unit in filler.split(" "))
        )
        self.assertGreater(len(filler.encode("utf-8")), 262144)

    def test_run_one_post_preserves_no_credential_artifacts(self):
        calls = []
        body = json.dumps(probe.make_request("deepseek-v4-flash", 10)).encode()

        def send(url, key, sent, timeout):
            calls.append((url, key, sent, timeout))
            data = completion()
            data["debug"] = {"api_key": SECRET, "Authorization": "Bearer " + SECRET}
            return 200, json.dumps(data)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            result = probe.run(route(), body, directory, 600, send=send)
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                calls[0], ("http://192.0.2.10/v1/chat/completions", SECRET, body, 600)
            )
            self.assertTrue(result["accepted_over_256k"])
            self.assertEqual(result["http_post_attempts"], 1)
            self.assertEqual(result["client_retries"], 0)
            self.assertIsNone(result["upstream_attempts"])
            self.assertFalse(result["configuration_modified"])
            self.assertEqual(result["database_calls"], 0)
            self.assertEqual(result["redis_calls"], 0)
            self.assertEqual((directory / "request.json").read_bytes(), body)
            for artifact in directory.iterdir():
                self.assertNotIn(SECRET, artifact.read_text(encoding="utf-8"))
            self.assertNotIn(SECRET, json.dumps(result))

    def test_timeout_is_recorded_once_without_retry_or_secret(self):
        calls = []

        def timeout(*args):
            calls.append(args)
            raise TimeoutError("Authorization: " + SECRET)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            result = probe.run(route(), b"{}", directory, 600, send=timeout)
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                result["verdict"], "request_or_recording_failed_capacity_unknown"
            )
            self.assertEqual(result["error_type"], "TimeoutError")
            self.assertIsNone(result["accepted_over_256k"])
            self.assertFalse(result["input_over_256k_verified"])
            self.assertNotIn(
                SECRET, (directory / "summary.json").read_text(encoding="utf-8")
            )

    def test_existing_result_is_not_overwritten_or_resent(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "result"
            directory.mkdir()
            sentinel = directory / "summary.json"
            sentinel.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                probe.run(
                    route(),
                    b"{}",
                    directory,
                    600,
                    send=lambda *_: self.fail("must not send"),
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")

    def test_http_error_returned_after_one_open_and_no_redirect(self):
        error = HTTPError(
            "http://192.0.2.10/v1/chat/completions",
            400,
            "Bad Request",
            {},
            BytesIO(b'{"error":"bad request"}'),
        )
        with patch.object(probe, "build_opener") as factory:
            opener = factory.return_value
            opener.open.side_effect = error
            status, raw = probe.post_once(
                "http://192.0.2.10/v1/chat/completions", SECRET, b"{}", 600
            )
            self.assertEqual(status, 400)
            self.assertIn("bad request", raw)
            opener.open.assert_called_once()
            handlers = factory.call_args.args
            self.assertEqual(handlers[0].proxies, {})
            self.assertIsInstance(handlers[1], probe.NoRedirect)
            self.assertIsNone(
                handlers[1].redirect_request(
                    None, None, 302, "", {}, "http://192.0.2.11"
                )
            )

    def test_redaction_removes_known_and_labelled_secrets(self):
        text = f'{SECRET} Bearer OTHER_TEST_TOKEN api_key="THIRD_TOKEN" password=FOURTH_TOKEN'
        safe = probe.redact(text, SECRET)
        for value in [SECRET, "OTHER_TEST_TOKEN", "THIRD_TOKEN", "FOURTH_TOKEN"]:
            self.assertNotIn(value, safe)

    def test_cancel_uses_separate_tty_streams_and_sends_nothing(self):
        reader, writer, output = StringIO("NO\n"), StringIO(), StringIO()
        with (
            patch("sys.argv", ["probe.py", "--units", "3"]),
            patch.object(probe, "load_route", return_value=route()),
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
        self.assertIn("已取消", output.getvalue())
        self.assertNotIn(SECRET, output.getvalue())


if __name__ == "__main__":
    unittest.main()
