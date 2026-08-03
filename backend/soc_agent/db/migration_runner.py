"""Alembic runner for SOC-owned database tables."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from soc_agent.db.config import to_sync_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def upgrade_soc_schema(database_url: str, revision: str = "head") -> None:
    """Upgrade SOC schema to the requested Alembic revision."""

    _ensure_sqlite_parent(database_url)
    command.upgrade(_alembic_config(database_url), revision)


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(to_sync_database_url(database_url))
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", to_sync_database_url(database_url))
    return config
