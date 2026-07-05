"""Command-line interface for the Phase 1 SOC Agent."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from soc_agent.actions.adapters import InMemoryAssetLookupActionAdapter, SocActionAdapterRegistry
from soc_agent.actions.mcp import (
    DeerFlowCachedMcpToolProvider,
    build_mcp_action_adapter_registry_from_file,
    inspect_mcp_tool_inventory,
    run_mcp_action_adapter_smoke,
)
from soc_agent.actions.proposals import SocLeadAgentActionProposalBoundary
from soc_agent.agent_profile import SocLeadAgentProfileInstaller
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    CorrectionCommand,
    EntrySurface,
    ReviewQueueCloseCommand,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocAgentActionCommand,
    Verdict,
)
from soc_agent.core import (
    SocAgentActionDispatcher,
    SocAgentApprovalService,
    SocAgentCapabilityRouter,
    SocAgentChatService,
    SocAnalysisService,
    SocDaemonService,
    SocNormalizationService,
    SocReviewService,
    SocServiceError,
    SocSkillResolutionService,
)
from soc_agent.daemon import (
    JsonLineKafkaDaemonMetricSink,
    KafkaAdapterError,
    KafkaAdapterNotConfiguredError,
    KafkaConsumerSettings,
    KafkaDaemonRunResult,
    KafkaDaemonStopSignal,
    KafkaRunnerProcessResult,
    SocKafkaConsumerRunner,
    SocKafkaDaemonRunner,
    build_kafka_consumer_port,
    build_kafka_daemon_status,
)
from soc_agent.db import (
    SqlAlchemyAlertRepository,
    create_soc_tables,
    resolve_database_url,
    to_sync_database_url,
    upgrade_soc_schema,
)
from soc_agent.eval import load_eval_responses_jsonl, run_offline_eval
from soc_agent.lead_agent import build_soc_lead_agent_profile
from soc_agent.lead_agent_chat import SocLeadAgentChatService


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return _analyze(args)
    if args.command == "list":
        return _list(args)
    if args.command == "show":
        return _show(args)
    if args.command == "replay":
        return _replay(args)
    if args.command == "correct":
        return _correct(args)
    if args.command == "normalize" and args.normalize_command == "inspect":
        return _normalize_inspect(args)
    if args.command == "normalize" and args.normalize_command == "drift":
        return _normalize_drift(args)
    if args.command == "review" and args.review_command == "list":
        return _review_list(args)
    if args.command == "review" and args.review_command == "context":
        return _review_context(args)
    if args.command == "review" and args.review_command == "close":
        return _review_close(args)
    if args.command == "review" and args.review_command == "tui":
        return _review_tui(args)
    if args.command == "chat" and args.chat_command == "tui":
        return _chat_tui(args)
    if args.command == "agent" and args.agent_command == "profile":
        return _agent_profile(args)
    if args.command == "agent" and args.agent_command == "resolve-skills":
        return _agent_resolve_skills(args)
    if args.command == "agent" and args.agent_command == "install-profile":
        return _agent_install_profile(args)
    if args.command == "mcp" and args.mcp_command == "smoke":
        return _mcp_smoke(args)
    if args.command == "mcp" and args.mcp_command == "tools":
        return _mcp_tools(args)
    if args.command == "daemon" and args.daemon_command == "process":
        return _daemon_process(args)
    if args.command == "daemon" and args.daemon_command == "consume":
        return _daemon_consume(args)
    if args.command == "daemon" and args.daemon_command == "run":
        return _daemon_run(args)
    if args.command == "daemon" and args.daemon_command == "status":
        return _daemon_status(args)
    if args.command == "eval" and args.eval_command == "offline":
        return _eval_offline(args)
    if args.command == "db" and args.db_command == "init":
        return _db_init(args)
    if args.command == "db" and args.db_command == "upgrade":
        return _db_upgrade(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soc", description="SOC Agent CLI")
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="Analyze one alert JSON payload")
    analyze.add_argument("path", nargs="?", help="Path to alert JSON file")
    analyze.add_argument("--json", dest="json_payload", help="Inline alert JSON object")
    analyze.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print output JSON",
    )
    analyze.add_argument(
        "--persist",
        action="store_true",
        help="Persist the run through AlertRepository",
    )
    _add_database_args(analyze)

    list_cmd = subparsers.add_parser("list", help="List persisted SOC alert summaries")
    list_cmd.add_argument("--limit", type=int, default=50, help="Maximum summaries to return")
    list_cmd.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(list_cmd)

    show = subparsers.add_parser("show", help="Show one persisted SOC analysis run")
    show.add_argument("run_id", help="Run id to load")
    show.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(show)

    replay = subparsers.add_parser("replay", help="Replay one persisted SOC analysis run")
    replay.add_argument("run_id", help="Run id to replay")
    replay.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(replay)

    correct = subparsers.add_parser("correct", help="Record a manual verdict correction")
    correct.add_argument("run_id", help="Run id to correct")
    correct.add_argument(
        "--verdict",
        required=True,
        choices=[verdict.value for verdict in Verdict],
        help="Corrected verdict",
    )
    correct.add_argument("--reason", required=True, help="Analyst correction reason")
    correct.add_argument("--confidence", type=float, default=None, help="Optional corrected confidence, 0..1")
    correct.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(correct)

    normalize = subparsers.add_parser("normalize", help="SOC normalization helpers")
    normalize_subparsers = normalize.add_subparsers(dest="normalize_command")
    normalize_inspect = normalize_subparsers.add_parser("inspect", help="Inspect normalized alert and extracted entities")
    normalize_inspect.add_argument("path", nargs="?", help="Path to alert JSON file")
    normalize_inspect.add_argument("--json", dest="json_payload", help="Inline alert JSON object")
    normalize_inspect.add_argument("--mapping", help="Path to SOC normalization mapping YAML")
    normalize_inspect.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    normalize_drift = normalize_subparsers.add_parser("drift", help="Aggregate normalization drift over alert JSON samples")
    normalize_drift.add_argument("path", nargs="?", help="Path to an alert JSON file or directory")
    normalize_drift.add_argument("--mapping", help="Path to SOC normalization mapping YAML")
    normalize_drift.add_argument("--glob", default="*.json", help="Glob used when PATH is a directory")
    normalize_drift.add_argument("--recent-runs", action="store_true", help="Aggregate persisted recent analysis runs")
    normalize_drift.add_argument("--limit", type=int, default=50, help="Maximum persisted runs to aggregate with --recent-runs")
    normalize_drift.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(normalize_drift)

    review = subparsers.add_parser("review", help="SOC review queue helpers")
    review_subparsers = review.add_subparsers(dest="review_command")
    review_list = review_subparsers.add_parser("list", help="List SOC review queue items")
    review_list.add_argument("--limit", type=int, default=50, help="Maximum queue items to return")
    review_list.add_argument(
        "--status",
        choices=[status.value for status in ReviewQueueStatus],
        default=ReviewQueueStatus.OPEN.value,
        help="Queue item status to list",
    )
    review_list.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_list)
    review_context = review_subparsers.add_parser("context", help="Show analyst investigation context for a queue item")
    review_context.add_argument("queue_id", help="Review queue id to open")
    review_context.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_context)
    review_close = review_subparsers.add_parser("close", help="Close one SOC review queue item")
    review_close.add_argument("queue_id", help="Review queue id to close")
    review_close.add_argument("--reason", required=True, help="Reason for closing the queue item")
    review_close.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_close)
    review_tui = review_subparsers.add_parser("tui", help="Open the SOC review queue terminal workbench")
    _add_database_args(review_tui)

    chat = subparsers.add_parser("chat", help="SOC interactive agent helpers")
    chat_subparsers = chat.add_subparsers(dest="chat_command")
    chat_tui = chat_subparsers.add_parser("tui", help="Open the SOC agent chat terminal workbench")
    chat_tui.add_argument("--queue-id", help="Open a review queue context on launch")
    chat_tui.add_argument("--message", help="Send an initial message on launch")
    chat_tui.add_argument("--lead-agent", action="store_true", help="Use DeerFlow lead_agent with agent_name=soc-triage")
    chat_tui.add_argument("--mcp-action-config", help="Optional SOC MCP read-only action adapter JSON/YAML config for lead-agent proposals")
    _add_database_args(chat_tui)

    agent = subparsers.add_parser("agent", help="SOC Lead Agent profile and skill helpers")
    agent_subparsers = agent.add_subparsers(dest="agent_command")
    agent_profile = agent_subparsers.add_parser("profile", help="Show the recommended DeerFlow custom-agent profile")
    agent_profile.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    agent_install_profile = agent_subparsers.add_parser("install-profile", help="Install the SOC profile into DeerFlow custom-agent storage")
    agent_install_profile.add_argument("--user-id", help="Install for a specific DeerFlow user id")
    agent_install_profile.add_argument("--dry-run", action="store_true", help="Show the target path and action without writing files")
    agent_install_profile.add_argument("--overwrite", action="store_true", help="Overwrite an existing user-scoped SOC profile")
    agent_install_profile.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    agent_resolve_skills = agent_subparsers.add_parser("resolve-skills", help="Resolve SOC domain skills for one alert payload")
    agent_resolve_skills.add_argument("path", nargs="?", help="Path to alert JSON file")
    agent_resolve_skills.add_argument("--json", dest="json_payload", help="Inline alert JSON object")
    agent_resolve_skills.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")

    mcp = subparsers.add_parser("mcp", help="SOC MCP action adapter helpers")
    mcp_subparsers = mcp.add_subparsers(dest="mcp_command")
    mcp_smoke = mcp_subparsers.add_parser("smoke", help="Smoke-test read-only MCP action adapter config")
    mcp_smoke.add_argument("config", help="Path to SOC MCP action adapter JSON/YAML config")
    mcp_smoke.add_argument("--route", required=True, help="SOC action route to test")
    mcp_smoke.add_argument("--action", help="SOC action name; defaults to --route")
    mcp_smoke.add_argument("--json", dest="json_payload", required=True, help="Inline SOC action payload JSON object")
    mcp_smoke.add_argument("--dry-run", action="store_true", help="Validate adapter/tool availability without invoking the MCP tool")
    mcp_smoke.add_argument("--actor-id", default="soc-mcp-smoke", help="Actor id recorded in the smoke context")
    mcp_smoke.add_argument("--trace-id", default="soc-mcp-smoke", help="Trace id recorded in the smoke context")
    mcp_smoke.add_argument("--idempotency-key", default="soc-mcp-smoke", help="Idempotency key recorded in the smoke context")
    mcp_smoke.add_argument("--report-path", help="Optional path to write the smoke report JSON")
    mcp_smoke.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    mcp_tools = mcp_subparsers.add_parser("tools", help="List DeerFlow cached MCP tools visible to SOC")
    mcp_tools.add_argument("--include-schema", action="store_true", help="Include MCP tool input schemas in the report")
    mcp_tools.add_argument("--report-path", help="Optional path to write the inventory report JSON")
    mcp_tools.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")

    daemon = subparsers.add_parser("daemon", help="SOC daemon helpers")
    daemon_subparsers = daemon.add_subparsers(dest="daemon_command")
    daemon_process = daemon_subparsers.add_parser("process", help="Process one decoded SOC daemon message JSON")
    daemon_process.add_argument("path", nargs="?", help="Path to daemon message JSON file")
    daemon_process.add_argument("--json", dest="json_payload", help="Inline daemon message JSON object")
    daemon_process.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(daemon_process)
    daemon_status = daemon_subparsers.add_parser("status", help="Show SOC Kafka daemon readiness status")
    daemon_status.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    daemon_status.add_argument("--skip-database-check", action="store_true", help="Only validate database URL configuration")
    daemon_status.add_argument("--check-broker", action="store_true", help="Attempt a lightweight Kafka broker poll")
    _add_database_args(daemon_status)
    daemon_consume = daemon_subparsers.add_parser("consume", help="Run the SOC Kafka consumer loop")
    daemon_consume.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Maximum records to process before exiting; defaults to SOC_KAFKA_MAX_POLL_RECORDS",
    )
    daemon_consume.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(daemon_consume)
    daemon_run = daemon_subparsers.add_parser("run", help="Run the SOC Kafka daemon until stopped")
    daemon_run.add_argument(
        "--max-loops",
        type=int,
        default=None,
        help="Optional loop cap for local validation; omit for long-running daemon mode",
    )
    daemon_run.add_argument(
        "--idle-sleep-ms",
        type=int,
        default=1000,
        help="Sleep duration after idle polls; use 0 for tests/local validation",
    )
    daemon_run.add_argument(
        "--error-backoff-ms",
        type=int,
        default=1000,
        help="Sleep duration after daemon poll/runtime errors; use 0 for tests/local validation",
    )
    daemon_run.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=3,
        help="Stop after this many consecutive daemon errors; use 0 to disable the cap",
    )
    daemon_run.add_argument(
        "--metric-jsonl",
        choices=["stdout", "stderr"],
        help="Emit daemon runtime metric events as JSON lines to the selected stream",
    )
    daemon_run.add_argument("--include-results", action="store_true", help="Include per-loop results in output JSON")
    daemon_run.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(daemon_run)

    eval_cmd = subparsers.add_parser("eval", help="SOC offline evaluation helpers")
    eval_subparsers = eval_cmd.add_subparsers(dest="eval_command")
    eval_offline = eval_subparsers.add_parser("offline", help="Run stub-vs-LLM replay diff over alert samples")
    eval_offline.add_argument("path", help="Path to an alert JSON file or directory")
    eval_offline.add_argument("--glob", default="*.json", help="Glob used when PATH is a directory")
    eval_offline.add_argument("--llm-response-jsonl", help="Replayable LLM response JSONL keyed by sample_id")
    eval_offline.add_argument("--model-name", default="replay-llm", help="Model name recorded for replayed LLM analyzer")
    eval_offline.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")

    db = subparsers.add_parser("db", help="SOC database helpers")
    db_subparsers = db.add_subparsers(dest="db_command")
    init = db_subparsers.add_parser("init", help="Create SOC database tables")
    _add_database_args(init)
    upgrade = db_subparsers.add_parser("upgrade", help="Run SOC Alembic migrations")
    upgrade.add_argument("revision", nargs="?", default="head", help="Alembic revision target")
    _add_database_args(upgrade)

    return parser


def _add_database_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-url",
        default=None,
        help="SOC database URL; defaults to SOC_DATABASE_URL",
    )


def _analyze(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        repository = _repository_from_args(args) if args.persist else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(payload)
    print(
        run.model_dump_json(
            indent=2 if args.pretty else None,
            exclude_none=True,
        )
    )
    return 0 if run.status.value in {"success", "needs_review"} else 1


def _show(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    run = repository.get_run(args.run_id)
    if run is None:
        print(f"error: run {args.run_id} not found", file=sys.stderr)
        return 3
    print(run.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _list(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summaries = repository.list_alert_summaries(limit=args.limit)
    print(json.dumps([summary.model_dump(mode="json", exclude_none=True) for summary in summaries], ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _replay(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        run = SocAnalysisService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
        ).replay(args.run_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(run.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if run.status.value in {"success", "needs_review"} else 1


def _correct(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        run = SocReviewService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
            evidence_repository=repository,
        ).correct(
            CorrectionCommand(
                run_id=args.run_id,
                corrected_verdict=Verdict(args.verdict),
                corrected_confidence=args.confidence,
                reason=args.reason,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(run.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _normalize_inspect(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        result = SocNormalizationService().inspect(payload, mapping_path=args.mapping)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report normalization failure
        print(f"error: normalization inspect failed: {exc}", file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _normalize_drift(args: argparse.Namespace) -> int:
    try:
        if args.recent_runs:
            if args.path:
                raise ValueError("PATH cannot be used with --recent-runs")
            if args.mapping:
                raise ValueError("--mapping cannot be used with --recent-runs")
            result = SocNormalizationService(repository=_repository_from_args(args)).drift_recent(limit=args.limit)
        else:
            if not args.path:
                raise ValueError("provide PATH or --recent-runs")
            samples = _load_payload_samples(args.path, args.glob)
            result = SocNormalizationService().drift(samples, mapping_path=args.mapping)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report drift failure
        print(f"error: normalization drift failed: {exc}", file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if result.failure_count == 0 else 1


def _review_list(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    items = SocReviewService(review_queue_repository=repository).list_queue(
        status=ReviewQueueStatus(args.status),
        limit=args.limit,
    )
    print(json.dumps([item.model_dump(mode="json", exclude_none=True) for item in items], ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _review_close(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        item = SocReviewService(review_queue_repository=repository).close_queue_item(ReviewQueueCloseCommand(queue_id=args.queue_id, reason=args.reason))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(item.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _review_context(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        context = SocReviewService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
            evidence_repository=repository,
        ).get_investigation_context(args.queue_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(context.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _review_tui(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        from soc_agent.tui.runner import run_review_tui

        run_review_tui(
            SocReviewService(
                repository=repository,
                summary_repository=repository,
                audit_repository=repository,
                review_queue_repository=repository,
                evidence_repository=repository,
            ),
            approval_service=SocAgentApprovalService(grant_repository=repository, request_repository=repository),
            database_label=_database_label(args.database_url),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _chat_tui(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        from soc_agent.tui.runner import run_chat_tui

        review_service = SocReviewService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
            evidence_repository=repository,
        )
        approval_service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
        read_only_adapter_registry = _read_only_adapter_registry_for_chat(args)
        read_only_routes = {descriptor.route for descriptor in read_only_adapter_registry.list_descriptors()}
        chat_service = (
            SocLeadAgentChatService(
                review_service=review_service,
                action_proposal_boundary=SocLeadAgentActionProposalBoundary(
                    approval_service=approval_service,
                    read_only_capability_router=SocAgentCapabilityRouter(allowed_routes=read_only_routes),
                    read_only_action_dispatcher=SocAgentActionDispatcher(
                        action_adapter_registry=read_only_adapter_registry,
                        evidence_repository=repository,
                    ),
                ),
            )
            if args.lead_agent
            else SocAgentChatService(review_service=review_service, approval_service=approval_service)
        )
        run_chat_tui(
            chat_service,
            initial_queue_id=args.queue_id,
            initial_message=args.message,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _read_only_adapter_registry_for_chat(args: argparse.Namespace) -> SocActionAdapterRegistry:
    config_path = getattr(args, "mcp_action_config", None)
    if config_path:
        return build_mcp_action_adapter_registry_from_file(
            config_path,
            DeerFlowCachedMcpToolProvider(use_one_shot_invocation=True),
        )
    return SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter()])


def _agent_profile(args: argparse.Namespace) -> int:
    profile = build_soc_lead_agent_profile()
    print(profile.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _agent_resolve_skills(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        resolution = SocSkillResolutionService().resolve_payload(payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(resolution.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _agent_install_profile(args: argparse.Namespace) -> int:
    try:
        result = SocLeadAgentProfileInstaller().install(
            user_id=args.user_id,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _mcp_smoke(args: argparse.Namespace) -> int:
    try:
        payload = _load_json_object(args.json_payload, payload_label="SOC action payload")
        command = SocAgentActionCommand(
            route=args.route,
            action=args.action or args.route,
            dry_run=args.dry_run,
            payload=payload,
        )
        context = ServiceRequestContext(
            actor=ActorContext(
                actor_id=args.actor_id,
                actor_type=ActorType.USER,
                surface=EntrySurface.CLI,
                roles=["soc_mcp_smoke"],
            ),
            trace_id=args.trace_id,
            idempotency_key=args.idempotency_key,
        )
        report = run_mcp_action_adapter_smoke(
            args.config,
            DeerFlowCachedMcpToolProvider(use_one_shot_invocation=True),
            command=command,
            context=context,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output = report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True)
    try:
        _write_report(args.report_path, output)
    except OSError as exc:
        print(f"error: cannot write MCP smoke report: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0 if report.status == "success" else 1


def _mcp_tools(args: argparse.Namespace) -> int:
    report = inspect_mcp_tool_inventory(
        DeerFlowCachedMcpToolProvider(),
        include_input_schema=args.include_schema,
    )
    output = report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True)
    try:
        _write_report(args.report_path, output)
    except OSError as exc:
        print(f"error: cannot write MCP tools report: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0 if report.status == "success" else 1


def _daemon_process(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        repository = _repository_from_args(args)
        analysis_service = SocAnalysisService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
        )
        approval_service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
        result = SocDaemonService(
            analysis_service=analysis_service,
            approval_service=approval_service,
        ).process_message(payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if result.status == "processed" else 1


def _daemon_consume(args: argparse.Namespace) -> int:
    settings = KafkaConsumerSettings.from_env()
    max_records = args.max_records if args.max_records is not None else settings.max_poll_records
    if max_records < 1:
        print("error: --max-records must be >= 1", file=sys.stderr)
        return 2

    try:
        daemon_service = _daemon_service_from_args(args) if settings.enabled else SocDaemonService()
        consumer = build_kafka_consumer_port(settings)
    except (ValueError, KafkaAdapterNotConfiguredError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    runner = SocKafkaConsumerRunner(
        consumer=consumer,
        daemon_service=daemon_service,
        alert_topics=frozenset(settings.alert_topics),
        approval_request_topics=frozenset(settings.approval_request_topics),
    )

    try:
        loop_result = runner.run(max_records=max_records, stop_on_idle=True)
    except (KafkaAdapterError, KafkaAdapterNotConfiguredError, SocServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        runner.close()

    print(
        json.dumps(
            {
                "schema_version": "soc.kafka_consume_result.v1",
                "settings": settings.model_dump(mode="json", exclude_none=True),
                "counters": {
                    "processed": loop_result.processed_count,
                    "dead_lettered": loop_result.dead_lettered_count,
                    "idle": loop_result.idle_count,
                    "committed": loop_result.committed_count,
                },
                "results": [_kafka_runner_result_payload(result) for result in loop_result.results],
            },
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


def _daemon_run(args: argparse.Namespace) -> int:
    if args.max_loops is not None and args.max_loops < 1:
        print("error: --max-loops must be >= 1", file=sys.stderr)
        return 2
    if args.idle_sleep_ms < 0:
        print("error: --idle-sleep-ms must be >= 0", file=sys.stderr)
        return 2
    if args.error_backoff_ms < 0:
        print("error: --error-backoff-ms must be >= 0", file=sys.stderr)
        return 2
    if args.max_consecutive_errors < 0:
        print("error: --max-consecutive-errors must be >= 0", file=sys.stderr)
        return 2

    settings = KafkaConsumerSettings.from_env()
    try:
        daemon_service = _daemon_service_from_args(args) if settings.enabled else SocDaemonService()
        consumer = build_kafka_consumer_port(settings)
    except (ValueError, KafkaAdapterNotConfiguredError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    runner = SocKafkaConsumerRunner(
        consumer=consumer,
        daemon_service=daemon_service,
        alert_topics=frozenset(settings.alert_topics),
        approval_request_topics=frozenset(settings.approval_request_topics),
    )
    stop_signal = KafkaDaemonStopSignal()
    previous_signal_handlers = _install_daemon_signal_handlers(stop_signal)
    daemon_runner = SocKafkaDaemonRunner(
        runner=runner,
        stop_signal=stop_signal,
        idle_sleep_seconds=args.idle_sleep_ms / 1000,
        error_backoff_seconds=args.error_backoff_ms / 1000,
        max_consecutive_errors=args.max_consecutive_errors or None,
        metric_sink=_daemon_metric_sink(args.metric_jsonl),
    )

    try:
        run_result = daemon_runner.run(max_loops=args.max_loops)
    except (KafkaAdapterError, KafkaAdapterNotConfiguredError, SocServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        _restore_signal_handlers(previous_signal_handlers)

    print(
        json.dumps(
            _kafka_daemon_run_payload(
                run_result,
                settings=settings,
                include_results=args.include_results,
            ),
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


def _daemon_status(args: argparse.Namespace) -> int:
    settings = KafkaConsumerSettings.from_env()
    status = build_kafka_daemon_status(
        database_url=args.database_url,
        kafka_settings=settings,
        check_database=not args.skip_database_check,
        check_broker=args.check_broker,
    )
    print(status.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if status.ready else 1


def _daemon_service_from_args(args: argparse.Namespace) -> SocDaemonService:
    repository = _repository_from_args(args)
    analysis_service = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    )
    approval_service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    return SocDaemonService(
        analysis_service=analysis_service,
        approval_service=approval_service,
    )


def _install_daemon_signal_handlers(stop_signal: KafkaDaemonStopSignal) -> dict[signal.Signals, Any]:
    previous_handlers: dict[signal.Signals, Any] = {}

    def _request_stop(signum: int, _frame: Any) -> None:
        signal_name = signal.Signals(signum).name.lower()
        stop_signal.request_stop(f"signal:{signal_name}")

    for daemon_signal in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[daemon_signal] = signal.getsignal(daemon_signal)
        signal.signal(daemon_signal, _request_stop)
    return previous_handlers


def _restore_signal_handlers(previous_handlers: dict[signal.Signals, Any]) -> None:
    for daemon_signal, previous_handler in previous_handlers.items():
        signal.signal(daemon_signal, previous_handler)


def _daemon_metric_sink(target: str | None):
    if target is None:
        return None
    if target == "stdout":
        return JsonLineKafkaDaemonMetricSink(sys.stdout)
    if target == "stderr":
        return JsonLineKafkaDaemonMetricSink(sys.stderr)
    raise ValueError(f"unsupported metric jsonl target: {target}")


def _eval_offline(args: argparse.Namespace) -> int:
    try:
        samples = _load_payload_samples(args.path, args.glob)
        responses = load_eval_responses_jsonl(args.llm_response_jsonl) if args.llm_response_jsonl else None
        report = run_offline_eval(samples, responses=responses, model_name=args.model_name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report eval failure
        print(f"error: offline eval failed: {exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if report.failed_count == 0 else 1


def _db_init(args: argparse.Namespace) -> int:
    try:
        engine = _engine_from_args(args)
        create_soc_tables(engine)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(f"error: database init failed: {exc}", file=sys.stderr)
        return 1
    print("SOC database tables are ready.")
    return 0


def _db_upgrade(args: argparse.Namespace) -> int:
    try:
        database_url = resolve_database_url(args.database_url)
        upgrade_soc_schema(database_url, revision=args.revision)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report migration failure
        print(f"error: database upgrade failed: {exc}", file=sys.stderr)
        return 1
    print(f"SOC database schema upgraded to {args.revision}.")
    return 0


def _repository_from_args(args: argparse.Namespace) -> SqlAlchemyAlertRepository:
    engine = _engine_from_args(args)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyAlertRepository(session_factory)


def _engine_from_args(args: argparse.Namespace):
    database_url = resolve_database_url(args.database_url)
    return create_engine(to_sync_database_url(database_url), pool_pre_ping=True)


def _database_label(explicit_url: str | None) -> str:
    if explicit_url:
        return "explicit database"
    return "SOC_DATABASE_URL / DeerFlow postgres"


def _kafka_runner_result_payload(result: KafkaRunnerProcessResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status,
        "committed": result.committed,
        "dead_lettered": result.dead_lettered,
    }
    if result.record is not None:
        payload["record"] = {
            "topic": result.record.topic,
            "partition": result.record.partition,
            "offset": result.record.offset,
            "key": result.record.key.decode("utf-8", errors="replace") if isinstance(result.record.key, bytes) else result.record.key,
        }
    if result.daemon_result is not None:
        payload["daemon_result"] = result.daemon_result.model_dump(mode="json", exclude_none=True)
    if result.error:
        payload["error"] = result.error
    return payload


def _kafka_daemon_run_payload(
    result: KafkaDaemonRunResult,
    *,
    settings: KafkaConsumerSettings,
    include_results: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "soc.kafka_daemon_run_result.v1",
        "settings": settings.model_dump(mode="json", exclude_none=True),
        "stop_reason": result.stop_reason,
        "loop_count": result.loop_count,
        "counters": {
            "processed": result.processed_count,
            "dead_lettered": result.dead_lettered_count,
            "idle": result.idle_count,
            "committed": result.committed_count,
        },
        "metrics": {
            "started_at": result.started_at.isoformat(),
            "stopped_at": result.stopped_at.isoformat(),
            "error_count": result.error_count,
            "consecutive_error_count": result.consecutive_error_count,
            "last_success_at": result.last_success_at.isoformat() if result.last_success_at else None,
            "last_error_at": result.last_error_at.isoformat() if result.last_error_at else None,
            "last_error_type": result.last_error_type,
            "last_error_message": result.last_error_message,
        },
    }
    if include_results:
        payload["results"] = [_kafka_runner_result_payload(item) for item in result.results]
    return payload


def _load_payload(path: str | None, json_payload: str | None) -> dict[str, Any]:
    if bool(path) == bool(json_payload):
        raise ValueError("provide exactly one of PATH or --json")

    try:
        if json_payload:
            data = _load_json_object(json_payload, payload_label="alert JSON")
        else:
            data = json.loads(Path(path or "").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read alert file: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("alert JSON must be an object")
    return data


def _load_json_object(json_payload: str, *, payload_label: str) -> dict[str, Any]:
    try:
        data = json.loads(json_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{payload_label} must be an object")
    return data


def _write_report(path: str | None, content: str) -> None:
    if not path:
        return
    Path(path).write_text(f"{content}\n", encoding="utf-8")


def _load_payload_samples(path: str, glob_pattern: str) -> list[tuple[str, dict[str, Any]]]:
    sample_path = Path(path)
    if not sample_path.exists():
        raise ValueError(f"path does not exist: {sample_path}")

    files = [sample_path] if sample_path.is_file() else sorted(file for file in sample_path.glob(glob_pattern) if file.is_file())
    if not files:
        raise ValueError(f"no alert JSON files matched: {sample_path} ({glob_pattern})")

    samples: list[tuple[str, dict[str, Any]]] = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {file}: {exc}") from exc
        except OSError as exc:
            raise ValueError(f"cannot read alert file {file}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"alert JSON must be an object: {file}")
        samples.append((str(file), data))
    return samples


if __name__ == "__main__":
    raise SystemExit(main())
