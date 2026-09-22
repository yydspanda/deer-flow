"""The offline cleanup must preserve learning/Memory and reject shared lineage."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, create_engine

import scripts.soc_validation_reset_store as reset_store
import soc_agent.db.models  # noqa: F401
from scripts.soc_validation_reset_store import preview, reset
from soc_agent.contracts import ActorContext, NormalizationMaintenanceIssue
from soc_agent.core.normalization_maintenance import _record_recurrence
from soc_agent.db.base import SocBase
from soc_agent.db.repositories import _normalization_issue_row_values


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


def _copy_learning_round(database, *, round_id, options=None):
    columns = [r[1] for r in database.execute("PRAGMA table_info(soc_corpus_rounds)")]
    row = list(database.execute("SELECT * FROM soc_corpus_rounds WHERE round_id='ROUND-learning'").fetchone())
    row[columns.index("round_id")] = round_id
    if options is not None:
        row[columns.index("record_payload")] = json.dumps({"options": options})
    database.execute(f"INSERT INTO soc_corpus_rounds VALUES ({','.join('?' for _ in row)})", row)
    database.commit()


def test_explicit_learning_baseline_preserves_other_historical_settings(database):
    options = preview(database, "EXP-test")["first_batch_options"]
    _copy_learning_round(database, round_id="ROUND-old-off", options={**options, "normalization_review_mode": "off"})
    before = _dump(database)
    with pytest.raises(ValueError, match="different saved settings"):
        preview(database, "EXP-test")
    report = preview(database, "EXP-test", learning_round_id="ROUND-learning")
    assert report["first_batch_options"] == options
    assert report["first_batch_baseline_round_id"] == "ROUND-learning"
    assert report["first_batch_rounds"] == 2
    assert _dump(database) == before
    retained_rounds = database.execute("SELECT * FROM soc_corpus_rounds WHERE batch='learning' ORDER BY round_id").fetchall()
    retained_memory = database.execute("SELECT * FROM soc_memory_records").fetchall()
    with database:
        database.execute("BEGIN IMMEDIATE")
        result = reset(database, "EXP-test", learning_round_id="ROUND-learning")
    assert result["first_batch_options"] == options
    assert result["first_batch_baseline_round_id"] == "ROUND-learning"
    assert database.execute("SELECT * FROM soc_corpus_rounds WHERE batch='learning' ORDER BY round_id").fetchall() == retained_rounds
    assert database.execute("SELECT * FROM soc_memory_records").fetchall() == retained_memory
    assert database.execute("SELECT alert_id FROM soc_analysis_runs").fetchall() == [("L",)]


@pytest.mark.parametrize("kind", ["missing", "validation", "other_experiment", "unrun", "bad_options", "shared_memory"])
def test_explicit_learning_baseline_cannot_bypass_guards(database, kind):
    baseline = "ROUND-learning"
    if kind == "missing":
        baseline = "ROUND-missing"
    elif kind == "validation":
        baseline = "ROUND-validation"
    elif kind == "other_experiment":
        database.execute("UPDATE soc_corpus_rounds SET experiment_id='EXP-other' WHERE round_id=?", (baseline,))
    elif kind == "unrun":
        _copy_learning_round(database, round_id="ROUND-unrun")
        baseline = "ROUND-unrun"
    elif kind == "bad_options":
        database.execute("UPDATE soc_corpus_rounds SET record_payload=? WHERE round_id=?", (json.dumps({"options": {"normalization_review_mode": "apply"}}), baseline))
    else:
        database.execute("UPDATE soc_memory_records SET source_run_id=?", ("RUN-" + "V" * 12,))
    database.commit()
    before = _dump(database)
    with pytest.raises(ValueError):
        preview(database, "EXP-test", learning_round_id=baseline)
    assert _dump(database) == before


def _save_issue(database, issue):
    values = {"issue_id": issue.issue_id, **_normalization_issue_row_values(issue, issue.model_dump(mode="json"))}
    values["issue_payload"] = json.dumps(values["issue_payload"], ensure_ascii=False)
    values = {name: value.isoformat() if isinstance(value, datetime) else value for name, value in values.items()}
    database.execute(f"INSERT INTO soc_normalization_maintenance_issues ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))
    database.commit()


def _issue(*, issue_id="NMI-shared", alert_id="V", run_id=None, status="open"):
    return NormalizationMaintenanceIssue(
        issue_id=issue_id,
        dedupe_key="normalization:" + issue_id,
        issue_type="baseline_missing",
        severity="info",
        adapter="pingan-fixture",
        tenant_id="pingan",
        source_system="ndr",
        run_id=run_id or "RUN-" + alert_id * 12,
        alert_id=alert_id,
        status=status,
        occurrence_count=4104,
        first_seen_at=datetime(2026, 9, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 9, 22, tzinfo=UTC),
        acknowledged_by=ActorContext(actor_id="engineer"),
        acknowledged_at=datetime(2026, 9, 20, tzinfo=UTC),
        resolved_by=ActorContext(actor_id="reviewer") if status in {"resolved", "ignored"} else None,
        resolved_at=datetime(2026, 9, 21, tzinfo=UTC) if status in {"resolved", "ignored"} else None,
        resolution_reason="保留人工处理与历史累计",
        details={"warnings": ["parser baseline missing"], "field_count": 17},
    )


def _issue_rows(database):
    cursor = database.execute("SELECT * FROM soc_normalization_maintenance_issues ORDER BY issue_id")
    columns = [item[0] for item in cursor.description]
    return {row[0]: dict(zip(columns, tuple(row), strict=True)) for row in cursor}


def _other_experiment(database):
    for table, changes in (
        ("soc_corpus_experiments", {"experiment_id": "EXP-other"}),
        ("soc_corpus_experiment_members", {"experiment_id": "EXP-other", "alert_id": "other", "record_payload": json.dumps({"payload_hash": "hash-other"})}),
        ("soc_analysis_runs", {"run_id": "RUN-other-experiment", "alert_id": "other", "input_hash": "hash-other", "run_payload": "{}"}),
    ):
        cursor = database.execute(f"SELECT * FROM {table} LIMIT 1")
        values = dict(zip((column[0] for column in cursor.description), cursor.fetchone(), strict=True))
        values.update(changes)
        database.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))
    database.commit()


@pytest.mark.parametrize("status", ["open", "acknowledged", "resolved", "ignored"])
@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_normalization_shared_issue_detaches_only_deleted_run_link(database, status, row_factory):
    # The same issue first belonged to a learning Run, then recurred in validation.
    issue = _issue(alert_id="L", status=status)
    old_payload = issue.model_dump(mode="json")
    database.execute("UPDATE soc_analysis_runs SET run_payload=json_set(run_payload,'$.normalization_monitoring_result',json(?)) WHERE alert_id='L'", (json.dumps({"issues": [old_payload]}),))
    _record_recurrence(issue, run=SimpleNamespace(run_id="RUN-" + "V" * 12, alert_id="V"), details=issue.details)
    issue.status = type(issue.status)(status)
    if status in {"resolved", "ignored"}:
        issue.resolved_by = ActorContext(actor_id="reviewer")
        issue.resolved_at = datetime(2026, 9, 21, tzinfo=UTC)
    issue.resolution_reason = "保留人工处理与历史累计"
    _save_issue(database, issue)
    _save_issue(database, _issue(issue_id="NMI-learning", alert_id="L"))
    _other_experiment(database)
    _save_issue(database, _issue(issue_id="NMI-other-experiment", alert_id="other", run_id="RUN-other-experiment"))
    before = _dump(database)
    preserved_runs = database.execute("SELECT * FROM soc_analysis_runs WHERE alert_id IN ('L','other') ORDER BY run_id").fetchall()
    preserved_memory = database.execute("SELECT * FROM soc_memory_records").fetchall()
    expected = _issue_rows(database)
    expected[issue.issue_id]["run_id"] = None
    payload = json.loads(expected[issue.issue_id]["issue_payload"])
    expected[issue.issue_id]["issue_payload"] = {**payload, "run_id": None}
    database.row_factory = row_factory
    database.execute("PRAGMA query_only=ON")
    report = preview(database, "EXP-test")
    assert report["detached_normalization_issue_count"] == 1
    assert _dump(database) == before
    database.execute("PRAGMA query_only=OFF")
    with database:
        database.execute("BEGIN IMMEDIATE")
        result = reset(database, "EXP-test")
    assert result["detached_normalization_issue_count"] == 1
    actual = _issue_rows(database)
    actual[issue.issue_id]["issue_payload"] = json.loads(actual[issue.issue_id]["issue_payload"])
    assert actual == expected
    assert [tuple(row) for row in database.execute("SELECT * FROM soc_analysis_runs WHERE alert_id IN ('L','other') ORDER BY run_id")] == preserved_runs
    assert [tuple(row) for row in database.execute("SELECT * FROM soc_memory_records")] == preserved_memory
    assert preview(database, "EXP-test")["detached_normalization_issue_count"] == 0


@pytest.mark.parametrize("field", ["issue_id", "run_id", "alert_id", "tenant_id"])
def test_normalization_issue_scalar_json_mismatch_refuses_cleanup(database, field):
    _save_issue(database, _issue())
    database.execute("UPDATE soc_normalization_maintenance_issues SET issue_payload=json_set(issue_payload,?,?)", ("$." + field, "different"))
    database.commit()
    before = _dump(database)
    with pytest.raises(ValueError, match="normalization.*identity"):
        preview(database, "EXP-test")
    assert _dump(database) == before


@pytest.mark.parametrize("kind", ["wrong_alert", "wrong_tenant", "nested_run", "other_json_column", "extra_scalar", "duplicate_json", "memory", "outside_table"])
def test_normalization_detach_keeps_all_other_provenance_guards(database, kind):
    issue = _issue()
    if kind == "wrong_alert":
        issue.alert_id = "L"
    elif kind == "wrong_tenant":
        issue.tenant_id = "other"
    elif kind == "nested_run":
        issue.details["source"] = {"run_id": issue.run_id}
    _save_issue(database, issue)
    if kind == "other_json_column":
        database.execute("ALTER TABLE soc_normalization_maintenance_issues ADD COLUMN extra_payload JSON")
        database.execute("UPDATE soc_normalization_maintenance_issues SET extra_payload=?", (json.dumps({"source": issue.run_id}),))
    elif kind == "extra_scalar":
        database.execute("ALTER TABLE soc_normalization_maintenance_issues ADD COLUMN source_run_id TEXT")
        database.execute("UPDATE soc_normalization_maintenance_issues SET source_run_id=?", (issue.run_id,))
    elif kind == "duplicate_json":
        database.execute("UPDATE soc_normalization_maintenance_issues SET issue_payload=substr(issue_payload,1,length(issue_payload)-1) || ?", (',"details":{"duplicate":true}}',))
    elif kind == "memory":
        database.execute("UPDATE soc_memory_records SET source_run_id=?", (issue.run_id,))
    elif kind == "outside_table":
        database.execute("CREATE TABLE soc_extra_history(run_id TEXT)")
        database.execute("INSERT INTO soc_extra_history VALUES (?)", (issue.run_id,))
    database.commit()
    before = _dump(database)
    with pytest.raises(ValueError):
        preview(database, "EXP-test")
    assert _dump(database) == before


def test_normalization_issue_update_trigger_refuses_cleanup(database):
    _save_issue(database, _issue())
    database.execute("CREATE TRIGGER modify_shared_count AFTER UPDATE ON soc_normalization_maintenance_issues BEGIN UPDATE soc_normalization_maintenance_issues SET occurrence_count=1; END")
    database.commit()
    before = _dump(database)
    with pytest.raises(ValueError, match="triggers"):
        preview(database, "EXP-test")
    assert _dump(database) == before


def test_normalization_detach_rolls_back_if_later_run_delete_fails(database):
    _save_issue(database, _issue())
    before = _dump(database)
    detached_before_delete = []

    def authorizer(action, table, *args):
        if action == sqlite3.SQLITE_DELETE and table == "soc_analysis_runs":
            detached_before_delete.append(database.execute("SELECT run_id,json_extract(issue_payload,'$.run_id') FROM soc_normalization_maintenance_issues").fetchone())
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    database.set_authorizer(authorizer)
    with pytest.raises(sqlite3.DatabaseError, match="authorized"):
        with database:
            database.execute("BEGIN IMMEDIATE")
            reset(database, "EXP-test")
    database.set_authorizer(None)
    assert detached_before_delete == [(None, None)]
    assert _dump(database) == before


def test_normalization_detach_cas_rejects_changed_operator_state(database, monkeypatch):
    _save_issue(database, _issue())
    before = _dump(database)
    original = reset_store._plan

    def change_after_plan(*args, **kwargs):
        plan = original(*args, **kwargs)
        database.execute("UPDATE soc_normalization_maintenance_issues SET resolution_reason='concurrent operator edit'")
        return plan

    monkeypatch.setattr(reset_store, "_plan", change_after_plan)
    with pytest.raises(ValueError, match="normalization issue changed during reset"):
        with database:
            database.execute("BEGIN IMMEDIATE")
            reset(database, "EXP-test")
    assert _dump(database) == before


def test_normalization_detach_final_check_rejects_other_field_changes(database):
    _save_issue(database, _issue())
    before = _dump(database)

    def authorizer(action, table, *args):
        if action == sqlite3.SQLITE_DELETE and table == "soc_analysis_runs":
            database.execute("UPDATE soc_normalization_maintenance_issues SET occurrence_count=1")
        return sqlite3.SQLITE_OK

    database.set_authorizer(authorizer)
    with pytest.raises(ValueError, match="protected normalization issue state changed"):
        with database:
            database.execute("BEGIN IMMEDIATE")
            reset(database, "EXP-test")
    database.set_authorizer(None)
    assert _dump(database) == before
