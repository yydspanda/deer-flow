"""Prepare private model-gateway and old-ZEUS profiles from reviewed source."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.integrations.pingan.legacy_model_gateway_profile import (  # noqa: E402
    LEGACY_MODEL_CONFIG_NAME,
    LEGACY_MODEL_GATEWAY_ENVIRONMENT,
    prepare_legacy_model_gateway_env,
    sanitized_profile_json,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-source",
        type=Path,
        help="Override the reviewed legacy openai_completion.py source",
    )
    parser.add_argument(
        "--root-config",
        type=Path,
        help="Override the reviewed legacy root_config.py source",
    )
    parser.add_argument(
        "--zeus-credential-source",
        type=Path,
        help="Override the reviewed legacy source containing the ZEUS PRD credential",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Override the target private environment file",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        help="Override the private DER key path inside the repository",
    )
    parser.add_argument(
        "--environment",
        default=LEGACY_MODEL_GATEWAY_ENVIRONMENT,
        help="Reviewed legacy environment (currently stg only)",
    )
    parser.add_argument(
        "--model-config",
        default=LEGACY_MODEL_CONFIG_NAME,
        help="Reviewed STG model configuration name",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Atomically write ignored private files with mode 0600",
    )
    args = parser.parse_args(argv)
    report = prepare_legacy_model_gateway_env(
        repo_root=REPO_ROOT,
        model_source_path=args.model_source,
        root_config_path=args.root_config,
        zeus_credential_source_path=args.zeus_credential_source,
        env_path=args.env_file,
        key_path=args.key_file,
        environment=args.environment,
        model_config_name=args.model_config,
        apply=args.apply,
    )
    print(sanitized_profile_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
