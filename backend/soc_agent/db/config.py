"""Database URL resolution for SOC persistence."""

from __future__ import annotations

import os


def resolve_database_url(explicit_url: str | None = None) -> str:
    """Resolve the SOC database URL.

    Resolution order:
    1. Explicit CLI/API argument.
    2. ``SOC_DATABASE_URL``.
    3. DeerFlow ``database.postgres_url`` when ``database.backend=postgres``.
    """

    if explicit_url:
        return explicit_url
    env_url = os.environ.get("SOC_DATABASE_URL")
    if env_url:
        return env_url
    config_url = _database_url_from_deerflow_config()
    if config_url:
        return config_url
    raise ValueError("database URL required; pass --database-url, set SOC_DATABASE_URL, or configure database.backend=postgres")


def to_sync_database_url(database_url: str) -> str:
    """Return a sync SQLAlchemy URL for SOC repository and migration code."""

    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def _database_url_from_deerflow_config() -> str | None:
    try:
        from deerflow.config import get_app_config
    except Exception:
        return None

    try:
        database = get_app_config().database
    except Exception:
        return None
    if database.backend != "postgres" or not database.postgres_url:
        return None
    return database.postgres_url
