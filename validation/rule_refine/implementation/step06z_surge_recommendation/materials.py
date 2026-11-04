"""Read complete alert exports for one rule; no model, grouping, or filter execution."""

import copy
import csv
import hashlib
import io
import json
import math
from datetime import datetime
from pathlib import Path

ALERT_SHA = "b407ab3ba4139ef456e5646cf8ffdda7951549019f2daffe3f4194607c9d46f9"
RULES_SHA = "729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976"
TARGET = ("security_qthids", "RPAADM_000598")
HISTORY_COUNT = 20
METADATA_FIELDS = {
    "id",
    "rule_id",
    "company_uuid",
    "organize_uuid_chain",
    "created_by",
    "created_date",
    "updated_by",
    "updated_date",
    "raw_record",
    "remark",
}


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _invalid_constant(unused_value):
    raise ValueError("nonfinite_json_value")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite_json_value")
    return result


def _object(value):
    for _ in range(3):
        if not isinstance(value, str):
            break
        value = json.loads(
            value,
            object_pairs_hook=_unique,
            parse_constant=_invalid_constant,
            parse_float=_finite_float,
        )
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    encoded(value)
    return value


def _without_metadata(value):
    """Strip row/term provenance, preserving nested engine conditions verbatim."""
    result = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in METADATA_FIELDS
    }
    if "terms" in result:
        result["terms"] = [
            {
                key: copy.deepcopy(item)
                for key, item in term.items()
                if key not in METADATA_FIELDS
            }
            for term in result["terms"]
        ]
    return result


def simulated_rule(original):
    """Remove only the case's later clause; retain this audit outside model material."""
    if (original.get("topic"), original.get("rule_code")) != TARGET:
        raise ValueError("known_exclusion_simulation_only_supports_case_rule")
    configuration = copy.deepcopy(original["configuration"])
    terms = configuration.get("terms")
    if not isinstance(terms, list):
        raise ValueError("rule_terms_required")
    removed = []
    for term in terms:
        if not isinstance(term, dict):
            raise ValueError("invalid_rule_term")
        if term.get("alias") != "B":
            continue
        for representation in ("config", "standard_field_config"):
            group = term.get(representation)
            if not isinstance(group, dict) or not isinstance(
                group.get("conditions"), list
            ):
                raise ValueError("both_rule_condition_representations_required")
            remaining = []
            for index, condition in enumerate(group["conditions"]):
                if not isinstance(condition, dict):
                    raise ValueError("invalid_rule_condition")
                if (
                    condition.get("leftFieldName"),
                    condition.get("operator"),
                    condition.get("rightFieldValue"),
                ) == ("event_content", "NotContains", "net1 accounts"):
                    removed.append(
                        {
                            "term_alias": "B",
                            "representation": representation,
                            "condition_index": index,
                            "condition": copy.deepcopy(condition),
                        }
                    )
                else:
                    remaining.append(condition)
            if not remaining:
                raise ValueError("simulation_would_empty_condition_group")
            group["conditions"] = remaining
    if sorted(item["representation"] for item in removed) != [
        "config",
        "standard_field_config",
    ]:
        raise ValueError("expected_exactly_one_later_clause_in_each_representation")
    configuration = _without_metadata(configuration)
    identity = {
        key: original[key]
        for key in ("company_code", "topic", "rule_code", "rule_name")
    }
    rule = {
        **identity,
        "configuration": configuration,
        "rule_config_version": "counterfactual-v1:sha256:" + _sha(configuration),
        "source": {
            "kind": "counterfactual_csv_simulation",
            "historical_configuration_reconstruction": False,
        },
    }
    return rule, removed


def _parse_created(value):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("alert_create_at_required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_alert_create_at") from None


def _rows(raw):
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(max(previous_limit, len(raw)))
    try:
        reader = csv.DictReader(
            io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True
        )
        names = reader.fieldnames or []
        if len(names) != len(set(names)) or not {"alert_id", "alert_data"}.issubset(
            names
        ):
            raise ValueError("invalid_alert_csv_headers")
        rows = list(reader)
        if not rows or any(
            None in row or any(v is None for v in row.values()) for row in rows
        ):
            raise ValueError("invalid_alert_csv_rows")
        return rows
    finally:
        csv.field_size_limit(previous_limit)


