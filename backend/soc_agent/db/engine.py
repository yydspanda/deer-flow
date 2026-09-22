"""Shared synchronous SOC engines, including local SQLite concurrency policy."""

from __future__ import annotations

import sqlite3
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from soc_agent.db.config import to_sync_database_url

SQLITE_BUSY_TIMEOUT_SECONDS = 30


def create_soc_engine(database_url: str) -> Engine:
    """Keep short writes bounded while file-backed readers retain their snapshots.

    WAL changes only SQLite's journal mode: transaction ownership, synchronous
    durability, model concurrency, and PostgreSQL settings remain unchanged.
    """

    sync_url = to_sync_database_url(database_url)
    url = make_url(sync_url)
    if url.get_backend_name() != "sqlite":
        return create_engine(sync_url, pool_pre_ping=True)

    engine = create_engine(sync_url, pool_pre_ping=True, connect_args={"timeout": SQLITE_BUSY_TIMEOUT_SECONDS})
    file_backed = bool(url.database and url.database not in {":memory:", "file::memory:"} and url.query.get("mode") != "memory")
    read_only = url.query.get("mode") == "ro" or str(url.query.get("immutable", "")).lower() in {"1", "true", "yes"}

    @event.listens_for(engine, "connect")
    def configure(connection: Any, _record: Any) -> None:
        # Initial connect hooks can fail before the pool owns the connection.
        # Close it explicitly on failure rather than leaving a database handle.
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
                if file_backed and not read_only:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    if cursor.fetchone()[0].lower() != "wal":
                        raise sqlite3.OperationalError("SOC file SQLite requires WAL journal mode")
            finally:
                cursor.close()
        except BaseException:
            connection.close()
            raise

    return engine
