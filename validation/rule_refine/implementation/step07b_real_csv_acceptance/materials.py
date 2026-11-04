"""Replay real exports through the installed history reader without writing DEV rows."""

import copy
import hashlib
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

import surge_materials

TARGET = surge_materials.TARGET
SYNTHETIC_SOURCES = {"synthetic_fixture", "synthetic_dev_fixture"}


def _hash(value):
    return hashlib.sha256(surge_materials.encoded(value)).hexdigest()


def _synthetic(identity, data):
    if isinstance(identity, str) and identity.startswith(
        "SYNTHETIC-DEV-RULE-REFINE-HISTORY-V1-"
    ):
        return True
    if not isinstance(data, dict):
        return False
    if (
        isinstance(data.get("material_source"), str)
        and data["material_source"] in SYNTHETIC_SOURCES
    ):
        return True
    main = data.get("alert")
    if not isinstance(main, dict):
        return False
    if (
        isinstance(main.get("material_source"), str)
        and main["material_source"] in SYNTHETIC_SOURCES
    ):
        return True
    for hit in main.get("hitLog", []) if isinstance(main.get("hitLog"), list) else []:
        if not isinstance(hit, dict):
            continue
        logs = hit.get("zeusRawLogs", [])
        if not isinstance(logs, list):
            continue
        for raw in logs:
            try:
                log = surge_materials._object(raw)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                continue
            if (
                isinstance(log.get("material_source"), str)
                and log["material_source"] in SYNTHETIC_SOURCES
            ):
                return True
    return False


def _normalized_id(value):
    if type(value) is int and value >= 0:
        value = str(value)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("invalid_alert_identity")
    return value


def _current(path):
    raw = path.read_bytes()
    rows = surge_materials._rows(raw)
    selected, target_ids, expected = [], set(), {}
    for row in rows:
        if (
            row.get("app_code", "zeus") != "zeus"
            or row.get("flow_id", "alert_agent") != "alert_agent"
        ):
            raise ValueError("current_export_workflow_mismatch")
        identity = _normalized_id(row["alert_id"])
        data = surge_materials._object(row["alert_data"])
        main = data.get("alert")
        if not isinstance(main, dict) or main.get("alertId") != identity:
            raise ValueError("current_export_identity_mismatch")
        if _synthetic(identity, data):
            raise ValueError("current_export_contains_synthetic_material")
        hits = main.get("hitLog")
        if not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits):
            raise ValueError("invalid_current_hit_logs")
        logs = []
        for hit in hits:
            if (hit.get("topic"), hit.get("ruleCode")) != TARGET:
                continue
            entries = hit.get("zeusRawLogs")
            if not isinstance(entries, list) or not entries:
                raise ValueError("current_target_raw_logs_required")
            logs.extend(surge_materials._object(entry) for entry in entries)
        if logs:
            if any(not log for log in logs):
                raise ValueError("empty_current_target_log")
            occurred = surge_materials._parse_created(main.get("createAt"))
            if identity in expected and expected[identity]["main_sha256"] != _hash(
                main
            ):
                raise ValueError("conflicting_current_export_identity")
            target_ids.add(identity)
            expected[identity] = {
                "main_sha256": _hash(main),
                "occurred_at": occurred.isoformat(sep=" "),
                "logs_sha256": _hash(logs),
            }
        # Retain other-rule versions too: the installed reader must see identity conflicts.
        selected.append((identity, surge_materials.encoded(data).decode("utf-8")))
    if not target_ids:
        raise ValueError("no_current_target_alerts")
    return (
        selected,
        sorted(target_ids),
        expected,
        {
            "kind": "current_csv",
            "path": str(path),
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "file_bytes": len(raw),
            "file_rows": len(rows),
            "current_target_unique_alerts": len(target_ids),
            "previous_model_answers_read": False,
        },
    )


