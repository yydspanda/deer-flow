bash <<'SOC_STEP04C'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
from pathlib import Path

root = Path.cwd()
dependencies = {'my_workflows/zeus/rule_refine/contracts.py': '59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a', 'my_workflows/zeus/rule_refine/rule_config_source.py': 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941', 'my_workflows/zeus/rule_refine/materials.py': '75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9', 'test/rule_refine/test_materials.py': '3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76', 'my_workflows/zeus/rule_refine/analysis.py': '1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117', 'test/rule_refine/test_analysis.py': 'a956151f1d3b2615cf745f1c879566af469fb18d8e8d1418c024fd2cf01d5f5f'}
for name, digest in dependencies.items():
    target = root / name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("停止：前序文件不存在或版本不同，未修改：" + name)
files = {
    'my_workflows/zeus/rule_refine/model_adapter.py': r'''"""复用项目 OpenAI 客户端；本层单次请求，代理内部尝试次数未知。"""

import hashlib
import json
import math
import time

from .analysis import analyze_materials


class _ResponseRejected(ValueError):
    pass


def _label(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and value == value.strip() and value.isprintable()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def analyze_with_model(materials, client, *, model, transport_mode, timeout_seconds=240):
    """client 由调用方提供；不加载应用配置，不负责保存、重发或执行建议。"""
    if not _label(model) or transport_mode not in ("mock", "live"):
        raise ValueError("必须指定模型名及 mock/live 调用模式")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 1 <= timeout_seconds <= 600
        or not math.isfinite(timeout_seconds)
    ):
        raise ValueError("超时必须为 1～600 秒")
    config = {
        "model": model,
        "stream": False,
        "sdk_max_retries": 0,
        "timeout_seconds": timeout_seconds,
        # 原代理的正文转换选项，不代表关闭模型思考。
        "extra_body": {"timeout": timeout_seconds, "need_trans": True},
    }
    audit = {
        "schema_version": "sec_know.rule_model_transport.v1",
        "transport_mode": transport_mode,
        "requested_model": model,
        "returned_model": None,
        "sdk_max_retries": 0,
        "timeout_seconds": timeout_seconds,
        "request_config_sha256": _hash(config),
        "endpoint_sha256": None,
        "sdk_request_calls": 0,
        "upstream_attempts": None,
        "finish_reason": None,
        "usage": {name: None for name in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "elapsed_ms": 0,
        "error_code": None,
    }

    def generate(messages):
        start = time.perf_counter()
        try:
            # SDK 副本不修改调用方的超时/重试设置。URL 仅记录摘要，不输出凭证。
            audit["endpoint_sha256"] = _hash(str(client.base_url))
            bounded = client.with_options(max_retries=0, timeout=timeout_seconds)
            create = bounded.chat.completions.create
            audit["sdk_request_calls"] += 1
            response = create(
                model=model,
                messages=messages,
                stream=False,
                extra_body=dict(config["extra_body"]),
            )
            returned_model = getattr(response, "model", None)
            audit["returned_model"] = returned_model if _label(returned_model) else None
            usage = getattr(response, "usage", None)
            for name in audit["usage"]:
                value = getattr(usage, name, None)
                audit["usage"][name] = value if type(value) is int and value >= 0 else None
            choices = getattr(response, "choices", None)
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or type(getattr(choices[0], "index", None)) is not int
                or choices[0].index != 0
            ):
                raise _ResponseRejected("invalid_choices")
            choice = choices[0]
            reason = getattr(choice, "finish_reason", None)
            audit["finish_reason"] = (
                reason if reason in ("stop", "length", "content_filter", "tool_calls", "function_call") else "unknown"
            )
            if reason != "stop":
                raise _ResponseRejected("incomplete_response")
            message = getattr(choice, "message", None)
            if getattr(message, "role", None) != "assistant":
                raise _ResponseRejected("invalid_message")
            if (
                getattr(message, "refusal", None) is not None
                or getattr(message, "tool_calls", None)
                or getattr(message, "function_call", None) is not None
            ):
                raise _ResponseRejected("unsupported_response")
            content = getattr(message, "content", None)
            if not isinstance(content, str) or not content.strip() or len(content) > 16384:
                raise _ResponseRejected("invalid_content")
            try:
                encoded = content.encode("utf-8")
            except UnicodeError:
                raise _ResponseRejected("invalid_content") from None
            if len(encoded) > 16384:
                raise _ResponseRejected("invalid_content")
            # 只返回正文；不读取 reasoning_content，不从混合文本中截取 JSON。
            return content
        except _ResponseRejected as error:
            audit["error_code"] = str(error)
            raise
        except Exception as error:
            audit["error_code"] = "request_failed" if audit["sdk_request_calls"] else "client_setup_failed"
            audit["error_type"] = type(error).__name__
            raise
        finally:
            audit["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)

    result = analyze_materials(
        materials, generate, response_source="model" if transport_mode == "live" else "synthetic_reply"
    )
    result["model_calls"] = audit["sdk_request_calls"] if transport_mode == "live" else 0
    result["model_call_basis"] = "sdk_create_invocations_not_upstream_attempts"
    result["model_transport"] = audit
    return result
''',
    'test/rule_refine/test_model_adapter.py': r'''"""模型适配离线验证：只使用注入的 Mock，不导入应用或 OpenAI SDK。"""

import copy
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_rule_refine_adapter_fixtures", Path(__file__).with_name("test_analysis.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
adapter = importlib.import_module(fixtures.fixtures.package.__name__ + ".model_adapter")


def response():
    return NS(
        model="demo-model-returned",
        choices=[
            NS(
                index=0,
                finish_reason="stop",
                message=NS(
                    role="assistant",
                    content=json.dumps(fixtures.draft(), ensure_ascii=False),
                    refusal=None,
                    tool_calls=[],
                    function_call=None,
                ),
            )
        ],
        usage=NS(prompt_tokens=100, completion_tokens=200, total_tokens=300),
    )


def client_for(reply=None):
    client = Mock(base_url="http://127.0.0.1:4001/v1")
    client.with_options.return_value.chat.completions.create.return_value = response() if reply is None else reply
    return client


class ModelAdapterTests(unittest.TestCase):
    def call(self, reply=None, data=None, client=None, **options):
        client = client_for(reply) if client is None else client
        options = {"model": "demo-model", "transport_mode": "mock", **options}
        result = adapter.analyze_with_model(fixtures.materials() if data is None else data, client, **options)
        return result, client

    def assert_failure(self, reply, code):
        result, client = self.call(reply)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertIsNone(result["recommendation"])
        self.assertEqual(result["model_transport"]["error_code"], code)
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 1)
        self.assertEqual(result["model_calls"], 0)
        client.with_options.return_value.chat.completions.create.assert_called_once()

    def test_normal_request_is_bounded_projected_once_and_audited(self):
        with patch.object(
            fixtures.analysis, "build_analysis_request", wraps=fixtures.analysis.build_analysis_request
        ) as p:
            result, client = self.call()
            p.assert_called_once()
        self.assertEqual(result["status"], "proposal")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["model_call_basis"], "sdk_create_invocations_not_upstream_attempts")
        client.with_options.assert_called_once_with(max_retries=0, timeout=240)
        client.with_options.return_value.chat.completions.create.assert_called_once_with(
            model="demo-model",
            messages=fixtures.analysis.build_analysis_request(fixtures.materials())["messages"],
            stream=False,
            extra_body={"timeout": 240, "need_trans": True},
        )
        audit = result["model_transport"]
        for key, value in {
            "sdk_request_calls": 1,
            "upstream_attempts": None,
            "sdk_max_retries": 0,
            "timeout_seconds": 240,
            "requested_model": "demo-model",
            "returned_model": "demo-model-returned",
            "finish_reason": "stop",
            "error_code": None,
        }.items():
            self.assertEqual(audit[key], value)
        self.assertEqual(audit["usage"], {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300})
        self.assertGreaterEqual(audit["elapsed_ms"], 0)
        for field in ("endpoint_sha256", "request_config_sha256"):
            self.assertRegex(audit[field], r"^[0-9a-f]{64}$")

    def test_invalid_choices(self):
        good = response().choices[0]
        for choices in ([], None, [good, good], [NS(index=1, message=good.message, finish_reason="stop")]):
            with self.subTest(choices=choices):
                reply = response()
                reply.choices = choices
                self.assert_failure(reply, "invalid_choices")

    def test_incomplete_response(self):
        for reason in (None, "length", "content_filter", "tool_calls", "unknown"):
            with self.subTest(reason=reason):
                reply = response()
                reply.choices[0].finish_reason = reason
                self.assert_failure(reply, "incomplete_response")

    def test_invalid_message(self):
        for message in (None, NS(role="user", content="{}"), NS(content="{}")):
            reply = response()
            reply.choices[0].message = message
            self.assert_failure(reply, "invalid_message")

    def test_refusal_and_tool_responses_are_rejected(self):
        for key, value in (
            ("refusal", "refused"),
            ("refusal", ""),
            ("tool_calls", [NS(id="tool")]),
            ("function_call", {}),
        ):
            with self.subTest(field=key):
                reply = response()
                setattr(reply.choices[0].message, key, value)
                self.assert_failure(reply, "unsupported_response")

    def test_invalid_or_oversized_content(self):
        for content in (None, "", " \n", 123, [], "x" * 16385, "文" * 6000, "\ud800"):
            reply = response()
            reply.choices[0].message.content = content
            self.assert_failure(reply, "invalid_content")

    def test_sdk_failure_is_not_retried_or_leaked(self):
        client = client_for()
        client.with_options.return_value.chat.completions.create.side_effect = RuntimeError("SECRET synthetic payload")
        result, _ = self.call(client=client)
        self.assertEqual(result["model_transport"]["error_code"], "request_failed")
        self.assertEqual(result["model_transport"]["error_type"], "RuntimeError")
        self.assertNotIn("SECRET", json.dumps(result))
        client.with_options.return_value.chat.completions.create.assert_called_once()

    def test_setup_failure_makes_no_request(self):
        client = client_for()
        client.with_options.side_effect = RuntimeError("SECRET setup failure")
        result, _ = self.call(client=client)
        self.assertEqual(result["model_transport"]["error_code"], "client_setup_failed")
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_invalid_options_are_rejected_before_using_client(self):
        cases = [{"model": x} for x in ("", " x", "x ", "x\ny", "x\x00y", "x\x7fy", "x" * 129, 1)]
        cases += [{"transport_mode": x} for x in (None, "demo", "unknown")]
        cases += [{"timeout_seconds": x} for x in (True, False, 0, 601, float("nan"), float("inf"), "240")]
        for options in cases:
            with self.subTest(options=options):
                client = client_for()
                with self.assertRaises(ValueError):
                    self.call(client=client, **options)
                self.assertEqual(client.mock_calls, [])

    def test_unusable_materials_do_not_use_client(self):
        for data in (fixtures.materials([]), {}, [], {**fixtures.materials(), "samples": [1]}):
            result, client = self.call(data=data)
            self.assertIn(result["status"], ("insufficient_evidence", "analysis_failed"))
            self.assertEqual(result["model_transport"]["sdk_request_calls"], 0)
            self.assertEqual(client.mock_calls, [])

    def test_independent_calls_preserve_input_and_audit(self):
        data = fixtures.materials()
        before = copy.deepcopy(data)
        first, _ = self.call(data=data)
        saved = copy.deepcopy(first)
        second, _ = self.call(data=data, model="other-model", timeout_seconds=12.5)
        self.assertEqual(data, before)
        self.assertEqual(first, saved)
        self.assertIsNot(first["model_transport"], second["model_transport"])
        self.assertNotEqual(
            first["model_transport"]["request_config_sha256"], second["model_transport"]["request_config_sha256"]
        )

    def test_invalid_json_still_fails_analysis_after_valid_transport(self):
        reply = response()
        reply.choices[0].message.content = '{"unknown": true}'
        result, _ = self.call(reply)
        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["error_code"], "invalid_model_output")
        self.assertIsNone(result["model_transport"]["error_code"])
        self.assertEqual(result["model_transport"]["sdk_request_calls"], 1)

    def test_missing_or_invalid_usage_is_unknown_and_reasoning_is_not_used(self):
        reply = response()
        reply.model = None
        reply.usage = None
        reply.choices[0].message.reasoning_content = "SECRET reasoning must not be stored"
        result, _ = self.call(reply)
        self.assertEqual(result["status"], "proposal")
        self.assertIsNone(result["model_transport"]["returned_model"])
        self.assertTrue(all(value is None for value in result["model_transport"]["usage"].values()))
        self.assertNotIn("SECRET", json.dumps(result))
        reply.usage = NS(prompt_tokens=True, completion_tokens=-1, total_tokens="300")
        result, _ = self.call(reply)
        self.assertTrue(all(value is None for value in result["model_transport"]["usage"].values()))


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        outcome = adapter.analyze_with_model(
            fixtures.materials(), client_for(), model="demo-model", transport_mode="mock"
        )
        print(
            json.dumps(
                {
                    "status": outcome["status"],
                    "adapter_ready": outcome["status"] == "proposal",
                    "sdk_request_calls": outcome["model_transport"]["sdk_request_calls"],
                    "model_calls": outcome["model_calls"],
                    "database_calls": 0,
                    "redis_calls": 0,
                },
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
''',
}
expected = {'my_workflows/zeus/rule_refine/model_adapter.py': 'be651bf6142293baf04c6f2c3c585ec0ea81ceeab1d20b1055f14dfe67f75790', 'test/rule_refine/test_model_adapter.py': 'caece3caae02e1eaa41fd2d1002d9bc5c8e8da82ebad6f5ce44e5893943884a1'}
for name, content in files.items():
    raw = content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected[name]:
        raise SystemExit("停止：粘贴内容校验失败，未修改：" + name)
    compile(content, name, "exec")
    target = root / name
    if target.exists() and target.read_bytes() != raw:
        raise SystemExit("停止：已有不同版本，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("新增：" + name)
    else:
        print("已存在且一致：" + name)
PYWRITE
.venv/bin/python test/rule_refine/test_model_adapter.py
.venv/bin/python test/rule_refine/test_model_adapter.py --demo
SOC_STEP04C
