"""Synthetic large-record revision reads and non-destructive index upgrades."""

from datetime import UTC, datetime, timedelta
from time import perf_counter

from sqlalchemy import create_engine, event, insert, inspect, select, update
from sqlalchemy.orm import sessionmaker
from test_soc_corpus_list_queries import _row

from soc_agent.db import create_soc_tables
from soc_agent.db.corpus_lists import SocCorpusListQueries
from soc_agent.db.migration_runner import upgrade_soc_schema
from soc_agent.db.models import SocAnalysisRunRow as Run
from soc_agent.db.models import SocDecisionTransitionRow as Transition
from soc_agent.db.models import SocMemoryPatternObservationRow as Observation

INDEX_NAME = "ix_soc_analysis_runs_corpus_revision"
INDEX_COLUMNS = ["alert_id", "input_hash", "run_id", "started_at", "updated_at", "status"]
NOW = datetime(2026, 9, 22, tzinfo=UTC)


def _run(index, *, payload_size=0):
    return {
        "run_id": f"run-{index}",
        "alert_id": str(index),
        "input_hash": f"input-{index}",
        "status": "completed",
        "pipeline_version": "test",
        "model_name": "not-invoked",
        "prompt_version": "test",
        "started_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "run_payload": {"synthetic": "x" * payload_size},
    }


def _observation(index, *, tenant="tenant", environment="dev"):
    return {
        "observation_id": f"observation-{index}",
        "idempotency_key": f"observation-{index}",
        "aggregation_key": "cohort",
        "lineage_key": "lineage",
        "content_hash": f"content-{index}",
        "tenant_id": tenant,
        "environment": environment,
        "data_class": "synthetic",
        "source_type": "alert",
        "source_id": f"source-{index}",
        "run_id": "run-0",
        "alert_id": "0",
        "pattern_dimension": "behavior",
        "pattern_value": "synthetic",
        "mocked": True,
        "observed_at": NOW,
        "window_start": NOW,
        "window_end": NOW,
        "created_at": NOW,
        "observation_payload": {"retained": "old observation"},
    }


def _transition():
    return {
        "transition_id": "transition-0",
        "transition_key": "transition-0",
        "run_id": "run-0",
        "alert_id": "0",
        "tenant_id": "tenant",
        "before_verdict": "suspicious",
        "after_verdict": "benign",
        "before_needs_review": True,
        "after_needs_review": False,
        "transition_kind": "memory",
        "policy_id": "test",
        "policy_version": "1",
        "policy_hash": "test",
        "created_by_actor_id": "test",
        "created_at": NOW,
        "transition_payload": {"retained": "old transition"},
    }


def test_large_run_revisions_use_covering_index_without_payload_table_reads(tmp_path, record_property):
    database = tmp_path / "large-revisions.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    create_soc_tables(engine)
    queries = SocCorpusListQueries(sessionmaker(engine))
    count, payload_size = 200, 500_000
    queries.insert_missing([_row(index) for index in range(count)])
    for start in range(0, count, 10):
        with engine.begin() as connection:
            connection.execute(insert(Run), [_run(index, payload_size=payload_size) for index in range(start, start + 10)])

    statements = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        if statement.startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        started = perf_counter()
        revisions = queries.source_revisions("catalog", tenant_id="tenant", environment="dev")
        elapsed = perf_counter() - started
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(revisions) == count
    run_statements = [(sql, parameters) for sql, parameters in statements if "soc_analysis_runs" in sql]
    assert len(run_statements) == 2
    with engine.connect() as connection:
        for statement, parameters in run_statements:
            plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
            # updated_at follows the large run_payload in SQLite records. Merely
            # omitting JSON from SELECT still traverses its overflow pages unless
            # all revision columns are supplied by the covering index.
            assert any(f"COVERING INDEX {INDEX_NAME}" in row[3] for row in plan), plan
            assert "run_payload" not in statement
    record_property("synthetic_run_count", count)
    record_property("synthetic_payload_bytes_per_run", payload_size)
    record_property("database_bytes", database.stat().st_size)
    record_property("revision_query_ms", round(elapsed * 1000, 3))
    record_property("model_calls", 0)
    # Real deployment timing depends on its storage; query-plan coverage is the
    # deterministic performance contract, rather than a workstation time limit.
    engine.dispose()