def _local_rows(path):
    audit = {
        "kind": "project_LOCAL_sqlite",
        "path": str(path),
        "read_only": True,
        "previous_model_answers_read": False,
        "rows_scanned": 0,
        "synthetic_rows_excluded": 0,
    }
    if not path.exists():
        return [], {**audit, "status": "not_present"}
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid_local_database_file")
    rows = []
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        cursor = db.execute(
            "SELECT alert_id, alert_data FROM uas_app_workflow_task_status "
            "WHERE app_code=? AND flow_id=? ORDER BY alert_id, alert_data",
            ("zeus", "alert_agent"),
        )
        for identity, payload in cursor:
            audit["rows_scanned"] += 1
            try:
                data = surge_materials._object(payload)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                data = None
            if _synthetic(identity, data):
                audit["synthetic_rows_excluded"] += 1
                continue
            # Invalid and conflicting identities remain visible to the installed reader.
            rows.append((identity, payload))
            digest.update(surge_materials.encoded([identity, payload]))
            digest.update(b"\n")
        db.rollback()
    audit.update(
        status="read",
        candidate_rows=len(rows),
        selected_columns_snapshot_sha256=digest.hexdigest(),
    )
    return rows, audit


def _pickle_rows(path):
    from restricted_dataframe_pickle import load_dataframe_pickle

    raw = path.read_bytes()
    frame = load_dataframe_pickle(
        path, required_columns={"alert_id", "alert_full_data"}
    )
    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(raw).digest():
        raise ValueError("history_file_changed_while_reading")
    rows, issues = [], Counter()
    synthetic = 0
    for _, row in frame.iterrows():
        try:
            identity = _normalized_id(row["alert_id"])
            request = surge_materials._object(row["alert_full_data"])
            if (
                request.get("app_code") != "zeus"
                or request.get("flow_id") != "alert_agent"
            ):
                issues["non_target_workflow"] += 1
                continue
            if _normalized_id(request.get("alert_id")) != identity:
                raise ValueError("historical_request_identity_mismatch")
            payload = request.get("alert_data")
            data = surge_materials._object(payload)
            if _synthetic(identity, data) or _synthetic(identity, request):
                synthetic += 1
                continue
            rows.append((identity, surge_materials.encoded(data).decode("utf-8")))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            issues["invalid_historical_row"] += 1
            # A bad version of a known ID must quarantine that ID, never let another
            # version silently become evidence. No payload is copied into the audit.
            try:
                rows.append((_normalized_id(row["alert_id"]), "null"))
            except ValueError:
                pass
    return rows, {
        "kind": "historical_small_pickle",
        "path": str(path),
        "status": "read",
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "file_bytes": len(raw),
        "file_rows": len(frame),
        "restricted_loader": True,
        "candidate_rows": len(rows),
        "synthetic_rows_excluded": synthetic,
        "issue_counts": dict(issues),
        "previous_model_answers_read": False,
    }


