"""Offline diagnostic checks: synthetic replies, no service or model access."""

import copy
import hashlib
import importlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import ExitStack, redirect_stdout
from functools import lru_cache
from pathlib import Path
from unittest.mock import Mock, patch

import build_command as builder


@lru_cache(maxsize=1)
def tested_runner():
    return builder.load_runner()


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_loader(root):
    name = "_topic_diagnostic_test_" + uuid.uuid4().hex
    module = types.ModuleType(name)
    module.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[name] = module
    return lambda part: importlib.import_module(name + "." + part)


class NoNetwork(unittest.TestCase):
    def setUp(self):
        self.guards = ExitStack()
        self.addCleanup(self.guards.close)
        for name in (
            "socket.create_connection",
            "socket.socket.connect",
            "socket.socket.connect_ex",
        ):
            self.guards.enter_context(
                patch(name, side_effect=AssertionError("offline-only test"))
            )
        self.temporary = tempfile.TemporaryDirectory(
            prefix="topic-reply-diagnostic-test-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "diagnostic"
        self.runner = tested_runner()


class CaptureTests(NoNetwork):
    def setUp(self):
        super().setUp()
        self.directory.mkdir(mode=0o700)
        self.request = {
            "phase": "generate",
            "prompt_version": "synthetic-prompt",
            "prompt_sha256": "a" * 64,
            "messages": [{"role": "user", "content": "NEVER-SAVE-REQUEST"}],
            "context": {"raw_logs": ["NEVER-SAVE-RAW-LOG"]},
        }
        self.response = {
            "content": '{"status":"proposal","value":"first","value":"second"}',
            "reasoning_content": "NEVER-SAVE-REASONING",
            "transport": {
                "requested_model": "synthetic-model",
                "returned_model": "synthetic-model",
                "finish_reason": "stop",
                "sdk_request_calls": 1,
                "response_received": True,
                "error_code": None,
                "elapsed_ms": 12.5,
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 5,
                    "total_tokens": 17,
                },
                "authorization": "NEVER-SAVE-KEY",
                "reasoning_content": "NEVER-SAVE-REASONING",
                "headers": {"secret": "NEVER-SAVE-HEADER"},
            },
        }

    def capture(self, response=None, phase="generate", directory=None):
        return self.runner.capture_reply(
            directory or self.directory,
            phase,
            {**self.request, "phase": phase},
            response or self.response,
        )

    def content_file(self):
        files = list(self.directory.rglob("*.final.content.txt"))
        self.assertEqual(len(files), 1)
        return files[0]

    def test_exact_reply_preserves_duplicate_members_without_private_transport(self):
        before = copy.deepcopy(self.response)
        result = self.capture()
        self.assertIsInstance(result, dict)
        self.assertEqual(
            self.content_file().read_text(encoding="utf-8"), self.response["content"]
        )
        text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in self.directory.rglob("*")
            if p.is_file()
        )
        for marker in (
            "NEVER-SAVE-REQUEST",
            "NEVER-SAVE-RAW-LOG",
            "NEVER-SAVE-REASONING",
            "NEVER-SAVE-KEY",
            "NEVER-SAVE-HEADER",
        ):
            self.assertNotIn(marker, text)
        self.assertEqual(self.response, before)
        self.assertIn(
            hashlib.sha256(self.response["content"].encode()).hexdigest(), text
        )

    def test_multibyte_prefix_is_bounded_valid_utf8_and_marked_truncated(self):
        response = copy.deepcopy(self.response)
        response["content"] = "测试" * (64 * 1024)
        result = self.capture(response)
        raw = self.content_file().read_bytes()
        self.assertLessEqual(len(raw), 64 * 1024)
        self.assertGreater(len(raw), 0)
        self.assertTrue(response["content"].startswith(raw.decode("utf-8")))
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertIn(
            hashlib.sha256(response["content"].encode()).hexdigest(), rendered
        )
        self.assertTrue(any(v is True for k, v in result.items() if "truncat" in k))

    def test_files_and_directories_have_private_permissions(self):
        old = os.umask(0)
        try:
            self.capture()
        finally:
            os.umask(old)
        for path in [self.directory, *self.directory.rglob("*")]:
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600
            )

    def test_same_phase_cannot_overwrite_existing_reply(self):
        self.capture()
        original = self.content_file().read_bytes()
        response = copy.deepcopy(self.response)
        response["content"] = "REPLACEMENT"
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            self.capture(response)
        self.assertEqual(self.content_file().read_bytes(), original)

    def test_unsafe_phase_cannot_escape_directory(self):
        for phase in ("../escape", "/tmp/escape", "third", "", "review/extra"):
            with (
                self.subTest(phase=phase),
                self.assertRaises((ValueError, OSError, RuntimeError)),
            ):
                self.capture(phase=phase)

    def test_symlink_destination_is_rejected(self):
        target = Path(self.temporary.name) / "outside"
        target.mkdir()
        self.directory.rmdir()
        self.directory.symlink_to(target, target_is_directory=True)
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            self.capture()
        self.assertEqual(list(target.iterdir()), [])

    def test_nested_usage_does_not_save_untrusted_strings_or_extra_fields(self):
        response = copy.deepcopy(self.response)
        response["transport"]["usage"] = {
            "prompt_tokens": "NEVER-SAVE-NUMERIC-STRING",
            "completion_tokens": {"secret": "NEVER-SAVE-NESTED"},
            "total_tokens": 17,
            "reasoning": "NEVER-SAVE-USAGE-REASONING",
        }
        try:
            self.capture(response)
        except (ValueError, TypeError):
            pass
        text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in self.directory.rglob("*")
            if p.is_file()
        )
        for marker in (
            "NEVER-SAVE-NUMERIC-STRING",
            "NEVER-SAVE-NESTED",
            "NEVER-SAVE-USAGE-REASONING",
        ):
            self.assertNotIn(marker, text)

    def test_nonstring_content_never_serializes_foreign_object(self):
        response = copy.deepcopy(self.response)
        response["content"] = {"secret": "NEVER-SAVE-NONSTRING"}
        try:
            self.capture(response)
        except (ValueError, TypeError):
            pass
        text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in self.directory.rglob("*")
            if p.is_file()
        )
        self.assertNotIn("NEVER-SAVE-NONSTRING", text)

    def test_absent_content_preserves_transport_without_fabricating_reply(self):
        response = copy.deepcopy(self.response)
        response["content"] = None
        response["transport"].update(
            response_received=False, error_code="request_failed"
        )
        result = self.capture(response)
        self.assertFalse(result["content_present"])
        self.assertIsNone(result["full_content_sha256"])
        self.assertEqual(list(self.directory.rglob("*.final.content.txt")), [])
        self.assertEqual(result["transport"]["error_code"], "request_failed")


