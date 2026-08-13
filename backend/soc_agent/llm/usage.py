"""Provider-compatible token usage normalization and bounded estimation."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Literal

USAGE_ESTIMATION_METHOD = "soc.content_token_estimate.v1"
UsageMeasurementStatus = Literal[
    "reported",
    "estimated",
    "mixed",
    "unavailable",
]

_INPUT_TOKEN_KEYS = (
    "input_tokens",
    "prompt_tokens",
    "input_token_count",
    "prompt_token_count",
    "promptTokenCount",
)
_OUTPUT_TOKEN_KEYS = (
    "output_tokens",
    "completion_tokens",
    "output_token_count",
    "completion_token_count",
    "candidatesTokenCount",
)
_TOTAL_TOKEN_KEYS = (
    "total_tokens",
    "total_token_count",
    "totalTokenCount",
)
_DETAIL_KEYS = (
    "input_token_details",
    "output_token_details",
    "prompt_tokens_details",
    "completion_tokens_details",
)


def resolve_chat_usage(
    *,
    messages: Sequence[Mapping[str, str]],
    response_content: Any,
    provider_usage: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prefer normalized provider usage, then estimate from exact visible content."""

    normalized = normalize_provider_usage(provider_usage)
    if normalized:
        missing_fields = {
            "input_tokens",
            "output_tokens",
        } - normalized.keys()
        if missing_fields:
            estimates = {
                "input_tokens": estimate_message_tokens(messages),
                "output_tokens": estimate_content_tokens(response_content),
            }
            for field in missing_fields:
                normalized[field] = estimates[field]
            if "total_tokens" not in normalized:
                normalized["total_tokens"] = int(normalized.get("input_tokens") or 0) + int(normalized.get("output_tokens") or 0)
            return normalized, {
                "status": "mixed",
                "method": USAGE_ESTIMATION_METHOD,
                "estimated": True,
                "estimated_fields": sorted(missing_fields),
                "accuracy": "partial_provider_usage_with_approximate_fill",
            }
        return normalized, {
            "status": "reported",
            "method": "provider_usage",
            "estimated": False,
        }

    input_tokens = estimate_message_tokens(messages)
    output_tokens = estimate_content_tokens(response_content)
    if input_tokens or output_tokens or response_content is not None:
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }, {
            "status": "estimated",
            "method": USAGE_ESTIMATION_METHOD,
            "estimated": True,
            "accuracy": "approximate_not_provider_billing_truth",
        }
    return {}, {
        "status": "unavailable",
        "method": None,
        "estimated": False,
    }


def normalize_provider_usage(
    usage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize common OpenAI-compatible and local-provider usage aliases."""

    if not isinstance(usage, Mapping):
        return {}
    input_tokens = _first_non_negative_int(usage, _INPUT_TOKEN_KEYS)
    output_tokens = _first_non_negative_int(usage, _OUTPUT_TOKEN_KEYS)
    total_tokens = _first_non_negative_int(usage, _TOTAL_TOKEN_KEYS)
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if output_tokens is None and total_tokens is not None and input_tokens is not None:
        difference = total_tokens - input_tokens
        if difference >= 0:
            output_tokens = difference
    if input_tokens is None and total_tokens is not None and output_tokens is not None:
        difference = total_tokens - output_tokens
        if difference >= 0:
            input_tokens = difference
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return {}

    normalized: dict[str, Any] = {}
    if input_tokens is not None:
        normalized["input_tokens"] = input_tokens
    if output_tokens is not None:
        normalized["output_tokens"] = output_tokens
    if total_tokens is not None:
        normalized["total_tokens"] = total_tokens
    for key in _DETAIL_KEYS:
        detail = usage.get(key)
        if isinstance(detail, Mapping):
            normalized[key] = dict(detail)
    return normalized


def estimate_message_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    """Estimate a chat request with deterministic per-message framing overhead."""

    if not messages:
        return 0
    total = 3
    for message in messages:
        total += 3
        total += estimate_content_tokens(message.get("role", ""))
        total += estimate_content_tokens(message.get("content", ""))
        if message.get("name"):
            total += estimate_content_tokens(message["name"])
    return total


def estimate_content_tokens(content: Any) -> int:
    """Estimate visible text tokens without requiring a model-specific tokenizer."""

    text = _visible_text(content)
    if not text:
        return 0
    wide_character_count = 0
    narrow_utf8_bytes = 0
    for character in text:
        if _is_wide_character(character):
            wide_character_count += 1
        else:
            narrow_utf8_bytes += len(character.encode("utf-8"))
    return wide_character_count + math.ceil(narrow_utf8_bytes / 4)


def usage_measurement_summary(
    response_metadata: Sequence[Mapping[str, Any]],
    *,
    expected_call_count: int,
) -> dict[str, Any]:
    """Summarize reported/estimated/unavailable usage across provider calls."""

    statuses: list[str] = []
    methods: list[str] = []
    for metadata in response_metadata:
        measurement = metadata if "status" in metadata else metadata.get("usage_measurement")
        if isinstance(measurement, Mapping):
            status = str(measurement.get("status") or "unavailable")
            method = measurement.get("method")
        else:
            status = "unavailable"
            method = None
        statuses.append(status)
        if isinstance(method, str) and method:
            methods.append(method)
    missing_count = max(0, expected_call_count - len(statuses)) + sum(status == "unavailable" for status in statuses)
    reported_count = sum(status == "reported" for status in statuses)
    estimated_count = sum(status == "estimated" for status in statuses)
    mixed_count = sum(status == "mixed" for status in statuses)
    available_estimate_count = estimated_count + mixed_count
    if missing_count:
        status = "partial" if reported_count or available_estimate_count else "unavailable"
    elif mixed_count or (reported_count and estimated_count):
        status = "mixed"
    elif estimated_count:
        status = "estimated"
    elif reported_count:
        status = "reported"
    else:
        status = "unavailable"
    return {
        "status": status,
        "provider_reported_call_count": reported_count,
        "estimated_call_count": estimated_count,
        "mixed_call_count": mixed_count,
        "unavailable_call_count": missing_count,
        "is_estimated": available_estimate_count > 0,
        "methods": list(dict.fromkeys(methods)),
    }


def usage_measurement_available(metadata: Mapping[str, Any]) -> bool:
    """Return whether one completed call has reported or estimated token usage."""

    measurement = metadata.get("usage_measurement")
    if not isinstance(measurement, Mapping):
        return False
    return measurement.get("status") in {"reported", "estimated", "mixed"}


def _first_non_negative_int(
    usage: Mapping[str, Any],
    keys: Sequence[str],
) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0 and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _visible_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if content is None:
        return ""
    try:
        return json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return str(content)


def _is_wide_character(character: str) -> bool:
    return unicodedata.east_asian_width(character) in {"W", "F"}


__all__ = [
    "USAGE_ESTIMATION_METHOD",
    "estimate_content_tokens",
    "estimate_message_tokens",
    "normalize_provider_usage",
    "resolve_chat_usage",
    "usage_measurement_summary",
    "usage_measurement_available",
]
