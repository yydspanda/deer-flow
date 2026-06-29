"""Command-line interface for the Phase 1 SOC Agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import CorrectionCommand, ReviewQueueCloseCommand, ReviewQueueStatus, Verdict
from soc_agent.core import SocAnalysisService, SocNormalizationService, SocReviewService, SocServiceError
from soc_agent.db import (
    SqlAlchemyAlertRepository,
    create_soc_tables,
    resolve_database_url,
    to_sync_database_url,
    upgrade_soc_schema,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return _analyze(args)
    if args.command == "list":
        return _list(args)
    if args.command == "show":
        return _show(args)
    if args.command == "replay":
        return _replay(args)
    if args.command == "correct":
        return _correct(args)
    if args.command == "normalize" and args.normalize_command == "inspect":
        return _normalize_inspect(args)
    if args.command == "review" and args.review_command == "list":
        return _review_list(args)
    if args.command == "review" and args.review_command == "context":
        return _review_context(args)
    if args.command == "review" and args.review_command == "close":
        return _review_close(args)
    if args.command == "db" and args.db_command == "init":
        return _db_init(args)
    if args.command == "db" and args.db_command == "upgrade":
        return _db_upgrade(args)

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

    list_cmd = subparsers.add_parser("list", help="List persisted SOC alert summaries")
    list_cmd.add_argument("--limit", type=int, default=50, help="Maximum summaries to return")
    list_cmd.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(list_cmd)

    show = subparsers.add_parser("show", help="Show one persisted SOC analysis run")
    show.add_argument("run_id", help="Run id to load")
    show.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(show)

    replay = subparsers.add_parser("replay", help="Replay one persisted SOC analysis run")
    replay.add_argument("run_id", help="Run id to replay")
    replay.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(replay)

    correct = subparsers.add_parser("correct", help="Record a manual verdict correction")
    correct.add_argument("run_id", help="Run id to correct")
    correct.add_argument(
        "--verdict",
        required=True,
        choices=[verdict.value for verdict in Verdict],
        help="Corrected verdict",
    )
    correct.add_argument("--reason", required=True, help="Analyst correction reason")
    correct.add_argument("--confidence", type=float, default=None, help="Optional corrected confidence, 0..1")
    correct.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(correct)

    normalize = subparsers.add_parser("normalize", help="SOC normalization helpers")
    normalize_subparsers = normalize.add_subparsers(dest="normalize_command")
    normalize_inspect = normalize_subparsers.add_parser("inspect", help="Inspect normalized alert and extracted entities")
    normalize_inspect.add_argument("path", nargs="?", help="Path to alert JSON file")
    normalize_inspect.add_argument("--json", dest="json_payload", help="Inline alert JSON object")
    normalize_inspect.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")

    review = subparsers.add_parser("review", help="SOC review queue helpers")
    review_subparsers = review.add_subparsers(dest="review_command")
    review_list = review_subparsers.add_parser("list", help="List SOC review queue items")
    review_list.add_argument("--limit", type=int, default=50, help="Maximum queue items to return")
    review_list.add_argument(
        "--status",
        choices=[status.value for status in ReviewQueueStatus],
        default=ReviewQueueStatus.OPEN.value,
        help="Queue item status to list",
    )
    review_list.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_list)
    review_context = review_subparsers.add_parser("context", help="Show analyst investigation context for a queue item")
    review_context.add_argument("queue_id", help="Review queue id to open")
    review_context.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_context)
    review_close = review_subparsers.add_parser("close", help="Close one SOC review queue item")
    review_close.add_argument("queue_id", help="Review queue id to close")
    review_close.add_argument("--reason", required=True, help="Reason for closing the queue item")
    review_close.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_close)

    db = subparsers.add_parser("db", help="SOC database helpers")
    db_subparsers = db.add_subparsers(dest="db_command")
    init = db_subparsers.add_parser("init", help="Create SOC database tables")
    _add_database_args(init)
    upgrade = db_subparsers.add_parser("upgrade", help="Run SOC Alembic migrations")
    upgrade.add_argument("revision", nargs="?", default="head", help="Alembic revision target")
    _add_database_args(upgrade)

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

    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(payload)
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


def _list(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summaries = repository.list_alert_summaries(limit=args.limit)
    print(json.dumps([summary.model_dump(mode="json", exclude_none=True) for summary in summaries], ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _replay(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        run = SocAnalysisService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
        ).replay(args.run_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(run.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if run.status.value in {"success", "needs_review"} else 1


def _correct(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        run = SocReviewService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
        ).correct(
            CorrectionCommand(
                run_id=args.run_id,
                corrected_verdict=Verdict(args.verdict),
                corrected_confidence=args.confidence,
                reason=args.reason,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(run.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _normalize_inspect(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        result = SocNormalizationService().inspect(payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report normalization failure
        print(f"error: normalization inspect failed: {exc}", file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _review_list(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    items = SocReviewService(review_queue_repository=repository).list_queue(
        status=ReviewQueueStatus(args.status),
        limit=args.limit,
    )
    print(json.dumps([item.model_dump(mode="json", exclude_none=True) for item in items], ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _review_close(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        item = SocReviewService(review_queue_repository=repository).close_queue_item(ReviewQueueCloseCommand(queue_id=args.queue_id, reason=args.reason))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(item.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _review_context(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        context = SocReviewService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
        ).get_investigation_context(args.queue_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(context.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


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


def _db_upgrade(args: argparse.Namespace) -> int:
    try:
        database_url = resolve_database_url(args.database_url)
        upgrade_soc_schema(database_url, revision=args.revision)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report migration failure
        print(f"error: database upgrade failed: {exc}", file=sys.stderr)
        return 1
    print(f"SOC database schema upgraded to {args.revision}.")
    return 0


def _repository_from_args(args: argparse.Namespace) -> SqlAlchemyAlertRepository:
    engine = _engine_from_args(args)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyAlertRepository(session_factory)


def _engine_from_args(args: argparse.Namespace):
    database_url = resolve_database_url(args.database_url)
    return create_engine(to_sync_database_url(database_url), pool_pre_ping=True)


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
