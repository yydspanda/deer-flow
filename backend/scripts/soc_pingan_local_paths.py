#!/usr/bin/env python3
"""Resolve PingAn DEV paths from this checkout instead of a user home path."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path


def resolve_repo_root(script_path: Path = Path(__file__)) -> Path:
    root = script_path.resolve().parents[2]
    required = (root / "backend" / "pyproject.toml", root / "AGENTS.md")
    if not all(path.is_file() for path in required):
        raise RuntimeError("cannot resolve the DeerFlow repository root from this script")
    return root


def resolved_paths(root: Path) -> dict[str, str]:
    return {
        "SOC_REPO_ROOT": str(root),
        "DEER_FLOW_CONFIG_PATH": str(root / "config.pingan-dev.local"),
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": str(root / "backend" / "samples" / "pingan_dev" / "extensions.example.json"),
        "SOC_DEV_SQLITE_PATH": str(root / "backend" / ".deer-flow" / "data" / "soc_agent_dev.db"),
        "SOC_INTERNAL_VALIDATION_ROOT": str(root / "backend" / ".deer-flow" / "soc-internal-validation"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", action="store_true", help="Print POSIX shell export statements")
    args = parser.parse_args()
    values = resolved_paths(resolve_repo_root())
    if args.shell:
        for name, value in values.items():
            print(f"export {name}={shlex.quote(value)}")
    else:
        print(json.dumps(values, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
