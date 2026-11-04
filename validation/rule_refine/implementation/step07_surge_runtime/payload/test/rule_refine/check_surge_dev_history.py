"""Seed isolated synthetic DEV task rows and verify original-alert history recall.

Uses only an existing LOCAL project SQLite database. No application imports,
workflow submission, Redis, model requests, callbacks, or schema changes.
"""

import argparse
import copy
import importlib.util
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

TABLE = "uas_app_workflow_task_status"
MARKER = "rule_refine_synthetic_dev_history_v1"
COMPANY = "SYNTHETIC_DEV_RULE_REFINE_COMPANY"
TOPIC = "synthetic-dev-rule-refine-history"
RULE = "SYNTHETIC_DEV_RULE_REFINE_HISTORY_V1"
ALERT_PREFIX = "SYNTHETIC-DEV-RULE-REFINE-HISTORY-V1-"
CUTOFF = datetime(2026, 9, 17, 10)
_SOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "my_workflows/zeus/rule_refine/surge_history.py"
)


def _encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _task_id(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "sec-know:" + MARKER + ":" + label))


def _fixture_payload(identity, occurred, *, rule=RULE, topic=TOPIC):
    if (
        identity.startswith(ALERT_PREFIX + "current-")
        or identity == ALERT_PREFIX + "future"
    ):
        activity = {
            "event_content": "inventory-probe --collect versions",
            "process_name": "inventory-probe.exe",
            "task_source": "planned_inventory",
        }
    else:
        historical_activities = (
            (
                "interactive-command --identity",
                "interactive-command.exe",
                "interactive_session",
            ),
            (
                "file-console --list sample-folder",
                "file-console.exe",
                "manual_file_check",
            ),
            (
                "service-status --name demo-cache",
                "service-status.exe",
                "manual_service_check",
            ),
            (
                "diagnostic-ping documentation.invalid",
                "diagnostic-ping.exe",
                "interactive_diagnostic",
            ),
        )
        index = (
            int(identity.rsplit("-", 1)[1])
            if identity.startswith(ALERT_PREFIX + "history-")
            else 0
        )
        activity = dict(
            zip(
                ("event_content", "process_name", "task_source"),
                historical_activities[index % len(historical_activities)],
            )
        )
    return {
        "material_source": "synthetic_dev_fixture",
        "seed_owner": MARKER,
        "alert": {
            "alertId": identity,
            "createAt": occurred.isoformat(sep=" "),
            "hitLog": [
                {
                    "topic": topic,
                    "ruleCode": rule,
                    "zeusRawLogs": [
                        {
                            **activity,
                            "fixture_alert_id": identity,
                            "material_source": "synthetic_dev_fixture",
                        }
                    ],
                }
            ],
        },
    }


def fixture_rows():
    """Fixed task IDs and original business times make reruns idempotent."""
    rows = []

    def add(label, identity, occurred, **kwargs):
        rows.append(
            {
                "id": _task_id(label),
                "app_code": "zeus",
                "flow_id": "alert_agent",
                "alert_id": identity,
                "alert_data": _encoded(_fixture_payload(identity, occurred, **kwargs)),
                "status": "successed",
                "failed_count": 0,
                "agent_response": None,
                "created_by": MARKER,
                "updated_by": MARKER,
                # Deliberately differs from alert time; recall must ignore insertion metadata.
                "created_date": "2026-11-04 00:00:00",
                "updated_date": "2026-11-04 00:00:00",
            }
        )

    for index in range(32):
        add(
            f"history-{index:02}",
            ALERT_PREFIX + f"history-{index:02}",
            CUTOFF - timedelta(hours=6 * (index + 1)),
        )
    for index in range(6):
        add(
            f"current-{index:02}",
            ALERT_PREFIX + f"current-{index:02}",
            CUTOFF + timedelta(minutes=index),
        )
    add(
        "other-rule",
        ALERT_PREFIX + "other-rule",
        CUTOFF - timedelta(minutes=1),
        rule=RULE + "_OTHER",
    )
    add(
        "other-topic",
        ALERT_PREFIX + "other-topic",
        CUTOFF - timedelta(minutes=1),
        topic=TOPIC + "-other",
    )
    add("future", ALERT_PREFIX + "future", CUTOFF + timedelta(days=1))
    add("duplicate", ALERT_PREFIX + "history-00", CUTOFF - timedelta(hours=6))
    return rows


