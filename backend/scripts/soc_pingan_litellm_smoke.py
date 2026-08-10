"""Issue one non-business chat completion against PingAn's local LiteLLM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.dev_validation import write_validation_report  # noqa: E402
from soc_agent.integrations.pingan.litellm_smoke import run_pingan_litellm_smoke  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge that one request will be sent to the configured loopback gateway",
    )
    parser.add_argument("--report-path", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    report = run_pingan_litellm_smoke()
    write_validation_report(report, args.report_path)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
