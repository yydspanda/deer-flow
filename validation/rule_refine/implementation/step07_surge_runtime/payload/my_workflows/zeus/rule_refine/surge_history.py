"""Read current rule materials and nearest prior alerts from a LOCAL SQLite snapshot.

Only original main-alert payloads are used. Receipt-window times, task insertion
times, and prior model answers are not historical business-time evidence.
"""

import copy
import hashlib
import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

HISTORY_COUNT = 20
_METADATA_FIELDS = {
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


class MaterialsPendingError(ValueError):
    """Current anchors cannot yet be assembled without omissions or conflicts."""

    code = "current_alert_materials_pending"

    def __init__(self, details):
        super().__init__(self.code)
        self.details = details


def _encoded(value):
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


def _invalid_constant(value):
    raise ValueError("nonfinite_json_value")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("nonfinite_json_value")
    return parsed


def _object(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
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
    _encoded(value)
    return value


def _identity(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("invalid_alert_identity")
    return value


def _time(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("alert_create_at_required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_alert_create_at") from None


def _without_metadata(configuration):
    if not isinstance(configuration, dict):
        raise ValueError("invalid_rule_configuration")
    result = {
        key: copy.deepcopy(value)
        for key, value in configuration.items()
        if key not in _METADATA_FIELDS
    }
    if "terms" in result:
        if not isinstance(result["terms"], list) or any(
            not isinstance(term, dict) for term in result["terms"]
        ):
            raise ValueError("invalid_rule_terms")
        result["terms"] = [
            {key: value for key, value in term.items() if key not in _METADATA_FIELDS}
            for term in result["terms"]
        ]
    return result


def _sample(record, role):
    return {
        "alert_id": record["alert_id"],
        "occurred_at": record["occurred_at"].isoformat(sep=" "),
        "raw_logs": copy.deepcopy(record["raw_logs"]),
        "source_role": role,
    }


class SurgeTaskSource:
    """On-demand full LOCAL task scan with no per-log or current-sample size cap."""

    def __init__(self, path, rules):
        self.path = Path(path).expanduser().resolve()
        self.rules = rules

    def _rule(self, signal):
        scope = (signal["company_code"], signal["topic"], signal["rule_code"])
        original = self.rules.get_rule(scope[2], topic=scope[1], company_code=scope[0])
        if (
            tuple(original.get(key) for key in ("company_code", "topic", "rule_code"))
            != scope
        ):
            raise ValueError("rule_provider_scope_mismatch")
        companies = {
            item.get("company_code")
            for item in self.rules.list_rules()
            if (item.get("topic"), item.get("rule_code")) == scope[1:]
        }
        if companies != {scope[0]}:
            raise ValueError("ambiguous_company_scope")
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
        rule["source"] = {
            "kind": original.get("source", {}).get("kind", "rule_provider")
        }
        return copy.deepcopy(original), rule

    def _scan(self, target):
        if not self.path.is_file():
            raise ValueError("local_task_database_missing")
        records, hashes, row_counts, invalid_ids = {}, {}, Counter(), set()
        issues = Counter()
        scanned = parsed = invalid_logs = 0
        with closing(
            sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            total_rows = db.execute(
                "SELECT COUNT(*) FROM uas_app_workflow_task_status"
            ).fetchone()[0]
            cursor = db.execute(
                "SELECT alert_id, alert_data FROM uas_app_workflow_task_status WHERE app_code = ? AND flow_id = ?",
                ("zeus", "alert_agent"),
            )
            for row_id, payload in cursor:
                scanned += 1
                identity = None
                try:
                    row_id = _identity(row_id)
                    envelope = _object(payload)
                    main = envelope.get("alert")
                    if not isinstance(main, dict):
                        raise ValueError("main_alert_required")
                    identity = _identity(main.get("alertId"))
                    if identity != row_id:
                        invalid_ids.update((row_id, identity))
                        raise ValueError("alert_identity_mismatch")
                    digest = hashlib.sha256(_encoded(main)).hexdigest()
                    hashes.setdefault(identity, set()).add(digest)
                    row_counts[identity] += 1
                    hits = main.get("hitLog")
                    if not isinstance(hits, list) or any(
                        not isinstance(hit, dict) for hit in hits
                    ):
                        raise ValueError("invalid_main_hit_logs")
                    logs = []
                    for hit in hits:
                        if (hit.get("topic"), hit.get("ruleCode")) != target:
                            continue
                        entries = hit.get("zeusRawLogs")
                        if not isinstance(entries, list) or not entries:
                            invalid_logs += 1
                            raise ValueError("target_raw_logs_required")
                        for entry in entries:
                            try:
                                log = _object(entry)
                                if not log:
                                    raise ValueError("empty_target_log")
                            except (
                                ValueError,
                                TypeError,
                                UnicodeError,
                                RecursionError,
                            ):
                                invalid_logs += 1
                                raise ValueError("invalid_target_log") from None
                            logs.append(log)
                    if logs:
                        occurred_at = _time(main.get("createAt"))
                        records.setdefault(
                            identity,
                            {
                                "alert_id": identity,
                                "occurred_at": occurred_at,
                                "raw_logs": logs,
                            },
                        )
                    parsed += 1
                except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
                    if isinstance(row_id, str) and row_id:
                        invalid_ids.add(row_id)
                    if identity:
                        invalid_ids.add(identity)
                    # Store stable reason labels only, never exception text carrying payloads.
                    reason = (
                        str(exc)
                        if type(exc) is ValueError
                        and str(exc)
                        in {
                            "invalid_alert_identity",
                            "main_alert_required",
                            "alert_identity_mismatch",
                            "invalid_main_hit_logs",
                            "target_raw_logs_required",
                            "invalid_target_log",
                            "alert_create_at_required",
                            "invalid_alert_create_at",
                            "json_object_required",
                            "duplicate_json_field",
                            "nonfinite_json_value",
                        }
                        else "invalid_alert_payload"
                    )
                    issues[reason] += 1
            db.rollback()
        conflicts = {
            identity for identity, versions in hashes.items() if len(versions) > 1
        }
        usable = {
            identity: record
            for identity, record in records.items()
            if identity not in conflicts and identity not in invalid_ids
        }
        stats = {
            "database_total_task_rows": total_rows,
            "scanned_task_rows": scanned,
            "parsed_rows": parsed,
            "invalid_rows": scanned - parsed,
            "invalid_logs": invalid_logs,
            "issue_counts": dict(sorted(issues.items())),
            "conflicting_identity_count": len(conflicts),
            "excluded_conflicting_target_alerts": len(conflicts.intersection(records)),
            "excluded_invalid_target_alerts": len(invalid_ids.intersection(records)),
            "duplicate_identity_rows": sum(
                count - 1
                for identity, count in row_counts.items()
                if identity not in conflicts and identity not in invalid_ids
            ),
            "matching_unique_alerts": len(usable),
        }
        return usable, conflicts, invalid_ids, stats

    def load_case(self, signal):
        if not isinstance(signal, dict) or signal.get("source_app") != "zeus":
            raise ValueError("unsupported_signal_source")
        for key in ("company_code", "topic", "rule_code"):
            _identity(signal.get(key))
        anchor_field = (
            "current_alert_ids" if "current_alert_ids" in signal else "sample_alert_ids"
        )
        anchor_ids = signal.get(anchor_field)
        if not isinstance(anchor_ids, list) or not anchor_ids:
            raise ValueError("current_alert_ids_required")
        anchor_ids = {_identity(value) for value in anchor_ids}
        original_rule, rule = self._rule(signal)
        records, conflicts, invalid_ids, scan_stats = self._scan(
            (signal["topic"], signal["rule_code"])
        )
        unavailable = anchor_ids.difference(records)
        if unavailable:
            raise MaterialsPendingError(
                {
                    "requested_current_anchors": len(anchor_ids),
                    "available_current_anchors": len(anchor_ids.intersection(records)),
                    "unavailable_current_anchors": len(unavailable),
                    "conflicting_current_anchors": len(
                        anchor_ids.intersection(conflicts)
                    ),
                    "invalid_current_anchors": len(
                        anchor_ids.intersection(invalid_ids)
                    ),
                }
            )
        if (
            len(
                {
                    record["occurred_at"].utcoffset() is not None
                    for record in records.values()
                }
            )
            > 1
        ):
            raise ValueError("mixed_aware_and_naive_alert_times")
        start = min(records[identity]["occurred_at"] for identity in anchor_ids)
        end = max(records[identity]["occurred_at"] for identity in anchor_ids)
        in_range = {
            identity: record
            for identity, record in records.items()
            if start <= record["occurred_at"] <= end
        }
        exact_current_ids = anchor_field == "current_alert_ids"
        selected_current = (
            [records[identity] for identity in anchor_ids]
            if exact_current_ids
            else in_range.values()
        )
        current = sorted(
            selected_current,
            key=lambda record: (record["occurred_at"], record["alert_id"]),
        )
        current_ids = {record["alert_id"] for record in current}
        history = sorted(
            sorted(
                (
                    record
                    for record in records.values()
                    if record["occurred_at"] < start
                    and record["alert_id"] not in current_ids
                ),
                key=lambda record: record["alert_id"],
            ),
            key=lambda record: record["occurred_at"],
            reverse=True,
        )
        selected_history = history[:HISTORY_COUNT]
        current_samples = [_sample(record, "current") for record in current]
        history_samples = [_sample(record, "history") for record in selected_history]
        history_dates = [record["occurred_at"] for record in selected_history]
        coverage = {
            **scan_stats,
            "cutoff": start.isoformat(sep=" "),
            "cutoff_basis": "oldest_signal_sample_alert_time"
            if anchor_field == "sample_alert_ids"
            else "oldest_signal_current_alert_time",
            "cutoff_is_verified_surge_start": False,
            "time_basis": "main_alert_createAt",
            "timestamp_basis": "main_alert_createAt",
            "timezone": "offset_in_source"
            if start.utcoffset() is not None
            else "not_provided",
            "current_anchor_basis": anchor_field,
            "current_anchor_count": len(anchor_ids),
            "current_selection_basis": "exact_signal_current_alert_ids"
            if exact_current_ids
            else "target_business_times_between_oldest_and_newest_current_anchor_inclusive",
            "excluded_in_range_not_receipt_ids": len(
                set(in_range).difference(anchor_ids)
            )
            if exact_current_ids
            else 0,
            "current_first_alert_at": start.isoformat(sep=" "),
            "current_last_alert_at": end.isoformat(sep=" "),
            "current_span_seconds": (end - start).total_seconds(),
            "current_alert_count": len(current_samples),
            "current_log_count": sum(
                len(sample["raw_logs"]) for sample in current_samples
            ),
            "historical_available_unique_alerts": len(history),
            "history_selected_unique_alerts": len(history_samples),
            "history_log_count": sum(
                len(sample["raw_logs"]) for sample in history_samples
            ),
            "history_not_selected_count": len(history) - len(history_samples),
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
            "history_selection_basis": "nearest_20_distinct_alerts_strictly_before_cutoff",
            "history_lookback_duration_limit": None,
            "history_is_complete_previous_window": False,
            "current_order": "occurred_at_ascending_then_alert_id_ascending",
            "history_order": "occurred_at_descending_then_alert_id_ascending",
            "current_ids_excluded_from_history": len(current_ids),
            "after_current_anchor_range_alerts": sum(
                record["occurred_at"] > end for record in records.values()
            ),
            "all_current_target_logs_retained": True,
            "database_scan_complete": True,
            "current_receipt_window_complete": False,
            "historical_model_answers_included": False,
            "history_normal_labels_assumed": False,
        }
        source = {
            "kind": "local_sqlite_task_history",
            "table": "uas_app_workflow_task_status",
            "task_scope": {"app_code": "zeus", "flow_id": "alert_agent"},
            "scan_basis": "all_matching_workflow_rows_in_one_read_transaction",
            "read_only": True,
            "company_scope_basis": "unique_topic_rule_company_in_rule_catalog",
            "business_time_field": "alert_data.alert.createAt",
            "task_created_date_used": False,
            "receipt_window": {
                "start": copy.deepcopy(signal.get("window_start")),
                "end": copy.deepcopy(signal.get("window_end")),
                "used_for_business_time_selection": False,
            },
            "historical_model_answers_included": False,
        }
        return {
            "rule": rule,
            "original_rule": original_rule,
            "current_samples": current_samples,
            "history_samples": history_samples,
            "coverage": coverage,
            "source": source,
        }
