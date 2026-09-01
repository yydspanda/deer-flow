"""Switch old-ZEUS lifecycle and callback providers without editing env files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.legacy_compat.provider_mode import (  # noqa: E402
    PingAnLegacyProviderModeConfigurationError,
    set_pingan_legacy_provider_mode,
)

DEFAULT_ENV_PATH = REPO_ROOT / ".env.soc-dev.local"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("fake", "internal"))
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="Private SOC DEV environment file",
    )
    args = parser.parse_args(argv)
    try:
        report = set_pingan_legacy_provider_mode(
            args.env_file,
            mode=args.mode,
        )
    except PingAnLegacyProviderModeConfigurationError as exc:
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
