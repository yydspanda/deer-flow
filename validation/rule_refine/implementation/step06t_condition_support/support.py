"""Explain failed condition support against exactly the originally visible logs."""

from collections import Counter


def value_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def compare(condition, value):
    if not isinstance(value, str):
        return False
    if condition.operator == "equal":
        return value == condition.value
    if condition.operator == "contain":
        return condition.value in value
    if condition.operator == "notcontain":
        return condition.value not in value
    return False


def inspect_top_level(condition, logs):
    present = [log[condition.name] for log in logs if condition.name in log]
    return {
        "log_count": len(logs),
        "field_present": len(present),
        "field_missing": len(logs) - len(present),
        "value_types": dict(sorted(Counter(map(value_type, present)).items())),
        "string_values": sum(isinstance(value, str) for value in present),
        "matching_strings": sum(compare(condition, value) for value in present),
    }


def structural_hints(condition, logs):
    """Hints only: no flattening, case normalization, coercion or JSON expansion."""
    nested = []
    case_variant = []
    truncated = False
    visited = 0
    for log in logs:
        case_variant.extend(
            value
            for key, value in log.items()
            if isinstance(key, str)
            and key != condition.name
            and key.casefold() == condition.name.casefold()
        )
        stack = [
            (value, 1) for value in log.values() if isinstance(value, (list, dict))
        ]
        while stack:
            value, depth = stack.pop()
            visited += 1
            if visited > 10000 or depth > 16:
                truncated = True
                continue
            if isinstance(value, dict):
                if condition.name in value:
                    nested.append(value[condition.name])
                children = value.values()
            else:
                children = value
            stack.extend(
                (child, depth + 1)
                for child in children
                if isinstance(child, (dict, list))
            )
    return {
        "nested_exact_name_occurrences": len(nested),
        "nested_matching_strings": sum(compare(condition, value) for value in nested),
        "different_case_top_level_occurrences": len(case_variant),
        "different_case_matching_strings": sum(
            compare(condition, value) for value in case_variant
        ),
        "structural_scan_truncated": truncated,
        "hints_used_to_accept_condition": False,
    }


def explain(context, draft, analysis):
    if draft.status != "proposal" or draft.filter is None:
        return {"status": "not_a_proposal", "conditions": []}
    samples = {sample["alert_id"]: sample for sample in context["samples"]}
    if not set(draft.sample_alert_ids).issubset(samples):
        raise ValueError("unknown_sample_reference")
    cited = [log for ref in draft.sample_alert_ids for log in samples[ref]["raw_logs"]]
    all_visible = [log for sample in samples.values() for log in sample["raw_logs"]]
    rows = []
    for number, condition in enumerate(
        draft.filter.__pydantic_extra__.values(), start=1
    ):
        own = inspect_top_level(condition, cited)
        all_ = inspect_top_level(condition, all_visible)
        hints = structural_hints(condition, cited)
        supported = analysis._supported(condition, cited)
        if supported != (own["matching_strings"] > 0):
            raise ValueError("condition_diagnostic_disagreement")
        if supported:
            reason = "supported"
        elif all_["matching_strings"]:
            reason = "supported_only_in_uncited_visible_samples"
        elif own["field_present"] == 0:
            reason = "field_missing_at_top_level"
        elif own["string_values"] == 0:
            reason = "field_type_not_supported"
        else:
            reason = "string_value_does_not_match_operator"
        rows.append(
            {
                "condition_number": number,
                "operator": condition.operator,
                "supported": supported,
                "reason": reason,
                "cited_samples": own,
                "all_originally_visible_samples": all_,
                "structural_hints": hints,
            }
        )
    return {
        "status": "checked",
        "scope": "original_model_visible_samples_only",
        "condition_count": len(rows),
        "unsupported_condition_count": sum(not row["supported"] for row in rows),
        "conditions": rows,
        "field_names_and_values_exported": False,
        "new_preview_used_as_old_response_evidence": False,
    }
