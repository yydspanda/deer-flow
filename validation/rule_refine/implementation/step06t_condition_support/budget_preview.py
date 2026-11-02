"""Preview whole historical logs against a configurable serialized-input budget."""

import hashlib
import json
from collections import Counter


ORIGINAL_SOURCE_SHA256 = (
    "e8c17d7048244a2fb88dff667b6b5700e64fd4ab81c956348ea5e03078645e99"
)
POLICY_VERSION = "historical-whole-log-request-budget-v1"
PROMPT_VERSION = "rule-refine-historical-full-input-v1"


def _encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _input_limit(max_input_kib):
    if type(max_input_kib) is not int or not 64 <= max_input_kib <= 512:
        raise ValueError("max_input_kib_must_be_integer_64_to_512")
    return max_input_kib * 1024


def _reviewed_sources(source, limit):
    if isinstance(source, str):
        source = source.encode("utf-8")
    if not isinstance(source, bytes) or len(source) > 64 * 1024:
        raise ValueError("reviewed_projection_source_required")
    if hashlib.sha256(source).hexdigest() != ORIGINAL_SOURCE_SHA256:
        raise ValueError("projection_source_sha256_mismatch")
    original = source.decode("utf-8")
    changed = original
    replacements = (
        ("CONTEXT_LIMIT = 60 * 1024", f"CONTEXT_LIMIT = {limit}"),
        (" or len(encode(log)) > 8 * 1024", ""),
        ('"invalid_or_oversized_log"', '"invalid_log"'),
        ('"invalid_or_oversized_logs"', '"invalid_logs"'),
        (
            '"sec_know.historical_rule_context.v1"',
            '"sec_know.historical_rule_context.v2"',
        ),
        (
            '                sample["raw_logs"].pop()',
            '                sample["raw_logs"].pop()\n                _record_budget_omission("log")',
        ),
        (
            '            context["samples"].pop()',
            '            context["samples"].pop()\n            _record_budget_omission("alert")',
        ),
    )
    for before, after in replacements:
        if changed.count(before) != 1:
            raise ValueError("projection_change_location_mismatch")
        changed = changed.replace(before, after, 1)
    return original, changed


def _projection(source, omissions):
    # Only the fixed SHA-verified source or its checked replacements reach exec.
    # The callback records numeric omissions, never original values or IDs.
    namespace = {
        "__name__": "_isolated_historical_budget_preview",
        "_record_budget_omission": lambda kind: omissions.update([kind]),
    }
    exec(compile(source, "<reviewed-historical-projection>", "exec"), namespace)
    return namespace


def _request(context, analysis, review):
    if (
        context.get("material_source") != "historical_export"
        or context.get("observed_surge") is not False
        or context.get("rate_window") is not None
    ):
        raise ValueError("historical_context_required")
    if analysis.SYSTEM.count(review.SAMPLE_SENTENCE) != 1:
        raise ValueError("reviewed_prompt_changed")
    messages = [
        {
            "role": "system",
            "content": analysis.SYSTEM.replace(
                review.SAMPLE_SENTENCE, review.HISTORICAL_SENTENCE
            ),
        },
        {"role": "user", "content": _encode(context).decode("utf-8")},
    ]
    raw = _encode(messages)
    return {
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(raw).hexdigest(),
        "messages": messages,
        "input_bytes": len(raw),
        "input_size_basis": "serialized_messages_utf8_json_bytes",
    }


def build_request(context, analysis, review, max_input_kib=128):
    """Bound complete messages, including system text and JSON escaping.

    This is a local input-text budget, not a model context/token limit or a
    reserved output quota. SDK envelope fields outside messages are excluded.
    """
    limit = _input_limit(max_input_kib)
    if not context.get("samples"):
        raise ValueError("no_usable_samples")
    request = _request(context, analysis, review)
    if request["input_bytes"] > limit:
        raise ValueError("historical_request_over_budget")
    return {**request, "max_input_kib": max_input_kib, "max_input_bytes": limit}


