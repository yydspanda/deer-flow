"""Command-line interface for the Phase 1 SOC Agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from soc_agent.core import SocAnalysisService


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return _analyze(args)

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

    return parser


def _analyze(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run = SocAnalysisService().analyze(payload)
    print(
        run.model_dump_json(
            indent=2 if args.pretty else None,
            exclude_none=True,
        )
    )
    return 0 if run.status.value in {"success", "needs_review"} else 1


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
