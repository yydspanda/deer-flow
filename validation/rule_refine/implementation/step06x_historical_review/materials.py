"""Select complete historical logs using the installed 6W request budget."""

import copy
import hashlib
import json
from collections import Counter
from numbers import Integral

import historical_review

SAMPLE = "full_alert_2026_month_forth_sample_200.pkl"
SAMPLE_SHA = "7dcf39081920a8531f7458149f1c98c9d256e297c62c10ee9d385c39a091f2cf"
CSV_SHA = "729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976"
TARGET = ("ptp-nids", "RPAADM_002627")
ROW_BYTES = 2 * 1024 * 1024
_NO_COLUMN = object()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("non_json_number")


def _data(value, column_identity, inventory, analysis):
    if isinstance(value, str):
        if len(value.encode("utf-8")) > ROW_BYTES:
            raise ValueError("row_over_budget")
        value = json.loads(value, object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("row_not_object")
    # This installed encoder enforces finite JSON, bytes and depth, without the
    # earlier inventory helper's separate node count and estimated-size rules.
    analysis._encode(value, ROW_BYTES)
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
    return (
        identity,
        alert,
        hashlib.sha256(analysis._encode(data, ROW_BYTES)).hexdigest(),
    )


def _refresh(context):
    coverage = context["coverage"]
    coverage["included_alerts"] = len(context["samples"])
    coverage["included_logs"] = sum(
        len(item["raw_logs"]) for item in context["samples"]
    )
    coverage["omitted_alerts"] = (
        coverage["eligible_unique_alerts"] - coverage["included_alerts"]
    )
    coverage["omitted_logs"] = (
        coverage["available_logs_in_nonconflicting_alerts"] - coverage["included_logs"]
    )


def _fits(context, analysis, limits):
    _refresh(context)
    try:
        historical_review.build_request(
            context, analysis, limits=limits, require_samples=False
        )
        return True
    except ValueError as error:
        if str(error) != "historical_request_over_budget":
            raise
        return False


def build_context(frame, rules, inventory, *, analysis, limits):
    """Historical-only projection; caller verifies source bytes before loading.

    Whole logs are selected in source order. No former model answer, live arrival
    timestamp, task row or burst signal is manufactured for this manual review.
    """
    if not frame.columns.is_unique or "alert_full_data" not in frame.columns:
        raise ValueError("unsupported_columns")
    rule = rules.get_rule(TARGET[1], topic=TARGET[0])
    analysis.validate_rule_match(
        rule,
        rule_code=TARGET[1],
        topic=TARGET[0],
        company_code=rule.get("company_code"),
    )
    identity_fields = {
        key: rule[key]
        for key in ("company_code", "rule_code", "rule_name", "rule_config_version")
    }
    if (
        analysis.RuleIdentity.model_validate(identity_fields).model_dump(mode="json")
        != identity_fields
    ):
        raise ValueError("invalid_rule_identity")
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
    analysis._encode(rule_view, analysis.MAX_MATERIALS_BYTES)
    groups, variants, issues = {}, {}, Counter()
    duplicate_target_rows = 0
    for position, value in enumerate(frame["alert_full_data"].iloc[:200]):
        try:
            column = (
                frame["alert_id"].iloc[position]
                if "alert_id" in frame.columns
                else _NO_COLUMN
            )
            identity, alert, digest = _data(value, column, inventory, analysis)
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
    available_logs = uninspected_logs = invalid_logs = 0
    candidates = []
    for identity, entry in groups.items():
        if identity in conflicted:
            continue
        available_logs += entry["available_logs"]
        uninspected_logs += entry["available_logs"] - len(entry["raw_logs"])
        valid_logs = []
        for log in entry["raw_logs"]:
            try:
                if not isinstance(log, dict) or not log:
                    raise ValueError("invalid_log")
                analysis._encode(log, ROW_BYTES)
                valid_logs.append(log)
            except (ValueError, TypeError, OverflowError, RecursionError):
                invalid_logs += 1
        if valid_logs:
            candidates.append((identity, valid_logs))
    coverage = {
        "file_rows": len(frame),
        "inspected_rows": min(len(frame), 200),
        "matching_unique_alerts": len(groups),
        "eligible_unique_alerts": len(candidates),
        "eligible_logs": sum(len(logs) for _, logs in candidates),
        "conflicting_identity_count": len(conflicted),
        "excluded_conflicting_target_alerts": len(set(groups) & conflicted),
        "duplicate_target_rows": duplicate_target_rows,
        "available_logs_in_nonconflicting_alerts": available_logs,
        "logs_not_inspected_due_to_limit": uninspected_logs,
        "invalid_logs": invalid_logs,
        "included_alerts": 0,
        "included_logs": 0,
        "omitted_alerts": len(candidates),
        "omitted_logs": available_logs,
        "budget_omitted_logs": 0,
        "budget_omitted_alerts": 0,
        "selection_omitted_logs": uninspected_logs,
        "selection_omitted_alerts": 0,
        "issue_counts": dict(issues),
        "selection_basis": "source_order_first_at_most_20_log_eligible_alerts_not_model_outcomes",
        "complete_source_coverage": False,
    }
    context = {
        "schema_version": "sec_know.historical_rule_context.v3",
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
    if not _fits(context, analysis, limits):
        raise ValueError("historical_fixed_content_over_budget")
    budget_alerts = set()
    for identity, logs in candidates:
        if len(context["samples"]) == 20:
            coverage["selection_omitted_alerts"] += 1
            coverage["selection_omitted_logs"] += len(logs)
            continue
        sample = {"alert_id": identity, "raw_logs": []}
        context["samples"].append(sample)
        for log in logs:
            if len(sample["raw_logs"]) == 3:
                coverage["selection_omitted_logs"] += 1
                continue
            sample["raw_logs"].append(copy.deepcopy(log))
            if not _fits(context, analysis, limits):
                sample["raw_logs"].pop()
                coverage["budget_omitted_logs"] += 1
        if not sample["raw_logs"]:
            context["samples"].pop()
            budget_alerts.add(identity)
            coverage["budget_omitted_alerts"] = len(budget_alerts)
    # Omission metadata itself may grow after an earlier candidate fitted exactly.
    while not _fits(context, analysis, limits):
        if not context["samples"]:
            raise ValueError("historical_fixed_content_over_budget")
        sample = context["samples"][-1]
        sample["raw_logs"].pop()
        coverage["budget_omitted_logs"] += 1
        if not sample["raw_logs"]:
            context["samples"].pop()
            budget_alerts.add(sample["alert_id"])
            coverage["budget_omitted_alerts"] = len(budget_alerts)
    analysis._encode(context, analysis.MAX_MATERIALS_BYTES)
    return context
