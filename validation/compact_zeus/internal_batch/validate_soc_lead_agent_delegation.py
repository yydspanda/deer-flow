#!/usr/bin/env python3
"""Run one repeatable SOC Lead Agent -> native specialist delegation smoke."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from deerflow.subagents.status_contract import (  # noqa: E402
    SUBAGENT_STATUS_KEY,
    SUBAGENT_STOP_REASON_KEY,
)
from soc_agent.contracts import (  # noqa: E402
    ActorAuthSource,
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocAgentChatRequest,
)
from soc_agent.core import SocReviewService  # noqa: E402
from soc_agent.db import SqlAlchemyAlertRepository, to_sync_database_url  # noqa: E402
from soc_agent.lead_agent_chat import SocLeadAgentChatService  # noqa: E402
from soc_agent.subagents import (  # noqa: E402
    SOC_NETWORK_SPECIALIST_NAME,
    SOC_SPECIALIST_SUBAGENT_NAMES,
)

REPORT_SCHEMA_VERSION = "soc.lead_agent_specialist_smoke.v1"
DEFAULT_MESSAGE = (
    "请先通过 DeerFlow 原生 task 工具委派 soc-network-specialist，仅基于当前 ReviewQueue 的受控证据复核攻击方向、"
    "反弹 shell 判断和最关键证据缺口；task.prompt 只写一个不超过 1200 字符的窄问题，不要重复案例字段，"
    "因为服务端会注入证据；然后由主 Agent 汇总真实专家返回，明确哪些是事实、推断和待人工核查项。"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--queue-id", required=True)
    parser.add_argument("--model-name", default="deepseek-v4-flash")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument(
        "--expected-specialist",
        choices=SOC_SPECIALIST_SUBAGENT_NAMES,
        default=SOC_NETWORK_SPECIALIST_NAME,
        help="Require exactly one completed delegation to this specialist.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Optional stable chat thread. A timestamped validation thread is used by default.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON report path; defaults below backend/.deer-flow/.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started_at = datetime.now(UTC)
    thread_id = args.thread_id or ("SOC-PI01G-SMOKE-" + started_at.strftime("%Y%m%dT%H%M%SZ"))
    output_path = args.output or (BACKEND_ROOT / ".deer-flow" / "soc-lead-agent-validation" / f"{thread_id}.json")

    repository = _repository(args.database_url)
    review_service = _review_service(repository)
    service = SocLeadAgentChatService(
        model_name=args.model_name,
        review_service=review_service,
    )
    service_context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-pi01g-validator",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.CLI,
            roles=["soc_analyst"],
            auth_source=ActorAuthSource.LOCAL_CLI,
        )
    )

    events = [
        event.model_dump(mode="json", exclude_none=True)
        for event in service.stream(
            SocAgentChatRequest(
                message=args.message,
                thread_id=thread_id,
                queue_id=args.queue_id,
            ),
            context=service_context,
        )
    ]
    report = _build_report(
        queue_id=args.queue_id,
        thread_id=thread_id,
        model_name=args.model_name,
        expected_specialist=args.expected_specialist,
        message=args.message,
        started_at=started_at,
        events=events,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {output_path}")
    return 0 if report["summary"]["passed"] else 2


def _repository(database_url: str) -> SqlAlchemyAlertRepository:
    engine = create_engine(
        to_sync_database_url(database_url),
        pool_pre_ping=True,
    )
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _review_service(repository: SqlAlchemyAlertRepository) -> SocReviewService:
    return SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        enrichment_execution_repository=repository,
        authorization_enrichment_repository=repository,
        disposition_proposal_repository=repository,
        disposition_evaluation_repository=repository,
        external_disposition_repository=repository,
        memory_candidate_repository=repository,
        memory_record_repository=repository,
    )


def _build_report(
    *,
    queue_id: str,
    thread_id: str,
    model_name: str,
    expected_specialist: str,
    message: str,
    started_at: datetime,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    custom_types = [str(event["data"].get("type")) for event in events if event["type"] == "custom" and event["data"].get("type")]
    task_started = custom_types.count("task_started")
    task_completed = custom_types.count("task_completed")
    task_failed = custom_types.count("task_failed")
    review_context_events = [event for event in events if event["type"] == "custom" and event["data"].get("kind") == "soc.lead_agent_review_context"]
    delegation_provenance = _find_additional_kwargs(
        events,
        "soc_specialist_delegation",
    )
    task_result_metadata = _find_task_result_metadata(events)
    capped_task_count = sum(1 for item in task_result_metadata if item.get(SUBAGENT_STOP_REASON_KEY) is not None)
    failed_delegation_count = sum(1 for item in delegation_provenance if item.get("result_status") != "accepted_advisory")
    completed_specialist_names = [str(item["specialist_name"]) for item in task_result_metadata if item.get(SUBAGENT_STATUS_KEY) == "completed" and item.get("specialist_name")]
    expected_specialist_completed = completed_specialist_names == [expected_specialist]
    review_context_provenance = _find_additional_kwargs(
        events,
        "soc_lead_agent_review_context",
    )
    assistant_text = "".join(str(event["data"].get("content") or "") for event in events if event["type"] == "messages-tuple" and event["data"].get("type") == "ai").strip()
    passed = all(
        (
            len(review_context_events) == 1,
            task_started >= 1,
            task_completed >= 1,
            task_failed == 0,
            bool(delegation_provenance),
            bool(task_result_metadata),
            capped_task_count == 0,
            failed_delegation_count == 0,
            expected_specialist_completed,
            bool(review_context_provenance),
            bool(assistant_text),
        )
    )
    finished_at = datetime.now(UTC)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": finished_at.isoformat(),
        "request": {
            "queue_id": queue_id,
            "thread_id": thread_id,
            "model_name": model_name,
            "expected_specialist": expected_specialist,
            "message": message,
        },
        "summary": {
            "passed": passed,
            "review_context_event_count": len(review_context_events),
            "task_started_count": task_started,
            "task_completed_count": task_completed,
            "task_failed_count": task_failed,
            "capped_task_count": capped_task_count,
            "failed_delegation_count": failed_delegation_count,
            "completed_specialist_names": completed_specialist_names,
            "expected_specialist_completed": expected_specialist_completed,
            "delegation_provenance_count": len(delegation_provenance),
            "review_context_provenance_count": len(review_context_provenance),
            "assistant_text_present": bool(assistant_text),
            "elapsed_seconds": round(
                (finished_at - started_at).total_seconds(),
                3,
            ),
            "real_model_called": True,
            "provider_acceptance_claimed": False,
        },
        "review_context": review_context_events[0]["data"] if review_context_events else None,
        "task_result_metadata": task_result_metadata,
        "delegation_provenance": delegation_provenance,
        "review_context_provenance": review_context_provenance,
        "assistant_text": assistant_text,
        "events": events,
    }


def _find_additional_kwargs(
    events: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for event in events:
        candidates: list[dict[str, Any]] = []
        data = event.get("data") or {}
        if isinstance(data.get("additional_kwargs"), dict):
            candidates.append(data["additional_kwargs"])
        if event.get("type") == "values":
            for item in data.get("messages") or []:
                if isinstance(item, dict) and isinstance(
                    item.get("additional_kwargs"),
                    dict,
                ):
                    candidates.append(item["additional_kwargs"])
        for additional_kwargs in candidates:
            value = additional_kwargs.get(key)
            if not isinstance(value, dict):
                continue
            fingerprint = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            values.append(value)
    return values


def _find_task_result_metadata(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return each terminal task result once, including cap/failure metadata."""

    values: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for event in events:
        data = event.get("data") or {}
        candidates: list[dict[str, Any]] = []
        if event.get("type") == "messages-tuple" and data.get("type") == "tool":
            candidates.append(data)
        if event.get("type") == "values":
            candidates.extend(item for item in data.get("messages") or [] if isinstance(item, dict) and item.get("type") == "tool")
        for candidate in candidates:
            additional_kwargs = candidate.get("additional_kwargs")
            if not isinstance(additional_kwargs, dict):
                continue
            if SUBAGENT_STATUS_KEY not in additional_kwargs:
                continue
            provenance = additional_kwargs.get("soc_specialist_delegation")
            if not isinstance(provenance, dict):
                continue
            tool_call_id = str(candidate.get("tool_call_id") or provenance.get("tool_call_id") or "")
            fingerprint = tool_call_id or json.dumps(
                additional_kwargs,
                ensure_ascii=False,
                sort_keys=True,
            )
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            values.append(
                {
                    "tool_call_id": tool_call_id or None,
                    "specialist_name": provenance.get("specialist_name"),
                    SUBAGENT_STATUS_KEY: additional_kwargs.get(SUBAGENT_STATUS_KEY),
                    SUBAGENT_STOP_REASON_KEY: additional_kwargs.get(SUBAGENT_STOP_REASON_KEY),
                    "result_status": provenance.get("result_status"),
                    "model_name": additional_kwargs.get("subagent_model_name"),
                    "token_usage": additional_kwargs.get("subagent_token_usage"),
                }
            )
    return values


if __name__ == "__main__":
    raise SystemExit(main())
