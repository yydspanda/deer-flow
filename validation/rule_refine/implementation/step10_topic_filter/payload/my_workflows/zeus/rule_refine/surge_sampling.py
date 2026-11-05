"""Time-distributed originals and optional AI-directed examples, without filter replay."""

from collections import deque

from . import surge_ai_review as ai_review

REVIEW_SAMPLING_POLICY = "whole-alert-review-new-and-cited-interleaved-v1"


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
    request_builder=None,
):
    request_builder = request_builder or ai_review.build_request
    if draft is not None:
        return _select_review(
            case, limits, operator_context, draft, generation, request_builder
        )
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
        return request_builder(
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
                request_builder=request_builder,
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


def _select_review(case, limits, operator_context, draft, generation, request_builder):
    """Interleave unseen originals and cited originals within the existing budget.

    A draft's reference list stays intact. References are pointers, not a demand
    to repeat every original. The request names those not displayed this time.
    """
    originals = case["current_samples"]
    history = list(case["history_samples"])
    earlier = {row["alert_id"] for row in (generation or {}).get("current_samples", [])}
    references = set(draft.get("sample_alert_ids", []))
    unseen = [row for row in originals if row["alert_id"] not in earlier]
    cited = [
        row
        for row in originals
        if row["alert_id"] in references and row["alert_id"] in earlier
    ]
    preference = requested_examples(originals, draft.get("sample_request"), earlier)
    new_order = list(
        {row["alert_id"]: row for row in preference + list(spread(unseen))}.values()
    )
    cited_order = list(spread(cited))

    def build(rows):
        ids = {row["alert_id"] for row in rows}
        fresh_count = len(ids - earlier)
        coverage = {
            **case["coverage"],
            "phase": "review",
            "sampling_policy_version": REVIEW_SAMPLING_POLICY,
            "current_model_samples": len(rows),
            "history_model_samples": len(history),
            "history_reference_target": 20,
            "current_sample_basis": "unseen_and_cited_whole_alerts_interleaved",
            "history_sample_basis": "latest_distinct_alerts_strictly_before_current_cutoff",
            "new_current_available": len(unseen),
            "new_current_samples": fresh_count,
            "new_current_unavailable_reason": (
                None
                if fresh_count
                else "all_current_already_displayed"
                if not unseen
                else "no_complete_new_alert_fits_with_draft_and_retained_history"
            ),
            "draft_current_references": len(cited),
            "draft_current_references_not_displayed": len(
                {row["alert_id"] for row in cited} - ids
            ),
            "filter_replay_performed": False,
            "filter_coverage_measured": False,
        }
        return request_builder(
            case["rule"],
            rows,
            history,
            coverage,
            draft=draft,
            phase="review",
            operator_context=operator_context,
            limits=limits,
        )

    def remove_oldest_uncited_history():
        for index in range(len(history) - 1, -1, -1):
            if history[index]["alert_id"] not in references:
                history.pop(index)
                return True
        return False

    while True:
        try:
            request = build([])
            break
        except ValueError as error:
            if not is_budget_error(error) or not remove_oldest_uncited_history():
                raise
    while True:
        current, budget_omitted = [], set()

        def add(sample):
            nonlocal request
            try:
                candidate = build(current + [sample])
            except ValueError as error:
                if not is_budget_error(error):
                    raise
                budget_omitted.add(sample["alert_id"])
                return False
            current.append(sample)
            request = candidate
            return True

        # Establish one fitting new original before any old reference can fill
        # the budget. A too-large first candidate must not hide later smaller
        # new alerts. Failed whole alerts are never truncated.
        remaining_new = []
        for index, sample in enumerate(new_order):
            if add(sample):
                remaining_new = new_order[index + 1 :]
                break
        # No per-side quota or fixed sample count. Alternate cited and new
        # originals, then give the nonempty side any remaining room.
        for index in range(max(len(cited_order), len(remaining_new))):
            if index < len(cited_order):
                add(cited_order[index])
            if index < len(remaining_new):
                add(remaining_new[index])
        # A no-change draft may cite nothing, or only history. Same-material
        # review is still valid when there is no unseen original that fits.
        if not current:
            for sample in spread(originals):
                add(sample)
        if current:
            break
        if not remove_oldest_uncited_history():
            raise ValueError("no_current_alert_fits_context_budget")
        request = build([])
    # Metadata is derived from these exact rows, so the request checked above is
    # also the final request; no later additions can exceed the checked budget.
    selected_ids = {row["alert_id"] for row in current}
    new_count = len(selected_ids - earlier)
    return {
        "request": request,
        "current_samples": current,
        "history_samples": history,
        "selection": {
            "phase": "review",
            "sampling_policy_version": REVIEW_SAMPLING_POLICY,
            "current_count": len(current),
            "history_count": len(history),
            "history_available_count": len(case["history_samples"]),
            "new_current_available": len(unseen),
            "new_current_count": new_count,
            "cited_current_reused": len(selected_ids & references & earlier),
            "uncited_current_reused": len((selected_ids & earlier) - references),
            "draft_current_references_not_displayed": len(
                {row["alert_id"] for row in cited} - selected_ids
            ),
            "new_current_unavailable_reason": request["context"]["coverage"][
                "new_current_unavailable_reason"
            ],
            "budget_omitted_current": len(budget_omitted - selected_ids),
            "ai_sample_request_used": bool(draft.get("sample_request")),
            "filter_replay_performed": False,
        },
    }
