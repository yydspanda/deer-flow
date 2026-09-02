"""Check one approved ZEUS alert lifecycle before a model-backed live run."""

from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.dev_validation import (  # noqa: E402
    write_validation_report,
)
from soc_agent.integrations.pingan.legacy_compat import (  # noqa: E402
    PingAnLegacyTaskRequest,
    run_pingan_zeus_lifecycle_smoke,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge one real read-only ZEUS lifecycle request",
    )
    parser.add_argument(
        "--request-file",
        required=True,
        type=Path,
        help="Private 0600 task request prepared for live acceptance",
    )
    parser.add_argument("--report-path", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    request = _read_private_request(args.request_file)
    report = run_pingan_zeus_lifecycle_smoke(request.alert_id)
    write_validation_report(report, args.report_path)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


def _read_private_request(path: Path) -> PingAnLegacyTaskRequest:
    if path.is_symlink():
        raise ValueError("lifecycle smoke request must not be a symbolic link")
    if not path.name.lower().endswith(".local.json"):
        raise ValueError("lifecycle smoke request filename must end in .local.json")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("lifecycle smoke request permissions must be 0600 or stricter")
    value = json.loads(path.read_text(encoding="utf-8"))
    return PingAnLegacyTaskRequest.model_validate(value)


if __name__ == "__main__":
    raise SystemExit(main())
