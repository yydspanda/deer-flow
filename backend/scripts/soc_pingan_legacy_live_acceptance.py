"""Run one real old-ZEUS submit/status/Runtime/callback acceptance case."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.db import (  # noqa: E402
    SqlAlchemyProcessingJobRepository,
    resolve_database_url,
    to_sync_database_url,
)
from soc_agent.integrations.pingan.dev_validation import (  # noqa: E402
    write_validation_report,
)
from soc_agent.integrations.pingan.legacy_compat.live_acceptance import (  # noqa: E402
    run_pingan_legacy_live_acceptance,
)

_REQUIRED_ACCEPTANCE_TABLES = frozenset(
    {
        "soc_processing_jobs",
        "soc_processing_job_events",
        "soc_callback_outbox",
        "soc_callback_attempts",
    }
)


def resolve_acceptance_database_url(
    explicit_url: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the exact Host DEV SOC database independently of caller cwd."""

    if explicit_url and explicit_url.strip():
        return explicit_url.strip()
    values = os.environ if environ is None else environ
    configured_url = values.get("SOC_DATABASE_URL", "").strip()
    if configured_url:
        return configured_url
    local_path = values.get("SOC_SQLITE_PATH", "").strip() or values.get("SOC_DEV_SQLITE_PATH", "").strip()
    if local_path:
        database_path = Path(local_path).expanduser()
        if not database_path.is_absolute():
            raise ValueError("SOC_SQLITE_PATH must be an absolute path")
        return "sqlite+pysqlite:///" + str(database_path)
    return resolve_database_url()


def assert_acceptance_database_ready(engine: Engine) -> str:
    """Fail before network submission unless the evidence database is usable."""

    if engine.url.get_backend_name() == "sqlite":
        raw_path = engine.url.database
        if not raw_path or raw_path == ":memory:":
            raise RuntimeError("live acceptance requires a persistent SOC SQLite database")
        database_path = Path(raw_path).expanduser()
        if not database_path.is_absolute():
            raise RuntimeError("live acceptance requires an absolute SOC SQLite path")
        if not database_path.is_file():
            raise RuntimeError("SOC database does not exist; start the selected PingAn host profile before live acceptance")
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM soc_alembic_version")).scalar_one_or_none()
        table_names = set(inspect(engine).get_table_names())
    except SQLAlchemyError as exc:
        raise RuntimeError("SOC database is unavailable or missing soc_alembic_version; run the SOC migration before live acceptance") from exc
    if not revision:
        raise RuntimeError("SOC database has no soc_alembic_version; run the SOC migration before live acceptance")
    missing_tables = sorted(_REQUIRED_ACCEPTANCE_TABLES - table_names)
    if missing_tables:
        raise RuntimeError("SOC database is missing the processing-job migration tables: " + ", ".join(missing_tables))
    return str(revision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge one real ZEUS lifecycle check, model run, and callback",
    )
    parser.add_argument(
        "--request-file",
        required=True,
        type=Path,
        help="Private 0600 JSON file ending in .local.json",
    )
    parser.add_argument("--report-path", required=True, type=Path)
    parser.add_argument("--database-url")
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=("Resume evidence inspection for this exact idempotent request after a prior client-side failure"),
    )
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    request = _read_private_request(args.request_file)
    engine: Engine | None = None
    try:
        database_url = to_sync_database_url(resolve_acceptance_database_url(args.database_url))
        engine = create_engine(database_url, pool_pre_ping=True)
        assert_acceptance_database_ready(engine)
    except (RuntimeError, SQLAlchemyError, ValueError) as exc:
        if engine is not None:
            engine.dispose()
        print(
            f"error: live acceptance database preflight failed: {exc}",
            file=sys.stderr,
        )
        return 1
    assert engine is not None
    try:
        repository = SqlAlchemyProcessingJobRepository(sessionmaker(bind=engine, expire_on_commit=False))
        report = run_pingan_legacy_live_acceptance(
            request,
            repository=repository,
            resume_existing=args.resume_existing,
        )
    finally:
        engine.dispose()
    write_validation_report(report, args.report_path)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


def _read_private_request(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("live acceptance request must not be a symbolic link")
    if not path.name.lower().endswith(".local.json"):
        raise ValueError("live acceptance request filename must end in .local.json")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("live acceptance request permissions must be 0600 or stricter")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("live acceptance request must be a JSON object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
