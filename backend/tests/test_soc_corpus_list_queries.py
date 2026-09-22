"""Synthetic query-capacity tests: no corpus data, Provider calls, or business writes."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from time import perf_counter

from sqlalchemy import create_engine, event, insert, inspect, update
from sqlalchemy.orm import sessionmaker

from soc_agent.db import create_soc_tables
from soc_agent.db.corpus_lists import EMPTY_REVISION, CorpusListSummary, SocCorpusListQueries
from soc_agent.db.migration_runner import upgrade_soc_schema
from soc_agent.db.models import SocAnalysisRunRow, SocCorpusListProjectionRow


def _row(index):
    summary = CorpusListSummary(
        alert_id=str(index),
        group_id="group",
        behavior_fingerprint="fingerprint",
        decision_eligible=True,
        readiness="candidate_window",
        workflow_state="completed",
        observed=True,
        decision_available=True,
        base_label_comparison="mismatched",
        effective_label_comparison="matched",
    )
    return {
        "catalog_id": "catalog",
        "alert_id": str(index),
        "input_hash": f"input-{index}",
        "sequence_number": index,
        "source_revision": EMPTY_REVISION,
        "search_text": f"{index}\n规则_100%",
        "group_id": "group",
        "source_type": "ndr",
        "labeled": True,
        "projection_payload": asdict(summary),
    }


def test_query_more_than_ten_thousand_results_has_exact_filters_and_pages(tmp_path, record_property):
    engine = create_engine(f"sqlite:///{tmp_path / 'capacity.sqlite'}")
    create_soc_tables(engine)
    queries = SocCorpusListQueries(sessionmaker(engine))
    queries.insert_missing([_row(index) for index in range(10_005)])
    # Re-registration must not overwrite an existing projection.
    queries.insert_missing([_row(0)])
    args = dict(search=None, readiness=None, source_type=None, group_id=None, comparison="matched", unprocessed_only=False, focus_alert_id=None, active_alert_ids=[], limit=20, offset=10_000)
    started = perf_counter()
    total, ids = queries.page("catalog", **args)
    elapsed = perf_counter() - started
    record_property("synthetic_result_count", 10_005)
    record_property("page_query_ms", round(elapsed * 1000, 3))
    record_property("model_calls", 0)
    assert total == 10_005
    assert ids == [str(index) for index in range(10_000, 10_005)]
    assert elapsed < 2
    assert len(queries.rows("catalog")) == 10_005
    assert queries.page("catalog", **{**args, "search": "规则_100%", "offset": 0})[0] == 10_005
    assert queries.page("catalog", **{**args, "search": "%", "offset": 0})[0] == 10_005
    assert queries.page("catalog", **{**args, "search": "nonexistent%", "offset": 0})[0] == 0
    assert queries.page("catalog", **{**args, "unprocessed_only": True, "offset": 0})[0] == 0
    assert queries.page("catalog", **{**args, "unprocessed_only": True, "offset": 0, "active_alert_ids": ["0"]}) == (1, ["0"])
    assert queries.page("catalog", **{**args, "unprocessed_only": True, "offset": 0, "focus_alert_id": "1"}) == (1, ["1"])
    old = queries.rows("catalog")["0"][1]
    queries.save("catalog", "0", "changed", replace(old, effective_label_comparison="mismatched"))
    assert queries.page("catalog", **args)[0] == 10_004


def test_batch_filter_is_applied_before_count_pagination_and_focus(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'batches.sqlite'}")
    create_soc_tables(engine)
    queries = SocCorpusListQueries(sessionmaker(engine))
    rows = [_row(i) for i in range(20)]
    for i, row in enumerate(rows):
        row["projection_payload"].update(batch="learning" if i < 10 else "validation", validation_tier=None if i < 10 else "main" if i < 18 else "supplementary")
    queries.insert_missing(rows)
    args = dict(search=None, readiness=None, source_type=None, group_id=None, comparison=None, unprocessed_only=False, focus_alert_id="0", active_alert_ids=[], limit=3, offset=1)
    assert queries.page("catalog", batch="validation", validation_tier="main", **args) == (8, ["11", "12", "13"])
    assert queries.page("catalog", batch="validation", validation_tier="supplementary", **{**args, "offset": 0}) == (2, ["18", "19"])
    assert queries.page("catalog", **{**args, "offset": 0}) == (20, ["0", "1", "2"])


def test_source_revision_excludes_unrelated_payloads_and_detects_run_updates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'revisions.sqlite'}")
    create_soc_tables(engine)
    queries = SocCorpusListQueries(sessionmaker(engine))
    queries.insert_missing([_row(0)])
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(SocAnalysisRunRow),
            [
                {
                    "run_id": f"run-{index}",
                    "alert_id": str(index),
                    "input_hash": f"input-{index}",
                    "status": "completed",
                    "pipeline_version": "test",
                    "model_name": "not-invoked",
                    "prompt_version": "test",
                    "started_at": now,
                    "created_at": now,
                    "updated_at": now,
                    "run_payload": {"invalid_on_purpose": "x" * 1024},
                }
                for index in range(10_001)
            ],
        )
    revisions = queries.source_revisions("catalog", tenant_id="tenant", environment="dev")
    assert set(revisions) == {"0"}
    with engine.begin() as connection:
        connection.execute(update(SocAnalysisRunRow).where(SocAnalysisRunRow.run_id == "run-0").values(updated_at=now + timedelta(seconds=1)))
    assert revisions != queries.source_revisions("catalog", tenant_id="tenant", environment="dev")


def test_corpus_projection_migration_preserves_existing_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrate.sqlite'}")
    url = str(engine.url)
    upgrade_soc_schema(url, revision="0027_processing_jobs")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE retained_marker (value TEXT)")
        connection.exec_driver_sql("INSERT INTO retained_marker VALUES ('preserved')")
    upgrade_soc_schema(url)
    upgrade_soc_schema(url)
    assert "soc_corpus_list_projections" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT value FROM retained_marker").scalar_one() == "preserved"
    actual = {column["name"] for column in inspect(engine).get_columns("soc_corpus_list_projections")}
    assert actual == set(SocCorpusListProjectionRow.__table__.columns.keys())


def test_run_status_filters_all_pages_and_active_reruns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'statuses.sqlite'}")
    create_soc_tables(engine)
    queries = SocCorpusListQueries(sessionmaker(engine))
    states = ["completed", "analysis_only", "failed", "running", "ready", "completed"]
    rows = [_row(i) for i in range(len(states))]
    for row, state in zip(rows, states, strict=True):
        row["projection_payload"].update(workflow_state=state)
        row["search_text"] = f"alert:{row['alert_id']}:end"
    queries.insert_missing(rows)
    args = dict(search=None, readiness=None, source_type=None, group_id=None, comparison=None, unprocessed_only=False, focus_alert_id=None, active_alert_ids=["5"], limit=1, offset=0)
    assert queries.page("catalog", run_status="success", **args) == (2, ["0"])
    assert queries.page("catalog", run_status="success", **{**args, "offset": 1}) == (2, ["1"])
    assert queries.page("catalog", run_status="running", **{**args, "limit": 20}) == (2, ["3", "5"])
    assert queries.page("catalog", run_status="failed", **args) == (1, ["2"])
    assert queries.page("catalog", run_status="not_run", **args) == (1, ["4"])
    assert queries.page("catalog", run_status="success", **{**args, "search": "alert:1:end"}) == (1, ["1"])
    # A job can fail before it writes a Runtime run, or after a prior success.
    # Durable activity wins even if a failure snapshot was read concurrently.
    failures = {**args, "failed_alert_ids": ["0", "3", "4", "5"]}
    assert queries.page("catalog", run_status="failed", **failures) == (4, ["0"])
    assert queries.page("catalog", run_status="failed", **{**failures, "offset": 3}) == (4, ["4"])
    assert queries.page("catalog", run_status="running", **failures) == (1, ["5"])
    assert queries.page("catalog", run_status="success", **failures) == (1, ["1"])
    assert queries.page("catalog", run_status="not_run", **failures) == (0, [])


def test_new_queued_attempts_replace_old_outcomes_before_filters_and_pagination(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'queued.sqlite'}")
    import sqlite3

    @event.listens_for(engine, "connect")
    def mac_parameter_limit(connection, _):
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 32_766)

    create_soc_tables(engine)
    queries = SocCorpusListQueries(sessionmaker(engine))
    rows = [_row(i) for i in range(12_284)]
    rows[1]["projection_payload"]["workflow_state"] = "failed"
    rows[2]["projection_payload"]["workflow_state"] = "running"
    queries.insert_missing(rows)
    args = dict(search=None, readiness=None, source_type=None, group_id=None, comparison=None, unprocessed_only=False, focus_alert_id=None, active_alert_ids=["0"], queued_alert_ids=[str(i) for i in range(12_284)], limit=20, offset=12_280)
    assert queries.page("catalog", run_status="not_run", **args) == (12_283, ["12281", "12282", "12283"])
    assert queries.page("catalog", run_status="failed", **args) == (0, [])
    assert queries.page("catalog", run_status="success", **args) == (0, [])
    assert queries.page("catalog", run_status="running", **{**args, "offset": 0}) == (1, ["0"])
    # Combined filters must bind the large pending set once, staying below the
    # deployed SQLite parameter limit while disregarding old matched decisions.
    assert queries.page("catalog", run_status="not_run", **{**args, "comparison": "not_run", "unprocessed_only": True})[0] == 12_283
    assert queries.page("catalog", **{**args, "comparison": "matched"}) == (0, [])
    readiness = {"queued_readiness": {str(i): "singleton_strong" for i in range(12_284)}}
    assert queries.page("catalog", run_status="not_run", **{**args, **readiness, "readiness": "singleton_strong", "comparison": "not_run", "unprocessed_only": True})[0] == 12_283
    assert queries.page("catalog", **{**args, **readiness, "readiness": "candidate_window"}) == (0, [])
