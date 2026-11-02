"""Project bounded historical samples without claiming a live volume signal."""

import copy
import hashlib
import json
from collections import Counter
from numbers import Integral

SAMPLE = "full_alert_2026_month_forth_sample_200.pkl"
SAMPLE_SHA = "7dcf39081920a8531f7458149f1c98c9d256e297c62c10ee9d385c39a091f2cf"
CSV_SHA = "729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976"
TARGET = ("ptp-nids", "RPAADM_002627")
CONTEXT_LIMIT = 60 * 1024


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("non_json_number")


def _data(value, column_identity, inventory):
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("row_over_budget")
        value = json.loads(value, object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("row_not_object")
    inventory.bounded_json_shape(value)
    wrapped = "alert_data" in value
    data = value.get("alert_data") if wrapped else value
    if not isinstance(data, dict) or not isinstance(data.get("alert"), dict):
        raise ValueError("alert_not_object")
    alert = data["alert"]
    identity = inventory.text_id(alert.get("alertId"))
    if identity is None:
        raise ValueError("alert_identity_invalid")
    if wrapped:
        if inventory.text_id(value.get("alert_id")) != identity:
            raise ValueError("request_identity_mismatch")
        if value.get("app_code") != "zeus" or value.get("flow_id") != "alert_agent":
            raise ValueError("other_workflow")
    if column_identity is not _NO_COLUMN:
        if isinstance(column_identity, Integral) and not isinstance(
            column_identity, bool
        ):
            column_identity = int(column_identity)
        if inventory.text_id(column_identity) != identity:
            raise ValueError("column_identity_mismatch")
    raw = encode(data)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("row_over_budget")
    return identity, alert, hashlib.sha256(raw).hexdigest()


_NO_COLUMN = object()


def build_context(frame, rules, inventory):
    """Caller must verify PKL/CSV bytes against SAMPLE_SHA/CSV_SHA before loading.

    Pure projection: no file/service access, no former model answers, no fabricated
    task identifiers, arrival timestamps, burst signal or hourly alert rate.
    """
    if not frame.columns.is_unique or "alert_full_data" not in frame.columns:
        raise ValueError("unsupported_columns")
    rule = rules.get_rule(TARGET[1], topic=TARGET[0])
    if rule.get("topic") != TARGET[0] or rule.get("rule_code") != TARGET[1]:
        raise ValueError("rule_identity_mismatch")
    rule_view = {
        key: copy.deepcopy(rule[key])
        for key in (
            "company_code",
            "topic",
            "rule_code",
            "rule_name",
            "rule_config_version",
            "source",
            "configuration",
        )
    }
    inventory.bounded_json_shape(rule_view)
    if len(encode(rule_view)) > 32 * 1024:
        raise ValueError("rule_configuration_over_budget")
    groups, variants, issues = {}, {}, Counter()
    duplicate_target_rows = 0
    for position, value in enumerate(frame["alert_full_data"].iloc[:200]):
        try:
            column = (
                frame["alert_id"].iloc[position]
                if "alert_id" in frame.columns
                else _NO_COLUMN
            )
            identity, alert, digest = _data(value, column, inventory)
            variants.setdefault(identity, set()).add(digest)
            hits = alert.get("hitLog")
            if not isinstance(hits, list) or len(hits) > 1000:
                issues["invalid_or_excessive_hits"] += 1
                continue
            matching = [
                hit
                for hit in hits
                if isinstance(hit, dict)
                and (hit.get("topic"), hit.get("ruleCode")) == TARGET
            ]
            if not matching:
                continue
            if identity in groups:
                duplicate_target_rows += 1
                continue
            logs, total = [], 0
            for hit in matching:
                values = hit.get("zeusRawLogs")
                if not isinstance(values, list):
                    issues["raw_logs_not_list"] += 1
                    continue
                total += len(values)
                logs.extend(values[: max(0, 100 - len(logs))])
            groups[identity] = {"raw_logs": logs, "available_logs": total}
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            issues["row_not_eligible"] += 1
    conflicted = {
        identity for identity, fingerprints in variants.items() if len(fingerprints) > 1
    }
    available_logs, uninspected_logs, invalid_logs = 0, 0, 0
    candidates = []
    for identity, entry in groups.items():
        if identity in conflicted:
            continue
        available_logs += entry["available_logs"]
        uninspected_logs += entry["available_logs"] - len(entry["raw_logs"])
        bounded_logs = []
        for log in entry["raw_logs"]:
            try:
                if not isinstance(log, dict) or not log or len(encode(log)) > 8 * 1024:
                    raise ValueError("invalid_or_oversized_log")
                bounded_logs.append(log)
            except (ValueError, TypeError, RecursionError):
                invalid_logs += 1
        if bounded_logs:
            candidates.append((identity, bounded_logs))
    coverage = {
        "file_rows": len(frame),
        "inspected_rows": min(len(frame), 200),
        "matching_unique_alerts": len(groups),
        "eligible_unique_alerts": len(candidates),
        "conflicting_identity_count": len(conflicted),
        "duplicate_target_rows": duplicate_target_rows,
        "available_logs_in_nonconflicting_alerts": available_logs,
        "logs_not_inspected_due_to_limit": uninspected_logs,
        "invalid_or_oversized_logs": invalid_logs,
        "included_alerts": 0,
        "included_logs": 0,
        "omitted_alerts": len(candidates),
        "omitted_logs": available_logs,
        "issue_counts": dict(issues),
        "selection_basis": "source_order_first_at_most_20_log_eligible_alerts_not_model_outcomes",
        "complete_source_coverage": False,
    }
    context = {
        "schema_version": "sec_know.historical_rule_context.v1",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "source": {
            "file_name": SAMPLE,
            "file_sha256": SAMPLE_SHA,
            "reference_csv_sha256": CSV_SHA,
        },
        "rule": rule_view,
        "samples": [],
        "coverage": coverage,
        "historical_model_answers_included": False,
        "sample_match_is_not_false_positive_or_live_reduction_rate": True,
    }
    # Reserve space for final numeric coverage updates; whole logs are omitted,
    # never shortened into different field values or misleading negative matches.
    for identity, logs in candidates:
        if len(context["samples"]) == 20:
            break
        sample = {"alert_id": identity, "raw_logs": []}
        context["samples"].append(sample)
        for log in logs:
            if len(sample["raw_logs"]) == 3:
                break
            sample["raw_logs"].append(copy.deepcopy(log))
            if len(encode(context)) > CONTEXT_LIMIT - 1024:
                sample["raw_logs"].pop()
        if not sample["raw_logs"]:
            context["samples"].pop()
    coverage["included_alerts"] = len(context["samples"])
    coverage["included_logs"] = sum(
        len(sample["raw_logs"]) for sample in context["samples"]
    )
    coverage["omitted_alerts"] = len(candidates) - coverage["included_alerts"]
    coverage["omitted_logs"] = available_logs - coverage["included_logs"]
    if len(encode(context)) > CONTEXT_LIMIT:
        raise ValueError("historical_context_over_budget")
    return context