def prepare_case(root, current_path, history_path, load, rules):
    """Return actual CSV current samples plus up to 20 genuine prior target alerts.

    ``load`` resolves an already installed step07 runtime module. ``history_path``
    may be None; an explicitly provided missing or malformed file fails visibly.
    Project SQLite is always read-only; only a disposable SQLite copy is written.
    """
    root = Path(root).expanduser().resolve()
    current_path = Path(current_path).expanduser().resolve()
    current, identities, expected, current_audit = _current(current_path)
    histories, database_audit = _local_rows(root / "nas/data/sec_know_model.sqlite")
    sources = [current_audit, database_audit]
    if history_path is not None:
        history_path = Path(history_path).expanduser().resolve()
        if not history_path.is_file():
            raise ValueError("historical_material_file_missing")
        if history_path.suffix.lower() in {".pkl", ".pickle"}:
            imported, audit = _pickle_rows(history_path)
        else:
            raise ValueError("historical_file_must_be_small_pickle")
        histories.extend(imported)
        sources.append(audit)
    original = rules.get_rule(TARGET[1], topic=TARGET[0], company_code=None)
    signal = {
        "schema_version": "sec_know.rule_burst_signal.v1",
        "mode": "historical_replay",
        "namespace": "real-csv-local-acceptance-v1",
        "signal_id": "real-csv-" + current_audit["file_sha256"],
        "source_app": "zeus",
        "company_code": original["company_code"],
        "topic": TARGET[0],
        "rule_code": TARGET[1],
        "current_alert_ids": identities,
        "sample_alert_ids": identities,
        "current_count": len(identities),
        "material_source": "historical_export",
        "observed_live_surge": False,
    }
    with tempfile.TemporaryDirectory(prefix="rule-refine-real-materials-") as folder:
        temp_path = Path(folder) / "materials.sqlite"
        with closing(sqlite3.connect(temp_path)) as db:
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status "
                "(app_code TEXT, flow_id TEXT, alert_id TEXT, alert_data TEXT)"
            )
            db.executemany(
                "INSERT INTO uas_app_workflow_task_status VALUES ('zeus', 'alert_agent', ?, ?)",
                [*current, *histories],
            )
            db.commit()
        case = load("surge_history").SurgeTaskSource(temp_path, rules).load_case(signal)
    for sample in case["current_samples"]:
        source = expected[sample["alert_id"]]
        if (
            sample["occurred_at"] != source["occurred_at"]
            or _hash(sample["raw_logs"]) != source["logs_sha256"]
        ):
            raise ValueError("current_material_recall_changed")
    if {sample["alert_id"] for sample in case["current_samples"]} != set(identities):
        raise ValueError("current_material_recall_incomplete")
    simulated, removed = surge_materials.simulated_rule(original)
    case["rule"] = simulated
    case["original_rule"] = copy.deepcopy(original)
    case["source"].update(
        kind="temporary_sqlite_from_real_exports_and_LOCAL_read_only",
        material_source="historical_export",
        current_origin="specified_real_csv",
        observed_live_surge=False,
        source_files_are_not_live_arrival_window=True,
        model_answers_used_as_history=False,
        source_csv_rows=current_audit["file_rows"],
        historical_selection_scope="available_LOCAL_rows_and_optional_small_pickle",
    )
    case["coverage"].update(
        real_current_csv_recall_verified=True,
        source_csv_current_log_coverage_complete=True,
        history_is_latest_available_in_supplied_sources=True,
        history_is_verified_latest_online=False,
    )
    audit = {
        "material_source": "historical_export",
        "sources": sources,
        "database_scope": "temporary_sqlite_replay_LOCAL_source_read_only",
        "temporary_database_removed": not temp_path.exists(),
        "business_database_rows_written": 0,
        "source_alert_identities_unchanged": True,
        "source_alert_content_unchanged": True,
        "source_alert_business_times_unchanged": True,
        "current_recall_verified": True,
        "current_target_alerts": len(identities),
        "current_target_logs": case["coverage"]["current_log_count"],
        "history_available": case["coverage"]["historical_available_unique_alerts"],
        "history_selected": len(case["history_samples"]),
        "history_target": 20,
        "history_gap_seconds": (
            surge_materials._parse_created(case["coverage"]["cutoff"])
            - surge_materials._parse_created(case["coverage"]["history_last_alert_at"])
        ).total_seconds()
        if case["history_samples"]
        else None,
        "history_normal_labels_assumed": False,
        "historical_model_answers_included": False,
        "synthetic_history_used": False,
        "counterfactual_rule": True,
        "historical_configuration_reconstruction": False,
        "removed_conditions": removed,
        "removed_conditions_provided_to_model": False,
        "original_rule_config_version": original["rule_config_version"],
        "source_rule_file_sha256": getattr(rules, "file_sha256", None),
        "original_rule_modified": False,
    }
    return case, signal, audit
