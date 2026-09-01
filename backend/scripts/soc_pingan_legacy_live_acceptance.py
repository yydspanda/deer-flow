"""Run one real old-ZEUS submit/status/Runtime/callback acceptance case."""

from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path

from sqlalchemy import create_engine
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
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    request = _read_private_request(args.request_file)
    engine = create_engine(
        to_sync_database_url(resolve_database_url(args.database_url)),
        pool_pre_ping=True,
    )
    try:
        repository = SqlAlchemyProcessingJobRepository(sessionmaker(bind=engine, expire_on_commit=False))
        report = run_pingan_legacy_live_acceptance(
            request,
            repository=repository,
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
