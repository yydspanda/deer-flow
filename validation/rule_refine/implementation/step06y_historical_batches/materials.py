"""Plan historical-rule coverage using the agreed context token estimate.

This module does not call models, load files, or contact application services.
The caller verifies the historical file and CSV bytes before loading the frame.
Every eligible log is planned once; request budgets split batches, never logs.
"""

import copy
import hashlib
import json
from collections import Counter

import historical_materials
import historical_review

SAMPLE = historical_materials.SAMPLE
SAMPLE_SHA = historical_materials.SAMPLE_SHA
CSV_SHA = historical_materials.CSV_SHA
TARGET = historical_materials.TARGET
ROW_BYTES = historical_materials.ROW_BYTES


class PlanningError(ValueError):
    """No executable plan is returned when fixed content or one log cannot fit."""

    def __init__(self, code, *, details, coverage):
        super().__init__(code)
        self.code = code
        self.details = copy.deepcopy(details)
        self.coverage = copy.deepcopy(coverage)


def _row_reason(error):
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(error, UnicodeError):
        return "invalid_utf8"
    reason = str(error)
    if reason in {"row_over_budget", "材料超过本步大小上限"}:
        return "row_over_budget"
    if reason in {
        "duplicate_json_field",
        "non_json_number",
        "row_not_object",
        "alert_not_object",
        "alert_identity_invalid",
        "request_identity_mismatch",
        "column_identity_mismatch",
        "other_workflow",
    }:
        return reason
    return "invalid_json_structure"