def _update_coverage(context):
    coverage = context["coverage"]
    coverage["included_alerts"] = len(context["samples"])
    coverage["included_logs"] = sum(
        len(sample["raw_logs"]) for sample in context["samples"]
    )
    coverage["omitted_alerts"] = (
        coverage["eligible_unique_alerts"] - coverage["included_alerts"]
    )
    coverage["omitted_logs"] = (
        coverage["available_logs_in_nonconflicting_alerts"] - coverage["included_logs"]
    )


def _statistics(context, input_bytes, invalid_field):
    coverage = context["coverage"]
    return {
        "matching_unique_alerts": coverage["matching_unique_alerts"],
        "eligible_alerts": coverage["eligible_unique_alerts"],
        "eligible_logs": coverage["available_logs_in_nonconflicting_alerts"]
        - coverage["logs_not_inspected_due_to_limit"]
        - coverage[invalid_field],
        "included_alerts": coverage["included_alerts"],
        "included_logs": coverage["included_logs"],
        invalid_field: coverage[invalid_field],
        "omitted_alerts": coverage["omitted_alerts"],
        "omitted_logs": coverage["omitted_logs"],
        "context_bytes": len(_encode(context)),
        "actual_input_bytes": input_bytes,
        "context_sha256": hashlib.sha256(_encode(context)).hexdigest(),
    }


def build_preview(
    frame,
    rules,
    inventory,
    original_materials_source,
    *,
    analysis,
    review,
    max_input_kib=128,
):
    """Return (context, summary), without files, services or model calls.

    Preserve the original parsing/identity rules and first-200/20-alert/3-log
    limits. Drop whole tail logs until the complete serialized messages fit;
    eligible logs do not necessarily fit the request or selection-count caps.
    """
    limit = _input_limit(max_input_kib)
    original, changed = _reviewed_sources(original_materials_source, limit)
    old_module = _projection(original, Counter())
    omissions = Counter()
    new_module = _projection(changed, omissions)
    old_context = old_module["build_context"](frame, rules, inventory)
    new_context = new_module["build_context"](frame, rules, inventory)
    old_request = _request(old_context, analysis, review)
    request = _request(new_context, analysis, review)
    trimmed_logs = trimmed_alerts = 0
    while request["input_bytes"] > limit and new_context["samples"]:
        sample = new_context["samples"][-1]
        sample["raw_logs"].pop()
        trimmed_logs += 1
        if not sample["raw_logs"]:
            new_context["samples"].pop()
            trimmed_alerts += 1
        _update_coverage(new_context)
        request = _request(new_context, analysis, review)
    if request["input_bytes"] > limit:
        raise ValueError("request_fixed_content_over_budget")
    new_statistics = _statistics(new_context, request["input_bytes"], "invalid_logs")
    new_statistics.update(
        budget_omitted_logs=omissions["log"] + trimmed_logs,
        budget_omitted_alerts=omissions["alert"] + trimmed_alerts,
        tail_trimmed_logs=trimmed_logs,
        tail_trimmed_alerts=trimmed_alerts,
    )
    return new_context, {
        "schema_version": "sec_know.historical_budget_preview.v2",
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "material_source": "historical_export",
        "status": "preview_only"
        if new_context["samples"]
        else "no_usable_samples_within_input_budget",
        "model_calls": 0,
        "old_source_sha256": ORIGINAL_SOURCE_SHA256,
        "preview_source_sha256": hashlib.sha256(changed.encode("utf-8")).hexdigest(),
        "max_input_kib": max_input_kib,
        "max_input_bytes": limit,
        "input_size_basis": request["input_size_basis"],
        "input_budget_is_model_limit": False,
        "budgets": {
            "old_eligible_log_max_bytes": 8192,
            "new_eligible_log_max_bytes": None,
            "row_parse_max_bytes": 2 * 1024 * 1024,
            "max_included_alerts": 20,
            "max_included_logs_per_alert": 3,
            "model_output_receiver_max_bytes": 16384,
            "output_limit_is_reserved_quota": False,
        },
        "selection_order_unchanged": True,
        "selection_basis": new_context["coverage"]["selection_basis"],
        "eligibility_is_not_model_coverage": True,
        "full_source_coverage_asserted": False,
        "original_source_modified": False,
        "original_report_modified": False,
        "old": _statistics(
            old_context, old_request["input_bytes"], "invalid_or_oversized_logs"
        ),
        "new": new_statistics,
    }
