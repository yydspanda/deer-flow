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

from soc_agent.actions.adapters import (
    InMemoryAssetLookupActionAdapter,
    InMemoryEndpointProcessTreeLookupActionAdapter,
    InMemoryHostEventContextLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    InMemoryThreatIntelIpReputationLookupActionAdapter,
    SocActionAdapterRegistry,
)
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
    CorrelationQuery,
    EntrySurface,
    InvestigationContext,
    NormalizationBaselineAcceptCommand,
    NormalizationBaselineStatus,
    NormalizationMaintenanceIssueStatus,
    NormalizationMaintenanceIssueUpdateCommand,
    ReviewNoteCommand,
    ReviewQueueCloseCommand,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocAgentActionCommand,
    SocDomainName,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateStatus,
    SocMemoryQuery,
    SocMemoryRecordStatus,
    Verdict,
)
from soc_agent.core import (
    DeterministicAnalysisRuntime,
    SocAgentActionDispatcher,
    SocAgentApprovalService,
    SocAgentCapabilityRouter,
    SocAgentChatService,
    SocAnalysisService,
    SocCorrelationService,
    SocDaemonService,
    SocMemoryService,
    SocNormalizationMaintenanceService,
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
from soc_agent.demo import run_pingan_investigation_demo
from soc_agent.eval import (
    DEFAULT_PINGAN_CAPABILITY_EVAL_DIR,
    build_confidence_label_set,
    calibrate_confidence,
    calibration_samples_from_label_set,
    load_analysis_runs_for_labeling,
    load_confidence_label_set,
    load_eval_responses_jsonl,
    load_pingan_capability_eval_fixtures,
    load_scenario_eval_report,
    run_offline_eval,
    run_pingan_capability_eval,
    run_pingan_domain_triage_eval,
    run_pingan_main_orchestrator_eval,
    run_scenario_eval,
    validate_confidence_label_set,
)
from soc_agent.lead_agent import build_soc_lead_agent_profile
from soc_agent.lead_agent_chat import SocLeadAgentChatService
from soc_agent.llm import (
    SocLLMSettings,
    build_configured_analyzer,
    build_configured_chat_client,
    configured_soc_llm_status,
)
from soc_agent.normalizers import (
    build_normalization_suggestion_prompt,
    build_normalization_suggestion_report,
    run_live_normalization_suggestion,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return _analyze(args)
    if args.command == "list":
        return _list(args)
    if args.command == "correlate":
        return _correlate(args)
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
    if args.command == "normalize" and args.normalize_command == "baseline-accept":
        return _normalize_baseline_accept(args)
    if args.command == "normalize" and args.normalize_command == "baselines":
        return _normalize_baselines(args)
    if args.command == "normalize" and args.normalize_command == "issues":
        return _normalize_issues(args)
    if args.command == "normalize" and args.normalize_command == "issue-update":
        return _normalize_issue_update(args)
    if args.command == "normalize" and args.normalize_command == "suggest":
        return _normalize_suggest(args)
    if args.command == "review" and args.review_command == "list":
        return _review_list(args)
    if args.command == "review" and args.review_command == "context":
        return _review_context(args)
    if args.command == "review" and args.review_command == "note":
        return _review_note(args)
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
    if args.command == "llm" and args.llm_command == "status":
        return _llm_status(args)
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
    if args.command == "eval" and args.eval_command == "pingan":
        return _eval_pingan(args)
    if args.command == "eval" and args.eval_command == "pingan-domain":
        return _eval_pingan_domain(args)
    if args.command == "eval" and args.eval_command == "pingan-main":
        return _eval_pingan_main(args)
    if args.command == "eval" and args.eval_command == "scenarios":
        return _eval_scenarios(args)
    if args.command == "eval" and args.eval_command == "labels" and args.eval_labels_command == "prepare":
        return _eval_labels_prepare(args)
    if args.command == "eval" and args.eval_command == "labels" and args.eval_labels_command == "validate":
        return _eval_labels_validate(args)
    if args.command == "eval" and args.eval_command == "confidence":
        return _eval_confidence(args)
    if args.command == "demo" and args.demo_command == "run":
        return _demo_run(args)
    if args.command == "demo" and args.demo_command == "alert":
        return _demo_alert(args)
    if args.command == "memory" and args.memory_command == "list":
        return _memory_list(args)
    if args.command == "memory" and args.memory_command == "get":
        return _memory_get(args)
    if args.command == "memory" and args.memory_command == "review":
        return _memory_review(args)
    if args.command == "memory" and args.memory_command == "search":
        return _memory_search(args)
    if args.command == "memory" and args.memory_command == "records" and args.memory_records_command == "list":
        return _memory_records_list(args)
    if args.command == "memory" and args.memory_command == "records" and args.memory_records_command == "get":
        return _memory_records_get(args)
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
    _add_analyzer_args(analyze)
    _add_database_args(analyze)

    list_cmd = subparsers.add_parser("list", help="List persisted SOC alert summaries")
    list_cmd.add_argument("--limit", type=int, default=50, help="Maximum summaries to return")
    list_cmd.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(list_cmd)

    correlate = subparsers.add_parser("correlate", help="Correlate one persisted SOC alert summary")
    correlate.add_argument("run_id", help="Run id whose alert summary should be correlated")
    correlate.add_argument("--limit", type=int, default=10, help="Maximum similar alerts to return")
    correlate.add_argument("--candidate-limit", type=int, default=200, help="Maximum recent summaries to score")
    correlate.add_argument("--evidence-limit", type=int, default=5, help="Maximum reusable evidence refs per match")
    correlate.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(correlate)

    show = subparsers.add_parser("show", help="Show one persisted SOC analysis run")
    show.add_argument("run_id", help="Run id to load")
    show.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(show)

    replay = subparsers.add_parser("replay", help="Replay one persisted SOC analysis run")
    replay.add_argument("run_id", help="Run id to replay")
    replay.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_analyzer_args(replay)
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
    normalize_drift.add_argument(
        "--schema-baseline",
        help="Prior drift-report JSON or fingerprint-list JSON used to flag novel message schemas",
    )
    normalize_drift.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(normalize_drift)
    normalize_baseline_accept = normalize_subparsers.add_parser(
        "baseline-accept",
        help="Accept a governed schema fingerprint baseline",
    )
    normalize_baseline_accept.add_argument("--adapter", required=True, help="Normalizer adapter name")
    normalize_baseline_accept.add_argument("--parser-name", required=True, help="Message parser name")
    normalize_baseline_accept.add_argument("--parser-version", required=True, help="Message parser version")
    normalize_baseline_accept.add_argument(
        "--fingerprint",
        action="append",
        required=True,
        help="Accepted schema fingerprint; repeat for multiple values",
    )
    normalize_baseline_accept.add_argument("--tenant-id", help="Optional tenant scope")
    normalize_baseline_accept.add_argument("--source-system", help="Optional source-system scope")
    normalize_baseline_accept.add_argument("--reason", required=True, help="Approval reason")
    normalize_baseline_accept.add_argument("--actor-id", default="soc-cli", help="Approving actor id")
    normalize_baseline_accept.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(normalize_baseline_accept)
    normalize_baselines = normalize_subparsers.add_parser("baselines", help="List schema baselines")
    normalize_baselines.add_argument(
        "--status",
        choices=[status.value for status in NormalizationBaselineStatus],
        default=NormalizationBaselineStatus.ACTIVE.value,
    )
    normalize_baselines.add_argument("--tenant-id")
    normalize_baselines.add_argument("--source-system")
    normalize_baselines.add_argument("--limit", type=int, default=50)
    normalize_baselines.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(normalize_baselines)
    normalize_issues = normalize_subparsers.add_parser("issues", help="List parser/mapping maintenance issues")
    normalize_issues.add_argument(
        "--status",
        choices=[status.value for status in NormalizationMaintenanceIssueStatus] + ["all"],
        default=NormalizationMaintenanceIssueStatus.OPEN.value,
    )
    normalize_issues.add_argument("--tenant-id")
    normalize_issues.add_argument("--source-system")
    normalize_issues.add_argument("--limit", type=int, default=50)
    normalize_issues.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(normalize_issues)
    normalize_issue_update = normalize_subparsers.add_parser(
        "issue-update",
        help="Acknowledge, resolve, or ignore a normalization issue",
    )
    normalize_issue_update.add_argument("issue_id")
    normalize_issue_update.add_argument(
        "--status",
        required=True,
        choices=["acknowledged", "resolved", "ignored"],
    )
    normalize_issue_update.add_argument("--reason", required=True)
    normalize_issue_update.add_argument("--actor-id", default="soc-cli")
    normalize_issue_update.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(normalize_issue_update)
    normalize_suggest = normalize_subparsers.add_parser(
        "suggest",
        help="Generate offline-only normalization mapping candidates",
    )
    normalize_suggest.add_argument("path", help="Path to one alert JSON file")
    normalize_suggest.add_argument("--llm-response", help="Optional replayed LLM response JSON/text file")
    normalize_suggest.add_argument("--live-llm", action="store_true", help="Call a configured DeerFlow model instead of replaying a response")
    normalize_suggest.add_argument("--model-name", help="Configured model for --live-llm or model label for replay")
    normalize_suggest.add_argument("--prompt-out", help="Write the sanitized offline prompt bundle as JSON")
    normalize_suggest.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")

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
    review_context.add_argument("--summary", action="store_true", help="Show compact analyst-facing summary")
    review_context.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_context)
    review_note = review_subparsers.add_parser("note", help="Record an analyst review note as pending memory candidate")
    review_note.add_argument("queue_id", help="Review queue id to annotate")
    review_note.add_argument("--note", required=True, help="Analyst note to capture as candidate memory")
    review_note.add_argument("--scenario-key", help="Optional scenario key this note applies to")
    review_note.add_argument("--domain", choices=[item.value for item in SocDomainName], help="Optional SOC domain this note applies to")
    review_note.add_argument("--finding-id", help="Optional domain finding id this note applies to")
    review_note.add_argument("--confidence", type=float, default=0.55, help="Candidate confidence, 0..1")
    review_note.add_argument("--metadata-json", help="Optional JSON object attached to the candidate source metadata")
    review_note.add_argument("--actor-id", default="soc-cli", help="Analyst actor id")
    review_note.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(review_note)
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

    llm = subparsers.add_parser("llm", help="SOC bounded LLM runtime helpers")
    llm_subparsers = llm.add_subparsers(dest="llm_command")
    llm_status = llm_subparsers.add_parser("status", help="Show secret-free SOC LLM model resolution status")
    _add_analyzer_args(llm_status)
    llm_status.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")

    daemon = subparsers.add_parser("daemon", help="SOC daemon helpers")
    daemon_subparsers = daemon.add_subparsers(dest="daemon_command")
    daemon_process = daemon_subparsers.add_parser("process", help="Process one decoded SOC daemon message JSON")
    daemon_process.add_argument("path", nargs="?", help="Path to daemon message JSON file")
    daemon_process.add_argument("--json", dest="json_payload", help="Inline daemon message JSON object")
    daemon_process.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_analyzer_args(daemon_process)
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
    _add_analyzer_args(daemon_consume)
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
    _add_analyzer_args(daemon_run)
    _add_database_args(daemon_run)

    eval_cmd = subparsers.add_parser("eval", help="SOC offline evaluation helpers")
    eval_subparsers = eval_cmd.add_subparsers(dest="eval_command")
    eval_offline = eval_subparsers.add_parser("offline", help="Run stub-vs-LLM replay diff over alert samples")
    eval_offline.add_argument("path", help="Path to an alert JSON file or directory")
    eval_offline.add_argument("--glob", default="*.json", help="Glob used when PATH is a directory")
    eval_offline.add_argument("--llm-response-jsonl", help="Replayable LLM response JSONL keyed by sample_id")
    eval_offline.add_argument("--live-llm", action="store_true", help="Call the configured model for each sample")
    eval_offline.add_argument("--model-name", help="Configured live model or replay model label")
    eval_offline.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    eval_scenarios = eval_subparsers.add_parser("scenarios", help="Run vendor-neutral deterministic scenario taxonomy eval")
    eval_scenarios.add_argument("path", help="Path to an alert JSON file or directory")
    eval_scenarios.add_argument("--glob", default="*.json", help="Glob used when PATH is a directory")
    eval_scenarios.add_argument("--baseline-json", help="Prior scenario eval report JSON used for replay diff")
    eval_scenarios.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    eval_pingan = eval_subparsers.add_parser("pingan", help="Run PingAn SOC capability fixture eval")
    eval_pingan.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR),
        help="Path to a PingAn capability fixture JSON file or directory",
    )
    eval_pingan.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    eval_pingan_domain = eval_subparsers.add_parser("pingan-domain", help="Run PingAn SOC domain triage fixture eval")
    eval_pingan_domain.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR),
        help="Path to a PingAn capability fixture JSON file or directory",
    )
    eval_pingan_domain.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    eval_pingan_main = eval_subparsers.add_parser("pingan-main", help="Run PingAn SOC main-orchestrator demo eval")
    eval_pingan_main.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR),
        help="Path to a PingAn capability fixture JSON file or directory",
    )
    eval_pingan_main.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    eval_labels = eval_subparsers.add_parser("labels", help="Prepare and validate governed human confidence labels")
    eval_labels_subparsers = eval_labels.add_subparsers(dest="eval_labels_command")
    eval_labels_prepare = eval_labels_subparsers.add_parser(
        "prepare",
        help="Create a pending label set from complete live-LLM AnalysisRun JSON artifacts",
    )
    eval_labels_prepare.add_argument("path", help="Path to an AnalysisRun JSON file or directory")
    eval_labels_prepare.add_argument("--glob", default="*.json", help="Glob used when PATH is a directory")
    eval_labels_prepare.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    eval_labels_validate = eval_labels_subparsers.add_parser(
        "validate",
        help="Validate analyst review completion and calibration scope",
    )
    eval_labels_validate.add_argument("path", help="Confidence label-set JSON path")
    eval_labels_validate.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    eval_confidence = eval_subparsers.add_parser(
        "confidence",
        help="Calibrate analysis confidence from one governed, fully reviewed label set",
    )
    eval_confidence.add_argument("path", help="Reviewed confidence label-set JSON path")
    eval_confidence.add_argument("--bins", type=int, default=10, help="Number of confidence bins")
    eval_confidence.add_argument("--target-accuracy", type=float, default=0.9)
    eval_confidence.add_argument("--minimum-samples", type=int, default=30)
    eval_confidence.add_argument("--minimum-threshold-samples", type=int, default=10)
    eval_confidence.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")

    demo = subparsers.add_parser("demo", help="SOC persistent demo helpers")
    demo_subparsers = demo.add_subparsers(dest="demo_command")
    demo_run = demo_subparsers.add_parser("run", help="Seed a reviewable SOC investigation demo")
    demo_run.add_argument(
        "scenario",
        nargs="?",
        choices=["all", "apt", "edr", "hids"],
        default="all",
        help="PingAn demo scenario to seed",
    )
    demo_run.add_argument(
        "--path",
        default=str(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR),
        help="Path to a PingAn capability fixture JSON file or directory",
    )
    demo_run.add_argument("--init-db", action="store_true", help="Create SOC tables before seeding")
    demo_run.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(demo_run)
    demo_alert = demo_subparsers.add_parser("alert", help="Run one alert through SOC services and print a review-ready summary")
    demo_alert.add_argument("path", nargs="?", help="Path to alert JSON file")
    demo_alert.add_argument("--json", dest="json_payload", help="Inline alert JSON object")
    demo_alert.add_argument("--init-db", action="store_true", help="Create SOC tables before running")
    demo_alert.add_argument("--review-note", help="Optional analyst note to capture after the queue item is created")
    demo_alert.add_argument("--scenario-key", help="Optional scenario key for --review-note")
    demo_alert.add_argument("--domain", choices=[item.value for item in SocDomainName], help="Optional SOC domain for --review-note")
    demo_alert.add_argument("--finding-id", help="Optional domain finding id for --review-note")
    demo_alert.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_analyzer_args(demo_alert)
    _add_database_args(demo_alert)

    memory = subparsers.add_parser("memory", help="SOC memory candidate helpers")
    memory_subparsers = memory.add_subparsers(dest="memory_command")
    memory_list = memory_subparsers.add_parser("list", help="List SOC memory candidates")
    memory_list.add_argument(
        "--status",
        choices=[status.value for status in SocMemoryCandidateStatus],
        default=SocMemoryCandidateStatus.PENDING_REVIEW.value,
        help="Memory candidate status to list",
    )
    memory_list.add_argument("--tenant-scope", help="Filter by tenant scope")
    memory_list.add_argument("--tenant-id", help="Filter by tenant id")
    memory_list.add_argument("--run-id", help="Filter by source run id")
    memory_list.add_argument("--alert-id", help="Filter by source alert id")
    memory_list.add_argument("--queue-id", help="Filter by source review queue id")
    memory_list.add_argument("--limit", type=int, default=50, help="Maximum candidates to return")
    memory_list.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(memory_list)
    memory_get = memory_subparsers.add_parser("get", help="Get one SOC memory candidate")
    memory_get.add_argument("candidate_id", help="Memory candidate id to load")
    memory_get.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(memory_get)
    memory_review = memory_subparsers.add_parser("review", help="Review one SOC memory candidate")
    memory_review.add_argument("candidate_id", help="Memory candidate id to review")
    memory_review.add_argument(
        "--decision",
        required=True,
        choices=[item.value for item in SocMemoryCandidateReviewDecision],
        help="Review decision to apply",
    )
    memory_review.add_argument("--reason", required=True, help="Human review reason")
    memory_review.add_argument("--record-summary", help="Optional confirmed memory summary override")
    memory_review.add_argument("--record-content", help="Optional confirmed memory content override")
    memory_review.add_argument("--actor-id", default="soc-cli", help="Review actor id")
    memory_review.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(memory_review)
    memory_search = memory_subparsers.add_parser("search", help="Search retrieval-enabled SOC memory records")
    memory_search.add_argument("--query-json", help="Inline SocMemoryQuery JSON object")
    memory_search.add_argument("--term", action="append", default=[], help="Text term to match; repeatable")
    memory_search.add_argument("--facet", action="append", default=[], help="Facet match as KEY=VALUE; repeatable")
    memory_search.add_argument("--tenant-scope", help="Filter by tenant scope")
    memory_search.add_argument("--tenant-id", help="Filter by tenant id")
    memory_search.add_argument("--limit", type=int, default=8, help="Maximum matches to return")
    memory_search.add_argument("--max-tokens", type=int, default=1200, help="Token budget for returned matches")
    memory_search.add_argument("--min-score", type=float, default=1.0, help="Minimum retrieval score")
    memory_search.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(memory_search)
    memory_records = memory_subparsers.add_parser("records", help="SOC confirmed memory record helpers")
    memory_records_subparsers = memory_records.add_subparsers(dest="memory_records_command")
    memory_records_list = memory_records_subparsers.add_parser("list", help="List SOC memory records")
    memory_records_list.add_argument(
        "--status",
        choices=["", *[item.value for item in SocMemoryRecordStatus]],
        default=SocMemoryRecordStatus.CONFIRMED.value,
        help="Filter by memory record status; use empty string to list all",
    )
    memory_records_list.add_argument("--tenant-scope", help="Filter by tenant scope")
    memory_records_list.add_argument("--tenant-id", help="Filter by tenant id")
    memory_records_list.add_argument("--source-candidate-id", help="Filter by source candidate id")
    memory_records_list.add_argument(
        "--retrieval-enabled",
        choices=["true", "false", "all"],
        default="all",
        help="Filter by retrieval-enabled gate",
    )
    memory_records_list.add_argument("--limit", type=int, default=50, help="Maximum records to return")
    memory_records_list.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(memory_records_list)
    memory_records_get = memory_records_subparsers.add_parser("get", help="Get one SOC memory record")
    memory_records_get.add_argument("memory_id", help="Memory record id to load")
    memory_records_get.add_argument("--pretty", action="store_true", help="Pretty-print output JSON")
    _add_database_args(memory_records_get)

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


