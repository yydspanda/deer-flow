from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "backend/scripts/soc_pingan_legacy_live_acceptance.py"
    spec = importlib.util.spec_from_file_location(
        "soc_pingan_legacy_live_acceptance_cli",
        script,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_acceptance_cli_prefers_resolved_host_dev_database_path(
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    database_path = tmp_path / "soc_agent_dev.db"

    database_url = module.resolve_acceptance_database_url(
        None,
        environ={"SOC_DEV_SQLITE_PATH": str(database_path)},
    )

    assert database_url == f"sqlite+pysqlite:///{database_path}"


def test_live_acceptance_cli_database_preflight_requires_soc_migration(
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    database_path = tmp_path / "soc_agent_dev.db"
    sqlite3.connect(database_path).close()
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="soc_alembic_version"):
        module.assert_acceptance_database_ready(engine)

    engine.dispose()


def test_live_acceptance_cli_database_preflight_accepts_migrated_database(
    tmp_path: Path,
) -> None:
    module = _load_script_module()
    database_path = tmp_path / "soc_agent_dev.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE soc_alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute(
            "INSERT INTO soc_alembic_version(version_num) VALUES (?)",
            ("0027_processing_jobs",),
        )
        for table_name in (
            "soc_processing_jobs",
            "soc_processing_job_events",
            "soc_callback_outbox",
            "soc_callback_attempts",
        ):
            connection.execute(f"CREATE TABLE {table_name} (id VARCHAR(64))")
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")

    assert module.assert_acceptance_database_ready(engine) == "0027_processing_jobs"

    engine.dispose()
