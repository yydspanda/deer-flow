"""Read original workflow payloads; reuse existing rule/history selection semantics."""

import hashlib
from collections import Counter

from .surge_history import SurgeTaskSource, _encoded, _identity, _object, _time


def iter_workflow_rows(engine=None, table=None):
    """Independent read-only transaction; never select prior model answers."""
    from sqlalchemy import select, text

    if engine is None:
        from model.db.base import engine
    if table is None:
        from model.db.orm_models import WorkflowTaskModel

        table = WorkflowTaskModel.__table__
    with engine.connect() as connection:
        transaction = connection.begin()
        result = None
        try:
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                connection.execute(text("SET LOCAL statement_timeout = '60000'"))
            statement = select(table.c.alert_id, table.c.alert_data).where(
                table.c.app_code == "zeus", table.c.flow_id == "alert_agent"
            )
            result = connection.execution_options(stream_results=True).execute(
                statement
            )
            for row in result.yield_per(256):
                yield row[0], row[1]
        finally:
            if result is not None:
                result.close()
            transaction.rollback()


def _execute_type(value):
    if type(value) is int and value in {0, 1, 2, 3}:
        return value
    if isinstance(value, str) and value in {"0", "1", "2", "3"}:
        return int(value)
    return None


class TopicTaskSource(SurgeTaskSource):
    def __init__(self, rules, row_loader=None):
        self.rules = rules
        self.row_loader = row_loader or iter_workflow_rows
        self._types = {}

    def _scan(self, target):
        records, hashes, counts, invalid_ids = {}, {}, Counter(), set()
        issues, types = Counter(), {}
        scanned = parsed = invalid_logs = 0
        rows = iter(self.row_loader())
        try:
            for row_id, payload in rows:
                scanned += 1
                identity = None
                try:
                    row_id = _identity(row_id)
                    main = _object(payload).get("alert")
                    if not isinstance(main, dict):
                        raise ValueError("main_alert_required")
                    identity = _identity(main.get("alertId"))
                    if identity != row_id:
                        invalid_ids.update((row_id, identity))
                        raise ValueError("alert_identity_mismatch")
                    hashes.setdefault(identity, set()).add(
                        hashlib.sha256(_encoded(main)).hexdigest()
                    )
                    counts[identity] += 1
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
                        records.setdefault(
                            identity,
                            {
                                "alert_id": identity,
                                "occurred_at": _time(main.get("createAt")),
                                "raw_logs": logs,
                            },
                        )
                        types[identity] = _execute_type(main.get("executeType"))
                    parsed += 1
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    if isinstance(row_id, str) and row_id:
                        invalid_ids.add(row_id)
                    if identity:
                        invalid_ids.add(identity)
                    issues["invalid_alert_payload"] += 1
        finally:
            close = getattr(rows, "close", None)
            if close is not None:
                close()
        conflicts = {
            identity for identity, versions in hashes.items() if len(versions) > 1
        }
        usable = {
            identity: record
            for identity, record in records.items()
            if identity not in conflicts and identity not in invalid_ids
        }
        self._types = {identity: types[identity] for identity in usable}
        return (
            usable,
            conflicts,
            invalid_ids,
            {
                "database_total_task_rows": None,
                "scanned_task_rows": scanned,
                "parsed_rows": parsed,
                "invalid_rows": scanned - parsed,
                "invalid_logs": invalid_logs,
                "issue_counts": dict(issues),
                "conflicting_identity_count": len(conflicts),
                "excluded_conflicting_target_alerts": len(
                    conflicts.intersection(records)
                ),
                "excluded_invalid_target_alerts": len(
                    invalid_ids.intersection(records)
                ),
                "duplicate_identity_rows": sum(
                    count - 1
                    for identity, count in counts.items()
                    if identity not in conflicts and identity not in invalid_ids
                ),
                "matching_unique_alerts": len(usable),
            },
        )

    def load_case(self, signal):
        case = super().load_case(signal)
        types = [self._types[sample["alert_id"]] for sample in case["current_samples"]]
        values = sorted({value for value in types if value is not None})
        missing = sum(value is None for value in types)
        case["execute_type"] = {
            "status": "resolved"
            if not missing and len(values) == 1
            else "needs_confirmation",
            "values": values,
            "basis": "current_alerts.alert.executeType",
            "current_alert_count": len(types),
            "missing_or_invalid_count": missing,
        }
        case["source"].update(
            kind="workflow_task_original_history",
            scan_basis="all_matching_workflow_rows_in_one_read_transaction",
        )
        return case
