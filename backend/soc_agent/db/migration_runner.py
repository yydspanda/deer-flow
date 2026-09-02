"""Alembic runner for SOC-owned database tables."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from soc_agent.db.config import to_sync_database_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
logger = logging.getLogger(__name__)


def upgrade_soc_schema(database_url: str, revision: str = "head") -> None:
    """Upgrade SOC schema to the requested Alembic revision."""

    sqlite_path = _sqlite_database_path(database_url)
    if sqlite_path is None:
        command.upgrade(_alembic_config(database_url), revision)
        return

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    disposable_initialization = not sqlite_path.exists()
    attempts = 2 if disposable_initialization else 1
    for attempt in range(attempts):
        try:
            command.upgrade(_alembic_config(database_url), revision)
            return
        except Exception as exc:
            if not disposable_initialization:
                raise
            if attempt + 1 >= attempts or not _is_transient_sqlite_io_error(exc):
                raise
            _remove_sqlite_artifacts(sqlite_path)
            logger.warning("retrying one clean SOC SQLite initialization after transient I/O failure")


def _sqlite_database_path(database_url: str) -> Path | None:
    url = make_url(to_sync_database_url(database_url))
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def _remove_sqlite_artifacts(database_path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(database_path) + suffix).unlink(missing_ok=True)


def _is_transient_sqlite_io_error(exc: Exception) -> bool:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if "disk i/o error" in str(current).lower():
            return True
        for nested in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", to_sync_database_url(database_url))
    return config
