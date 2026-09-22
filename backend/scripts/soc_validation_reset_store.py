"""Offline DEV-only deletion plan for one corpus experiment's validation results.

No engine, model, migration or service construction. The Host maintenance wrapper
owns process exclusion, a verified full backup and the outer SQLite transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter

SCHEMA = "0032_corpus_revision_index"
ACTIVE = {"claimed", "prechecking", "analyzing", "projecting"}
RUN_CHILDREN = (
    "soc_memory_uses",
    "soc_decision_transitions",
    "soc_disposition_transitions",
    "soc_tenant_policy_decisions",
    "soc_decision_audit_log",
    "soc_alert_summaries",
    "soc_review_queue",
)
PROTECTED_MEMORY = (
    "soc_memory_records",
    "soc_memory_candidates",
    "soc_memory_pattern_observations",
    "soc_memory_working_drafts",
    "soc_memory_record_facets",
    "soc_memory_feedback",
    "soc_memory_health",
    "soc_memory_revision_proposals",
)


def _name(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError("unexpected database identifier")
    return '"' + value + '"'


def _schema(conn: sqlite3.Connection) -> dict[str, set[str]]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'soc_%'")]
    return {table: {r[1] for r in conn.execute(f"PRAGMA table_info({_name(table)})")} for table in tables}


def _matching(conn: sqlite3.Connection, table: str, column: str, values) -> set[int]:
    values = sorted(set(values))
    result = set()
    for offset in range(0, len(values), 400):
        page = values[offset : offset + 400]
        query = f"SELECT rowid FROM {_name(table)} WHERE {_name(column)} IN ({','.join('?' for _ in page)})"
        result.update(row[0] for row in conn.execute(query, page))
    return result


def _row_values(conn: sqlite3.Connection, table: str, column: str, rowids: set[int]) -> set[str]:
    result = set()
    ordered = sorted(rowids)
    for offset in range(0, len(ordered), 400):
        page = ordered[offset : offset + 400]
        result.update(row[0] for row in conn.execute(f"SELECT {_name(column)} FROM {_name(table)} WHERE rowid IN ({','.join('?' for _ in page)})", page) if row[0] is not None)
    return result


def _contains_reference(value, tokens: set[str]) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str) and (item in tokens or any(token in tokens for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", item))):
            return True
    return False


def _check_json_references(conn, schema, deletes, tokens):
    if not tokens:
        return  # The post-delete check must not reread gigabytes without targets.
    for table in schema:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({_name(table)})") if row[2].upper() == "JSON" or row[1].endswith("_payload")]
        if not columns:
            continue
        retained = [row[0] for row in conn.execute(f"SELECT rowid FROM {_name(table)}") if row[0] not in deletes.get(table, set())]
        for column in columns:
            for offset in range(0, len(retained), 400):
                page = retained[offset : offset + 400]
                # Exclude deleted records in SQL before fetching large Run JSON.
                query = f"SELECT {_name(column)} FROM {_name(table)} WHERE rowid IN ({','.join('?' for _ in page)})"
                for (payload,) in conn.execute(query, page):
                    if not payload:
                        continue
                    try:
                        value = json.loads(payload)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"invalid retained JSON in {table}; cleanup refused") from exc
                    if _contains_reference(value, tokens):
                        raise ValueError(f"protected provenance in {table}; cleanup refused")


def _plan(conn: sqlite3.Connection, experiment_id: str) -> tuple[dict, dict[str, set[int]]]:
    revision = [tuple(row) for row in conn.execute("SELECT version_num FROM soc_alembic_version")]
    if revision != [(SCHEMA,)]:
        raise ValueError(f"requires database schema {SCHEMA}; no migration is performed")
    schema = _schema(conn)
    required = {"soc_corpus_experiments", "soc_corpus_experiment_members", "soc_corpus_rounds", "soc_corpus_round_items", "soc_processing_jobs", "soc_processing_job_events", "soc_analysis_runs", "soc_corpus_list_projections"}
    if not required <= schema.keys():
        raise ValueError("incomplete corpus database schema")
    experiment = conn.execute("SELECT record_payload FROM soc_corpus_experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
    if experiment is None:
        raise ValueError("experiment not found")
    identity = json.loads(experiment[0])
    if identity.get("tenant_id") != "pingan" or identity.get("environment") != "dev-corpus-eval":
        raise ValueError("only the PingAn DEV corpus experiment may be reset")
    members = conn.execute("SELECT alert_id, json_extract(record_payload,'$.payload_hash') FROM soc_corpus_experiment_members WHERE experiment_id=? AND batch='validation'", (experiment_id,)).fetchall()
    if not members:
        raise ValueError("experiment has no validation members")
    member_hashes = dict(members)
    if any(not isinstance(value, str) or not value for value in member_hashes.values()):
        raise ValueError("validation member input hash is missing")
    first_members = {r[0] for r in conn.execute("SELECT alert_id FROM soc_corpus_experiment_members WHERE experiment_id=? AND batch='learning'", (experiment_id,))}
    if first_members & member_hashes.keys():
        raise ValueError("learning and validation members overlap")
    learning_options = conn.execute("SELECT json_extract(record_payload,'$.options') FROM soc_corpus_rounds WHERE experiment_id=? AND batch='learning' ORDER BY created_at DESC, round_id DESC", (experiment_id,)).fetchall()
    if any(r[0] is None for r in learning_options):
        raise ValueError("first batch has missing saved settings; cleanup refused")
    options = {json.dumps(json.loads(r[0]), sort_keys=True) for r in learning_options}
    if len(options) != 1:
        raise ValueError("first batch has missing or different saved settings; choose a verified baseline before reset")
    first_options = json.loads(next(iter(options)))
    rounds = conn.execute("SELECT rowid, round_id, state FROM soc_corpus_rounds WHERE experiment_id=? AND batch='validation'", (experiment_id,)).fetchall()
    if any(r[2] == "running" for r in rounds):
        raise ValueError("pause the second batch before stopping Host DEV")
    round_ids = {r[1] for r in rounds}
    jobs = conn.execute(
        "SELECT j.rowid,j.job_id,j.alert_id,j.idempotency_key,j.run_id,j.status,j.workload_kind,"
        "j.tenant_id,j.input_payload,j.metadata_payload,i.round_id,i.alert_id "
        "FROM soc_processing_jobs j JOIN soc_corpus_round_items i ON i.job_id=j.job_id "
        "JOIN soc_corpus_rounds r ON r.round_id=i.round_id "
        "WHERE r.experiment_id=? AND r.batch='validation'",
        (experiment_id,),
    ).fetchall()
    if any(j[5] in ACTIVE for j in jobs):
        raise ValueError("second-batch work is still active; pause and wait before maintenance")
    item_rows = _matching(conn, "soc_corpus_round_items", "round_id", round_ids)
    if len(jobs) != len(item_rows) or len({j[1] for j in jobs}) != len(jobs):
        raise ValueError("second-batch items have missing or shared jobs")
    for job in jobs:
        if job[2] not in member_hashes or job[6] != "corpus_experiment" or job[7] != "pingan" or job[2] != job[11]:
            raise ValueError("second-batch job ownership is inconsistent")
        if job[5] not in {"queued", "completed", "failed", "skipped_external_handled", "expired_before_analysis"}:
            raise ValueError("unknown job status; cleanup refused")
        expected_input = {"experiment_id": experiment_id, "round_id": job[10], "alert_id": job[2], "payload_hash": member_hashes[job[2]]}
        metadata = json.loads(job[9])
        if json.loads(job[8]) != expected_input or metadata.get("batch") != "validation" or metadata.get("round_id") != job[10]:
            raise ValueError("second-batch job input or metadata differs from its fixed member")
    job_ids = {j[1] for j in jobs}
    journal_keys = {(j[2], hashlib.sha256(json.dumps(j[3], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()) for j in jobs}
    direct_runs = {j[4] for j in jobs if j[4]}
    # A failed job can lack run_id even though Runtime already saved its request
    # journal. Match the exact job key and corpus input, never alert ID alone.
    candidate_runs = conn.execute(
        "SELECT a.rowid,a.run_id,a.alert_id,json_extract(a.run_payload,'$.request_journal.idempotency_key_hash') "
        "FROM soc_analysis_runs a JOIN soc_corpus_experiment_members m "
        "ON m.alert_id=a.alert_id AND json_extract(m.record_payload,'$.payload_hash')=a.input_hash "
        "WHERE m.experiment_id=? AND m.batch='validation'",
        (experiment_id,),
    ).fetchall()
    run_rows = {r[0] for r in candidate_runs if r[1] in direct_runs or (r[2], r[3]) in journal_keys}
    run_ids = {r[1] for r in candidate_runs if r[0] in run_rows}
    existing_direct = _matching(conn, "soc_analysis_runs", "run_id", direct_runs)
    if not existing_direct <= run_rows:
        raise ValueError("a validation job references a Run outside its fixed input scope")
    # Do not expose a previous unrelated run for these alerts after deleting the
    # selected experiment. Operators must resolve that lineage explicitly.
    if {r[0] for r in candidate_runs} - run_rows:
        raise ValueError("validation inputs have other Run histories outside this experiment; cleanup refused")
    deletes = {table: _matching(conn, table, "run_id", run_ids) for table in RUN_CHILDREN if table in schema}
    deletes["soc_analysis_runs"] = run_rows
    deletes["soc_processing_job_events"] = _matching(conn, "soc_processing_job_events", "job_id", job_ids)
    deletes["soc_processing_jobs"] = {j[0] for j in jobs}
    deletes["soc_corpus_round_items"] = item_rows
    deletes["soc_corpus_rounds"] = {r[0] for r in rounds}
    deletes["soc_corpus_list_projections"] = {
        r[0]
        for r in conn.execute(
            "SELECT p.rowid FROM soc_corpus_list_projections p JOIN soc_corpus_experiment_members m ON m.alert_id=p.alert_id AND json_extract(m.record_payload,'$.payload_hash')=p.input_hash WHERE m.experiment_id=? AND m.batch='validation'",
            (experiment_id,),
        )
    }
    queue_ids = _row_values(conn, "soc_review_queue", "queue_id", deletes.get("soc_review_queue", set())) if "soc_review_queue" in schema else set()
    use_ids = _row_values(conn, "soc_memory_uses", "use_id", deletes.get("soc_memory_uses", set())) if "soc_memory_uses" in schema else set()
    refs = {
        "run_id": run_ids,
        "source_run_id": run_ids,
        "target_run_id": run_ids,
        "replay_of_run_id": run_ids,
        "job_id": job_ids,
        "round_id": round_ids,
        "parent_round_id": round_ids,
        "queue_id": queue_ids,
        "source_queue_id": queue_ids,
        "target_queue_id": queue_ids,
        "use_id": use_ids,
        "source_memory_use_id": use_ids,
    }
    for table, column, aliases in (
        ("soc_decision_audit_log", "audit_id", ("audit_id",)),
        ("soc_decision_transitions", "transition_id", ("transition_id", "decision_transition_id")),
        ("soc_disposition_transitions", "transition_id", ("transition_id", "disposition_transition_id")),
        ("soc_tenant_policy_decisions", "decision_id", ("decision_id", "tenant_policy_decision_id")),
    ):
        if table in schema:
            values = _row_values(conn, table, column, deletes.get(table, set()))
            for alias in aliases:
                refs.setdefault(alias, set()).update(values)
    for table, columns in schema.items():
        for column, values in refs.items():
            if column in columns and _matching(conn, table, column, values) - deletes.get(table, set()):
                raise ValueError(f"protected reference in {table}.{column}; first-batch/Memory/other histories must be preserved")
    # Frozen requests, comparison baselines and Memory can cite multiple Runs
    # only in JSON. Check retained payloads as well as scalar reference columns.
    _check_json_references(conn, schema, deletes, set().union(*refs.values()))
    if any(table in deletes for _, table in conn.execute("SELECT name,tbl_name FROM sqlite_master WHERE type='trigger'")):
        raise ValueError("custom triggers on cleanup tables require independent review")
    # Approved settings, records and first-batch identity are evidence in the
    # backup receipt, not a substitute for a fresh API-created validation round.
    report = {
        "experiment_id": experiment_id,
        "batch": "validation",
        "schema_revision": SCHEMA,
        "validation_members": len(members),
        "rounds": len(rounds),
        "jobs": len(jobs),
        "runs": len(run_ids),
        "job_counts_including_history": dict(Counter(j[5] for j in jobs)),
        "first_batch_options": first_options,
        "first_batch_rounds": len(learning_options),
        "protected_memory_counts": {t: conn.execute(f"SELECT count(*) FROM {_name(t)}").fetchone()[0] for t in PROTECTED_MEMORY if t in schema},
        "delete_counts": {table: len(rows) for table, rows in deletes.items()},
    }
    return report, deletes


def preview(conn: sqlite3.Connection, experiment_id: str) -> dict:
    """Read metadata and validate ownership; never write even a temporary table."""
    return _plan(conn, experiment_id)[0]


def reset(conn: sqlite3.Connection, experiment_id: str) -> dict:
    """Delete only the verified dependency set inside a caller-owned transaction."""
    if not conn.in_transaction:
        raise ValueError("reset requires the maintenance caller's transaction")
    report, deletes = _plan(conn, experiment_id)
    for table, rowids in deletes.items():
        ordered = sorted(rowids)
        for offset in range(0, len(ordered), 400):
            page = ordered[offset : offset + 400]
            result = conn.execute(f"DELETE FROM {_name(table)} WHERE rowid IN ({','.join('?' for _ in page)})", page)
            if result.rowcount != len(page):
                raise ValueError("database changed during reset; rollback required")
    after = preview(conn, experiment_id)
    if after["rounds"] or after["jobs"] or after["runs"]:
        raise ValueError("validation reset did not clear all selected work")
    for key in ("first_batch_options", "first_batch_rounds", "protected_memory_counts", "validation_members"):
        if after[key] != report[key]:
            raise ValueError("protected first-batch or Memory state changed; rollback required")
    return {**report, "status": "reset", "next_step": "start Host DEV, then create a fresh validation round using first_batch_options and reviewed Memory"}
