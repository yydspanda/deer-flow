"""Switch the governed PingAn Host deployment between DEV and STG profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.runtime_environment import (  # noqa: E402
    PingAnRuntimeEnvironmentConfigurationError,
    set_pingan_runtime_environment,
)

DEFAULT_ENV_PATH = REPO_ROOT / ".env.soc-dev.local"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=("dev", "stg"))
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="Private PingAn host environment file",
    )
    args = parser.parse_args(argv)
    try:
        report = set_pingan_runtime_environment(
            args.env_file,
            environment=args.environment,
        )
    except PingAnRuntimeEnvironmentConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
