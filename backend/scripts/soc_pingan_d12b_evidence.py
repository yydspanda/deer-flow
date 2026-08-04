"""Run D12-B MCP dispatch, evidence persistence, and context readback acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.actions.mcp import (  # noqa: E402
    DeerFlowCachedMcpToolProvider,
    build_mcp_action_adapter_registry_from_file,
)
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    resolve_database_url,
    to_sync_database_url,
)
from soc_agent.integrations.pingan.d12b_acceptance import (  # noqa: E402
    PingAnAssetCaseMatrixError,
    load_pingan_asset_case_matrix,
)
from soc_agent.integrations.pingan.d12b_evidence_acceptance import (  # noqa: E402
    PingAnD12BEvidenceAcceptanceStatus,
    run_pingan_d12b_evidence_acceptance,
)
from soc_agent.integrations.pingan.dev_validation import write_validation_report  # noqa: E402

DEFAULT_ACTION_CONFIG = BACKEND_ROOT / "samples" / "mcp" / "pingan_asset" / "action_adapters.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--queue-id", required=True)
    parser.add_argument("--thread-id")
    parser.add_argument("--database-url")
    parser.add_argument("--action-config", type=Path, default=DEFAULT_ACTION_CONFIG)
    parser.add_argument("--report-path", required=True, type=Path)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Explicitly allow one read-only internal MCP asset.locate request.",
    )
    args = parser.parse_args(argv)

    if not args.confirm_live:
        parser.error("--confirm-live is required because this command invokes an internal MCP tool")
    try:
        matrix = load_pingan_asset_case_matrix(args.cases, require_private=True)
        database_url = resolve_database_url(args.database_url)
    except (PingAnAssetCaseMatrixError, ValueError) as exc:
        parser.error(str(exc))

    engine = create_engine(to_sync_database_url(database_url), pool_pre_ping=True)
    try:
        repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
        provider = DeerFlowCachedMcpToolProvider(use_one_shot_invocation=True)
        registry = build_mcp_action_adapter_registry_from_file(
            args.action_config,
            provider,
        )
        report = run_pingan_d12b_evidence_acceptance(
            matrix,
            case_id=args.case_id,
            queue_id=args.queue_id,
            thread_id=args.thread_id,
            action_adapter_registry=registry,
            repository=repository,
        )
    finally:
        engine.dispose()

    write_validation_report(report, args.report_path)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.status is PingAnD12BEvidenceAcceptanceStatus.PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