class FixtureRules:
    """Private in-memory rule catalog; no CSV or production configuration changes."""

    def __init__(self):
        self.rule = {
            "company_code": COMPANY,
            "topic": TOPIC,
            "rule_code": RULE,
            "rule_name": "Synthetic DEV history recall fixture",
            "rule_config_version": "synthetic-dev-history-rule-v1",
            "configuration": {"topic": TOPIC, "terms": []},
            "source": {"kind": "synthetic_dev_fixture"},
        }

    def get_rule(self, rule_code, *, topic, company_code):
        if (company_code, topic, rule_code) != (COMPANY, TOPIC, RULE):
            raise ValueError("fixture_rule_scope_mismatch")
        return copy.deepcopy(self.rule)

    def list_rules(self):
        return [copy.deepcopy(self.rule)]


def _require_local(environ):
    values = [environ[key] for key in ("env_profile", "ENV_PROFILE") if key in environ]
    if not values or any(str(value).strip().upper() != "LOCAL" for value in values):
        raise ValueError("explicit_LOCAL_profile_required")


def _path(project_root):
    value = os.fspath(project_root)
    if "://" in value or value.startswith(("//", "\\\\")):
        raise ValueError("local_project_path_required")
    root = Path(os.path.abspath(os.path.expanduser(value)))
    path = root / "nas/data/sec_know_model.sqlite"
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("symlink_path_not_allowed")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).is_symlink():
            raise ValueError("symlink_sqlite_sidecar_not_allowed")
    if not root.is_dir() or not path.is_file():
        raise ValueError("existing_local_database_required")
    return path


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _columns(db):
    row = db.execute(
        "SELECT type FROM sqlite_master WHERE name = ?", (TABLE,)
    ).fetchone()
    if row is None or row[0] != "table":
        raise ValueError("existing_task_table_required")
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? LIMIT 1",
        (TABLE,),
    ).fetchone():
        raise ValueError("task_table_triggers_require_review")
    schema = db.execute(f"PRAGMA table_xinfo({_quoted(TABLE)})").fetchall()
    columns = {row[1]: row for row in schema}
    required = {"id", "app_code", "flow_id", "alert_id", "alert_data", "status"}
    if not required.issubset(columns):
        raise ValueError("required_task_columns_missing")
    supported = set(fixture_rows()[0])
    if any(columns[name][6] for name in required):
        raise ValueError("generated_required_task_columns_not_supported")
    defaults = {}
    for row in schema:
        name, required, default, hidden = row[1], row[3], row[4], row[6]
        if hidden:
            continue
        if name in supported:
            defaults[name] = None
        elif default is not None:
            value = default.strip()
            while value.startswith("(") and value.endswith(")"):
                value = value[1:-1].strip()
            if value.upper() == "NULL" and not required:
                defaults[name] = None
            elif re.fullmatch(r"[-+]?\d+", value):
                defaults[name] = int(value)
            elif len(value) >= 2 and value.startswith("'") and value.endswith("'"):
                body = value[1:-1]
                if "'" in body.replace("''", ""):
                    raise ValueError("unsupported_task_column_default")
                defaults[name] = body.replace("''", "'")
            else:
                raise ValueError("unsupported_task_column_default")
        elif not required:
            defaults[name] = None
        else:
            raise ValueError("unsupported_required_task_columns")
    return defaults


def _verify_existing(db, rows, columns):
    by_id = {row["id"]: row for row in rows}
    alert_ids = sorted({row["alert_id"] for row in rows})
    clause = (
        "id IN ("
        + ",".join("?" for _ in by_id)
        + ") OR alert_id IN ("
        + ",".join("?" for _ in alert_ids)
        + ")"
    )
    args = [*by_id, *alert_ids]
    if "created_by" in columns:
        clause += " OR created_by = ?"
        args.append(MARKER)
    selected = ",".join(_quoted(name) for name in columns)
    seen = set()
    for values in db.execute(
        f"SELECT {selected} FROM {_quoted(TABLE)} WHERE {clause}", args
    ):
        actual = dict(zip(columns, values))
        task_id = actual["id"]
        if task_id not in by_id or task_id in seen:
            raise ValueError("synthetic_fixture_identity_conflict")
        expected = {name: by_id[task_id][name] for name in columns}
        if actual != expected:
            raise ValueError("synthetic_fixture_record_changed")
        seen.add(task_id)
    return seen


