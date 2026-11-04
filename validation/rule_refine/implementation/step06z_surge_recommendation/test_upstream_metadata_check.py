import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler

import upstream_metadata_check as check


SECRET = "SECRET_MUST_NOT_LEAK"


class UpstreamMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "llm_service").mkdir()
        self.route = {
            "type": "OPENAI",
            "base_url": check.BASE_URL,
            "upstream_model": check.UPSTREAM_MODEL,
            "api_key": SECRET,
        }
        self.config = self.root / "config/rule_refine.analysis.local.json"
        self.config.write_text(
            json.dumps({"model": check.SELECTED_MODEL}), encoding="utf-8"
        )
        self.source = self.root / "llm_service/openai_completion.py"
        self.write_source()
        self.calls = []

    def write_source(self, prefix="", suffix=""):
        self.source.write_text(
            prefix
            + "\nPRD_MODEL_CONFIGS: never_evaluate_annotation() = "
            + repr({check.SELECTED_MODEL: self.route})
            + "\n"
            + suffix,
            encoding="utf-8",
        )

    def get(self, url, *, token=None):
        self.calls.append((url, token))
        if url == check.HEALTH_URL:
            self.assertIsNone(token)
            return {
                "upstreams": {
                    check.SELECTED_MODEL: {"type": "OPENAI", "base_url": check.BASE_URL}
                }
            }
        self.assertEqual(token, SECRET)
        return {"data": []}

    def test_annotated_literal_never_executes_source(self):
        self.write_source(prefix="raise RuntimeError('MUST_NOT_EXECUTE')\n")
        report = check.inspect(self.root, get=self.get)
        self.assertEqual(report["remote_get_calls"], 4)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["context_capacity_status"], "unknown")
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(
            [url for url, _ in self.calls],
            [check.HEALTH_URL] + [check.ORIGIN + path for path in check.METADATA_PATHS],
        )

    def test_wrong_selection_sends_no_requests(self):
        self.config.write_text('{"model": "Other"}', encoding="utf-8")
        report = check.inspect(self.root, get=self.get)
        self.assertEqual(report["status"], "local_route_not_verified")
        self.assertEqual(self.calls, [])

    def test_only_exact_confirmed_endpoint_and_model_are_allowed(self):
        for field, value in (
            ("base_url", "http://different.example/v1"),
            ("base_url", check.BASE_URL + "/"),
            ("base_url", "http://user:secret@30.154.3.119/v1"),
            ("type", "EAGW"),
            ("upstream_model", "deepseek-v4-flash-backup"),
        ):
            with self.subTest(field=field, value=value):
                old = self.route[field]
                self.route[field] = value
                self.write_source()
                report = check.inspect(self.root, get=self.get)
                self.assertEqual(report["status"], "local_route_not_verified")
                self.assertEqual(self.calls, [])
                self.route[field] = old

    def test_runtime_mismatch_cannot_send_auth_to_remote(self):
        def get(url, *, token=None):
            self.calls.append((url, token))
            return {
                "upstreams": {
                    check.SELECTED_MODEL: {
                        "type": "OPENAI",
                        "base_url": "http://other/v1",
                    }
                }
            }

        report = check.inspect(self.root, get=get)
        self.assertEqual(report["status"], "running_route_not_verified")
        self.assertEqual(self.calls, [(check.HEALTH_URL, None)])
        self.assertEqual(report["remote_get_calls"], 0)

    def test_missing_dynamic_and_duplicate_routes_do_not_execute(self):
        invalid = (
            "PRD_MODEL_CONFIGS = build_config()",
            "PRD_MODEL_CONFIGS = {}",
            "PRD_MODEL_CONFIGS = {**load_config()}",
            "PRD_MODEL_CONFIGS = {selected_model(): {}}",
            "PRD_MODEL_CONFIGS = {" + repr(check.SELECTED_MODEL) + ": make_route()}",
            "PRD_MODEL_CONFIGS = "
            + repr({check.SELECTED_MODEL: self.route})
            + "\nPRD_MODEL_CONFIGS = {}",
        )
        for source in invalid:
            with self.subTest(source=source[:40]):
                self.source.write_text(source, encoding="utf-8")
                report = check.inspect(self.root, get=self.get)
                self.assertEqual(report["status"], "local_route_not_verified")
                self.assertEqual(self.calls, [])

    def test_auth_denied_stops_without_bypass_or_retry(self):
        for status in (401, 403):
            self.calls.clear()

            def get(url, *, token=None):
                if url == check.HEALTH_URL:
                    return self.get(url)
                self.calls.append((url, token))
                raise HTTPError(url, status, SECRET, {"Authorization": SECRET}, None)

            report = check.inspect(self.root, get=get)
            self.assertEqual(report["remote_get_calls"], 1)
            self.assertEqual(len(self.calls), 2)
            self.assertEqual(report["endpoints"][0]["http_status"], status)
            self.assertEqual(
                [row["status"] for row in report["endpoints"][1:]],
                ["skipped_after_auth_denied"] * 3,
            )
            self.assertNotIn(SECRET, json.dumps(report))

    def test_error_body_message_and_headers_never_print(self):
        def get(url, *, token=None):
            if url == check.HEALTH_URL:
                return self.get(url)
            raise RuntimeError("body api_key=" + SECRET)

        report = check.inspect(self.root, get=get)
        self.assertEqual(report["status"], "metadata_unavailable")
        self.assertEqual(report["remote_get_calls"], 4)
        self.assertNotIn(SECRET, json.dumps(report))

    def test_only_selected_model_rows_and_allowlisted_fields_return(self):
        body = {
            "data": [
                {"id": "unrelated", "context_length": 999, "api_key": SECRET},
                {
                    "id": check.UPSTREAM_MODEL,
                    "context_length": 262144,
                    "api_key": SECRET,
                    "version": SECRET,
                    "model_info": {"max_output_tokens": 8192, "credential": SECRET},
                    "litellm_params": {
                        "model": "openai/deepseek-v4-flash",
                        "api_key": SECRET,
                        "api_base": "http://private",
                    },
                },
            ]
        }
        report = check.filter_metadata("/v1/models", body, secret=SECRET)
        self.assertEqual(report["matching_rows"], 1)
        fields = {tuple(field["path"]): field["value"] for field in report["fields"]}
        self.assertEqual(
            fields,
            {
                ("data", 1, "context_length"): 262144,
                ("data", 1, "model_info", "max_output_tokens"): 8192,
                ("data", 1, "litellm_params", "model"): "openai/deepseek-v4-flash",
            },
        )
        self.assertNotIn(SECRET, json.dumps(report))

    def test_model_info_uses_model_name_not_unrelated_deployment_id(self):
        body = {
            "data": [
                {
                    "model_name": "other",
                    "model_info": {"id": check.UPSTREAM_MODEL, "max_model_len": 1},
                },
                {
                    "model_name": check.UPSTREAM_MODEL,
                    "model_info": {"max_model_len": 262144},
                },
            ]
        }
        report = check.filter_metadata(check.METADATA_PATHS[1], body, secret=SECRET)
        self.assertEqual(report["matching_rows"], 1)
        self.assertEqual(
            report["fields"][0]["path"], ["data", 1, "model_info", "max_model_len"]
        )

    def test_service_context_without_model_identity_is_unverified(self):
        report = check.filter_metadata(
            "/get_server_info",
            {
                "server_args": {
                    "context_length": 262144,
                    "api_key": SECRET,
                    "model_path": "/private/weights",
                },
                "version": "0.5.4",
            },
            secret=SECRET,
        )
        self.assertEqual(
            report["association"], "service_metadata_model_association_unverified"
        )
        self.assertIn(
            {
                "path": ["server_args", "context_length"],
                "value": 262144,
                "meaning": "advertised_context_limit",
            },
            report["fields"],
        )
        self.assertNotIn("/private", json.dumps(report))
        self.assertNotIn(SECRET, json.dumps(report))

    def test_mismatched_served_model_excludes_all_server_fields(self):
        for name in ("other", ["other"], {"secret": SECRET}, None):
            report = check.filter_metadata(
                "/get_model_info",
                {"served_model_name": name, "context_length": 262144},
                secret=SECRET,
            )
            self.assertEqual(report["association"], "served_model_mismatch")
            self.assertEqual(report["fields"], [])

    def test_capacity_summary_never_promotes_output_or_ambiguous_limits(self):
        def get(url, *, token=None):
            if url == check.HEALTH_URL:
                return self.get(url)
            return {
                "served_model_name": check.UPSTREAM_MODEL,
                "max_tokens": 8192,
                "max_output_tokens": 8192,
            }

        report = check.inspect(self.root, get=get)
        self.assertEqual(report["status"], "metadata_observed")
        self.assertEqual(report["context_capacity_status"], "unknown")
        self.assertFalse(report["actual_context_capacity_verified"])
        field = next(
            item
            for item in report["endpoints"][2]["fields"]
            if item["path"] == ["max_tokens"]
        )
        self.assertEqual(field["meaning"], "ambiguous_token_limit_not_context_proof")

    def test_selected_advertised_capacity_is_not_capacity_probe(self):
        def get(url, *, token=None):
            if url == check.HEALTH_URL:
                return self.get(url)
            return {"served_model_name": check.UPSTREAM_MODEL, "context_length": 262144}

        report = check.inspect(self.root, get=get)
        self.assertEqual(
            report["context_capacity_status"],
            "selected_model_context_metadata_observed_not_capacity_tested",
        )
        self.assertFalse(report["capacity_probe_performed"])

    def test_invalid_metadata_scalars_are_ignored(self):
        for value in (True, "262144", -1, 0, 1.5, None, [262144]):
            report = check.filter_metadata(
                "/get_model_info",
                {"context_length": value, "version": "html <body>"},
                secret=SECRET,
            )
            self.assertEqual(report["fields"], [])