class ProcessorTests(NoNetwork):
    @classmethod
    def setUpClass(cls):
        cls.runtime_directory = tempfile.TemporaryDirectory(
            prefix="topic-diagnostic-runtime-"
        )
        cls.addClassCleanup(cls.runtime_directory.cleanup)
        cls.runtime_root = Path(cls.runtime_directory.name)
        for name, raw in builder.runtime_files().items():
            path = cls.runtime_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        cls.fixtures = load_file(
            "topic_diagnostic_synthetic_fixtures",
            cls.runtime_root / "test/rule_refine/test_topic_processor.py",
        )

    def setUp(self):
        super().setUp()
        self.load = runtime_loader(self.runtime_root)
        self.prepared = {
            "load": self.load,
            "case": self.fixtures.case(),
            "signal": self.fixtures.signal(),
            "rules_factory": lambda: Mock(
                get_rule=Mock(return_value=self.fixtures.rule())
            ),
            "model": "synthetic-model",
            "budget": self.load("surge_budget").ContextBudget(),
            "operator_context": "Synthetic only",
            "namespace": "synthetic-diagnostic-only",
        }
        self.delivery_guard = self.guards.enter_context(
            patch.object(
                self.load("topic_processor").TopicDelivery,
                "deliver",
                side_effect=AssertionError("diagnostic must not enter delivery"),
            )
        )

    def response(self, request):
        answer = self.fixtures.reply(request)
        answer["filter"] = {
            "topic": "synthetic-topic",
            "match": {
                "name": "action",
                "operator": "Eq",
                "value": "inventory-versions",
                "field_role": "event_field",
                "basis": "Original event action fact",
            },
        }
        del answer["topic_review"]["field_roles"]
        return {
            "content": json.dumps(answer),
            "transport": {
                "finish_reason": "stop",
                "error_code": None,
                "sdk_request_calls": 1,
                "response_received": True,
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 5,
                    "total_tokens": 17,
                },
            },
        }

    def assert_no_delivery(self):
        self.delivery_guard.assert_not_called()
        self.assertEqual(list(self.directory.rglob("delivery.json")), [])
        self.assertEqual(list(self.directory.rglob("submitted.json")), [])

    def test_valid_two_phase_run_captures_both_replies_without_delivery(self):
        requests = []

        def generate(request):
            requests.append(request)
            return self.response(request)

        original = copy.deepcopy(self.prepared["case"])
        outcome = self.runner.process(self.prepared, self.directory, generate)
        self.assertEqual([r["phase"] for r in requests], ["generate", "review"])
        self.assertEqual(len(list(self.directory.rglob("*.final.content.txt"))), 2)
        self.assertEqual(outcome["reason"], "diagnostic_only_not_submitted")
        self.assertEqual(self.prepared["case"], original)
        self.assert_no_delivery()
        saved = "\n".join(
            p.read_text(encoding="utf-8")
            for p in self.directory.rglob("*")
            if p.is_file()
        )
        self.assertNotIn("NEVER-PERSIST-RAW", saved)

    def test_conflicting_review_keeps_original_and_stops_at_two_calls(self):
        calls = []
        malformed = '{"status":"proposal","value":"first","value":"second"}'

        def generate(request):
            calls.append(request["phase"])
            result = self.response(request)
            if request["phase"] == "review":
                result["content"] = malformed
            return result

        outcome = self.runner.process(self.prepared, self.directory, generate)
        self.assertEqual(calls, ["generate", "review"])
        self.assertEqual(outcome["reason"], "duplicate_json_key")
        self.assertIn(
            malformed,
            [
                p.read_text(encoding="utf-8")
                for p in self.directory.rglob("*.final.content.txt")
            ],
        )
        self.assert_no_delivery()

    def test_conflicting_generation_stops_before_review(self):
        generate = Mock(
            return_value={
                "content": '{"status":"proposal","status":"insufficient_evidence"}',
                "transport": {
                    "finish_reason": "stop",
                    "error_code": None,
                    "sdk_request_calls": 1,
                },
            }
        )
        outcome = self.runner.process(self.prepared, self.directory, generate)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(outcome["reason"], "duplicate_json_key")
        self.assertEqual(len(list(self.directory.rglob("*.final.content.txt"))), 1)
        self.assert_no_delivery()

    def test_capture_write_failure_stops_before_second_call(self):
        generate = Mock(side_effect=self.response)
        with patch.object(
            self.runner, "capture_reply", side_effect=OSError("NEVER-SAVE-ERROR-TEXT")
        ):
            outcome = self.runner.process(self.prepared, self.directory, generate)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(outcome["status"], "blocked")
        self.assert_no_delivery()
        saved = "\n".join(
            p.read_text(encoding="utf-8") for p in self.directory.rglob("*.json")
        )
        self.assertNotIn("NEVER-SAVE-ERROR-TEXT", saved)

    def test_transport_length_is_captured_but_never_reviewed(self):
        def incomplete(request):
            result = self.response(request)
            result["transport"].update(
                finish_reason="length", error_code="incomplete_model_response"
            )
            return result

        generate = Mock(side_effect=incomplete)
        outcome = self.runner.process(self.prepared, self.directory, generate)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(len(list(self.directory.rglob("*.final.content.txt"))), 1)
        self.assert_no_delivery()


