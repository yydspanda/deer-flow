"""复用项目 OpenAI 客户端；本层单次请求，代理内部尝试次数未知。"""

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
