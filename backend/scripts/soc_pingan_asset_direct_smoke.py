"""Call the PingAn asset Provider directly for one approved D12-B case."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.asset_location import PingAnAssetLocationQuery  # noqa: E402
from soc_agent.integrations.pingan.dev_validation import (  # noqa: E402
    run_pingan_asset_direct_smoke,
    write_validation_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--asset-type",
        required=True,
        choices=("IP", "DOMAIN", "WEB", "HOST", "USER"),
    )
    parser.add_argument("--role", default="")
    parser.add_argument("--um")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Explicitly allow a request to the governed internal Provider target.",
    )
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required for direct Provider smoke")

    report = run_pingan_asset_direct_smoke(
        PingAnAssetLocationQuery(
            query=args.query,
            asset_type=args.asset_type,
            role=args.role,
            um=args.um,
        )
    )
    if args.report_path is not None:
        write_validation_report(report, args.report_path)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.outcome in {"found", "not_found", "ambiguous"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
