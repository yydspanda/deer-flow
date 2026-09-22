"""Isolated SQLite contention must retry persistence, never Runtime/model work."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from soc_agent.contracts import AnalysisRequestJournalStatus, AnalysisRun, AnalysisRunStatus, DecisionReviewReason
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.engine import create_soc_engine
from soc_agent.pipeline.analyzer import StubLLMAnalyzer


class CountingAnalyzer(StubLLMAnalyzer):
    def __init__(self):
        self.calls = 0

    def analyze(self, request):
        self.calls += 1
        return super().analyze(request)


class ReviewRuntime:
    """Exercise all four bundle rows with a deterministic, non-network analyzer."""

    def __init__(self, analyzer=None):
        self.analyzer = analyzer or CountingAnalyzer()
        self.runtime = DeterministicAnalysisRuntime(analyzer=self.analyzer)
        self.run = None

    def analyze(self, payload):
        return self.runtime.analyze(payload)

    def analyze_journaled(self, payload, *, before_provider):
        run = self.runtime.analyze_journaled(payload, before_provider=before_provider)
        assert run.decision is not None
        run.decision = run.decision.model_copy(update={"needs_review": True, "review_reasons": [DecisionReviewReason.FACT_CONFLICT]})
        run.status = AnalysisRunStatus.NEEDS_REVIEW
        self.run = run
        return run


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "contention.sqlite"
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0.01})
    create_soc_tables(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    sessions = []
    rolled_back = []

    @contextmanager
    def session_factory():
        with factory() as session:
            sessions.append(session)
            event.listen(session, "after_rollback", lambda current: rolled_back.append(current))
            yield session

    repository = SqlAlchemyAlertRepository(session_factory)
    yield path, engine, repository, sessions, rolled_back
    engine.dispose()


def _analyze(repository, runtime, payload=None):
    if payload is None:
        payload = json.loads((Path(__file__).resolve().parents[1] / "samples/alerts/pingan_legacy_apt.json").read_text(encoding="utf-8"))
    return SocAnalysisService(
        runtime=runtime,
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
    ).analyze(payload)


@contextmanager
def _competing_writer(path, engine, *, release_after_failure):
    """Acquire a real competing writer after SELECT, before the final UPDATE.

    SQLAlchemy's error hook releases the writer only after sqlite has rejected
    the first UPDATE. No timing assumptions or background thread are needed.
    """
    writer = sqlite3.connect(path, timeout=0.01)
    failures = []
    acquired = False

    def before_update(connection, cursor, statement, parameters, context, executemany):
        nonlocal acquired
        if not acquired and statement.startswith("UPDATE soc_analysis_runs"):
            writer.execute("BEGIN IMMEDIATE")
            acquired = True

    def on_error(context):
        error = context.original_exception
        if isinstance(error, sqlite3.OperationalError) and error.sqlite_errorcode == sqlite3.SQLITE_BUSY:
            failures.append(error)
            if release_after_failure:
                writer.rollback()

    event.listen(engine, "before_cursor_execute", before_update)
    event.listen(engine, "handle_error", on_error)
    try:
        yield failures
    finally:
        writer.rollback()
        writer.close()
        event.remove(engine, "before_cursor_execute", before_update)
        event.remove(engine, "handle_error", on_error)


def _assert_bundle(repository, run):
    assert repository.get_run(run.run_id) == run
    assert repository.get_alert_summary(run.run_id).status is AnalysisRunStatus.NEEDS_REVIEW
    items = repository.list_review_items(limit=10)
    assert len(items) == 1
    assert items[0].run_id == run.run_id
    audits = repository.list_audit_records(run.run_id)
    assert len(audits) == 1
    assert audits[0].run_id == run.run_id


def _assert_journal_only(repository, run_id):
    saved = repository.get_run(run_id)
    assert saved.status is AnalysisRunStatus.RUNNING
    assert saved.request_journal.status is AnalysisRequestJournalStatus.RUNNING
    assert repository.get_alert_summary(run_id) is None
    assert repository.list_review_items(limit=10) == []
    assert repository.list_audit_records(run_id) == []


def test_save_run_retries_busy_with_a_fresh_rolled_back_session(store):
    path, engine, repository, sessions, rolled_back = store
    run = AnalysisRun(run_id="RUN-CONTENDED", alert_id="ALERT-CONTENDED", status=AnalysisRunStatus.RUNNING, input_payload={})
    repository.save_run(run)
    sessions.clear()
    run.status = AnalysisRunStatus.SUCCESS

    with _competing_writer(path, engine, release_after_failure=True) as failures:
        repository.save_run(run)

    assert len(failures) == 1
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert rolled_back == [sessions[0]]
    assert repository.get_run(run.run_id) == run


def test_analysis_bundle_retries_autoflush_busy_without_repeating_model(store):
    path, engine, repository, sessions, rolled_back = store
    runtime = ReviewRuntime()
    with _competing_writer(path, engine, release_after_failure=True) as failures:
        run = _analyze(repository, runtime)

    assert len(failures) == 1
    assert runtime.analyzer.calls == 1
    assert len(rolled_back) == 1
    failed_session = rolled_back[0]
    assert sessions[sessions.index(failed_session) + 1] is not failed_session
    assert run.request_journal.status is AnalysisRequestJournalStatus.COMPLETED
    _assert_bundle(repository, run)


def test_persistent_busy_is_bounded_and_preserves_running_journal(store):
    path, engine, repository, sessions, rolled_back = store
    runtime = ReviewRuntime()
    with _competing_writer(path, engine, release_after_failure=False) as failures:
        with pytest.raises(OperationalError, match="database is locked"):
            _analyze(repository, runtime)

    assert len(failures) == 3
    assert len(rolled_back) == 3
    assert len({id(session) for session in rolled_back}) == 3
    assert runtime.analyzer.calls == 1
    _assert_journal_only(repository, runtime.run.run_id)


def test_busy_at_audit_insert_rolls_back_the_whole_bundle_before_retry(store):
    _path, engine, repository, _sessions, rolled_back = store
    runtime = ReviewRuntime()
    attempts = []
    rolled_back_bundles = []

    def inspect_rollback(session):
        _assert_journal_only(repository, runtime.run.run_id)
        rolled_back_bundles.append(runtime.run.run_id)

    def fail_first_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO soc_decision_audit_log"):
            attempts.append(statement)
            if len(attempts) == 1:
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise OperationalError(statement, parameters, error)

    event.listen(engine, "before_cursor_execute", fail_first_audit)
    event.listen(Session, "after_rollback", inspect_rollback)
    try:
        run = _analyze(repository, runtime)
    finally:
        event.remove(engine, "before_cursor_execute", fail_first_audit)
        event.remove(Session, "after_rollback", inspect_rollback)

    assert len(attempts) == 2
    assert len(rolled_back) == 1
    assert rolled_back_bundles == [run.run_id]
    assert runtime.analyzer.calls == 1
    _assert_bundle(repository, run)


def test_caller_owned_transaction_propagates_busy_without_partial_retry(store):
    path, engine, repository, _sessions, rolled_back = store
    run = AnalysisRun(run_id="RUN-OWNED", alert_id="ALERT-OWNED", status=AnalysisRunStatus.RUNNING, input_payload={})
    repository.save_run(run)
    run.status = AnalysisRunStatus.SUCCESS

    with _competing_writer(path, engine, release_after_failure=True) as failures:
        with pytest.raises(OperationalError, match="database is locked"):
            with repository.mutation_transaction() as transaction:
                transaction.save_run(run)

    assert len(failures) == 1
    assert len(rolled_back) == 1
    assert repository.get_run(run.run_id).status is AnalysisRunStatus.RUNNING


@pytest.mark.parametrize("error_code", [sqlite3.SQLITE_BUSY_SNAPSHOT, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_LOCKED_SHAREDCACHE])
def test_extended_sqlite_contention_codes_retry_only_persistence(store, error_code):
    _path, engine, repository, sessions, rolled_back = store
    run = AnalysisRun(run_id="RUN-EXTENDED", alert_id="ALERT-EXTENDED", status=AnalysisRunStatus.RUNNING, input_payload={})
    attempts = []

    def fail_first_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO soc_analysis_runs"):
            attempts.append(statement)
            if len(attempts) == 1:
                error = sqlite3.OperationalError("synthetic contention")
                error.sqlite_errorcode = error_code
                raise OperationalError(statement, parameters, error)

    event.listen(engine, "before_cursor_execute", fail_first_insert)
    try:
        repository.save_run(run)
    finally:
        event.remove(engine, "before_cursor_execute", fail_first_insert)

    assert len(attempts) == 2
    assert len(sessions) == 2
    assert rolled_back == [sessions[0]]
    assert repository.get_run(run.run_id) == run


@pytest.mark.parametrize("dialect_name,error_code", [("sqlite", sqlite3.SQLITE_IOERR), ("sqlite", sqlite3.SQLITE_CORRUPT), ("postgresql", sqlite3.SQLITE_BUSY)])
def test_non_retryable_or_other_dialect_errors_are_not_replayed(store, monkeypatch, dialect_name, error_code):
    _path, engine, repository, sessions, _rolled_back = store
    run = AnalysisRun(run_id="RUN-FAIL-FAST", alert_id="ALERT-FAIL-FAST", status=AnalysisRunStatus.RUNNING, input_payload={})
    attempts = []
    # Only spoof dialect classification; this is not PostgreSQL integration evidence.
    monkeypatch.setattr(engine.dialect, "name", dialect_name)

    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO soc_analysis_runs"):
            attempts.append(statement)
            error = sqlite3.OperationalError("non-retryable test error")
            error.sqlite_errorcode = error_code
            raise OperationalError(statement, parameters, error)

    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(OperationalError, match="non-retryable test error"):
            repository.save_run(run)
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)

    assert len(attempts) == 1
    assert len(sessions) == 1
    assert repository.get_run(run.run_id) is None


def test_eight_overlapping_analyses_persist_large_bundles_with_an_open_reader(tmp_path):
    path = tmp_path / "eight-workers.sqlite"
    engine = create_soc_engine(f"sqlite:///{path}")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    original = AnalysisRun(run_id="RUN-OLD", alert_id="ALERT-OLD", status=AnalysisRunStatus.SUCCESS, input_payload={"retained": "old result"})
    repository.save_run(original)
    barrier = Barrier(8)

    class ConcurrentAnalyzer(CountingAnalyzer):
        def analyze(self, request):
            barrier.wait(timeout=30)
            return super().analyze(request)

    runtimes = [ReviewRuntime(ConcurrentAnalyzer()) for _ in range(8)]
    payload = json.loads((Path(__file__).resolve().parents[1] / "samples/alerts/approved_scanner.json").read_text(encoding="utf-8"))

    def analyze(index):
        return _analyze(repository, runtimes[index], {**payload, "alert_id": f"ALERT-PARALLEL-{index}", "replay_evidence": "x" * 500_000})

    reader = sqlite3.connect(path, timeout=0.01)
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM soc_analysis_runs").fetchone()[0] == 1
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(analyze, index) for index in range(8)]
            runs = [future.result(timeout=45) for future in futures]
        # The original read snapshot stays usable while all eight writers finish.
        assert reader.execute("SELECT count(*) FROM soc_analysis_runs").fetchone()[0] == 1
        assert len({run.run_id for run in runs}) == 8
        assert [runtime.analyzer.calls for runtime in runtimes] == [1] * 8
        for run in runs:
            assert len(run.input_payload["replay_evidence"]) == 500_000
            assert repository.get_run(run.run_id) == run
            assert repository.get_alert_summary(run.run_id).status is AnalysisRunStatus.NEEDS_REVIEW
            assert len(repository.list_audit_records(run.run_id)) == 1
        assert len(repository.list_review_items(limit=20)) == 8
        assert len(repository.list_runs(limit=20)) == 9
        assert repository.get_run(original.run_id) == original
    finally:
        reader.close()
        engine.dispose()
