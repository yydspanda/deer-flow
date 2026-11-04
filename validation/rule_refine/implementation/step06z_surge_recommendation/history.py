"""Read retained pre-surge references; this helper is not wired into the CSV runner.

The existing counter owns tenant/topic/rule keys and receipt timestamps. The task
source owns decoded task rows; the materials/provider layer must still validate
raw alert identity, scope, conflicts and log availability before model use. A
returned task payload is not a canonical sample or a historical benign label.
"""

import copy
import json
import math

PAGE_SIZE = 20  # LocalWorkflowTaskSource accepts at most 20 references per call.
MAX_SCANNED_REFERENCES = 1000
MAX_TASK_ROWS = 100
MAX_TASK_BYTES = 32 * 1024 * 1024


def _identity(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("invalid_history_alert_identity")
    return value


def load_recent_history(
    counter, observation, before_ms, task_source, limit=20, exclude_ids=()
):
    """Find up to 20 distinct retained references with decoded original payloads.

    Time is strictly before ``before_ms`` and has no one-hour lower bound. Pass
    the same running counter and task source used for current material. Only
    Redis reads and ``task_source.load_task_rows`` are called. Read failures
    propagate; they must not be reported as absent history.

    Paging is bounded and not an atomic snapshot. Expiry or concurrent index
    changes can affect ties; coverage never asserts all historical alerts exist
    in this retained index. Multiple task variants remain for material checking.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("history_limit_must_be_between_1_and_20")
    if type(before_ms) is not int or not 0 <= before_ms <= 2**53 - 1:
        raise ValueError("history_before_ms_must_be_exact_integer_milliseconds")
    if not isinstance(observation, dict) or observation.get("mode") not in {
        "live",
        "replay",
    }:
        raise ValueError("history_requires_live_or_replay_observation")
    if observation.get("namespace", counter.namespace) != counter.namespace:
        raise ValueError("history_counter_namespace_mismatch")
    # Reuse the counter's authoritative namespace/mode/scope validation.
    key = counter._keys(observation)[0]
    if isinstance(exclude_ids, (str, bytes)):
        raise ValueError("history_exclusions_must_be_an_identity_collection")
    excluded = {_identity(value) for value in exclude_ids}
    scope = {
        name: observation[name]
        for name in ("source_app", "company_code", "topic", "rule_code")
    }
    coverage = {
        "selection_basis": "latest_distinct_retained_received_references_before_boundary",
        "before_ms_exclusive": before_ms,
        "mode": counter.mode,
        "namespace": counter.namespace,
        "scope": scope,
        "time_basis": "redis_observed_at"
        if counter.mode == "live"
        else "provided_received_at",
        "requested_alert_count": limit,
        "returned_task_alert_count": 0,
        "scanned_reference_count": 0,
        "duplicate_reference_count": 0,
        "excluded_reference_count": 0,
        "unavailable_reference_count": 0,
        "unexpected_task_row_count": 0,
        "additional_task_row_count": 0,
        "task_read_calls": 0,
        "retention_seconds": counter.settings[4],
        "history_outside_retained_index": "unknown",
        "complete_historical_coverage": False,
        "atomic_snapshot": False,
        "raw_log_scope_verified": False,
        "raw_log_scope_validation": "required_by_materials_or_provider_before_model_use",
        "historical_model_answers_included": False,
        "historical_benign_status": "not_asserted",
        "oldest_selected_received_at_ms": None,
        "newest_selected_received_at_ms": None,
        "scan_reference_limit": MAX_SCANNED_REFERENCES,
        "stop_reason": "scan_reference_limit",
    }
    selected, task_rows, seen = [], [], set()
    upper, cursor_score, tied_offset = f"({before_ms}", None, 0
    returned_bytes = 0
    stop = False
    while coverage["scanned_reference_count"] < MAX_SCANNED_REFERENCES:
        page_size = min(
            PAGE_SIZE, MAX_SCANNED_REFERENCES - coverage["scanned_reference_count"]
        )
        page = counter.client.zrevrangebyscore(
            key, upper, "-inf", start=tied_offset, num=page_size, withscores=True
        )
        if not page:
            coverage["stop_reason"] = "retained_index_exhausted"
            break
        if len(page) > page_size:
            raise ValueError("history_index_page_exceeds_requested_size")
        references = []
        scores = []
        for member, score in page:
            if (
                type(score) not in (int, float)
                or not math.isfinite(score)
                or int(score) != score
                or not 0 <= score < before_ms
            ):
                raise ValueError("invalid_history_receipt_timestamp")
            score = int(score)
            scores.append(score)
            identity = _identity(member)
            coverage["scanned_reference_count"] += 1
            if identity in seen:
                coverage["duplicate_reference_count"] += 1
                continue
            seen.add(identity)
            if identity in excluded:
                coverage["excluded_reference_count"] += 1
                continue
            references.append((identity, score))
        # Continue within a tied timestamp before moving to older timestamps.
        last_score = scores[-1]
        tied_offset = (tied_offset if cursor_score == last_score else 0) + scores.count(
            last_score
        )
        cursor_score = last_score
        upper = last_score
        if references:
            query = {
                "schema_version": "sec_know.rule_burst_signal.v1",
                "mode": counter.mode,
                "namespace": counter.namespace,
                **scope,
                "sample_alert_ids": [identity for identity, _ in references],
            }
            rows = task_source.load_task_rows(query)
            coverage["task_read_calls"] += 1
            if not isinstance(rows, list) or len(rows) > MAX_TASK_ROWS:
                raise ValueError("history_task_source_row_contract_invalid")
            groups = {identity: [] for identity, _ in references}
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError("history_task_row_must_be_an_object")
                if (
                    item.get("alert_id") not in groups
                    or item.get("app_code") != scope["source_app"]
                    or item.get("flow_id") != "alert_agent"
                ):
                    coverage["unexpected_task_row_count"] += 1
                    continue
                groups[item["alert_id"]].append(item)
            for identity, score in references:
                variants = groups[identity]
                if not variants or any(
                    not isinstance(item.get("alert_data"), dict)
                    or not item["alert_data"]
                    for item in variants
                ):
                    coverage["unavailable_reference_count"] += 1
                    continue
                # Preserve source fields and variants without interpreting the
                # vendor payload. Old model answers never enter this material.
                clean = [
                    {
                        name: value
                        for name, value in item.items()
                        if name not in {"agent_response", "agent_response_unavailable"}
                    }
                    for item in variants
                ]
                size = len(
                    json.dumps(clean, ensure_ascii=False, allow_nan=False).encode(
                        "utf-8"
                    )
                )
                if (
                    len(task_rows) + len(clean) > MAX_TASK_ROWS
                    or returned_bytes + size > MAX_TASK_BYTES
                ):
                    coverage["stop_reason"] = "returned_task_material_limit"
                    stop = True
                    break
                selected.append(identity)
                task_rows.extend(copy.deepcopy(clean))
                returned_bytes += size
                coverage["additional_task_row_count"] += len(clean) - 1
                coverage["oldest_selected_received_at_ms"] = score
                if coverage["newest_selected_received_at_ms"] is None:
                    coverage["newest_selected_received_at_ms"] = score
                if len(selected) == limit:
                    coverage["stop_reason"] = "requested_count_reached"
                    stop = True
                    break
        if stop:
            break
        if len(page) < page_size:
            coverage["stop_reason"] = "retained_index_exhausted"
            break
    coverage["returned_task_alert_count"] = len(selected)
    coverage["returned_task_row_count"] = len(task_rows)
    coverage["returned_task_json_bytes"] = returned_bytes
    return {"sample_alert_ids": selected, "task_rows": task_rows, "coverage": coverage}
