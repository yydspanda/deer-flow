"""Prepare a private YHSYS workflow profile from reviewed legacy source."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.legacy_workflow_profile import (  # noqa: E402
    prepare_legacy_workflow_env,
    sanitized_profile_json,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Override the reviewed legacy agent_config.py source",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Override the target private environment file",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Atomically write the Git-ignored environment file with mode 0600",
    )
    args = parser.parse_args(argv)
    report = prepare_legacy_workflow_env(
        repo_root=REPO_ROOT,
        source_path=args.source,
        env_path=args.env_file,
        apply=args.apply,
    )
    print(sanitized_profile_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
