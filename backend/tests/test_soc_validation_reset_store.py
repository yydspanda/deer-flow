"""The offline cleanup must preserve learning/Memory and reject shared lineage."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, create_engine

import soc_agent.db.models  # noqa: F401
from scripts.soc_validation_reset_store import preview, reset
from soc_agent.db.base import SocBase


def _insert(connection, table_name, marker, **values):
    table = SocBase.metadata.tables[table_name]
    data = {}
    for column in table.columns:
        if column.name in values:
            data[column.name] = values[column.name]
        elif column.nullable:
            data[column.name] = None
        elif isinstance(column.type, JSON):
            data[column.name] = {}
        elif isinstance(column.type, DateTime):
            data[column.name] = datetime(2026, 9, 22, tzinfo=UTC)
        elif isinstance(column.type, Boolean):
            data[column.name] = False
        elif isinstance(column.type, (Integer, Float)):
            data[column.name] = 1
        else:
            data[column.name] = marker + ":" + column.name
    connection.execute(table.insert().values(**data))


def _key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "reset.db"
    engine = create_engine(f"sqlite:///{path}")
    SocBase.metadata.create_all(engine)
    options = {"normalization_review_mode": "apply", "refresh_normalization": False, "tenant_policy_enabled": False, "tenant_policy_advisor_enabled": False, "tenant_policy_signal_providers_enabled": False}
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE soc_alembic_version(version_num TEXT)")
        c.exec_driver_sql("INSERT INTO soc_alembic_version VALUES ('0032_corpus_revision_index')")
        _insert(c, "soc_corpus_experiments", "exp", experiment_id="EXP-test", record_payload={"tenant_id": "pingan", "environment": "dev-corpus-eval"})
        for index, (alert, batch) in enumerate((("L", "learning"), ("V", "validation"), ("F", "validation"), ("Q", "validation"))):
            _insert(c, "soc_corpus_experiment_members", alert, experiment_id="EXP-test", alert_id=alert, batch=batch, sequence_number=index, record_payload={"payload_hash": "hash-" + alert})
        for batch in ("learning", "validation"):
            _insert(
                c,
                "soc_corpus_rounds",
                batch,
                round_id="ROUND-" + batch,
                experiment_id="EXP-test",
                batch=batch,
                state="completed" if batch == "learning" else "blocked",
                record_payload={"options": options, "state_reason": "configuration_changed"},
            )
        for index, (alert, status) in enumerate((("L", "completed"), ("V", "completed"), ("F", "failed"), ("Q", "queued"))):
            batch = "learning" if alert == "L" else "validation"
            run_id = "RUN-" + alert * 12
            _insert(
                c,
                "soc_processing_jobs",
                alert,
                job_id="JOB-" + alert,
                alert_id=alert,
                tenant_id="pingan",
                workload_kind="corpus_experiment",
                status=status,
                idempotency_key="key-" + alert,
                run_id=run_id if status == "completed" else None,
                input_payload={"experiment_id": "EXP-test", "round_id": "ROUND-" + batch, "alert_id": alert, "payload_hash": "hash-" + alert},
                metadata_payload={"round_id": "ROUND-" + batch, "batch": batch},
            )
            _insert(c, "soc_processing_job_events", alert, job_id="JOB-" + alert)
            _insert(c, "soc_corpus_round_items", alert, round_id="ROUND-" + batch, alert_id=alert, job_id="JOB-" + alert, sequence_number=index)
            _insert(c, "soc_corpus_list_projections", alert, catalog_id="catalog", alert_id=alert, input_hash="hash-" + alert)
            if status != "queued":
                _insert(c, "soc_analysis_runs", alert, run_id=run_id, alert_id=alert, input_hash="hash-" + alert, run_payload={"request_journal": {"idempotency_key_hash": _key("key-" + alert)}})
                _insert(c, "soc_alert_summaries", alert, run_id=run_id, alert_id=alert)
                _insert(c, "soc_decision_audit_log", alert, run_id=run_id, alert_id=alert)
                _insert(c, "soc_review_queue", alert, queue_id="QUEUE-" + alert, run_id=run_id, alert_id=alert)
                _insert(c, "soc_memory_uses", alert, use_id="USE-" + alert * 12, memory_id="MEMORY-first", run_id=run_id, alert_id=alert)
        _insert(c, "soc_memory_records", "memory", memory_id="MEMORY-first", source_run_id="RUN-" + "L" * 12, record_payload={"source": {"run_id": "RUN-" + "L" * 12}})
    engine.dispose()
    with sqlite3.connect(path) as conn:
        yield conn


def _dump(conn):
    return "\n".join(conn.iterdump())


def test_preview_read_only_and_reset_removes_all_validation_attempts(database):
    before = _dump(database)
    database.execute("PRAGMA query_only=ON")
    report = preview(database, "EXP-test")
    assert report["runs"] == 2  # Includes failed F whose job.run_id is null.
    assert report["jobs"] == 3
    assert report["validation_members"] == 3
    assert report["first_batch_options"]["normalization_review_mode"] == "apply"
    assert _dump(database) == before
    database.execute("PRAGMA query_only=OFF")
    protected = {table: database.execute(f'SELECT * FROM "{table}"').fetchall() for table in ("soc_memory_records", "soc_corpus_experiments", "soc_corpus_experiment_members")}
    learning_run = database.execute("SELECT * FROM soc_analysis_runs WHERE alert_id='L'").fetchall()
    database.execute("BEGIN IMMEDIATE")
    result = reset(database, "EXP-test")
    assert result["status"] == "reset"
    database.commit()
    assert database.execute("SELECT alert_id FROM soc_analysis_runs").fetchall() == [("L",)]
    assert database.execute("SELECT alert_id FROM soc_processing_jobs").fetchall() == [("L",)]
    assert database.execute("SELECT batch FROM soc_corpus_rounds").fetchall() == [("learning",)]
    assert database.execute("SELECT alert_id FROM soc_corpus_list_projections").fetchall() == [("L",)]
    assert database.execute("SELECT * FROM soc_analysis_runs WHERE alert_id='L'").fetchall() == learning_run
    for table, rows in protected.items():
        assert database.execute(f'SELECT * FROM "{table}"').fetchall() == rows
    assert preview(database, "EXP-test")["jobs"] == 0


@pytest.mark.parametrize("kind", ["active", "running_round", "memory_source", "memory_json", "memory_use_json", "external_reference", "different_first_settings", "replay", "schema", "wrong_environment"])
def test_protected_conditions_refuse_cleanup_without_writes(database, kind):
    target = "RUN-" + "V" * 12
    if kind == "active":
        database.execute("UPDATE soc_processing_jobs SET status='analyzing' WHERE alert_id='F'")
    elif kind == "running_round":
        database.execute("UPDATE soc_corpus_rounds SET state='running' WHERE batch='validation'")
    elif kind == "memory_source":
        database.execute("UPDATE soc_memory_records SET source_run_id=?", (target,))
    elif kind in {"memory_json", "memory_use_json"}:
        value = target if kind == "memory_json" else "USE-" + "V" * 12
        database.execute("UPDATE soc_memory_records SET record_payload=?", (json.dumps({"evidence": {"source": value}}),))
    elif kind == "external_reference":
        database.execute("CREATE TABLE soc_custom_history(run_id TEXT)")
        database.execute("INSERT INTO soc_custom_history VALUES (?)", (target,))
    elif kind == "different_first_settings":
        database.execute("UPDATE soc_corpus_rounds SET record_payload='{}' WHERE batch='learning'")
    elif kind == "replay":
        database.execute("UPDATE soc_analysis_runs SET replay_of_run_id=? WHERE alert_id='L'", (target,))
    elif kind == "schema":
        database.execute("UPDATE soc_alembic_version SET version_num='0031_memory_working_drafts'")
    elif kind == "wrong_environment":
        database.execute("UPDATE soc_corpus_experiments SET record_payload=?", (json.dumps({"tenant_id": "pingan", "environment": "stg"}),))
    database.commit()
    before = _dump(database)
    with pytest.raises(ValueError):
        preview(database, "EXP-test")
    assert _dump(database) == before


def test_delete_failure_rolls_back_everything(database):
    before = _dump(database)
    database.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE and table == "soc_analysis_runs" else sqlite3.SQLITE_OK)
    with pytest.raises(sqlite3.DatabaseError, match="authorized"):
        with database:
            database.execute("BEGIN IMMEDIATE")
            reset(database, "EXP-test")
    database.set_authorizer(None)
    assert _dump(database) == before


def test_reset_requires_callers_explicit_transaction(database):
    with pytest.raises(ValueError, match="transaction"):
        reset(database, "EXP-test")


def test_wrapper_row_factory_and_custom_triggers(database):
    database.row_factory = sqlite3.Row
    assert preview(database, "EXP-test")["runs"] == 2
    database.execute("CREATE TRIGGER mutate_memory AFTER DELETE ON soc_analysis_runs BEGIN DELETE FROM soc_memory_records; END")
    database.commit()
    with pytest.raises(ValueError, match="triggers"):
        preview(database, "EXP-test")


@pytest.mark.parametrize("kind", ["wrong_input", "wrong_tenant", "wrong_batch", "missing_job", "unknown_status", "foreign_history"])
def test_inconsistent_validation_ownership_is_rejected(database, kind):
    if kind == "wrong_input":
        database.execute("UPDATE soc_processing_jobs SET input_payload=json_set(input_payload,'$.payload_hash','wrong') WHERE alert_id='V'")
    elif kind == "wrong_tenant":
        database.execute("UPDATE soc_processing_jobs SET tenant_id='other' WHERE alert_id='V'")
    elif kind == "wrong_batch":
        database.execute("UPDATE soc_processing_jobs SET metadata_payload=json_set(metadata_payload,'$.batch','learning') WHERE alert_id='V'")
    elif kind == "missing_job":
        database.execute("DELETE FROM soc_processing_jobs WHERE alert_id='Q'")
    elif kind == "unknown_status":
        database.execute("UPDATE soc_processing_jobs SET status='new_active_state' WHERE alert_id='V'")
    elif kind == "foreign_history":
        database.execute("UPDATE soc_analysis_runs SET run_payload='{}' WHERE alert_id='F'")
    database.commit()
    before = _dump(database)
    with pytest.raises(ValueError):
        preview(database, "EXP-test")
    assert _dump(database) == before


@pytest.mark.parametrize("kind", ["escaped_json", "declared_json_column", "one_missing_options", "audit_reference"])
def test_retained_provenance_cannot_be_orphaned(database, kind):
    if kind == "escaped_json":
        database.execute("UPDATE soc_memory_records SET record_payload=?", ('{"source":"\\u0052UN-' + "V" * 12 + '"}',))
    elif kind == "declared_json_column":
        database.execute("CREATE TABLE soc_custom_history(evidence JSON)")
        database.execute("INSERT INTO soc_custom_history VALUES (?)", (json.dumps({"source": "RUN-" + "V" * 12}),))
    elif kind == "one_missing_options":
        columns = [r[1] for r in database.execute("PRAGMA table_info(soc_corpus_rounds)")]
        row = list(database.execute("SELECT * FROM soc_corpus_rounds WHERE batch='learning'").fetchone())
        row[columns.index("round_id")] = "ROUND-no-settings"
        row[columns.index("record_payload")] = "{}"
        database.execute(f"INSERT INTO soc_corpus_rounds VALUES ({','.join('?' for _ in row)})", row)
    else:
        # An external disposition can carry only the result audit ID, without run_id.
        database.execute("UPDATE soc_decision_audit_log SET audit_id='AUDIT-validation' WHERE alert_id='V'")
        database.execute("CREATE TABLE soc_retained_disposition(audit_id TEXT)")
        database.execute("INSERT INTO soc_retained_disposition VALUES ('AUDIT-validation')")
    database.commit()
    before = _dump(database)
    with pytest.raises(ValueError):
        preview(database, "EXP-test")
    assert _dump(database) == before
