#!/usr/bin/env python3
"""Run the hermetic PingAn legacy execution-plane acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.legacy_compat.acceptance import (  # noqa: E402
    run_pingan_legacy_fake_acceptance,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-path",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--sample-path",
        type=Path,
        default=REPO_ROOT / "datas" / "legacy_demos" / "apt-1965449.json",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_key = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = BACKEND_ROOT / ".deer-flow" / "internal-host-dev"
    database_path = args.database_path or (output_dir / f"legacy-fake-acceptance-{run_key}.sqlite")
    report_path = args.report_path or (output_dir / f"legacy-fake-acceptance-{run_key}.json")
    database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    database_url = f"sqlite:///{database_path.resolve()}"
    report = run_pingan_legacy_fake_acceptance(
        database_url=database_url,
        sample_path=args.sample_path.resolve(),
        report_path=report_path.resolve(),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
