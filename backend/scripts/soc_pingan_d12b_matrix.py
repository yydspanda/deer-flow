"""Preview or run the approved PingAn D12-B real-Provider case matrix."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.d12b_acceptance import (  # noqa: E402
    PingAnAssetCaseMatrixError,
    PingAnAssetCaseMatrixStatus,
    build_pingan_asset_case_matrix_plan,
    load_pingan_asset_case_matrix,
    run_pingan_asset_case_matrix,
)
from soc_agent.integrations.pingan.dev_validation import write_validation_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--plan-only",
        action="store_true",
        help="Validate matrix coverage and print a value-free plan without external requests.",
    )
    mode.add_argument(
        "--confirm-live",
        action="store_true",
        help="Explicitly allow the matrix to issue requests to the configured internal DEV Provider.",
    )
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args(argv)

    if args.confirm_live and args.report_path is None:
        parser.error("--confirm-live requires --report-path")
    try:
        matrix = load_pingan_asset_case_matrix(
            args.cases,
            require_private=args.confirm_live,
        )
    except PingAnAssetCaseMatrixError as exc:
        parser.error(str(exc))

    if args.plan_only:
        plan = build_pingan_asset_case_matrix_plan(matrix)
        print(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0 if plan.complete else 1

    report = run_pingan_asset_case_matrix(matrix)
    write_validation_report(report, args.report_path)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.status is PingAnAssetCaseMatrixStatus.PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
