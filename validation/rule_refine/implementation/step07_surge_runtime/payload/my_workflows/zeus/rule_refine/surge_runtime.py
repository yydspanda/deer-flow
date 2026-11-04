"""Connect the two-phase workflow to existing model capacity and local materials."""

import hashlib
import json
import time

from .surge_budget import ContextBudget
from .surge_history import SurgeTaskSource
from .surge_processor import SurgeProcessor


def budget_from_settings(settings):
    return ContextBudget(
        **{
            key: settings[key]
            for key in ("context_tokens", "input_fraction", "timeout_seconds")
        }
    )


def call_model(client, request, *, model, budget):
    """One SDK invocation; preserve text even on a non-successful finish reason."""
    transport = {
        "schema_version": "sec_know.rule_model_transport.v1",
        "transport_mode": "live",
        "requested_model": model,
        "returned_model": None,
        "sdk_max_retries": 0,
        "sdk_request_calls": 0,
        "response_received": False,
        "upstream_attempts": None,
        "timeout_seconds": budget.timeout_seconds,
        "requested_response_format": {"type": "json_object"},
        "response_format_enforcement_verified": False,
        "model_output_token_limit": None,
        "finish_reason": None,
        "usage": None,
        "error_code": None,
    }
    content = None
    started = time.monotonic()
    try:
        messages = request["messages"]
        raw = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(raw) > budget.input_bytes:
            raise ValueError("request_structure_over_budget")
        estimated = 256 + sum(len(m["content"].encode("utf-8")) + 64 for m in messages)
        if estimated > budget.input_token_budget:
            raise ValueError("input_budget_exceeded")
        options = {
            "model": model,
            "messages": messages,
            "stream": False,
            "response_format": {"type": "json_object"},
            "extra_body": {"timeout": budget.timeout_seconds, "need_trans": True},
        }
        transport["request_config_sha256"] = hashlib.sha256(
            json.dumps(
                {k: v for k, v in options.items() if k != "messages"}, sort_keys=True
            ).encode()
        ).hexdigest()
        endpoint = str(getattr(client, "base_url", ""))
        transport["endpoint_sha256"] = hashlib.sha256(endpoint.encode()).hexdigest()
        sdk = client.with_options(timeout=budget.timeout_seconds, max_retries=0)
        transport["sdk_request_calls"] = 1
        reply = sdk.chat.completions.create(**options)
        transport["response_received"] = True
        transport["returned_model"] = getattr(reply, "model", None)
        usage = getattr(reply, "usage", None)
        transport["usage"] = {
            key: getattr(usage, key, None)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        result_code = getattr(reply, "resultCode", None)
        if result_code not in (None, "0", 0):
            transport["provider_result_code"] = str(result_code)
            transport["error_code"] = "provider_result_failed"
        choices = getattr(reply, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("invalid_response_choices")
        choice = choices[0]
        transport["finish_reason"] = getattr(choice, "finish_reason", None)
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty_model_content")
        if len(content.encode("utf-8")) > budget.output_bytes:
            content = None
            raise ValueError("response_structure_over_budget")
        if (
            getattr(message, "role", None) != "assistant"
            or getattr(choice, "index", None) != 0
        ):
            raise ValueError("invalid_response_message")
        if getattr(message, "tool_calls", None) or getattr(message, "refusal", None):
            raise ValueError("unexpected_response_kind")
        if transport["finish_reason"] != "stop":
            transport["error_code"] = "incomplete_model_response"
    except Exception as error:
        transport["error_type"] = type(error).__name__
        # No provider body/headers in logs: raw inputs and SDK secrets stay private.
        transport["error_code"] = (
            "invalid_response" if transport["response_received"] else "request_failed"
        )
        status = getattr(error, "status_code", None)
        if type(status) is int:
            transport["http_status"] = status
    transport["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    return {"content": content, "transport": transport}


def load_window_case(source, counter, redis_client, signal):
    """Expand the frozen receipt window to all retained distinct alert identities."""
    observation = {
        key: signal[key]
        for key in ("mode", "source_app", "company_code", "topic", "rule_code")
    }
    observation["company_scope_source"] = "rule_config_unique_match"
    key = counter._keys(observation)[0]
    ids = redis_client.zrangebyscore(
        key, "(" + str(signal["window_start_ms"]), signal["window_end_ms"]
    )
    ids = [item.decode("utf-8") if isinstance(item, bytes) else item for item in ids]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(
        set(ids)
    ):
        raise ValueError("invalid_current_window_identities")
    anchors = signal["sample_alert_ids"]
    if not set(anchors).issubset(ids) or len(ids) < signal["current_count"]:
        # Retention expiry must not turn a partial window into alleged full coverage.
        raise ValueError("current_receipt_window_no_longer_complete")
    case = source.load_case({**signal, "current_alert_ids": ids})
    case["coverage"].update(
        current_receipt_ids=len(ids),
        retained_receipt_window_read_complete=True,
        frozen_signal_membership_verified=False,
        additional_same_window_ids=max(0, len(ids) - signal["current_count"]),
        current_anchor_basis="all_retained_redis_receipt_window_identities",
    )
    return case


def build_processor(
    directory,
    *,
    database,
    rules,
    load_rules,
    counter,
    redis_client,
    generate,
    model,
    settings,
):
    source = SurgeTaskSource(database, rules)
    return SurgeProcessor(
        directory,
        load_case=lambda signal: load_window_case(
            source, counter, redis_client, signal
        ),
        load_rules=load_rules,
        generate=generate,
        model=model,
        budget=budget_from_settings(settings),
        operator_context=settings["operator_context"],
    )
