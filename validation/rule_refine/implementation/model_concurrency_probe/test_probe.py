"""Offline tests for the direct concurrent probe; all transports are synthetic."""

import importlib.util
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError


SPEC = importlib.util.spec_from_file_location(
    "model_concurrency_probe_under_test", Path(__file__).with_name("probe.py")
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

SECRET = "SYNTHETIC_TEST_CREDENTIAL_DO_NOT_PERSIST"
PRIVATE_REPLY = "SYNTHETIC_PRIVATE_MODEL_REPLY_DO_NOT_PERSIST"
ALIAS = "Pub_DeepSeek_V4_Flash"


def route():
    return {
        "type": "OPENAI",
        "base_url": "http://192.0.2.10/v1",
        "upstream_model": "deepseek-v4-flash",
        "api_key": SECRET,
        "source_sha256": "a" * 64,
    }


def completion(**overrides):
    result = {
        "model": "deepseek-v4-flash",
        "choices": [{"finish_reason": "stop", "message": {"content": PRIVATE_REPLY}}],
        "usage": {
            "prompt_tokens": 5010,
            "completion_tokens": 2,
            "total_tokens": 5012,
        },
    }
    result.update(overrides)
    return json.dumps(result).encode("utf-8")


def records(directory):
    result = []
    for path in directory.rglob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("index"), int):
            result.append(value)
    return result


class ConfigurationAndPayloadTests(unittest.TestCase):
    def test_route_is_literal_only_and_never_executes_application_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "llm_service" / "openai_completion.py"
            source.parent.mkdir()
            source.write_text(
                "raise RuntimeError('must not execute')\nPRD_MODEL_CONFIGS = "
                + repr({ALIAS: route()}),
                encoding="utf-8",
            )
            before = source.read_bytes()
            selected = probe.load_route(root)
            self.assertEqual(selected["api_key"], SECRET)
            self.assertEqual(selected["upstream_model"], "deepseek-v4-flash")
            self.assertEqual(source.read_bytes(), before)
            source.write_text(
                "PRD_MODEL_CONFIGS = "
                + repr({ALIAS: route()}).replace(repr(SECRET), "dangerous_call()"),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                probe.load_route(root)

    def test_payloads_are_independent_reproducible_and_have_no_hidden_token_quota(self):
        first = probe.make_request("deepseek-v4-flash", 500, 0, "run-a", 20261104)
        self.assertEqual(
            first,
            probe.make_request("deepseek-v4-flash", 500, 0, "run-a", 20261104),
        )
        self.assertEqual(set(first), {"model", "stream", "messages"})
        self.assertFalse(first["stream"])
        self.assertIn("Reply only OK", json.dumps(first))
        payloads = [
            probe.make_request("deepseek-v4-flash", 500, i, "run-a", 20261104)
            for i in range(27)
        ]
        self.assertEqual(len({json.dumps(item["messages"]) for item in payloads}), 27)
        self.assertEqual(
            {
                tuple(message["role"] for message in item["messages"])
                for item in payloads
            },
            {("system", "user")},
        )

    def test_classification_does_not_confuse_http_success_with_model_success(self):
        success = probe.classify(200, completion())
        self.assertTrue(success["success"])
        self.assertEqual(success["finish_reason"], "stop")
        self.assertEqual(success["usage"]["prompt_tokens"], 5010)
        for status, body in (
            (429, completion()),
            (200, b"not-json"),
            (200, completion(resultCode="10012")),
            (200, completion(success=False)),
            (200, completion(error={"message": SECRET})),
            (200, completion(choices=[])),
        ):
            with self.subTest(status=status):
                classified = probe.classify(status, body)
                self.assertFalse(classified["success"])
                self.assertTrue(classified["error_code"])
                self.assertNotIn(PRIVATE_REPLY, json.dumps(classified))
                self.assertNotIn(SECRET, json.dumps(classified))
        truncated = probe.classify(
            200,
            completion(
                choices=[{"finish_reason": "length", "message": {"content": "OK"}}]
            ),
        )
        self.assertFalse(truncated["success"])
        self.assertEqual(truncated["error_code"], "output_incomplete")


class ConcurrentBatchTests(unittest.TestCase):
    def setUp(self):
        self.output = StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_twenty_seven_simultaneous_unique_posts_and_durable_partial_results(self):
        barrier = threading.Barrier(27)
        release_last = threading.Event()
        lock = threading.Lock()
        calls = []
        active = 0
        maximum = 0

        def send(url, key, body, timeout):
            nonlocal active, maximum
            with lock:
                position = len(calls)
                calls.append((url, key, body, timeout))
                active += 1
                maximum = max(maximum, active)
            try:
                barrier.wait(timeout=10)
                if position == 26:
                    if not release_last.wait(timeout=10):
                        raise RuntimeError("test did not release final request")
                return 200, completion(debug={"api_key": SECRET}), 0.01, 0.02
            finally:
                with lock:
                    active -= 1

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "probe"
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    probe.run_batch,
                    route(),
                    directory,
                    concurrency=27,
                    units=500,
                    timeout=600,
                    seed=20261104,
                    send=send,
                )
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if len(records(directory)) == 26:
                            break
                        if pending.done():
                            break
                        time.sleep(0.01)
                    self.assertEqual(len(records(directory)), 26)
                    self.assertFalse(pending.done())
                finally:
                    release_last.set()
                summary = pending.result(timeout=10)

            self.assertEqual(len(calls), 27)
            self.assertEqual(maximum, 27)
            self.assertEqual(len({body for _, _, body, _ in calls}), 27)
            self.assertEqual(
                {url for url, _, _, _ in calls},
                {route()["base_url"] + "/chat/completions"},
            )
            self.assertEqual({key for _, key, _, _ in calls}, {SECRET})
            self.assertEqual({timeout for _, _, _, timeout in calls}, {600})
            self.assertEqual(summary["success_count"], 27)
            self.assertEqual(summary["failed_count"], 0)
            self.assertEqual(summary["peak_in_flight"], 27)
            self.assertEqual(len(records(directory)), 27)
            artifacts = "\n".join(
                path.read_text(encoding="utf-8") for path in directory.rglob("*.json")
            )
            self.assertNotIn(SECRET, artifacts)
            self.assertNotIn(PRIVATE_REPLY, artifacts)
            self.assertNotIn(SECRET, self.output.getvalue())
            self.assertNotIn(PRIVATE_REPLY, self.output.getvalue())

    def test_one_failure_does_not_cancel_others_or_trigger_retry(self):
        lock = threading.Lock()
        calls = []
        barrier = threading.Barrier(27)

        def send(url, key, body, timeout):
            with lock:
                position = len(calls)
                calls.append(body)
            barrier.wait(timeout=10)
            if position == 0:
                raise TimeoutError(SECRET)
            if position == 1:
                return 429, json.dumps({"error": SECRET}).encode(), 0.01, 0.02
            return 200, completion(), 0.01, 0.02

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "probe"
            summary = probe.run_batch(
                route(), directory, concurrency=27, units=100, send=send
            )
            self.assertEqual(len(calls), 27)
            self.assertEqual(summary["success_count"], 25)
            self.assertEqual(summary["failed_count"], 2)
            self.assertEqual(len(records(directory)), 27)
            self.assertEqual(
                sum(bool(item["success"]) for item in records(directory)), 25
            )
            for path in directory.rglob("*.json"):
                self.assertNotIn(SECRET, path.read_text(encoding="utf-8"))


