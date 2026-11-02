"""本地试调用脚本测试：只注入假客户端，绝不连接模型或其他服务。"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, Mock, mock_open, patch

spec = importlib.util.spec_from_file_location("_rule_refine_probe_test", Path(__file__).with_name("probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

STEPS = {
    "contracts.py": "step01_contracts",
    "rule_config_source.py": "step03d_preparation",
    "materials.py": "step04a_materials",
    "test_materials.py": "step04a_materials",
    "analysis.py": "step04b_analysis",
    "test_analysis.py": "step04b_analysis",
    "model_adapter.py": "step04c_model_adapter",
}
URL = "http://127.0.0.1:4001/v1/"
SECRET = "synthetic-secret"


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rule-refine-probe-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.parent = Path(self.temporary.name) / "results"
        implementation = Path(__file__).resolve().parents[1]
        for relative in probe.EXPECTED:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(implementation / STEPS[target.name] / "payload" / relative, target)
        fixture, _ = probe.load_modules(self.root)
        self.content = json.dumps(fixture.draft(), ensure_ascii=False)
        self.response = NS(
            model="demo-response-model",
            usage=None,
            choices=[
                NS(
                    index=0,
                    finish_reason="stop",
                    message=NS(
                        role="assistant",
                        content=self.content,
                        refusal=None,
                        tool_calls=[],
                        function_call=None,
                        reasoning_content="SECRET reasoning never save",
                    ),
                )
            ],
        )
        self.client = Mock(base_url=URL)
        self.create = self.client.with_options.return_value.chat.completions.create
        self.create.return_value = self.response
        self.factory = Mock(return_value=self.client)

    def run_probe(self, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return probe.run_probe(
                self.root,
                self.parent,
                confirmed=options.pop("confirmed", True),
                client_factory=self.factory,
                base_url=URL,
                api_key=SECRET,
                sdk_version="synthetic-sdk",
                **options,
            )

    def files(self, summary):
        directory = Path(summary["output_dir"])
        return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")}

    def test_confirmed_once_preserves_parameters_closes_and_saves_private_artifacts(self):
        summary = self.run_probe()
        self.assertTrue(summary["format_valid"])
        self.assertEqual(summary["status"], "proposal")
        self.factory.assert_called_once_with(base_url=URL, api_key=SECRET, max_retries=0, timeout=240)
        self.client.with_options.assert_called_once_with(max_retries=0, timeout=240)
        self.create.assert_called_once()
        self.client.close.assert_called_once_with()
        saved = self.files(summary)
        self.assertEqual(
            set(saved),
            {
                "materials.json",
                "request.json",
                "output-schema.json",
                "manifest.json",
                "response-content.json",
                "result.json",
                "summary.json",
            },
        )
        self.create.assert_called_once_with(
            model=probe.MODEL,
            messages=saved["request.json"]["messages"],
            stream=False,
            extra_body={"timeout": 240, "need_trans": True},
        )
        self.assertEqual(saved["materials.json"]["mode"], "demo")
        self.assertEqual(saved["manifest.json"]["data_source"], "synthetic_fixture")
        self.assertEqual(saved["manifest.json"]["model_transport"], "live")
        self.assertEqual(saved["manifest.json"]["operator_confirmation"], "LIVE")
        self.assertIsNone(saved["manifest.json"]["upstream_attempts"])
        self.assertEqual(saved["response-content.json"]["content"], self.content)
        encoded = json.dumps(saved, ensure_ascii=False)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("SECRET reasoning", encoded)
        self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode), 0o700)
        directory = Path(summary["output_dir"])
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in directory.glob("*.json")))

    def test_missing_confirmation_creates_nothing_and_never_loads_or_calls(self):
        for confirmed in (False, None, 1, "LIVE"):
            with self.subTest(confirmed=confirmed), patch.object(probe, "load_modules") as load:
                with self.assertRaises(ValueError):
                    self.run_probe(confirmed=confirmed)
                load.assert_not_called()
                self.factory.assert_not_called()
                self.assertFalse(self.parent.exists())

    def test_initial_save_failure_prevents_client_creation(self):
        with patch.object(probe, "save", side_effect=OSError("synthetic-write-error")):
            with self.assertRaises(OSError):
                self.run_probe()
        self.factory.assert_not_called()
        self.create.assert_not_called()

    def test_sdk_timeout_saves_static_failure_without_retry_or_exception_text(self):
        self.create.side_effect = TimeoutError(SECRET + " raw request details")
        summary = self.run_probe()
        self.assertFalse(summary["format_valid"])
        self.assertEqual(summary["error_code"], "request_failed")
        self.assertEqual(summary["error_type"], "TimeoutError")
        self.assertEqual(summary["sdk_request_calls"], 1)
        saved = self.files(summary)
        self.assertEqual(saved["result.json"]["status"], "analysis_failed")
        self.assertFalse(saved["response-content.json"]["received"])
        self.assertNotIn(SECRET, json.dumps(saved))
        self.create.assert_called_once()
        self.client.close.assert_called_once_with()

    def test_bad_json_or_truncated_response_is_not_format_valid(self):
        for content, reason, code in (
            ("{", "stop", "invalid_model_output"),
            (self.content, "length", "incomplete_response"),
        ):
            with self.subTest(reason=reason):
                self.response.choices[0].message.content = content
                self.response.choices[0].finish_reason = reason
                summary = self.run_probe()
                self.assertFalse(summary["format_valid"])
                self.assertEqual(summary["error_code"], code)
                self.assertEqual(summary["sdk_request_calls"], 1)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.client.close.call_count, 2)

    def test_capture_limit_counts_utf8_bytes_and_rejects_invalid_unicode(self):
        cases = [
            ("x" * 65536, None),
            ("x" * 65537, "content_over_64KiB"),
            ("文" * 22000, "content_over_64KiB"),
            ("\ud800", "invalid_unicode"),
        ]
        for content, reason in cases:
            with self.subTest(size=len(content), reason=reason):
                self.response.choices[0].message.content = content
                capture = probe.CaptureClient(self.client)
                output = capture.with_options(timeout=240).chat.completions.create(model="demo", messages=[])
                self.assertIs(output, self.response)
                self.assertTrue(capture.captured["received"])
                if reason is None:
                    self.assertEqual(capture.captured["content"], content)
                else:
                    self.assertIsNone(capture.captured["content"])
                    self.assertEqual(capture.captured["omitted_reason"], reason)
                self.assertNotIn("reasoning", json.dumps(capture.captured))

    def test_nonlocal_empty_or_whitespace_profiles_exit_before_loading_configuration(self):
        for name in ("env_profile", "ENV_PROFILE"):
            for value in ("STG", "PRD", "", " ", " LOCAL", "LOCAL "):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}, clear=True):
                    with patch.object(probe, "load_modules") as load, contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(probe.main(), 2)
                    load.assert_not_called()

    def test_tty_confirmation_requires_literal_live(self):
        for answer, expected in (("LIVE\n", True), ("live\n", False), ("cancel\n", False), ("", False)):
            with self.subTest(answer=answer), patch("builtins.open", mock_open(read_data=answer)) as opened:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertIs(probe.confirm_live(URL), expected)
                opened.assert_called_once_with("/dev/tty", "r", encoding="utf-8")

    def test_main_creates_proxy_disabled_transport_only_after_confirmation(self):
        fake_modules = {
            name: ModuleType(name) for name in ("httpx", "openai", "util", "util.root_config", "nas", "nas.config")
        }
        for package in ("util", "nas"):
            fake_modules[package].__path__ = []
        fake_modules["util"].root_config = fake_modules["util.root_config"]
        fake_modules["nas"].config = fake_modules["nas.config"]
        fake_modules["util.root_config"].LITELLM_OPENAI_SERVER = URL
        fake_modules["nas.config"].APP_KEY_DICT = {"litellm_key": SECRET}
        fake_modules["openai"].OpenAI = self.factory
        context = MagicMock()
        transport = context.__enter__.return_value
        http_factory = Mock(return_value=context)
        fake_modules["httpx"].Client = http_factory
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, fake_modules))
            stack.enter_context(patch.dict(os.environ, {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}))
            stack.enter_context(patch.object(probe.Path, "cwd", return_value=self.root))
            stack.enter_context(patch.object(probe.Path, "home", return_value=self.parent))
            stack.enter_context(patch.object(probe.importlib.metadata, "version", return_value="synthetic-sdk"))
            confirmer = stack.enter_context(patch.object(probe, "confirm_live", side_effect=[False, True]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(probe.main(), 0)
            http_factory.assert_not_called()
            self.factory.assert_not_called()
            self.create.assert_not_called()
            self.assertFalse(self.parent.exists())
            self.assertEqual(probe.main(), 0)
        self.assertEqual(confirmer.call_count, 2)
        http_factory.assert_called_once_with(trust_env=False, timeout=240)
        self.factory.assert_called_once_with(
            http_client=transport,
            base_url=URL,
            api_key=SECRET,
            max_retries=0,
            timeout=240,
        )
        self.create.assert_called_once()
        self.client.close.assert_called_once_with()
        context.__enter__.assert_called_once_with()
        context.__exit__.assert_called_once_with(None, None, None)

    def test_connection_validation_rejects_remote_or_credential_urls(self):
        for url in (
            "http://example.invalid/v1",
            "http://user:pass@localhost/v1",
            "http://localhost/v1?key=x",
            "http://localhost/v1#secret",
            "file:///tmp/model",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    probe.validate_connection(url, SECRET)
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
