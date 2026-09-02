from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from soc_agent.db import migration_runner, upgrade_soc_schema
from soc_agent.db.config import SOC_LOCAL_SQLITE_FILENAME, resolve_database_url


def _configure_deerflow_database(monkeypatch: pytest.MonkeyPatch, database: SimpleNamespace) -> None:
    monkeypatch.setattr(
        "deerflow.config.get_app_config",
        lambda: SimpleNamespace(database=database),
    )


def test_resolve_database_url_prefers_explicit_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOC_DATABASE_URL", "sqlite:////tmp/from-env.db")

    assert resolve_database_url("sqlite:////tmp/explicit.db") == "sqlite:////tmp/explicit.db"


def test_resolve_database_url_prefers_environment_over_deerflow_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("SOC_DATABASE_URL", "sqlite:////tmp/from-env.db")
    _configure_deerflow_database(
        monkeypatch,
        SimpleNamespace(backend="sqlite", sqlite_dir=str(tmp_path)),
    )

    assert resolve_database_url() == "sqlite:////tmp/from-env.db"


def test_resolve_database_url_uses_isolated_sqlite_for_deerflow_sqlite_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("SOC_DATABASE_URL", raising=False)
    _configure_deerflow_database(
        monkeypatch,
        SimpleNamespace(backend="sqlite", sqlite_dir=str(tmp_path)),
    )

    assert resolve_database_url() == f"sqlite:///{tmp_path / SOC_LOCAL_SQLITE_FILENAME}"


def test_resolve_database_url_uses_deerflow_postgres_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOC_DATABASE_URL", raising=False)
    _configure_deerflow_database(
        monkeypatch,
        SimpleNamespace(
            backend="postgres",
            postgres_url="postgresql://soc:secret@db.internal/soc",
        ),
    )

    assert resolve_database_url() == "postgresql://soc:secret@db.internal/soc"


def test_resolve_database_url_rejects_memory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOC_DATABASE_URL", raising=False)
    _configure_deerflow_database(
        monkeypatch,
        SimpleNamespace(backend="memory"),
    )

    with pytest.raises(ValueError, match=r"database\.backend=sqlite\|postgres"):
        resolve_database_url()


def test_upgrade_soc_schema_creates_missing_sqlite_parent(tmp_path) -> None:
    database_path = tmp_path / "missing" / "nested" / SOC_LOCAL_SQLITE_FILENAME

    upgrade_soc_schema(f"sqlite:///{database_path}")

    assert database_path.is_file()


def test_upgrade_soc_schema_retries_transient_io_for_new_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    database_path = tmp_path / SOC_LOCAL_SQLITE_FILENAME
    real_upgrade = migration_runner.command.upgrade
    attempts = 0

    def flaky_upgrade(config, revision):  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            with sqlite3.connect(database_path) as connection:
                connection.execute("CREATE TABLE partial_attempt(id INTEGER)")
            raise sqlite3.OperationalError("disk I/O error")
        return real_upgrade(config, revision)

    monkeypatch.setattr(migration_runner.command, "upgrade", flaky_upgrade)

    upgrade_soc_schema(f"sqlite:///{database_path}")

    assert attempts == 2
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        revision = connection.execute("SELECT version_num FROM soc_alembic_version").fetchone()[0]
    assert "partial_attempt" not in tables
    assert revision == "0027_processing_jobs"


def test_upgrade_soc_schema_never_retries_or_removes_existing_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    database_path = tmp_path / SOC_LOCAL_SQLITE_FILENAME
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE operator_data(id INTEGER)")
    attempts = 0

    def failing_upgrade(config, revision):  # noqa: ANN001, ARG001
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(migration_runner.command, "upgrade", failing_upgrade)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        upgrade_soc_schema(f"sqlite:///{database_path}")

    assert attempts == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'operator_data'").fetchone()[0] == "operator_data"


def test_upgrade_soc_schema_treats_preexisting_empty_sqlite_as_operator_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    database_path = tmp_path / SOC_LOCAL_SQLITE_FILENAME
    database_path.touch()
    attempts = 0

    def failing_upgrade(config, revision):  # noqa: ANN001, ARG001
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(migration_runner.command, "upgrade", failing_upgrade)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        upgrade_soc_schema(f"sqlite:///{database_path}")

    assert attempts == 1
    assert database_path.is_file()