def _recall(path):
    spec = importlib.util.spec_from_file_location(
        "_dev_surge_history_source", _SOURCE_PATH
    )
    source_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_module)
    current_ids = [ALERT_PREFIX + f"current-{index:02}" for index in range(6)]
    signal = {
        "source_app": "zeus",
        "company_code": COMPANY,
        "topic": TOPIC,
        "rule_code": RULE,
        "sample_alert_ids": current_ids,
        "window_start": "2026-11-04T00:00:00+08:00",
        "window_end": "2026-11-04T01:00:00+08:00",
    }
    case = source_module.SurgeTaskSource(path, FixtureRules()).load_case(signal)
    expected_history = [ALERT_PREFIX + f"history-{index:02}" for index in range(20)]
    recalled = [sample["alert_id"] for sample in case["history_samples"]]
    current = [sample["alert_id"] for sample in case["current_samples"]]
    passed = (
        recalled == expected_history
        and current == current_ids
        and case["coverage"]["historical_available_unique_alerts"] == 32
    )
    return {
        "recall_passed": passed,
        "expected_current_alerts": 6,
        "recalled_current_alerts": len(current),
        "available_historical_alerts": case["coverage"][
            "historical_available_unique_alerts"
        ],
        "expected_historical_alerts": 20,
        "recalled_historical_alerts": len(recalled),
        "history_basis": case["coverage"]["history_selection_basis"],
        "cutoff_basis": case["coverage"]["cutoff_basis"],
        "time_basis": case["coverage"]["time_basis"],
        "history_lookback_duration_limit": None,
        "historical_model_answers_included": False,
        "history_normal_labels_assumed": False,
        "source": case["source"],
    }


def run(project_root, *, cleanup=False, environ=None):
    _require_local(os.environ if environ is None else environ)
    path = _path(project_root)
    rows = fixture_rows()
    summary = {
        "check": "rule_refine_dev_history_seed",
        "material_source": "synthetic_dev_fixture",
        "database_scope": "existing_project_LOCAL_sqlite",
        "database_path": str(path),
        "cleanup_requested": cleanup,
        "fixture_task_rows": len(rows),
        "inserted": 0,
        "reused": 0,
        "deleted": 0,
        "model_calls": 0,
        "redis_calls": 0,
        "business_alerts_submitted": 0,
        "celery_tasks_dispatched": 0,
        "zeus_callback_calls": 0,
        "application_code_updated": False,
        "schema_updated": False,
        "automatic_apply": False,
    }
    inode = path.stat()
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    ) as db:
        current_inode = path.stat()
        if (inode.st_dev, inode.st_ino) != (current_inode.st_dev, current_inode.st_ino):
            raise ValueError("database_path_changed")
        db.execute("BEGIN IMMEDIATE")
        try:
            defaults = _columns(db)
            columns = list(defaults)
            rows = [
                {**defaults, **{name: row[name] for name in columns if name in row}}
                for row in rows
            ]
            existing = _verify_existing(db, rows, columns)
            summary["reused"] = len(existing)
            if cleanup:
                for task_id in sorted(existing):
                    cursor = db.execute(
                        f"DELETE FROM {_quoted(TABLE)} WHERE id = ?", (task_id,)
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("synthetic_fixture_delete_count_mismatch")
                summary["deleted"] = len(existing)
            else:
                statement = (
                    f"INSERT INTO {_quoted(TABLE)} ("
                    + ",".join(_quoted(name) for name in columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")"
                )
                for row in rows:
                    if row["id"] not in existing:
                        db.execute(statement, [row[name] for name in columns])
                        summary["inserted"] += 1
                if len(_verify_existing(db, rows, columns)) != len(rows):
                    raise ValueError("synthetic_fixture_insert_count_mismatch")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    if cleanup:
        return {
            **summary,
            "status": "cleaned",
            "recall_passed": None,
            "source": {"kind": "synthetic_dev_fixture"},
        }
    try:
        recall = _recall(path)
    except (ValueError, sqlite3.Error, OSError, ImportError) as exc:
        return {
            **summary,
            "status": "recall_failed",
            "recall_passed": False,
            "error_type": type(exc).__name__,
            "fixture_rows_remain_for_inspection": True,
        }
    return {
        **summary,
        **recall,
        "status": "passed" if recall["recall_passed"] else "recall_failed",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete only unchanged rows belonging to this synthetic fixture",
    )
    args = parser.parse_args(argv)
    try:
        result = run(args.project_root, cleanup=args.cleanup)
    except (ValueError, sqlite3.Error, OSError) as exc:
        result = {
            "check": "rule_refine_dev_history_seed",
            "status": "blocked",
            "error_type": type(exc).__name__,
            "reason": str(exc)
            if type(exc) is ValueError
            else "local_database_operation_failed",
            "model_calls": 0,
            "redis_calls": 0,
            "business_alerts_submitted": 0,
            "automatic_apply": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"passed", "cleaned"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
