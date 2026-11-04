"""Read-only route checks use temporary files and HTTP doubles, never services."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request
from unittest.mock import Mock, patch

import context_route_check as check


class ContextRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        (self.root / "llm_service").mkdir()
        self.write(
            "config/rule_refine.analysis.local.json",
            json.dumps({"model": check.DEFAULT_MODEL}),
        )
        self.write(
            "llm_service/openai_completion.py",
            """raise RuntimeError("MUST_NOT_EXECUTE")
PRD_MODEL_CONFIGS = {
 "Pub_DeepSeek_V4_Flash": {
  "type": "OPENAI", "base_url": "http://name:SECRET_AUTH@private.invalid:8080/v1?key=SECRET_QUERY#SECRET_FRAGMENT",
  "upstream_model": "deepseek-v4-flash", "api_key": "SECRET_KEY",
 },
 "OTHER_MODEL": {"type":"OPENAI", "base_url":"http://OTHER_SECRET.invalid/v1", "api_key":"OTHER_KEY"}
}
STG_MODEL_CONFIGS = {"Pub_DeepSeek_V4_Flash": {"type": "EAGW", "base_url": unknown_config}}
""",
        )
        self.write(
            "llm_service/litellm_proxy_config.local.yaml",
            "litellm_settings:\n  drop_params: true\n  max_chat_completion_tokens: 8192\n"
            "  api_key: SECRET_YAML\ngeneral_settings:\n  max_tokens: 123\n  master_key: SECRET_MASTER\n",
        )
        self.get = Mock(
            return_value={
                "upstreams": {
                    check.DEFAULT_MODEL: {
                        "type": "OPENAI",
                        "base_url": "http://another:SECRET_RUNTIME@private.invalid:8080/v1?token=SECRET_RUNTIME_QUERY",
                        "api_key": "SECRET_RUNTIME_KEY",
                    },
                    "UNSELECTED": {"base_url": "http://HIDDEN_OTHER.invalid"},
                },
                "unrelated_secret": "SECRET_RESPONSE",
            }
        )

    def write(self, relative, text):
        (self.root / relative).write_text(text, encoding="utf-8")

    def test_static_code_never_executes_and_only_selected_route_is_reported(self):
        result = check.inspect(self.root, get=self.get)
        self.get.assert_called_once_with(check.HEALTH_URL)
        self.assertEqual(result["status"], "route_observed_context_capacity_unknown")
        self.assertEqual(
            result["running_adapter"]["endpoint"], "http://private.invalid:8080/v1"
        )
        self.assertTrue(result["disk_literal_matches_running_endpoint"])
        self.assertEqual(len(result["disk_route_candidates"]), 2)
        self.assertIsNone(result["disk_route_candidates"][1]["endpoint"])
        self.assertEqual(
            result["local_yaml_settings"]["values"],
            {"drop_params": True, "max_chat_completion_tokens": 8192},
        )
        self.assertTrue(result["code_on_disk_is_not_runtime_proof"])
        self.assertTrue(result["server_deployment_configuration_required"])
        self.assertFalse(result["capacity_probe_performed"])
        self.assertEqual(result["model_calls"], 0)
        text = json.dumps(result)
        for hidden in (
            "SECRET",
            "OTHER_MODEL",
            "OTHER_KEY",
            "HIDDEN_OTHER",
            "MUST_NOT_EXECUTE",
        ):
            self.assertNotIn(hidden, text)
        for entry in result["source_files"].values():
            self.assertEqual(len(entry["sha256"]), 64)

    def test_network_failure_cannot_establish_running_route(self):
        self.get.side_effect = URLError("SECRET_NETWORK_DETAILS")
        result = check.inspect(self.root, get=self.get)
        self.assertEqual(result["status"], "runtime_route_unknown")
        self.assertEqual(result["running_adapter"]["status"], "health_unavailable")
        self.assertIsNone(result["disk_literal_matches_running_endpoint"])
        self.assertNotIn("SECRET_NETWORK_DETAILS", json.dumps(result))

    def test_annotated_route_assignments_are_parsed_without_evaluating_annotations(
        self,
    ):
        path = self.root / "llm_service/openai_completion.py"
        source = path.read_text(encoding="utf-8")
        annotated = source.replace(
            "PRD_MODEL_CONFIGS =", "PRD_MODEL_CONFIGS: explode_annotation() ="
        ).replace("STG_MODEL_CONFIGS =", "STG_MODEL_CONFIGS: MissingType =")
        original_routes = check.literal_routes(source, check.DEFAULT_MODEL)
        annotated_routes = check.literal_routes(annotated, check.DEFAULT_MODEL)
        self.assertEqual(annotated_routes, original_routes)
        self.assertEqual(len(annotated_routes), 2)
        self.assertEqual(annotated_routes[0]["upstream_model"], "deepseek-v4-flash")

    def test_health_without_selected_route_does_not_fall_back_to_other_model(self):
        self.get.return_value = {
            "upstreams": {
                "OTHER": {"type": "OPENAI", "base_url": "http://other.invalid"}
            }
        }
        result = check.inspect(self.root, get=self.get)
        self.assertEqual(result["status"], "runtime_route_unknown")
        self.assertEqual(
            result["running_adapter"]["status"], "selected_route_not_reported"
        )
        self.assertIsNone(result["disk_literal_matches_running_endpoint"])

    def test_http_redirect_is_not_followed_or_printed(self):
        handler = check.NoRedirect()
        request = Request(check.HEALTH_URL)
        self.assertIsNone(
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://external.invalid?key=SECRET_REDIRECT",
            )
        )
        self.get.side_effect = HTTPError(
            check.HEALTH_URL, 302, "Found", {}, io.BytesIO(b"SECRET_REDIRECT_BODY")
        )
        result = check.inspect(self.root, get=self.get)
        self.assertEqual(
            result["running_adapter"],
            {"status": "health_http_error", "http_status": 302},
        )
        self.assertNotIn("SECRET_REDIRECT", json.dumps(result))

    def test_http_reader_is_fixed_loopback_no_proxy_bounded_and_get_only(self):
        stream = Mock()
        stream.read.return_value = b'{"upstreams":{}}'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=stream)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(check, "build_opener", return_value=opener) as build:
            self.assertEqual(check.read_get(check.HEALTH_URL), {"upstreams": {}})
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], check.NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, check.HEALTH_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 3})
        stream.read.assert_called_once_with(check.MAX_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "only_fixed_loopback"):
            check.read_get("http://external.invalid/health")

    def test_oversized_health_response_rejected_without_json_decoding(self):
        opener = Mock()
        stream = Mock()
        stream.read.return_value = b"x" * (check.MAX_BYTES + 1)
        opener.open.return_value.__enter__ = Mock(return_value=stream)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with (
            patch.object(check, "build_opener", return_value=opener),
            self.assertRaisesRegex(ValueError, "health_response_too_large"),
        ):
            check.read_get(check.HEALTH_URL)

    def test_invalid_model_label_does_not_trigger_request(self):
        self.write(
            "config/rule_refine.analysis.local.json", '{"model":"bad model SECRET"}'
        )
        result = check.inspect(self.root, get=self.get)
        self.assertEqual(result["status"], "invalid_selected_model")
        self.get.assert_not_called()
        self.assertNotIn("SECRET", json.dumps(result))

    def test_missing_files_use_explicit_default_without_claiming_disk_match(self):
        empty = self.root / "missing"
        result = check.inspect(empty, get=self.get)
        self.assertEqual(result["selected_model"], check.DEFAULT_MODEL)
        self.assertEqual(result["selected_model_basis"], "default")
        self.assertEqual(result["disk_route_candidates"], [])
        self.assertIsNone(result["disk_literal_matches_running_endpoint"])
        self.assertFalse(empty.exists())

    def test_endpoint_sanitizes_userinfo_query_fragment_and_rejects_non_http(self):
        self.assertEqual(
            check.endpoint("https://user:secret@[::1]:443/v1?q=secret#secret"),
            "https://[::1]:443/v1",
        )
        for url in (
            "file:///private",
            "http://host.invalid:bad/v1",
            "http://host.invalid/\nSECRET",
            None,
        ):
            self.assertIsNone(check.endpoint(url))

    def test_yaml_nested_or_ambiguous_settings_not_reported_as_global_values(self):
        value = check.yaml_settings(
            "litellm_settings:\n  nested:\n    max_tokens: 9\n"
            "  drop_params: true\n  max_tokens: 10\n  max_tokens: 20\n"
        )
        self.assertEqual(value, {"drop_params": True})


if __name__ == "__main__":
    unittest.main()
