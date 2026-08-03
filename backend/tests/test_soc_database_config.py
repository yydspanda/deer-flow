from __future__ import annotations

from types import SimpleNamespace

import pytest

from soc_agent.db import upgrade_soc_schema
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
