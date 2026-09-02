#!/usr/bin/env python3
"""Resolve PingAn host paths from this checkout and selected Runtime profile."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path

_RUNTIME_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?SOC_PINGAN_ENV\s*=\s*(?P<value>.*?)\s*$")


def resolve_repo_root(script_path: Path = Path(__file__)) -> Path:
    root = script_path.resolve().parents[2]
    required = (root / "backend" / "pyproject.toml", root / "AGENTS.md")
    if not all(path.is_file() for path in required):
        raise RuntimeError("cannot resolve the DeerFlow repository root from this script")
    return root


def resolve_runtime_environment(
    root: Path,
    *,
    explicit: str | None = None,
) -> str:
    candidate = (explicit or os.environ.get("SOC_PINGAN_ENV", "")).strip().lower()
    if not candidate:
        env_path = root / ".env.soc-dev.local"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                match = _RUNTIME_ASSIGNMENT.fullmatch(line)
                if match is None:
                    continue
                tokens = shlex.split(match.group("value"), comments=True, posix=True)
                if len(tokens) != 1:
                    raise RuntimeError("SOC_PINGAN_ENV has an unsupported value")
                candidate = tokens[0].strip().lower()
                break
    candidate = candidate or "dev"
    if candidate not in {"dev", "stg"}:
        raise RuntimeError("PingAn host runtime environment must be dev or stg")
    return candidate


def resolved_paths(root: Path, *, environment: str | None = None) -> dict[str, str]:
    runtime_environment = resolve_runtime_environment(root, explicit=environment)
    database_path = root / "backend" / ".deer-flow" / "data" / f"soc_agent_{runtime_environment}.db"
    values = {
        "SOC_REPO_ROOT": str(root),
        "DEER_FLOW_CONFIG_PATH": str(root / "config.pingan-dev.local"),
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": str(root / "backend" / "samples" / "pingan_dev" / "extensions.example.json"),
        "SOC_RUNTIME_ENVIRONMENT": runtime_environment,
        "SOC_SQLITE_PATH": str(database_path),
        "SOC_DATABASE_URL": "sqlite+pysqlite:///" + str(database_path),
        "SOC_INTERNAL_VALIDATION_ROOT": str(root / "backend" / ".deer-flow" / "soc-internal-validation"),
    }
    if runtime_environment == "dev":
        values["SOC_DEV_SQLITE_PATH"] = str(database_path)
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", action="store_true", help="Print POSIX shell export statements")
    parser.add_argument(
        "--environment",
        choices=("dev", "stg"),
        help="Override the profile read from SOC_PINGAN_ENV/private env",
    )
    args = parser.parse_args()
    values = resolved_paths(resolve_repo_root(), environment=args.environment)
    if args.shell:
        for name, value in values.items():
            print(f"export {name}={shlex.quote(value)}")
    else:
        print(json.dumps(values, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
