#!/usr/bin/env python3
"""Compile and query the historical PingAn EDR software-path catalog."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.software_path_catalog import (  # noqa: E402
    PingAnSoftwarePathCatalog,
    PingAnSoftwarePathCatalogError,
    compile_pingan_software_path_catalog,
)

DEFAULT_SOURCE = REPO_ROOT / "validation/original_works/raw_program/Deepseek_Qwen_32B_EDR_Analysis_Ignored_Paths_Sup (1).xlsx"
DEFAULT_CATALOG = BACKEND_ROOT / ".deer-flow/pingan-context/software-path-catalog.sqlite"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            report = compile_pingan_software_path_catalog(args.source, args.catalog)
            payload = report.model_dump(mode="json")
            report_path = args.report_path or args.catalog.with_suffix(".build-report.json")
            _write_json_atomic(report_path, payload)
        elif args.command == "query":
            catalog = PingAnSoftwarePathCatalog(args.catalog, freshness_days=args.freshness_days)
            as_of = _parse_as_of(args.as_of)
            payload = catalog.lookup(args.path, md5=args.md5, as_of=as_of).model_dump(
                mode="json",
                exclude_none=True,
            )
            if args.report_path:
                _write_json_atomic(args.report_path, payload)
        else:  # pragma: no cover - argparse enforces a subcommand
            raise ValueError(f"unsupported command: {args.command}")
    except (OSError, ValueError, PingAnSoftwarePathCatalogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Compile the private XLSX source into SQLite")
    build.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    build.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    build.add_argument("--report-path", type=Path)

    query = commands.add_parser("query", help="Run one exact path/hash lookup")
    query.add_argument("path")
    query.add_argument("--md5")
    query.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    query.add_argument("--freshness-days", type=int, default=180)
    query.add_argument("--as-of", help="Optional ISO-8601 evaluation time; defaults to now")
    query.add_argument("--report-path", type=Path)
    return parser


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