class TransportAndSummaryTests(unittest.TestCase):
    def test_one_http_attempt_no_redirect_and_header_time_is_not_body_time(self):
        url = route()["base_url"] + "/chat/completions"
        body = b'{"synthetic":true}'
        response_body = completion()
        for status in (200, 429):
            with self.subTest(status=status):
                opener = MagicMock()
                if status == 200:
                    response = MagicMock()
                    response.__enter__.return_value = response
                    response.code = status
                    response.read.return_value = response_body
                    opener.open.return_value = response
                else:
                    opener.open.side_effect = HTTPError(
                        url, status, "rate limit", {}, BytesIO(response_body)
                    )
                with (
                    patch.object(probe, "build_opener", return_value=opener) as build,
                    patch.object(
                        probe.time, "perf_counter", side_effect=[100.0, 100.125, 100.5]
                    ),
                ):
                    result = probe.post_once(url, SECRET, body, 600)
                self.assertEqual(result, (status, response_body, 0.125, 0.5))
                self.assertEqual(opener.open.call_count, 1)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.data, body)
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(opener.open.call_args.kwargs, {"timeout": 600})
                handlers = build.call_args.args
                self.assertTrue(any(isinstance(h, probe.NoRedirect) for h in handlers))
                self.assertTrue(
                    any(
                        isinstance(h, probe.ProxyHandler) and h.proxies == {}
                        for h in handlers
                    )
                )
                self.assertIsNone(
                    probe.NoRedirect().redirect_request(
                        None, None, 302, "", {}, "http://192.0.2.20"
                    )
                )

    def test_summary_keeps_failures_missing_usage_and_client_scope_distinct(self):
        results = [
            {
                "success": True,
                "elapsed_seconds": 2.0,
                "http_status": 200,
                "usage": {"prompt_tokens": 5000, "completion_tokens": 2},
            },
            {"success": True, "elapsed_seconds": 4.0, "http_status": 200, "usage": {}},
            {
                "success": False,
                "elapsed_seconds": 1.0,
                "http_status": 429,
                "error_code": "http_429",
                "usage": {},
            },
        ]
        summary = probe.summarize(results, 5.0, 3, 0.01)
        self.assertEqual(summary["request_count"], 3)
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["success_usage_missing_count"], 1)
        self.assertEqual(summary["success_latency_seconds"]["p50"], 3.0)
        self.assertEqual(summary["all_request_latency_seconds"]["min"], 1.0)
        self.assertEqual(summary["prompt_tokens"]["min"], 5000)
        self.assertEqual(summary["batch_successes_per_second"], 0.4)
        self.assertFalse(summary["server_concurrency_verified"])
        self.assertFalse(summary["celery_capacity_verified"])
        self.assertFalse(summary["sustained_throughput_verified"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