class FixedTransportTests(unittest.TestCase):
    @patch.object(check, "build_opener")
    def test_fixed_get_auth_timeout_bound_and_no_environment_proxy(self, build):
        response = MagicMock()
        response.read.return_value = b'{"data": []}'
        build.return_value.open.return_value.__enter__.return_value = response
        check.read_get(check.ORIGIN + "/v1/models", token=SECRET)
        handlers = build.call_args.args
        self.assertTrue(
            any(
                isinstance(handler, ProxyHandler) and handler.proxies == {}
                for handler in handlers
            )
        )
        self.assertTrue(
            any(isinstance(handler, check.NoRedirect) for handler in handlers)
        )
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
        self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 5)
        response.read.assert_called_once_with(check.MAX_BYTES + 1)

    @patch.object(check, "build_opener")
    def test_health_has_no_credentials(self, build):
        build.return_value.open.return_value.__enter__.return_value.read.return_value = b"{}"
        check.read_get(check.HEALTH_URL)
        request = build.return_value.open.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        with self.assertRaises(check.CheckError):
            check.read_get(check.HEALTH_URL, token=SECRET)

    @patch.object(check, "build_opener")
    def test_non_allowlisted_destinations_and_generation_paths_never_open(self, build):
        for url in (
            "http://other/v1/models",
            check.ORIGIN + "/health",
            check.BASE_URL + "/chat/completions",
            check.ORIGIN + "/get_server_info?dump=1",
        ):
            with self.assertRaises(check.CheckError):
                check.read_get(url, token=SECRET)
        build.assert_not_called()

    def test_redirect_handler_does_not_follow_even_same_origin(self):
        self.assertIsNone(
            check.NoRedirect().redirect_request(
                None, None, 302, "", {}, "http://other/private"
            )
        )
        self.assertIsNone(
            check.NoRedirect().redirect_request(
                None, None, 302, "", {}, check.ORIGIN + "/generate"
            )
        )

    @patch.object(check, "build_opener")
    def test_oversized_html_and_array_responses_are_not_exposed(self, build):
        response = build.return_value.open.return_value.__enter__.return_value
        for raw in (b"x" * (check.MAX_BYTES + 1), b"<html>SECRET</html>", b"[]"):
            response.read.return_value = raw
            with self.assertRaises(check.CheckError) as caught:
                check.read_get(check.ORIGIN + "/v1/models", token=SECRET)
            self.assertNotIn("SECRET", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
