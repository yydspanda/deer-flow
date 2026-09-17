"""Synthetic query-capacity tests: no corpus data, Provider calls, or business writes."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from time import perf_counter

from sqlalchemy import create_engine, insert, inspect, update
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
