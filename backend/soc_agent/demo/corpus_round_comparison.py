"""Small persisted comparison projections; raw evidence and labels stay out."""

from soc_agent.utils.hashing import stable_hash


def job_result_row(job) -> dict:
    outcome = job.result_payload or {}
    return {
        "job_id": job.job_id,
        "run_id": job.run_id,
        "status": job.status.value,
        "attempt_count": job.attempt_count,
        "summary": outcome.get("summary", {}),
        "snapshot_changed_during_run": outcome.get("snapshot_changed_during_run", False),
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


def comparison_side(row: dict) -> dict:
    summary = row.get("summary") or {}
    selected = {key: summary.get(key) for key in ("base_verdict", "effective_verdict", "recommended_handling", "processing_path", "total_duration_ms", "model_name", "decision_usable")}
    selected["memory_uses"] = [{key: use[key] for key in ("memory_id", "memory_version", "directive_applied", "effect") if key in use} for use in summary.get("memory_uses", [])]
    measurements = summary.get("measurements") or {}
    selected["measurements"] = {key: measurements.get(key) for key in ("total_tokens", "provider_call_count", "usage_measurement_status")}
    return {
        **{key: row.get(key) for key in ("job_id", "run_id", "status", "attempt_count", "error_code", "error_message")},
        "snapshot_changed_during_run": bool(row.get("snapshot_changed_during_run", False)),
        "summary": selected,
    }


def comparison_results_hash(rows: list[dict]) -> str:
    return stable_hash(sorted(({"alert_id": row["alert_id"], **comparison_side(row)} for row in rows), key=lambda row: row["alert_id"]))


def compare_round_item(item) -> dict:
    baseline = item.job.metadata.get("comparison_baseline")
    before = baseline.get("result") if baseline else None
    after = comparison_side(job_result_row(item.job))
    differences = []
    if baseline is None:
        status = "not_captured"
    elif before is None:
        status = "new_sample"
    elif any(side["status"] != "completed" or side["snapshot_changed_during_run"] or side["summary"].get("recommended_handling") not in {"ignore", "transfer"} for side in (before, after)):
        status = "not_comparable"
    else:
        for key in ("recommended_handling", "effective_verdict"):
            if before["summary"].get(key) != after["summary"].get(key):
                differences.append(key)
        if sorted(before["summary"].get("memory_uses", []), key=stable_hash) != sorted(after["summary"].get("memory_uses", []), key=stable_hash):
            differences.append("memory_uses")
        status = "handling_changed" if "recommended_handling" in differences else "handling_unchanged"
    return {"alert_id": item.alert_id, "group_id": item.group_id, "before": before, "after": after, "comparison_status": status, "changed_fields": differences}