def _add_analyzer_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--analyzer-mode",
        choices=["stub", "llm"],
        help="Override SOC_ANALYZER_MODE for this command",
    )
    parser.add_argument(
        "--model-name",
        help="Override SOC_LLM_MODEL with a DeerFlow configured model name",
    )


def _analyze(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        repository = _repository_from_args(args) if args.persist else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        run = _analysis_service_for_repository(
            repository,
            settings=_llm_settings_from_args(args),
        ).analyze(payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
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


def _correlate(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        result = SocCorrelationService(
            summary_repository=repository,
            evidence_repository=repository,
        ).correlate(
            CorrelationQuery(
                run_id=args.run_id,
                limit=args.limit,
                candidate_limit=args.candidate_limit,
                evidence_limit_per_match=args.evidence_limit,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _replay(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        run = _analysis_service_for_repository(
            repository,
            settings=_llm_settings_from_args(args),
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
            external_disposition_repository=repository,
            memory_candidate_repository=repository,
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
        known_schema_fingerprints = _load_schema_fingerprint_baseline(args.schema_baseline)
        if args.recent_runs:
            if args.path:
                raise ValueError("PATH cannot be used with --recent-runs")
            if args.mapping:
                raise ValueError("--mapping cannot be used with --recent-runs")
            result = SocNormalizationService(repository=_repository_from_args(args)).drift_recent(
                limit=args.limit,
                known_schema_fingerprints=known_schema_fingerprints,
            )
        else:
            if not args.path:
                raise ValueError("provide PATH or --recent-runs")
            samples = _load_payload_samples(args.path, args.glob)
            result = SocNormalizationService().drift(
                samples,
                mapping_path=args.mapping,
                known_schema_fingerprints=known_schema_fingerprints,
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report drift failure
        print(f"error: normalization drift failed: {exc}", file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if result.failure_count == 0 else 1


def _normalize_baseline_accept(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        baseline = SocNormalizationMaintenanceService(
            baseline_repository=repository,
            issue_repository=repository,
        ).accept_baseline(
            NormalizationBaselineAcceptCommand(
                tenant_id=args.tenant_id,
                source_system=args.source_system,
                adapter=args.adapter,
                parser_name=args.parser_name,
                parser_version=args.parser_version,
                accepted_fingerprints=args.fingerprint,
                reason=args.reason,
            ),
            context=_normalization_cli_context(args.actor_id, roles=["soc_engineer"]),
        )
    except (ValueError, SocServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(baseline.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _normalize_baselines(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        baselines = SocNormalizationMaintenanceService(
            baseline_repository=repository,
        ).list_baselines(
            status=NormalizationBaselineStatus(args.status),
            tenant_id=args.tenant_id,
            source_system=args.source_system,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            [item.model_dump(mode="json", exclude_none=True) for item in baselines],
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


def _normalize_issues(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        status = None if args.status == "all" else NormalizationMaintenanceIssueStatus(args.status)
        issues = SocNormalizationMaintenanceService(
            issue_repository=repository,
        ).list_issues(
            status=status,
            tenant_id=args.tenant_id,
            source_system=args.source_system,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            [item.model_dump(mode="json", exclude_none=True) for item in issues],
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 0


def _normalize_issue_update(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        issue = SocNormalizationMaintenanceService(
            issue_repository=repository,
        ).update_issue(
            NormalizationMaintenanceIssueUpdateCommand(
                issue_id=args.issue_id,
                status=args.status,
                reason=args.reason,
            ),
            context=_normalization_cli_context(args.actor_id, roles=["soc_engineer"]),
        )
    except (ValueError, SocServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(issue.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _normalize_suggest(args: argparse.Namespace) -> int:
    try:
        if args.live_llm and args.llm_response:
            raise ValueError("--live-llm and --llm-response are mutually exclusive")
        payload = _load_payload(args.path, None)
        run = SocAnalysisService().analyze(payload)
        prompt = build_normalization_suggestion_prompt(run)
        if args.live_llm:
            settings = SocLLMSettings.from_env().with_overrides(
                mode="llm",
                model_name=args.model_name,
            )
            client, model_name = build_configured_chat_client(settings=settings)
            report = run_live_normalization_suggestion(
                run,
                client=client,
                model_name=model_name,
            )
        else:
            response_content = _load_optional_response(args.llm_response)
            report = build_normalization_suggestion_report(
                run,
                response_content=response_content,
                model_name=args.model_name,
            )
        if args.prompt_out:
            _write_report(args.prompt_out, prompt.model_dump_json(indent=2, exclude_none=True))
    except (ValueError, OSError) as exc:
        print(f"error: normalization suggestion failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports provider failures
        print(f"error: normalization suggestion model call failed: {exc}", file=sys.stderr)
        return 1
    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _normalization_cli_context(actor_id: str, *, roles: list[str]) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id=actor_id,
            actor_type=ActorType.USER,
            surface=EntrySurface.CLI,
            roles=roles,
        )
    )


def _load_optional_response(path: str | None) -> Any | None:
    if path is None:
        return None
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


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
            external_disposition_repository=repository,
            memory_candidate_repository=repository,
            memory_record_repository=repository,
        ).get_investigation_context(args.queue_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if args.summary:
        print(json.dumps(_review_context_summary_payload(context), ensure_ascii=False, indent=2 if args.pretty else None))
    else:
        print(context.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _review_note(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        metadata = _load_json_object(args.metadata_json, payload_label="metadata JSON") if args.metadata_json else {}
        result = SocReviewService(
            repository=repository,
            summary_repository=repository,
            audit_repository=repository,
            review_queue_repository=repository,
            evidence_repository=repository,
            external_disposition_repository=repository,
            memory_candidate_repository=repository,
            memory_record_repository=repository,
        ).add_note(
            ReviewNoteCommand(
                queue_id=args.queue_id,
                note=args.note,
                scenario_key=args.scenario_key,
                domain=SocDomainName(args.domain) if args.domain else None,
                finding_id=args.finding_id,
                confidence=args.confidence,
                metadata=metadata,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id=args.actor_id,
                    actor_type=ActorType.USER,
                    surface=EntrySurface.CLI,
                    roles=["soc_analyst"],
                )
            ),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _review_context_summary_payload(context: InvestigationContext) -> dict[str, Any]:
    view = context.investigation_view
    findings = [
        {
            "finding_id": finding.finding_id,
            "domain": finding.domain.value,
            "scenario_key": finding.scenario_key,
            "scenario_name": finding.scenario_name,
            "title": finding.title,
            "disposition": finding.disposition.value,
            "severity": finding.severity.value,
            "confidence": finding.confidence,
            "current_conclusion": finding.current_conclusion.model_dump(mode="json", exclude_none=True),
            "evidence_gaps": finding.evidence_profile.gaps,
            "recommendations": finding.recommendations,
            "human_checklist": finding.human_checklist,
        }
        for result in context.domain_triage_results
        for finding in result.findings
    ]
    memory_candidates = [
        {
            "candidate_id": candidate.candidate_id,
            "status": candidate.status.value,
            "candidate_type": candidate.candidate_type.value,
            "source_type": candidate.source.source_type.value,
            "summary": candidate.summary,
            "runtime_decision_allowed": candidate.runtime_decision_allowed,
        }
        for candidate in context.memory_candidates
    ]
    relevant_memories = []
    if context.relevant_memories is not None:
        relevant_memories = [
            {
                "memory_id": match.memory_id,
                "score": match.score,
                "summary": match.summary,
                "match_reasons": match.match_reasons,
                "retrieval_enabled": match.retrieval_enabled,
            }
            for match in context.relevant_memories.matches
        ]
    counts = dict(view.counts) if view is not None else {}
    queue_id = context.queue_item.queue_id
    return {
        "schema_version": "soc.review_context_summary.v1",
        "queue_id": queue_id,
        "run_id": context.run.run_id,
        "alert_id": context.run.alert_id,
        "status": context.queue_item.status.value,
        "priority": context.queue_item.priority.value,
        "runtime_verdict": context.run.decision.verdict.value if context.run.decision is not None else None,
        "runtime_confidence": context.run.decision.confidence if context.run.decision is not None else None,
        "needs_review": context.run.decision.needs_review if context.run.decision is not None else True,
        "primary_summary": view.primary_summary if view is not None else context.queue_item.summary,
        "primary_reason": view.primary_reason if view is not None else context.queue_item.reason,
        "counts": counts,
        "scenario_findings": findings,
        "memory_candidates": memory_candidates,
        "relevant_memories": relevant_memories,
        "next_commands": [
            f"soc review note {queue_id} --note '<analyst note>' --pretty",
            f"soc chat tui --queue-id {queue_id} --lead-agent",
            f"soc memory list --queue-id {queue_id} --pretty",
        ],
        "boundary_notes": view.boundary_notes if view is not None else [],
    }


def _memory_list(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        candidates = SocMemoryService(candidate_repository=repository, record_repository=repository).list_candidates(
            status=SocMemoryCandidateStatus(args.status) if args.status else None,
            tenant_scope=args.tenant_scope,
            tenant_id=args.tenant_id,
            run_id=args.run_id,
            alert_id=args.alert_id,
            queue_id=args.queue_id,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(json.dumps([item.model_dump(mode="json", exclude_none=True) for item in candidates], ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _memory_get(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        candidate = SocMemoryService(candidate_repository=repository, record_repository=repository).get_candidate(args.candidate_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(candidate.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _memory_review(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        result = SocMemoryService(candidate_repository=repository, record_repository=repository).review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=args.candidate_id,
                decision=SocMemoryCandidateReviewDecision(args.decision),
                reason=args.reason,
                record_summary=args.record_summary,
                record_content=args.record_content,
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id=args.actor_id,
                    actor_type=ActorType.USER,
                    surface=EntrySurface.CLI,
                    roles=["soc_analyst"],
                )
            ),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _memory_search(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        query = _memory_query_from_args(args)
        result = SocMemoryService(candidate_repository=repository, record_repository=repository).find_relevant_records(query)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(result.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _memory_query_from_args(args: argparse.Namespace) -> SocMemoryQuery:
    if args.query_json:
        return SocMemoryQuery.model_validate(_load_json_object(args.query_json, payload_label="memory query JSON"))
    facets: dict[str, list[str]] = {}
    for facet in args.facet:
        if "=" not in facet:
            raise ValueError("--facet must use KEY=VALUE")
        key, value = facet.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError("--facet must use non-empty KEY=VALUE")
        facets.setdefault(key, []).append(value)
    return SocMemoryQuery(
        tenant_scope=args.tenant_scope,
        tenant_id=args.tenant_id,
        facets=facets,
        text_terms=args.term,
        limit=args.limit,
        max_tokens=args.max_tokens,
        min_score=args.min_score,
        metadata={"source": "cli"},
    )


def _memory_records_list(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        records = SocMemoryService(candidate_repository=repository, record_repository=repository).list_records(
            status=SocMemoryRecordStatus(args.status) if args.status else None,
            tenant_scope=args.tenant_scope,
            tenant_id=args.tenant_id,
            source_candidate_id=args.source_candidate_id,
            retrieval_enabled=_optional_bool(args.retrieval_enabled),
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(json.dumps([item.model_dump(mode="json", exclude_none=True) for item in records], ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _memory_records_get(args: argparse.Namespace) -> int:
    try:
        repository = _repository_from_args(args)
        record = SocMemoryService(candidate_repository=repository, record_repository=repository).get_record(args.memory_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SocServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print(record.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _optional_bool(value: str) -> bool | None:
    if value == "all":
        return None
    return value == "true"


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
                external_disposition_repository=repository,
                memory_candidate_repository=repository,
                memory_record_repository=repository,
            ),
            approval_service=SocAgentApprovalService(grant_repository=repository, request_repository=repository),
            normalization_service=SocNormalizationMaintenanceService(
                baseline_repository=repository,
                issue_repository=repository,
            ),
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
            external_disposition_repository=repository,
            memory_candidate_repository=repository,
            memory_record_repository=repository,
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
    return SocActionAdapterRegistry(
        [
            InMemoryAssetLookupActionAdapter(),
            InMemoryEndpointProcessTreeLookupActionAdapter(),
            InMemoryHostEventContextLookupActionAdapter(),
            InMemorySecurityTagLookupActionAdapter(),
            InMemoryThreatIntelIpReputationLookupActionAdapter(),
        ]
    )


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


def _llm_status(args: argparse.Namespace) -> int:
    try:
        status = configured_soc_llm_status(settings=_llm_settings_from_args(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _daemon_process(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        repository = _repository_from_args(args)
        analysis_service = _analysis_service_for_repository(
            repository,
            settings=_llm_settings_from_args(args),
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
    analysis_service = _analysis_service_for_repository(
        repository,
        settings=_llm_settings_from_args(args),
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
        if args.live_llm and args.llm_response_jsonl:
            raise ValueError("--live-llm and --llm-response-jsonl are mutually exclusive")
        samples = _load_payload_samples(args.path, args.glob)
        responses = load_eval_responses_jsonl(args.llm_response_jsonl) if args.llm_response_jsonl else None
        client = None
        model_name = args.model_name or "replay-llm"
        if args.live_llm:
            settings = SocLLMSettings.from_env().with_overrides(
                mode="llm",
                model_name=args.model_name,
            )
            client, model_name = build_configured_chat_client(settings=settings)
        report = run_offline_eval(
            samples,
            responses=responses,
            client=client,
            model_name=model_name,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report eval failure
        print(f"error: offline eval failed: {exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if report.failed_count == 0 else 1


def _eval_pingan(args: argparse.Namespace) -> int:
    try:
        fixtures = load_pingan_capability_eval_fixtures(args.path)
        report = run_pingan_capability_eval(fixtures)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report eval failure
        print(f"error: PingAn capability eval failed: {exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if report.failed_count == 0 else 1


def _eval_pingan_domain(args: argparse.Namespace) -> int:
    try:
        fixtures = load_pingan_capability_eval_fixtures(args.path)
        report = run_pingan_domain_triage_eval(fixtures)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report eval failure
        print(f"error: PingAn domain triage eval failed: {exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if report.failed_count == 0 else 1


def _eval_pingan_main(args: argparse.Namespace) -> int:
    try:
        fixtures = load_pingan_capability_eval_fixtures(args.path)
        report = run_pingan_main_orchestrator_eval(fixtures)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report eval failure
        print(f"error: PingAn main orchestrator eval failed: {exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if report.failed_count == 0 else 1


def _eval_scenarios(args: argparse.Namespace) -> int:
    try:
        samples = _load_payload_samples(args.path, args.glob)
        baseline = load_scenario_eval_report(args.baseline_json) if args.baseline_json else None
        report = run_scenario_eval(samples, baseline=baseline)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report eval failure
        print(f"error: scenario eval failed: {exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if report.failed_count == 0 else 1


def _eval_confidence(args: argparse.Namespace) -> int:
    try:
        label_set = load_confidence_label_set(args.path)
        samples = calibration_samples_from_label_set(label_set)
        report = calibrate_confidence(
            samples,
            bin_count=args.bins,
            target_accuracy=args.target_accuracy,
            minimum_samples=args.minimum_samples,
            minimum_threshold_samples=args.minimum_threshold_samples,
            label_set_id=label_set.label_set_id,
        )
    except ValueError as exc:
        print(f"error: confidence calibration failed: {exc}", file=sys.stderr)
        return 2
    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _eval_labels_prepare(args: argparse.Namespace) -> int:
    try:
        runs = load_analysis_runs_for_labeling(args.path, glob_pattern=args.glob)
        label_set = build_confidence_label_set(runs)
    except ValueError as exc:
        print(f"error: confidence label preparation failed: {exc}", file=sys.stderr)
        return 2
    print(label_set.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0


def _eval_labels_validate(args: argparse.Namespace) -> int:
    try:
        label_set = load_confidence_label_set(args.path)
        report = validate_confidence_label_set(label_set)
    except ValueError as exc:
        print(f"error: confidence label validation failed: {exc}", file=sys.stderr)
        return 2
    print(report.model_dump_json(indent=2 if args.pretty else None, exclude_none=True))
    return 0 if report.calibratable else 1


def _demo_alert(args: argparse.Namespace) -> int:
    try:
        payload = _load_payload(args.path, args.json_payload)
        if args.init_db:
            create_soc_tables(_engine_from_args(args))
        repository = _repository_from_args(args)
        run = _analysis_service_for_repository(
            repository,
            settings=_llm_settings_from_args(args),
        ).analyze(
            payload,
            context=ServiceRequestContext(actor=ActorContext(actor_id="soc-demo", actor_type=ActorType.USER, surface=EntrySurface.CLI, roles=["soc_analyst"])),
        )
        review_item = repository.get_open_review_item_by_run(run.run_id)
        review_summary: dict[str, Any] | None = None
        note_candidate_id: str | None = None
        if review_item is not None:
            review_service = SocReviewService(
                repository=repository,
                summary_repository=repository,
                audit_repository=repository,
                review_queue_repository=repository,
                evidence_repository=repository,
                external_disposition_repository=repository,
                memory_candidate_repository=repository,
                memory_record_repository=repository,
            )
            if args.review_note:
                note_result = review_service.add_note(
                    ReviewNoteCommand(
                        queue_id=review_item.queue_id,
                        note=args.review_note,
                        scenario_key=args.scenario_key,
                        domain=SocDomainName(args.domain) if args.domain else None,
                        finding_id=args.finding_id,
                    ),
                    context=ServiceRequestContext(actor=ActorContext(actor_id="soc-demo", actor_type=ActorType.USER, surface=EntrySurface.CLI, roles=["soc_analyst"])),
                )
                if note_result.memory_candidate is not None:
                    note_candidate_id = note_result.memory_candidate.candidate_id
            review_summary = _review_context_summary_payload(review_service.get_investigation_context(review_item.queue_id))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(f"error: database access failed: {exc}", file=sys.stderr)
        return 1
    except SocServiceError as exc:
        print(f"error: SOC alert demo failed: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report demo failure
        print(f"error: SOC alert demo failed: {exc}", file=sys.stderr)
        return 1

    output = {
        "schema_version": "soc.one_alert_demo_result.v1",
        "run_id": run.run_id,
        "alert_id": run.alert_id,
        "status": run.status.value,
        "queue_id": review_item.queue_id if review_item is not None else None,
        "queue_created": review_item is not None,
        "note_memory_candidate_id": note_candidate_id,
        "runtime_verdict": run.decision.verdict.value if run.decision is not None else None,
        "runtime_confidence": run.decision.confidence if run.decision is not None else None,
        "review_context": review_summary,
        "next_commands": [
            f"soc show {run.run_id} --pretty",
            *(f"soc review context {review_item.queue_id} --summary --pretty" for _ in [review_item] if review_item is not None),
            *(f"soc chat tui --queue-id {review_item.queue_id} --lead-agent" for _ in [review_item] if review_item is not None),
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if run.status.value in {"success", "needs_review"} else 1


def _demo_run(args: argparse.Namespace) -> int:
    try:
        if args.init_db:
            create_soc_tables(_engine_from_args(args))
        fixtures = load_pingan_capability_eval_fixtures(args.path)
        repository = _repository_from_args(args)
        report = run_pingan_investigation_demo(fixtures, repository=repository, scenario=args.scenario)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(f"error: database access failed: {exc}", file=sys.stderr)
        return 1
    except SocServiceError as exc:
        print(f"error: SOC demo failed: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report demo seed failure
        print(f"error: SOC demo failed: {exc}", file=sys.stderr)
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


def _analysis_service_for_repository(
    repository: SqlAlchemyAlertRepository | None,
    *,
    settings: SocLLMSettings | None = None,
) -> SocAnalysisService:
    maintenance = (
        SocNormalizationMaintenanceService(
            baseline_repository=repository,
            issue_repository=repository,
        )
        if repository is not None
        else None
    )
    return SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=build_configured_analyzer(settings=settings),
        ),
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
        normalization_maintenance_monitor=maintenance,
    )


def _llm_settings_from_args(args: argparse.Namespace) -> SocLLMSettings:
    return SocLLMSettings.from_env().with_overrides(
        mode=getattr(args, "analyzer_mode", None),
        model_name=getattr(args, "model_name", None),
    )


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


def _load_schema_fingerprint_baseline(path: str | None) -> set[str] | None:
    if path is None:
        return None
    baseline_path = Path(path)
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid schema baseline JSON in {baseline_path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read schema baseline {baseline_path}: {exc}") from exc

    values: Any
    if isinstance(data, list):
        values = data
    elif isinstance(data, dict) and isinstance(data.get("accepted_schema_fingerprints"), list):
        values = data["accepted_schema_fingerprints"]
    elif isinstance(data, dict) and isinstance(data.get("schema_fingerprint_counts"), dict):
        values = list(data["schema_fingerprint_counts"])
    else:
        raise ValueError("schema baseline must be a fingerprint list, contain accepted_schema_fingerprints, or be a prior normalization drift report")
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("schema baseline fingerprints must be non-empty strings")
    return set(values)


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