def _read_export(path, scope, target):
    raw = Path(path).read_bytes()
    rows = _rows(raw)
    records = []
    for position, row in enumerate(rows):
        if row.get("flow_id", "alert_agent") != "alert_agent":
            raise ValueError("unexpected_workflow")
        data = _object(row["alert_data"])
        alert = data.get("alert")
        if not isinstance(alert, dict):
            raise ValueError("main_alert_required")
        identity = alert.get("alertId")
        if (
            not isinstance(identity, str)
            or not identity
            or identity != identity.strip()
            or identity != row["alert_id"]
        ):
            raise ValueError("alert_identity_mismatch")
        hits = alert.get("hitLog")
        if not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits):
            raise ValueError("invalid_main_hit_logs")
        logs, refs = [], []
        for hit_index, hit in enumerate(hits):
            if (hit.get("topic"), hit.get("ruleCode")) != target:
                continue
            entries = hit.get("zeusRawLogs")
            if not isinstance(entries, list) or not entries:
                raise ValueError("target_raw_logs_required")
            for log_index, entry in enumerate(entries):
                log = _object(entry)
                if not log:
                    raise ValueError("empty_target_log")
                logs.append(log)
                refs.append(
                    {
                        "source": scope,
                        "source_row_position_zero_based": position,
                        "hit_index": hit_index,
                        "raw_log_index": log_index,
                        "log_sha256": _sha(log),
                    }
                )
        # Main-alert identity is independent of the previous model answer and related alerts.
        records.append(
            {
                "alert_id": identity,
                "main_alert_sha256": _sha(alert),
                "raw_logs": logs,
                "occurred_at": _parse_created(alert.get("createAt")) if logs else None,
                "source": scope,
                "position": position,
                "log_provenance": refs,
            }
        )
    return records, {
        "scope": scope,
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "file_rows": len(rows),
        "parsed_rows": len(rows),
        "bytes": len(raw),
    }


def _rule(rules_source, rule_code, topic, company_code, simulate):
    original = rules_source.get_rule(rule_code, topic=topic, company_code=company_code)
    if (original.get("topic"), original.get("rule_code")) != (topic, rule_code):
        raise ValueError("rule_provider_scope_mismatch")
    if company_code is not None and original.get("company_code") != company_code:
        raise ValueError("rule_provider_company_mismatch")
    # Current request/export schema carries no rule-owning company. Do not guess it.
    companies = {
        item["company_code"]
        for item in rules_source.list_rules()
        if (item.get("topic"), item.get("rule_code")) == (topic, rule_code)
    }
    if companies != {original.get("company_code")}:
        raise ValueError("ambiguous_company_scope")
    if simulate:
        rule, removed = simulated_rule(original)
    else:
        rule = {
            key: copy.deepcopy(original[key])
            for key in (
                "company_code",
                "topic",
                "rule_code",
                "rule_name",
                "rule_config_version",
            )
        }
        rule["configuration"] = _without_metadata(original["configuration"])
        rule["source"] = {"kind": "csv_snapshot"}
        removed = []
    return original, rule, removed


def _sample(record):
    return {
        "alert_id": record["alert_id"],
        "occurred_at": record["occurred_at"].isoformat(sep=" "),
        "raw_logs": copy.deepcopy(record["raw_logs"]),
    }


