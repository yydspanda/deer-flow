"""Aggregate historical-log eligibility without exporting identities or values."""

from collections import Counter


_SIZE_BINS = (
    (1024, "up_to_1_kib"),
    (4 * 1024, "over_1_up_to_4_kib"),
    (8 * 1024, "over_4_up_to_8_kib"),
    (16 * 1024, "over_8_up_to_16_kib"),
    (32 * 1024, "over_16_up_to_32_kib"),
    (64 * 1024, "over_32_up_to_64_kib"),
)


def _classify(log, materials):
    """Use the original serialized byte count; never trim or flatten a log."""
    if not isinstance(log, dict):
        return "non_object", None
    if not log:
        return "empty_object", None
    try:
        size = len(materials.encode(log))
    except (ValueError, TypeError, RecursionError):
        return "invalid_encoding", None
    return ("oversized" if size > 8 * 1024 else "eligible"), size


def audit_logs(frame, materials, inventory):
    """Mirror the 6R inspection scope, stopping before material selection.

    Dependencies are pure modules supplied by the caller. Source hashes/loading
    and original-context comparison belong to the caller. Counts cover at most
    the first 200 rows and first 100 logs of each nonconflicting target alert;
    they do not measure live traffic or relax the original context budgets.
    """
    if not frame.columns.is_unique or "alert_full_data" not in frame.columns:
        raise ValueError("unsupported_columns")
    groups, variants, issues = {}, {}, Counter()
    duplicate_target_rows = 0
    for position, value in enumerate(frame["alert_full_data"].iloc[:200]):
        try:
            column = (
                frame["alert_id"].iloc[position]
                if "alert_id" in frame.columns
                else materials._NO_COLUMN
            )
            identity, alert, digest = materials._data(value, column, inventory)
            variants.setdefault(identity, set()).add(digest)
            hits = alert.get("hitLog")
            if not isinstance(hits, list) or len(hits) > 1000:
                issues["invalid_or_excessive_hits"] += 1
                continue
            matching = [
                hit
                for hit in hits
                if isinstance(hit, dict)
                and (hit.get("topic"), hit.get("ruleCode")) == materials.TARGET
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
    counts = dict.fromkeys(
        ("eligible", "non_object", "empty_object", "invalid_encoding", "oversized"), 0
    )
    bins = dict.fromkeys([name for _, name in _SIZE_BINS] + ["over_64_kib"], 0)
    available = uninspected = alert_count = eligible_alerts = 0
    sized_count = 0
    minimum = maximum = None
    for identity, entry in groups.items():
        if identity in conflicted:
            continue
        alert_count += 1
        available += entry["available_logs"]
        uninspected += entry["available_logs"] - len(entry["raw_logs"])
        has_eligible = False
        for log in entry["raw_logs"]:
            category, size = _classify(log, materials)
            counts[category] += 1
            has_eligible = has_eligible or category == "eligible"
            if size is None:
                continue
            sized_count += 1
            minimum = size if minimum is None else min(minimum, size)
            maximum = size if maximum is None else max(maximum, size)
            label = next(
                (name for limit, name in _SIZE_BINS if size <= limit), "over_64_kib"
            )
            bins[label] += 1
        eligible_alerts += int(has_eligible)
    return {
        "schema_version": "sec_know.historical_log_eligibility_audit.v1",
        "material_source": "historical_export",
        "file_rows": len(frame),
        "inspected_rows": min(len(frame), 200),
        "matching_unique_alerts": len(groups),
        "conflicting_identity_count": len(conflicted),
        "excluded_conflicting_target_alerts": len(set(groups) & conflicted),
        "duplicate_target_rows": duplicate_target_rows,
        "nonconflicting_target_alerts": alert_count,
        "alerts_with_eligible_logs": eligible_alerts,
        "alerts_without_eligible_logs": alert_count - eligible_alerts,
        "available_logs_in_nonconflicting_alerts": available,
        "logs_inspected": sum(counts.values()),
        "logs_not_inspected_due_to_limit": uninspected,
        "log_categories": counts,
        "invalid_or_oversized_logs": sum(counts.values()) - counts["eligible"],
        "serialized_sizes": {
            "scope": "inspected_nonempty_dictionary_logs_with_successful_encoding",
            "encoding": "original_materials_encode_utf8_bytes",
            "count": sized_count,
            "minimum_bytes": minimum,
            "maximum_bytes": maximum,
            "bins": bins,
        },
        "limits": {
            "row_scan": 200,
            "hits_per_alert": 1000,
            "log_scan_per_alert": 100,
            "eligible_log_max_bytes": 8 * 1024,
            "model_selection_recomputed": False,
        },
        "issue_counts": dict(issues),
        "alert_identities_exported": False,
        "raw_values_exported": False,
    }
