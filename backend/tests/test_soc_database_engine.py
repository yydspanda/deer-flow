"""File SQLite must tolerate the SOC reader/worker topology without data changes."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from soc_agent.db import create_soc_engine


@pytest.fixture
def patched_sqlite(monkeypatch):
    # Exercise the patched-runtime policy on isolated databases even when the
    # test runner embeds an older SQLite. This does not certify that runtime.
    import sqlite3

    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 3))


def test_existing_database_keeps_records_and_uses_wal_on_every_connection(tmp_path, patched_sqlite):
    import sqlite3

    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE old_records (value TEXT)")
        connection.execute("INSERT INTO old_records VALUES ('reviewed memory')")
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    engine = create_soc_engine(f"sqlite:///{path}?timeout=0.001")
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
                assert connection.exec_driver_sql("SELECT value FROM old_records").scalar_one() == "reviewed memory"
                # Keep the driver's legacy transaction behavior and durability policy.
                assert not connection.connection.driver_connection.in_transaction
                assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "sqlite:///file::memory:?cache=shared&uri=true", "sqlite:///file:soc-memory?mode=memory&cache=shared&uri=true"])
def test_in_memory_database_remains_supported(url):
    engine = create_soc_engine(url)
    try:
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "memory"
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
            connection.exec_driver_sql("CREATE TABLE example (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO example VALUES (1)")
            assert connection.exec_driver_sql("SELECT value FROM example").scalar_one() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("query", ["mode=ro", "immutable=1"])
def test_existing_read_only_database_does_not_switch_journal_mode(tmp_path, query):
    import sqlite3

    path = tmp_path / "read-only.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE evidence (value INTEGER)")
        connection.execute("INSERT INTO evidence VALUES (7)")
    engine = create_soc_engine(f"sqlite:///file:{path}?{query}&uri=true")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
            assert connection.exec_driver_sql("SELECT value FROM evidence").scalar_one() == 7
            with pytest.raises(OperationalError, match="readonly"):
                connection.exec_driver_sql("INSERT INTO evidence VALUES (8)")
    finally:
        engine.dispose()


def test_independent_engine_writer_can_commit_while_reader_keeps_snapshot(tmp_path, patched_sqlite):
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    reader_engine, writer_engine = create_soc_engine(url), create_soc_engine(url)
    try:
        with reader_engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE values_table (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO values_table VALUES (1)")
        with reader_engine.connect() as reader:
            reader.exec_driver_sql("BEGIN")
            assert reader.exec_driver_sql("SELECT value FROM values_table").scalar_one() == 1
            with writer_engine.begin() as writer:
                writer.exec_driver_sql("UPDATE values_table SET value=2")
            assert reader.exec_driver_sql("SELECT value FROM values_table").scalar_one() == 1
            reader.rollback()
            assert reader.exec_driver_sql("SELECT value FROM values_table").scalar_one() == 2
    finally:
        reader_engine.dispose()
        writer_engine.dispose()


def test_independent_writers_wait_for_short_transaction_without_losing_writes(tmp_path):
    url = f"sqlite:///{tmp_path / 'writers.db'}"
    first_engine, second_engine = create_soc_engine(url), create_soc_engine(url)
    attempted = Event()
    try:
        with first_engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE counter (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO counter VALUES (0)")

        @event.listens_for(second_engine, "before_cursor_execute")
        def observe_write(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("UPDATE"):
                attempted.set()

        def write():
            with second_engine.begin() as connection:
                connection.exec_driver_sql("UPDATE counter SET value=value+1")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with first_engine.begin() as connection:
                connection.exec_driver_sql("UPDATE counter SET value=value+1")
                future = pool.submit(write)
                assert attempted.wait(2), "second writer did not reach its write"
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.1)
            future.result(timeout=3)
        with first_engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT value FROM counter").scalar_one() == 2
    finally:
        first_engine.dispose()
        second_engine.dispose()


def test_failed_connection_initialization_closes_the_dbapi_connection(tmp_path, monkeypatch, patched_sqlite):
    import sqlite3

    path = tmp_path / "initialization.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE retained (value INTEGER)")
    engine = create_soc_engine(f"sqlite:///{path}")
    connections = []
    original_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def open_read_only(*_args, **_kwargs):
        connection = original_connect(f"file:{path}?mode=ro", uri=True, factory=TrackedConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(engine.dialect.loaded_dbapi, "connect", open_read_only)
    try:
        with pytest.raises(OperationalError, match="readonly"):
            engine.connect()
        assert len(connections) == 1
        assert connections[0].closed
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "version,expected_mode",
    [
        ((3, 44, 5), "delete"),
        ((3, 44, 6), "wal"),
        ((3, 44, 7), "wal"),
        ((3, 45, 3), "delete"),
        ((3, 46, 0), "delete"),
        ((3, 50, 6), "delete"),
        ((3, 50, 7), "wal"),
        ((3, 50, 8), "wal"),
        ((3, 51, 2), "delete"),
        ((3, 51, 3), "wal"),
        ((3, 52, 0), "wal"),
    ],
)
def test_wal_requires_the_upstream_fix_and_older_runtimes_keep_existing_data(tmp_path, monkeypatch, caplog, version, expected_mode):
    import sqlite3

    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    path = tmp_path / "retained.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE retained (value TEXT)")
        connection.execute("INSERT INTO retained VALUES ('reviewed memory')")
    engine = create_soc_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == expected_mode
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
                assert connection.exec_driver_sql("SELECT value FROM retained").scalar_one() == "reviewed memory"
            first.exec_driver_sql("INSERT INTO retained VALUES ('new result')")
            first.commit()
        warnings = [record.message for record in caplog.records if "WAL-reset" in record.message]
        assert len(warnings) == (1 if expected_mode == "delete" else 0)
    finally:
        engine.dispose()


def test_old_runtime_rejects_existing_wal_without_converting_or_losing_records(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 45, 3))
    path = tmp_path / "already-wal.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE retained (value TEXT)")
        connection.execute("INSERT INTO retained VALUES ('reviewed memory')")
    engine = create_soc_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(OperationalError, match="WAL-reset.*3.51.3"):
            engine.connect()
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("SELECT value FROM retained").fetchone()[0] == "reviewed memory"
    finally:
        engine.dispose()


def test_postgresql_keeps_existing_engine_options(monkeypatch):
    from soc_agent.db import engine as module

    calls = []
    sentinel = object()

    def create(url, **kwargs):
        calls.append((url, kwargs))
        return sentinel

    monkeypatch.setattr(module, "create_engine", create)
    assert create_soc_engine("postgresql+asyncpg://user@localhost/soc") is sentinel
    assert calls == [("postgresql+psycopg://user@localhost/soc", {"pool_pre_ping": True})]
