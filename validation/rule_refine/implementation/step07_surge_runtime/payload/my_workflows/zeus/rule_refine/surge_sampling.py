"""Time-distributed originals and optional AI-directed examples, without filter replay."""

from collections import deque

from . import surge_ai_review as ai_review


def spread(items):
    """Progressively cover the whole ordered range instead of taking a prefix."""
    queue = deque([(0, len(items))])
    while queue:
        left, right = queue.popleft()
        if left >= right:
            continue
        middle = (left + right) // 2
        yield items[middle]
        queue.extend(((left, middle), (middle + 1, right)))


def requested_examples(samples, request, excluded):
    """Find logs with/without the text; retain at most two distinct complete alerts."""
    if not request:
        return []
    field, text = request["field"], request["text"]
    found = {}
    for sample in samples:
        if sample["alert_id"] in excluded:
            continue
        values = [field_value(log, field) for log in sample["raw_logs"]]
        strings = [value for value in values if isinstance(value, str)]
        if not strings:
            continue
        for value in strings:
            found.setdefault(text in value, sample)
        if len(found) == 2:
            break
    # One mixed alert can supply both examples; never duplicate that alert.
    return list({sample["alert_id"]: sample for sample in found.values()}.values())


def field_value(log, field):
    if field in log:
        return log[field]
    value = log
    for key in field.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def select(
    case,
    limits,
    *,
    operator_context="",
    draft=None,
    generation=None,
    independent_initial=False,
):
    phase = "review" if draft is not None else "generate"
    history = list(case["history_samples"])
    originals = case["current_samples"]
    earlier = {s["alert_id"] for s in (generation or {}).get("current_samples", [])}
    references = set((draft or {}).get("sample_alert_ids", []))
    required = [s for s in originals if s["alert_id"] in references]
    current = list(required)
    selected = {s["alert_id"] for s in current}
    # The two phases start with separate, interleaved portions of the timeline.
    candidates = originals[1::2] if draft is not None else originals[::2]
    if not candidates:
        candidates = originals
    preference = requested_examples(
        originals, (draft or {}).get("sample_request"), earlier
    )
    candidates = preference + list(spread(candidates))
    if draft is not None:
        candidates += list(spread(originals))
    budget_omitted = set()
    attempted = set()

    def build(rows):
        coverage = {
            **case["coverage"],
            "phase": phase,
            "current_model_samples": len(rows),
            "history_model_samples": len(history),
            "history_reference_target": 20,
            "current_sample_basis": "time_distributed_whole_alerts_with_optional_ai_request",
            "history_sample_basis": "latest_distinct_alerts_strictly_before_current_cutoff",
            "new_current_samples": sum(s["alert_id"] not in earlier for s in rows),
            "filter_replay_performed": False,
            "filter_coverage_measured": False,
        }
        return ai_review.build_request(
            case["rule"],
            rows,
            history,
            coverage,
            draft=draft,
            phase=phase,
            operator_context=operator_context,
            limits=limits,
            independent=independent_initial and phase == "generate",
        )

    # If unusually large historical originals alone fill the context, retain the
    # latest ones. Record the reduced model-visible count; never truncate a log.
    while True:
        try:
            request = build(current)
            break
        except ValueError as error:
            removable = [
                i
                for i, item in enumerate(history)
                if item["alert_id"] not in references
            ]
            if not is_budget_error(error) or not removable:
                raise
            history.pop(removable[-1])
    for sample in candidates:
        identity = sample["alert_id"]
        if (
            identity in attempted
            or identity in selected
            or (
                draft is not None and identity in earlier and identity not in references
            )
        ):
            continue
        attempted.add(identity)
        # A compact sample is a lower bound on its addition to the request. Avoid
        # repeatedly serializing a full context when a whole alert cannot fit.
        minimal_bytes = len(
            ai_review.encoded({"alert_id": identity, "raw_logs": sample["raw_logs"]})
        )
        if (
            minimal_bytes
            > limits.input_token_budget - request["estimated_input_tokens"] + 32
        ):
            budget_omitted.add(identity)
            continue
        try:
            request = build(current + [sample])
        except ValueError as error:
            if not is_budget_error(error):
                raise
            budget_omitted.add(identity)
            continue
        current.append(sample)
        selected.add(identity)
    if not current:
        removable = [
            i for i, item in enumerate(history) if item["alert_id"] not in references
        ]
        if removable:
            history.pop(removable[-1])
            smaller = {**case, "history_samples": history}
            result = select(
                smaller,
                limits,
                operator_context=operator_context,
                draft=draft,
                generation=generation,
                independent_initial=independent_initial,
            )
            result["selection"]["history_available_count"] = len(
                case["history_samples"]
            )
            return result
        raise ValueError("no_current_alert_fits_context_budget")
    request = build(current)
    return {
        "request": request,
        "current_samples": current,
        "history_samples": history,
        "selection": {
            "phase": phase,
            "current_count": len(current),
            "history_count": len(history),
            "history_available_count": len(case["history_samples"]),
            "new_current_count": sum(s["alert_id"] not in earlier for s in current),
            "cited_current_reused": len(required),
            "budget_omitted_current": len(budget_omitted),
            "ai_sample_request_used": bool((draft or {}).get("sample_request")),
            "filter_replay_performed": False,
        },
    }


def is_budget_error(error):
    return getattr(error, "code", str(error)) in {
        "input_budget_exceeded",
        "input_token_budget_exceeded",
        "request_too_large",
        "input_too_large",
        "request_structure_over_budget",
    }
