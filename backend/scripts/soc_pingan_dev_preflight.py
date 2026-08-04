"""Run the PingAn D12-B no-network configuration preflight."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.dev_validation import (  # noqa: E402
    run_pingan_dev_preflight,
    write_validation_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args(argv)

    report = run_pingan_dev_preflight()
    if args.report_path is not None:
        write_validation_report(report, args.report_path)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
