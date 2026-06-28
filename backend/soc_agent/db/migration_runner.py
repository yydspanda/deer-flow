"""Alembic runner for SOC-owned database tables."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from soc_agent.db.config import to_sync_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def upgrade_soc_schema(database_url: str, revision: str = "head") -> None:
    """Upgrade SOC schema to the requested Alembic revision."""

    command.upgrade(_alembic_config(database_url), revision)


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", to_sync_database_url(database_url))
    return config
