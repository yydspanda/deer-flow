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


def _route_decision_text(*, route: str, allowed: bool, reason: str) -> str:
    status = "allowed" if allowed else "denied"
    parts = [f"SOC route {status}"]
    if route:
        parts.append(f"route={route}")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)