def test_revision_index_upgrade_preserves_0031_business_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.sqlite'}")
    upgrade_soc_schema(str(engine.url), revision="0031_memory_working_drafts")
    with engine.begin() as connection:
        connection.execute(insert(Run), [_run(0, payload_size=500_000)])
        connection.execute(insert(Observation), [_observation(0)])
        connection.execute(insert(Transition), [_transition()])
        connection.exec_driver_sql("CREATE TABLE retained_marker (value TEXT)")
        connection.exec_driver_sql("INSERT INTO retained_marker VALUES ('existing application data')")
        before = {table.__tablename__: connection.execute(select(table)).mappings().all() for table in (Run, Observation, Transition)}
    old_schema = inspect(engine)
    before_tables = set(old_schema.get_table_names())
    before_indexes = {item["name"] for item in old_schema.get_indexes(Run.__tablename__)}
    assert INDEX_NAME not in before_indexes

    upgrade_soc_schema(str(engine.url))
    upgrade_soc_schema(str(engine.url))

    new_schema = inspect(engine)
    assert set(new_schema.get_table_names()) == before_tables
    indexes = {item["name"]: item for item in new_schema.get_indexes(Run.__tablename__)}
    assert set(indexes) == before_indexes | {INDEX_NAME}
    assert indexes[INDEX_NAME]["column_names"] == INDEX_COLUMNS
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM soc_alembic_version").scalar_one() == "0032_corpus_revision_index"
        assert connection.exec_driver_sql("SELECT value FROM retained_marker").scalar_one() == "existing application data"
        after = {table.__tablename__: connection.execute(select(table)).mappings().all() for table in (Run, Observation, Transition)}
    assert after == before
    engine.dispose()


def test_indexed_revisions_keep_late_changes_and_scope_boundaries(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'freshness.sqlite'}")
    create_soc_tables(engine)
    queries = SocCorpusListQueries(sessionmaker(engine))
    queries.insert_missing([_row(0), _row(1)])
    with engine.begin() as connection:
        connection.execute(insert(Run), [_run(0), _run(1)])

    def revisions():
        return queries.source_revisions("catalog", tenant_id="tenant", environment="dev")

    before = revisions()
    with engine.begin() as connection:
        connection.execute(update(Run).where(Run.run_id == "run-0").values(updated_at=NOW + timedelta(seconds=1)))
    after_update = revisions()
    assert after_update["0"] != before["0"]
    assert after_update["1"] == before["1"]
    with engine.begin() as connection:
        connection.execute(update(Run).where(Run.run_id == "run-0").values(status="failed"))
    after_status = revisions()
    assert after_status["0"] != after_update["0"]
    assert after_status["1"] == before["1"]
    with engine.begin() as connection:
        connection.execute(insert(Transition), [_transition()])
    after_transition = revisions()
    assert after_transition["0"] != after_status["0"]
    assert after_transition["1"] == before["1"]
    with engine.begin() as connection:
        connection.execute(insert(Observation), [_observation(0)])
    after_observation = revisions()
    assert after_observation["0"] != after_transition["0"]
    assert after_observation["1"] == before["1"]
    with engine.begin() as connection:
        connection.execute(insert(Run), [{**_run(0), "run_id": "foreign-input", "input_hash": "foreign-input"}])
        connection.execute(insert(Observation), [_observation(1, tenant="another-tenant"), _observation(2, environment="stg")])
    assert revisions() == after_observation
    engine.dispose()
