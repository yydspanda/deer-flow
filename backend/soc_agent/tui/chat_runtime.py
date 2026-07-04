"""Runtime bridge between SOC chat stream events and DeerFlow TUI actions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from deerflow.tui.runtime import translate as translate_deerflow_event
from deerflow.tui.view_state import Action, AssistantError, RunEnded, RunStarted, SystemMessage
from soc_agent.contracts import ServiceRequestContext, SocAgentChatRequest, SocAgentStreamEvent


class _SocChatServiceLike(Protocol):
    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]:
        """Yield SOC chat stream events."""


def translate(event: SocAgentStreamEvent) -> list[Action]:
    """Map one SOC chat stream event to DeerFlow TUI reducer actions."""
    if event.type == "custom":
        return _translate_custom(event.data)
    return translate_deerflow_event(event)


def stream_actions(
    service: _SocChatServiceLike,
    request: SocAgentChatRequest | str,
    *,
    context: ServiceRequestContext | None = None,
) -> Iterator[Action]:
    """Yield a bracketed TUI action stream for one SOC chat turn."""
    yield RunStarted()
    try:
        for event in service.stream(request, context=context):
            yield from translate(event)
            if event.type == "end":
                return
        yield RunEnded()
    except Exception as exc:  # noqa: BLE001 - surface service/runtime errors in TUI
        yield AssistantError(str(exc) or exc.__class__.__name__)
        yield RunEnded()


def _translate_custom(data: dict[str, Any]) -> list[Action]:
    kind = data.get("kind")
    if kind == "soc.review_context":
        return [
            SystemMessage(
                _review_context_text(
                    queue_id=_as_str(data.get("queue_id")),
                    run_id=_as_str(data.get("run_id")),
                    alert_id=_as_str(data.get("alert_id")),
                )
            )
        ]
    if kind == "soc.skill_context":
        return [SystemMessage(_skill_context_text(data))]
    if kind == "soc.lead_agent_entry":
        return [
            SystemMessage(
                _lead_agent_entry_text(
                    agent_name=_as_str(data.get("agent_name")),
                    thread_id=_as_str(data.get("thread_id")),
                )
            )
        ]
    if kind == "soc.lead_agent_review_context":
        return [
            SystemMessage(
                _lead_agent_review_context_text(
                    queue_id=_as_str(data.get("queue_id")),
                    run_id=_as_str(data.get("run_id")),
                    alert_id=_as_str(data.get("alert_id")),
                    context_hash=_as_str(data.get("context_hash")),
                )
            )
        ]
    if kind == "soc.action_proposal":
        return [
            SystemMessage(
                _action_proposal_text(
                    proposal_id=_as_str(data.get("proposal_id")),
                    action=_as_str(data.get("action")),
                    confidence=data.get("confidence"),
                    reason=_as_str(data.get("reason")),
                )
            )
        ]
    if kind == "soc.action_proposal_error":
        return [SystemMessage(f"SOC action proposal rejected | {_as_str(data.get('error'))}", tone="error")]
    if kind == "soc.route_decision":
        return [
            SystemMessage(
                _route_decision_text(
                    route=_as_str(data.get("route")),
                    allowed=bool(data.get("allowed")),
                    reason=_as_str(data.get("reason")),
                ),
                tone="info" if data.get("allowed") else "error",
            )
        ]
    if kind == "soc.permission_decision":
        allowed = bool(data.get("allowed"))
        requires_human_approval = bool(data.get("requires_human_approval"))
        return [
            SystemMessage(
                _permission_decision_text(
                    action=_as_str(data.get("action")),
                    allowed=allowed,
                    risk_level=_as_str(data.get("risk_level")),
                    reason=_as_str(data.get("reason")),
                    requires_human_approval=requires_human_approval,
                ),
                tone="info" if allowed else "error",
            )
        ]
    if kind == "soc.approval_request":
        return [
            SystemMessage(
                _approval_request_text(
                    approval_request_id=_as_str(data.get("approval_request_id")),
                    action=_as_str(data.get("action")),
                    risk_level=_as_str(data.get("risk_level")),
                    status=_as_str(data.get("status")),
                ),
                tone="error",
            )
        ]
    if kind == "soc.action_result":
        status = _as_str(data.get("status"))
        return [
            SystemMessage(
                _action_result_text(
                    action=_as_str(data.get("action")),
                    status=status,
                    message=_as_str(data.get("message")),
                ),
                tone="error" if status in {"denied", "failed"} else "info",
            )
        ]
    return []


def _review_context_text(*, queue_id: str, run_id: str, alert_id: str) -> str:
    parts = ["SOC review context loaded"]
    if queue_id:
        parts.append(f"queue={queue_id}")
    if alert_id:
        parts.append(f"alert={alert_id}")
    if run_id:
        parts.append(f"run={run_id}")
    return " | ".join(parts)


def _skill_context_text(data: dict[str, Any]) -> str:
    selected = data.get("selected_skills")
    skill_names: list[str] = []
    if isinstance(selected, list):
        for item in selected:
            if isinstance(item, dict):
                skill_name = item.get("skill_name")
                if isinstance(skill_name, str) and skill_name:
                    skill_names.append(skill_name)
    parts = ["SOC skill context"]
    if skill_names:
        parts.append(f"skills={','.join(skill_names)}")
    token_budget = data.get("total_token_budget")
    if isinstance(token_budget, int):
        parts.append(f"token_budget={token_budget}")
    return " | ".join(parts)


def _lead_agent_entry_text(*, agent_name: str, thread_id: str) -> str:
    parts = ["SOC lead agent entry"]
    if agent_name:
        parts.append(f"agent={agent_name}")
    if thread_id:
        parts.append(f"thread={thread_id}")
    return " | ".join(parts)


def _lead_agent_review_context_text(*, queue_id: str, run_id: str, alert_id: str, context_hash: str) -> str:
    parts = ["SOC lead agent review context"]
    if queue_id:
        parts.append(f"queue={queue_id}")
    if alert_id:
        parts.append(f"alert={alert_id}")
    if run_id:
        parts.append(f"run={run_id}")
    if context_hash:
        parts.append(f"hash={context_hash[:12]}")
    return " | ".join(parts)


def _action_proposal_text(*, proposal_id: str, action: str, confidence: Any, reason: str) -> str:
    parts = ["SOC action proposal"]
    if proposal_id:
        parts.append(f"id={proposal_id}")
    if action:
        parts.append(f"action={action}")
    if isinstance(confidence, int | float):
        parts.append(f"confidence={confidence:.2f}")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def _route_decision_text(*, route: str, allowed: bool, reason: str) -> str:
    status = "allowed" if allowed else "denied"
    parts = [f"SOC route {status}"]
    if route:
        parts.append(f"route={route}")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def _permission_decision_text(
    *,
    action: str,
    allowed: bool,
    risk_level: str,
    reason: str,
    requires_human_approval: bool,
) -> str:
    status = "allowed" if allowed else "denied"
    parts = [f"SOC permission {status}"]
    if action:
        parts.append(f"action={action}")
    if risk_level:
        parts.append(f"risk={risk_level}")
    if requires_human_approval:
        parts.append("approval_required")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def _approval_request_text(*, approval_request_id: str, action: str, risk_level: str, status: str) -> str:
    parts = ["SOC approval request"]
    if approval_request_id:
        parts.append(f"id={approval_request_id}")
    if action:
        parts.append(f"action={action}")
    if risk_level:
        parts.append(f"risk={risk_level}")
    if status:
        parts.append(f"status={status}")
    return " | ".join(parts)


def _action_result_text(*, action: str, status: str, message: str) -> str:
    parts = ["SOC action result"]
    if action:
        parts.append(f"action={action}")
    if status:
        parts.append(f"status={status}")
    if message:
        parts.append(message)
    return " | ".join(parts)


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)
