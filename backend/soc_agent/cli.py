"""Command-line interface for the Phase 1 SOC Agent."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from soc_agent.core import SocAnalysisService, SocServiceError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return _analyze(args)
    if args.command == "show":
        return _show(args)
    if args.command == "replay":
        return _replay(args)
    if args.command == "db" and args.db_command == "init":
        return _db_init(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soc", description="SOC Agent CLI")
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="Analyze one alert JSON payload")
    analyze.add_argument("path", nargs="?", help="Path to alert JSON file")
    analyze.add_argument("--json", dest="json_payload", help="Inline alert JSON object")
    analyze.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print output JSON",
    )
    analyze.add_argument(
        "--persist",
        action="store_true",
        help="Persist the run through AlertRepository",
    )
    _add_database_args(analyze)

    show = subparsers.add_parser("show", help="Show one persisted SOC analysis run")
    show.add_argument("run_id", help="Run id to load")
    show.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(show)

    replay = subparsers.add_parser("replay", help="Replay one persisted SOC analysis run")
    replay.add_argument("run_id", help="Run id to replay")
    replay.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(replay)

    db = subparsers.add_parser("db", help="SOC database helpers")
    db_subparsers = db.add_subparsers(dest="db_command")
    init = db_subparsers.add_parser("init", help="Create SOC database tables")
    _add_database_args(init)

    return parser


def _add_database_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-url",
        default=None,
        help="SOC database URL; defaults to SOC_DATABASE_URL",
    )


def _analyze(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        repository = _repository_from_args(args) if args.persist else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run = SocAnalysisService(repository=repository).analyze(payload)
    print(
        run.model_dump_json(
            indent=2 if args.pretty else None,
            exclude_none=True,
        )
    )
    return 0 if run.status.value in {"success", "needs_review"} else 1


def _show(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run = repository.get_run(args.run_id)
    if run is None:
        print(f"error: run {args.run_id} not found", file=sys.stderr)
        return 3
    print(run.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _replay(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        run = SocAnalysisService(repository=repository).replay(args.run_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(run.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if run.status.value in {"success", "needs_review"} else 1


def _db_init(args: argparse.Namespace) -> int:
    try:
        engine = _engine_from_args(args)
        create_soc_tables(engine)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(f"error: database init failed: {exc}", file=sys.stderr)
        return 1
    print("SOC database tables are ready.")
    return 0


def _repository_from_args(args: argparse.Namespace) -> SqlAlchemyAlertRepository:
    engine = _engine_from_args(args)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyAlertRepository(session_factory)


def _engine_from_args(args: argparse.Namespace):
    database_url = args.database_url or os.environ.get("SOC_DATABASE_URL")
    if not database_url:
        raise ValueError("database URL required; pass --database-url or set SOC_DATABASE_URL")
    return create_engine(_sync_database_url(database_url), pool_pre_ping=True)


def _sync_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def _load_payload(path: str | None, json_payload: str | None) -> dict[str, Any]:
    if bool(path) == bool(json_payload):
        raise ValueError("provide exactly one of PATH or --json")

    try:
        if json_payload:
            data = json.loads(json_payload)
        else:
            data = json.loads(Path(path or "").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read alert file: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("alert JSON must be an object")
    return data


if __name__ == "__main__":
    raise SystemExit(main())