def build_case(
    alerts_path,
    rules_source,
    *,
    history_path=None,
    rule_code=TARGET[1],
    topic=TARGET[0],
    company_code=None,
    cutoff=None,
    simulate_known_exclusion=True,
):
    """Return all current logs and nearest 20 pre-cutoff alerts; never invent a baseline.

    ``history_path`` is another CSV task export with the same envelope schema. With no
    explicit cutoff, the earliest nonconflicting target alert in ``alerts_path`` starts
    the current batch. Conflicting main-alert identities are excluded from both sides.
    """
    original, rule, removed = _rule(
        rules_source, rule_code, topic, company_code, simulate_known_exclusion
    )
    records, current_source = _read_export(
        alerts_path, "current_file", (topic, rule_code)
    )
    sources = [current_source]
    if history_path is not None:
        history_records, history_source = _read_export(
            history_path, "history_file", (topic, rule_code)
        )
        records += history_records
        sources.append(history_source)
    identities = {}
    for record in records:
        identities.setdefault(record["alert_id"], []).append(record)
    conflicts = {
        identity
        for identity, versions in identities.items()
        if len({record["main_alert_sha256"] for record in versions}) > 1
    }
    conflicting_target = {
        identity
        for identity in conflicts
        if any(record["raw_logs"] for record in identities[identity])
    }
    target_records = [
        record
        for record in records
        if record["raw_logs"] and record["alert_id"] not in conflicts
    ]
    current_file = [
        record for record in target_records if record["source"] == "current_file"
    ]
    if not current_file:
        raise ValueError("no_nonconflicting_current_target_alerts")
    dates = [record["occurred_at"] for record in target_records]
    if cutoff is not None:
        dates.append(_parse_created(cutoff))
    if len({value.utcoffset() is not None for value in dates}) > 1:
        raise ValueError("mixed_aware_and_naive_alert_times")
    start = (
        _parse_created(cutoff)
        if cutoff is not None
        else min(record["occurred_at"] for record in current_file)
    )
    current_by_id = {}
    for record in current_file:
        if record["occurred_at"] >= start:
            current_by_id.setdefault(record["alert_id"], record)
    if not current_by_id:
        raise ValueError("no_current_target_alerts_at_or_after_cutoff")
    history_by_id = {}
    overlapping_ids = set()
    for record in target_records:
        identity = record["alert_id"]
        if identity in current_by_id:
            if record["source"] == "history_file":
                overlapping_ids.add(identity)
            continue
        if record["occurred_at"] < start:
            history_by_id.setdefault(identity, record)
    # Ordering is independent of export order, model output, and behavior features.
    selected_current = sorted(
        current_by_id.values(),
        key=lambda record: (record["occurred_at"], record["alert_id"]),
    )
    candidates = sorted(
        sorted(history_by_id.values(), key=lambda record: record["alert_id"]),
        key=lambda record: record["occurred_at"],
        reverse=True,
    )
    selected_history = candidates[:HISTORY_COUNT]
    current_samples = [_sample(record) for record in selected_current]
    history_samples = [_sample(record) for record in selected_history]
    current_dates = [record["occurred_at"] for record in selected_current]
    history_dates = [record["occurred_at"] for record in selected_history]
    time_coverage = {
        "cutoff": start.isoformat(sep=" "),
        "timestamp_basis": "main_alert_createAt",
        "timezone": "offset_in_source"
        if start.utcoffset() is not None
        else "not_provided",
        "current_first_alert_at": min(current_dates).isoformat(sep=" "),
        "current_last_alert_at": max(current_dates).isoformat(sep=" "),
        "current_span_seconds": (
            max(current_dates) - min(current_dates)
        ).total_seconds(),
        "history_first_alert_at": min(history_dates).isoformat(sep=" ")
        if history_dates
        else None,
        "history_last_alert_at": max(history_dates).isoformat(sep=" ")
        if history_dates
        else None,
        "history_span_seconds": (
            max(history_dates) - min(history_dates)
        ).total_seconds()
        if history_dates
        else None,
    }
    coverage = {
        **time_coverage,
        "file_rows": sum(source["file_rows"] for source in sources),
        "parsed_rows": sum(source["parsed_rows"] for source in sources),
        "current_file_rows": current_source["file_rows"],
        "history_file_rows": sources[1]["file_rows"] if len(sources) > 1 else 0,
        "matching_unique_alerts": len(
            {record["alert_id"] for record in target_records}
        ),
        "conflicting_identity_count": len(conflicts),
        "excluded_conflicting_target_alerts": len(conflicting_target),
        "duplicate_identity_rows": sum(
            len(versions) - 1
            for identity, versions in identities.items()
            if identity not in conflicts
        ),
        "current_alert_count": len(current_samples),
        "current_log_count": sum(len(sample["raw_logs"]) for sample in current_samples),
        "historical_available_unique_alerts": len(candidates),
        "history_selected_unique_alerts": len(history_samples),
        "history_log_count": sum(len(sample["raw_logs"]) for sample in history_samples),
        "history_not_selected_count": max(0, len(candidates) - HISTORY_COUNT),
        "current_ids_excluded_from_history": len(overlapping_ids),
        "history_selection_basis": "nearest_20_distinct_alerts_strictly_before_cutoff",
        "current_order": "occurred_at_ascending_then_alert_id_ascending",
        "history_order": "occurred_at_descending_then_alert_id_ascending",
        "history_lookback_duration_limit": None,
        "history_is_complete_previous_window": False,
        "all_current_target_logs_retained": True,
        "invalid_rows": 0,
        "invalid_logs": 0,
    }
    audit = {
        **time_coverage,
        "sources": sources,
        "source_rule_file_sha256": rules_source.file_sha256,
        "source_rule_config_version": original["rule_config_version"],
        "removed_conditions": removed,
        "simulation_only": simulate_known_exclusion,
        "historical_configuration_reconstruction": False,
        "original_configuration_modified": False,
        "company_scope_source": "csv_unique_topic_rule_match",
        "company_code": original["company_code"],
        "cutoff_basis": "operator_supplied"
        if cutoff is not None
        else "earliest_nonconflicting_current_file_target_alert",
        "conflicting_alert_ids": sorted(conflicts),
        "coverage": copy.deepcopy(coverage),
        "sample_provenance": {
            record["alert_id"]: record["log_provenance"]
            for record in [*selected_current, *selected_history]
        },
        "historical_model_answers_included": False,
        "model_calls": 0,
        "automatic_apply": False,
        "filter_evaluation_performed": False,
    }
    return {
        "rule": rule,
        "original_rule": original,
        "current_samples": current_samples,
        "history_samples": history_samples,
        "coverage": coverage,
        "audit": audit,
    }