class MainTests(NoNetwork):
    def setUp(self):
        super().setUp()
        self.prepared = {
            "model": "synthetic-model",
            "case": {
                "current_samples": [{}],
                "history_samples": [{}],
                "execute_type": {"status": "resolved", "values": [3]},
            },
            "preview": {
                "selection": {"phase": "generate", "current_count": 1},
                "request": {"input_bytes": 128, "estimated_input_tokens": 64},
            },
            "budget": types.SimpleNamespace(input_token_budget=1024),
        }

    def run_main(self, argv=(), answer="LIVE", validation_error=None):
        output = io.StringIO()
        with (
            patch.object(
                self.runner, "validate_sources", side_effect=validation_error
            ) as validate,
            patch.object(self.runner, "prepare", return_value=self.prepared) as prepare,
            patch.object(self.runner, "ask", return_value=answer) as ask,
            patch.object(
                self.runner, "execute", return_value={"status": "diagnostic_completed"}
            ) as execute,
            redirect_stdout(output),
        ):
            code = self.runner.main(list(argv))
        return code, output.getvalue(), validate, prepare, ask, execute

    def test_preview_has_zero_posts_and_does_not_execute_or_confirm(self):
        code, output, validate, prepare, ask, execute = self.run_main()
        self.assertEqual(code, 0)
        preview = json.loads(output)
        self.assertEqual(preview["max_zeus_posts"], 0)
        self.assertEqual(preview["max_sdk_requests"], 2)
        self.assertTrue(preview["original_prompts_and_sampling_unchanged"])
        validate.assert_called_once()
        prepare.assert_called_once()
        ask.assert_not_called()
        execute.assert_not_called()

    def test_cancelled_live_does_not_execute(self):
        code, _, _, _, ask, execute = self.run_main(("--live",), answer="NO")
        self.assertEqual(code, 0)
        ask.assert_called_once()
        execute.assert_not_called()

    def test_live_confirmation_executes_once(self):
        code, _, _, _, ask, execute = self.run_main(("--live",))
        self.assertEqual(code, 0)
        ask.assert_called_once()
        execute.assert_called_once_with(self.prepared)

    def test_source_validation_stops_before_material_loading_or_live_confirmation(self):
        code, output, validate, prepare, ask, execute = self.run_main(
            ("--live",),
            validation_error=ValueError("diagnostic_source_mismatch:synthetic.py"),
        )
        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(output)["error_code"], "diagnostic_source_mismatch:synthetic.py"
        )
        validate.assert_called_once()
        prepare.assert_not_called()
        ask.assert_not_called()
        execute.assert_not_called()

    def test_mixed_execute_type_stops_before_confirmation(self):
        self.prepared["case"]["execute_type"]["status"] = "mixed"
        code, _, _, _, ask, execute = self.run_main(("--live",))
        self.assertEqual(code, 1)
        ask.assert_not_called()
        execute.assert_not_called()

    def test_source_digest_check_detects_change_without_importing_module(self):
        source = Path(self.temporary.name) / "synthetic.py"
        raw = b"raise AssertionError('must not execute')\n"
        source.write_bytes(raw)
        with patch.object(
            self.runner,
            "EXPECTED_SOURCES",
            {"synthetic.py": hashlib.sha256(raw).hexdigest()},
        ):
            self.runner.validate_sources(source.parent)
            source.write_text("# changed\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "diagnostic_source_mismatch:synthetic.py"
            ):
                self.runner.validate_sources(source.parent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