def _rule_view(rules, analysis):
    rule = rules.get_rule(TARGET[1], topic=TARGET[0])
    analysis.validate_rule_match(
        rule,
        rule_code=TARGET[1],
        topic=TARGET[0],
        company_code=rule.get("company_code"),
    )
    identity = {
        key: rule[key]
        for key in ("company_code", "rule_code", "rule_name", "rule_config_version")
    }
    if (
        analysis.RuleIdentity.model_validate(identity).model_dump(mode="json")
        != identity
    ):
        raise ValueError("invalid_rule_identity")
    result = {
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
    analysis._encode(result, analysis.MAX_MATERIALS_BYTES)
    return result


def _scan(frame, inventory, analysis):
    variants, groups, seen = {}, {}, set()
    row_issues, issues, log_issues = Counter(), Counter(), Counter()
    duplicate_rows = duplicate_target_rows = 0
    for position, value in enumerate(frame["alert_full_data"]):
        column = (
            frame["alert_id"].iloc[position]
            if "alert_id" in frame.columns
            else historical_materials._NO_COLUMN
        )
        try:
            identity, alert, digest = historical_materials._data(
                value, column, inventory, analysis
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
        ) as error:
            row_issues[_row_reason(error)] += 1
            continue
        # Record all valid payload identities before checking rule scope. A later
        # nonmatching variant still invalidates an earlier matching identity.
        variants.setdefault(identity, set()).add(digest)
        hits = alert.get("hitLog")
        if not isinstance(hits, list):
            row_issues["hit_log_not_list"] += 1
            continue
        matching = [
            (hit_index, hit)
            for hit_index, hit in enumerate(hits)
            if isinstance(hit, dict)
            and (hit.get("topic"), hit.get("ruleCode")) == TARGET
        ]
        if (identity, digest) in seen:
            duplicate_rows += 1
            duplicate_target_rows += bool(matching)
            continue
        seen.add((identity, digest))
        issues["invalid_hit_entries"] += sum(not isinstance(hit, dict) for hit in hits)
        if not matching:
            continue
        records = groups.setdefault(identity, [])
        for hit_index, hit in matching:
            logs = hit.get("zeusRawLogs")
            if not isinstance(logs, list):
                issues["raw_logs_not_list"] += 1
                continue
            if not logs:
                issues["empty_raw_logs_list"] += 1
                continue
            for log_index, log in enumerate(logs):
                records.append(
                    (
                        log,
                        {
                            "source_row_position_zero_based": position,
                            "hit_index": hit_index,
                            "raw_log_index": log_index,
                        },
                    )
                )
    conflicted = {identity for identity, values in variants.items() if len(values) > 1}
    candidates, available_logs = [], 0
    eligible_ids = set()
    for identity, records in groups.items():
        if identity in conflicted:
            continue
        available_logs += len(records)
        for log, ref in records:
            if not isinstance(log, dict):
                log_issues["log_not_object"] += 1
                continue
            if not log:
                log_issues["empty_log_object"] += 1
                continue
            try:
                raw = analysis._encode(log, ROW_BYTES)
            except (ValueError, TypeError, OverflowError, RecursionError) as error:
                reason = _row_reason(error)
                log_issues[
                    "log_over_budget"
                    if reason == "row_over_budget"
                    else "invalid_log_json"
                ] += 1
                continue
            eligible_ids.add(identity)
            candidates.append(
                (identity, log, {**ref, "log_sha256": hashlib.sha256(raw).hexdigest()})
            )
    invalid_rows = sum(row_issues.values())
    invalid_logs = sum(log_issues.values())
    issues = +issues  # Omit zero-count entries.
    coverage = {
        "file_rows": len(frame),
        "inspected_rows": len(frame),
        "matching_unique_alerts": len(groups),
        "eligible_unique_alerts": len(eligible_ids),
        "eligible_logs": len(candidates),
        "conflicting_identity_count": len(conflicted),
        "excluded_conflicting_target_alerts": len(set(groups) & conflicted),
        "excluded_conflicting_target_logs": sum(
            len(records)
            for identity, records in groups.items()
            if identity in conflicted
        ),
        "duplicate_payload_rows": duplicate_rows,
        "duplicate_target_rows": duplicate_target_rows,
        "invalid_rows": invalid_rows,
        "row_issue_counts": dict(sorted(row_issues.items())),
        "invalid_logs": invalid_logs,
        "log_issue_counts": dict(sorted(log_issues.items())),
        "issue_counts": dict(sorted(issues.items())),
        "available_logs_in_nonconflicting_alerts": available_logs,
        "matching_alerts_without_eligible_logs": len(
            set(groups) - conflicted - eligible_ids
        ),
        "logs_not_inspected_due_to_limit": 0,
        "included_alerts": 0,
        "included_logs": 0,
        "planned_unique_alerts": 0,
        "planned_logs": 0,
        "omitted_alerts": len(eligible_ids),
        "omitted_logs": available_logs,
        "budget_omitted_alerts": 0,
        "budget_omitted_logs": 0,
        "selection_omitted_alerts": 0,
        "selection_omitted_logs": 0,
        "selection_basis": "all_rows_all_valid_target_logs_source_order_batched",
        "source_coverage_scope": "target_rule_in_verified_historical_file",
        "source_row_reference_basis": "zero_based_dataframe_position_not_index_label",
        "all_eligible_logs_planned": False,
        "complete_source_coverage": False,
        "source_exclusions_present": bool(
            invalid_rows or invalid_logs or conflicted or issues
        ),
    }
    return candidates, coverage


def _context(rule, source, coverage, batch_index):
    return {
        "schema_version": "sec_know.historical_rule_context.v4",
        "material_source": "historical_export",
        "trigger": "manual_sample_review",
        "observed_surge": False,
        "rate_window": None,
        "source": copy.deepcopy(source),
        "rule": copy.deepcopy(rule),
        "batch_index": batch_index,
        "batching": {
            "basis": "whole_logs_under_estimated_input_tokens",
            "alert_count_limit": None,
            "logs_per_alert_limit": None,
            "split_only_when_estimated_context_budget_exceeded": True,
        },
        "analysis_scope_note": (
            "本计划已扫描整个文件，并将全部符合条件的有效日志分批提供；"
            "本次只分析本批，其他批次没有被预算永久丢弃。"
            "source_scope为整个文件范围，coverage为本批范围；"
            "其他批次的日志不作为本批结论的证据。"
            "跨批结果由本地报告逐批展示，执行失败或提前停止会单独记录。"
        ),
        "samples": [],
        "source_scope": {
            "file_rows": coverage["file_rows"],
            "inspected_rows": coverage["inspected_rows"],
            "matching_unique_alerts": coverage["matching_unique_alerts"],
            "eligible_unique_alerts": coverage["eligible_unique_alerts"],
            "eligible_logs": coverage["eligible_logs"],
            "invalid_rows": coverage["invalid_rows"],
            "invalid_logs": coverage["invalid_logs"],
            "excluded_conflicting_target_alerts": coverage[
                "excluded_conflicting_target_alerts"
            ],
            "all_eligible_logs_planned": True,
            "source_exclusions_present": coverage["source_exclusions_present"],
        },
        "coverage": {
            "included_alerts": 0,
            "included_logs": 0,
            "eligible_unique_alerts_outside_this_batch": coverage[
                "eligible_unique_alerts"
            ],
            "eligible_logs_outside_this_batch": coverage["eligible_logs"],
            "counts_scope": "this_batch_only",
            "evidence_scope": "only_logs_in_this_batch",
        },
        "historical_model_answers_included": False,
        "sample_match_is_not_false_positive_or_live_reduction_rate": True,
    }


def _refresh(context):
    coverage = context["coverage"]
    coverage["included_alerts"] = len(context["samples"])
    coverage["included_logs"] = sum(
        len(item["raw_logs"]) for item in context["samples"]
    )
    coverage["eligible_unique_alerts_outside_this_batch"] = (
        context["source_scope"]["eligible_unique_alerts"] - coverage["included_alerts"]
    )
    coverage["eligible_logs_outside_this_batch"] = (
        context["source_scope"]["eligible_logs"] - coverage["included_logs"]
    )


def _measure_request(context, analysis, limits):
    _refresh(context)
    # Construct the same full messages that will be sent. The conservative
    # estimator is disclosed by build_request; this is not a tokenizer count.
    # Measurement alone may exceed the token budget, but it never calls a model.
    return historical_review.build_request(
        context,
        analysis,
        limits=limits,
        require_samples=False,
        enforce_budget=False,
    )


def _budget_details(request, limits):
    return {
        "required_estimated_tokens": request["estimated_input_tokens"],
        "input_token_budget": limits.input_token_budget,
        "required_input_bytes": request["input_bytes"],
        "token_estimation_basis": request["token_estimation_basis"],
        "token_count_exact": False,
        "model_calls": 0,
        "action": "whole_log_not_truncated; inspect_context_requirement",
    }


def _append(context, identity, log, ref):
    samples = context["samples"]
    if not samples or samples[-1]["alert_id"] != identity:
        samples.append({"alert_id": identity, "raw_logs": [], "log_refs": []})
    samples[-1]["raw_logs"].append(copy.deepcopy(log))
    samples[-1]["log_refs"].append(copy.deepcopy(ref))


def _pop(context):
    sample = context["samples"][-1]
    sample["raw_logs"].pop()
    sample["log_refs"].pop()
    if not sample["raw_logs"]:
        context["samples"].pop()
    _refresh(context)


def build_plan(frame, rules, inventory, *, analysis, limits):
    """Fully scan and validate before returning any executable batch context.

    IDs come from original alert payloads. Log positions refer to the first
    identical source row; identical log CONTENT at different positions remains
    distinct. Conflicting alert identities are excluded regardless of scope.
    """
    if not frame.columns.is_unique or "alert_full_data" not in frame.columns:
        raise ValueError("unsupported_columns")
    rule = _rule_view(rules, analysis)
    candidates, coverage = _scan(frame, inventory, analysis)
    source = {
        "file_name": SAMPLE,
        "file_sha256": SAMPLE_SHA,
        "reference_csv_sha256": CSV_SHA,
    }
    batches = []
    context = _context(rule, source, coverage, 1)
    fixed_request = _measure_request(context, analysis, limits)
    if fixed_request["estimated_input_tokens"] > limits.input_token_budget:
        raise PlanningError(
            "historical_fixed_content_over_budget",
            details=_budget_details(fixed_request, limits),
            coverage=coverage,
        )
    for identity, log, ref in candidates:
        _append(context, identity, log, ref)
        required = _measure_request(context, analysis, limits)
        if required["estimated_input_tokens"] <= limits.input_token_budget:
            continue
        _pop(context)
        if context["samples"]:
            batches.append(context)
            context = _context(rule, source, coverage, len(batches) + 1)
        _append(context, identity, log, ref)
        required = _measure_request(context, analysis, limits)
        if required["estimated_input_tokens"] > limits.input_token_budget:
            raise PlanningError(
                "historical_single_log_over_budget",
                details={
                    "alert_id": identity,
                    "log_ref": ref,
                    **_budget_details(required, limits),
                },
                coverage=coverage,
            )
    if context["samples"]:
        batches.append(context)
    # Freeze-check every FINAL request under the agreed token estimate budget.
    requests = [
        historical_review.build_request(batch, analysis, limits=limits)
        for batch in batches
    ]
    coverage.update(
        included_alerts=coverage["eligible_unique_alerts"],
        included_logs=coverage["eligible_logs"],
        planned_unique_alerts=coverage["eligible_unique_alerts"],
        planned_logs=coverage["eligible_logs"],
        omitted_alerts=0,
        omitted_logs=coverage["invalid_logs"],
        all_eligible_logs_planned=True,
        complete_source_coverage=not coverage["source_exclusions_present"],
    )
    return {
        "schema_version": "sec_know.historical_batch_plan.v2",
        "source": source,
        "coverage": coverage,
        "batches": batches,
        "input_bytes_per_batch": [request["input_bytes"] for request in requests],
        "batch_count": len(batches),
        "estimated_tokens_per_batch": [
            request["estimated_input_tokens"] for request in requests
        ],
        "input_token_budget": limits.input_token_budget,
        "assumed_context_tokens": limits.assumed_context_tokens,
        "token_count_exact": False,
        "alert_count_limit": None,
        "logs_per_alert_limit": None,
        "model_calls": 0,
        "automatic_apply": False,
    }
