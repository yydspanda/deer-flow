from __future__ import annotations

from collections.abc import Iterator

from deerflow.tui.view_state import (
    AssistantDelta,
    AssistantError,
    RunEnded,
    RunStarted,
    SystemMessage,
    initial_state,
    reduce,
)
from soc_agent.contracts import ActorContext, EntrySurface, ServiceRequestContext, SocAgentChatRequest, SocAgentStreamEvent
from soc_agent.tui.chat_runtime import stream_actions, translate


def test_soc_chat_runtime_translates_deerflow_like_message_event() -> None:
    event = SocAgentStreamEvent(type="messages-tuple", data={"type": "ai", "id": "m1", "content": "Ready"})

    assert translate(event) == [AssistantDelta(id="m1", text="Ready")]


def test_soc_chat_runtime_translates_review_context_custom_event() -> None:
    event = SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.review_context",
            "queue_id": "REV-1",
            "run_id": "RUN-1",
            "alert_id": "ALT-1",
        },
    )

    assert translate(event) == [SystemMessage("SOC review context loaded | queue=REV-1 | alert=ALT-1 | run=RUN-1")]


def test_soc_chat_runtime_ignores_unknown_custom_event() -> None:
    event = SocAgentStreamEvent(type="custom", data={"kind": "soc.unknown"})

    assert translate(event) == []


def test_soc_chat_runtime_translates_route_decision_custom_event() -> None:
    event = SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.route_decision",
            "route": "review.open_context",
            "allowed": True,
            "reason": "route review.open_context is allowed by whitelist",
        },
    )

    assert translate(event) == [SystemMessage("SOC route allowed | route=review.open_context | route review.open_context is allowed by whitelist")]


def test_soc_chat_runtime_translates_denied_route_as_error() -> None:
    event = SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.route_decision",
            "route": "command.unknown",
            "allowed": False,
            "reason": "route command.unknown is not allowed",
        },
    )

    assert translate(event) == [SystemMessage("SOC route denied | route=command.unknown | route command.unknown is not allowed", tone="error")]


def test_soc_chat_runtime_translates_permission_decision_custom_event() -> None:
    event = SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.permission_decision",
            "action": "review.open_context",
            "allowed": True,
            "risk_level": "read_only",
            "reason": "action review.open_context is read-only",
        },
    )

    assert translate(event) == [SystemMessage("SOC permission allowed | action=review.open_context | risk=read_only | action review.open_context is read-only")]


def test_soc_chat_runtime_translates_approval_required_permission_as_error() -> None:
    event = SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.permission_decision",
            "action": "response.block_ip",
            "allowed": False,
            "risk_level": "high_risk",
            "reason": "action response.block_ip requires human approval",
            "requires_human_approval": True,
        },
    )

    assert translate(event) == [
        SystemMessage(
            "SOC permission denied | action=response.block_ip | risk=high_risk | approval_required | action response.block_ip requires human approval",
            tone="error",
        )
    ]


def test_soc_chat_runtime_translates_action_result_custom_event() -> None:
    event = SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.action_result",
            "action": "review.open_context",
            "status": "success",
            "message": "Loaded review context REV-1",
        },
    )

    assert translate(event) == [SystemMessage("SOC action result | action=review.open_context | status=success | Loaded review context REV-1")]


def test_soc_chat_runtime_translates_failed_action_result_as_error() -> None:
    event = SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.action_result",
            "action": "review.open_context",
            "status": "failed",
            "message": "queue_id is required",
        },
    )

    assert translate(event) == [SystemMessage("SOC action result | action=review.open_context | status=failed | queue_id is required", tone="error")]


class _FakeChatService:
    def __init__(self, events: list[SocAgentStreamEvent]) -> None:
        self._events = events
        self.calls: list[tuple[SocAgentChatRequest | str, ServiceRequestContext | None]] = []

    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]:
        self.calls.append((request, context))
        yield from self._events


def test_soc_chat_runtime_stream_actions_brackets_service_stream() -> None:
    service = _FakeChatService(
        [
            SocAgentStreamEvent(type="values", data={"title": "SOC Review REV-1", "thread_id": "th-1"}),
            SocAgentStreamEvent(type="messages-tuple", data={"type": "ai", "id": "m1", "content": "Loaded"}),
            SocAgentStreamEvent(type="end", data={"usage": {"total_tokens": 3}, "thread_id": "th-1"}),
        ]
    )
    request = SocAgentChatRequest(message="open", queue_id="REV-1")
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst", surface=EntrySurface.TUI))

    actions = list(stream_actions(service, request, context=context))

    assert isinstance(actions[0], RunStarted)
    assert isinstance(actions[-1], RunEnded)
    assert actions[-1].usage == {"total_tokens": 3}
    assert service.calls == [(request, context)]


def test_soc_chat_runtime_reduces_to_deerflow_view_state() -> None:
    service = _FakeChatService(
        [
            SocAgentStreamEvent(type="custom", data={"kind": "soc.route_decision", "route": "review.open_context", "allowed": True}),
            SocAgentStreamEvent(type="custom", data={"kind": "soc.permission_decision", "action": "review.open_context", "allowed": True, "risk_level": "read_only"}),
            SocAgentStreamEvent(type="custom", data={"kind": "soc.action_result", "action": "review.open_context", "status": "success"}),
            SocAgentStreamEvent(type="custom", data={"kind": "soc.review_context", "queue_id": "REV-1"}),
            SocAgentStreamEvent(type="messages-tuple", data={"type": "ai", "id": "m1", "content": "Next step"}),
            SocAgentStreamEvent(type="end", data={"usage": {}}),
        ]
    )

    state = initial_state()
    for action in stream_actions(service, "open REV-1"):
        state = reduce(state, action)

    assert [row.kind for row in state.rows] == ["system", "system", "system", "system", "assistant"]
    assert state.rows[0].text == "SOC route allowed | route=review.open_context"
    assert state.rows[1].text == "SOC permission allowed | action=review.open_context | risk=read_only"
    assert state.rows[2].text == "SOC action result | action=review.open_context | status=success"
    assert state.rows[3].text == "SOC review context loaded | queue=REV-1"
    assert state.rows[4].text == "Next step"
    assert state.streaming is False


class _BoomChatService:
    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]:
        yield SocAgentStreamEvent(type="messages-tuple", data={"type": "ai", "id": "m1", "content": "partial"})
        raise RuntimeError("soc chat down")


def test_soc_chat_runtime_surfaces_service_errors() -> None:
    actions = list(stream_actions(_BoomChatService(), "go"))

    assert any(isinstance(action, AssistantError) and "soc chat down" in action.text for action in actions)
    assert isinstance(actions[-1], RunEnded)
