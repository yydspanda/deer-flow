"""Prepare one private old-ZEUS live-acceptance request without JSON editing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.legacy_compat.request_preparation import (  # noqa: E402
    PingAnLegacyRequestPreparationError,
    prepare_pingan_legacy_live_request,
)

DEFAULT_INDEX_PATH = REPO_ROOT / "validation/compact_zeus/data/corpus" / "full_alert_dams_labeled_merged.workbench-index.json"
DEFAULT_OUTPUT_PATH = BACKEND_ROOT / ".deer-flow/soc-internal-validation/legacy-compat" / "task-request.local.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alert-id",
        help="Approved ZEUS alert ID whose staged snapshot is still pending review",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=DEFAULT_INDEX_PATH,
        help="Staged corpus workbench index",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Private request file ending in .local.json",
    )
    parser.add_argument(
        "--session-id",
        help="Optional deterministic session ID; omitted generates a fresh value",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an earlier private request with a fresh request",
    )
    args = parser.parse_args(argv)

    alert_id = (args.alert_id or "").strip()
    if not alert_id:
        try:
            alert_id = input("Enter an approved ZEUS alert_id that is still pending review: ").strip()
        except EOFError:
            alert_id = ""
    if not alert_id:
        print("error: alert_id is required", file=sys.stderr)
        return 2

    try:
        report = prepare_pingan_legacy_live_request(
            alert_id=alert_id,
            index_path=args.index_path,
            output_path=args.output_path,
            session_id=args.session_id,
            overwrite=args.overwrite,
        )
    except PingAnLegacyRequestPreparationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
