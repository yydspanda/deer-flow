"""Shared synchronous SOC engines, including local SQLite concurrency policy."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from soc_agent.db.config import to_sync_database_url

SQLITE_BUSY_TIMEOUT_SECONDS = 30
logger = logging.getLogger(__name__)


def _wal_reset_fixed(version: tuple[int, ...]) -> bool:
    # https://sqlite.org/wal.html#the_wal_reset_bug
    return version >= (3, 51, 3) or (version[:2] == (3, 44) and version >= (3, 44, 6)) or (version[:2] == (3, 50) and version >= (3, 50, 7))


def create_soc_engine(database_url: str) -> Engine:
    """Keep short writes bounded while file-backed readers retain their snapshots.

    Patched SQLite runtimes enable WAL; older ones retain rollback journaling.
    Transaction ownership, synchronous durability, model concurrency, and
    PostgreSQL settings remain unchanged.
    """

    sync_url = to_sync_database_url(database_url)
    url = make_url(sync_url)
    if url.get_backend_name() != "sqlite":
        return create_engine(sync_url, pool_pre_ping=True)

    engine = create_engine(sync_url, pool_pre_ping=True, connect_args={"timeout": SQLITE_BUSY_TIMEOUT_SECONDS})
    file_backed = bool(url.database and url.database not in {":memory:", "file::memory:"} and url.query.get("mode") != "memory")
    read_only = url.query.get("mode") == "ro" or str(url.query.get("immutable", "")).lower() in {"1", "true", "yes"}
    safe_wal = _wal_reset_fixed(sqlite3.sqlite_version_info)
    if file_backed and not read_only and not safe_wal:
        logger.warning(
            "SQLite %s lacks the WAL-reset fix; automatic WAL is disabled and rollback journaling is retained. Upgrade SQLite to 3.51.3+ (or patched 3.44.6 / 3.50.7) for concurrent WAL readers and writers.",
            ".".join(map(str, sqlite3.sqlite_version_info)),
        )

    @event.listens_for(engine, "connect")
    def configure(connection: Any, _record: Any) -> None:
        # Initial connect hooks can fail before the pool owns the connection.
        # Close it explicitly on failure rather than leaving a database handle.
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
                if file_backed and not read_only:
                    if safe_wal:
                        cursor.execute("PRAGMA journal_mode=WAL")
                        if cursor.fetchone()[0].lower() != "wal":
                            raise sqlite3.OperationalError("SOC file SQLite requires WAL journal mode")
                    else:
                        cursor.execute("PRAGMA journal_mode")
                        if cursor.fetchone()[0].lower() == "wal":
                            raise sqlite3.OperationalError(
                                "SOC SQLite database already uses WAL, but this runtime lacks the WAL-reset fix; "
                                "upgrade SQLite to 3.51.3+ (or patched 3.44.6 / 3.50.7) before opening it for writes. "
                                "The database and journal files have not been converted or removed."
                            )
            finally:
                cursor.close()
        except BaseException:
            connection.close()
            raise

    return engine
