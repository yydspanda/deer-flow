from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.gateway import soc_lead_agent_messages
from app.gateway.soc_lead_agent_messages import (
    SocLeadAgentMessageConflictError,
    SocLeadAgentMessageNotFoundError,
    resolve_soc_lead_agent_message,
)
from soc_agent.context_bridge import (
    SOC_LEAD_AGENT_REVIEW_CONTEXT_PROVENANCE_MESSAGE_KEY,
    SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY,
)
from soc_agent.contracts import (
    SocLeadAgentReviewContextProvenance,
    SocLeadAgentReviewThreadBinding,
)


class _ThreadStore:
    def __init__(self, record: dict | None) -> None:
        self._record = record

    async def get(self, thread_id: str) -> dict | None:
        assert thread_id == "thread-soc-1"
        return self._record


class _Accessor:
    def __init__(self, snapshot: object) -> None:
        self._snapshot = snapshot

    async def aget(self, config: dict) -> object:
        assert config == {"configurable": {"thread_id": "thread-soc-1"}}
        return self._snapshot


def _request(*, record: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(thread_store=_ThreadStore(record)),
        )
    )


def _snapshot(messages: list[object]) -> SimpleNamespace:
    return SimpleNamespace(
        values={"messages": messages},
        config={"configurable": {"checkpoint_id": "checkpoint-7"}},
    )


def _install_accessor(monkeypatch: pytest.MonkeyPatch, messages: list[object]) -> None:
    async def build_accessor(request, *, thread_id, fail_closed):
        assert thread_id == "thread-soc-1"
        assert fail_closed is True
        return _Accessor(_snapshot(messages)), {"configurable": {"thread_id": "thread-soc-1"}}

    monkeypatch.setattr(
        soc_lead_agent_messages,
        "build_thread_checkpoint_state_accessor",
        build_accessor,
    )


def _binding(*, queue_id: str = "REV-1") -> dict:
    return SocLeadAgentReviewThreadBinding(
        queue_id=queue_id,
        run_id="RUN-1",
        alert_id="ALT-1",
        bound_by_actor_id="analyst-1",
    ).model_dump(mode="json")


def _provenance(*, queue_id: str = "REV-1") -> dict:
    return SocLeadAgentReviewContextProvenance(
        artifact_id="LCTX-1",
        queue_id=queue_id,
        run_id="RUN-1",
        alert_id="ALT-1",
        context_hash="a" * 64,
        chat_thread_id="thread-soc-1",
        chat_run_id="chat-run-1",
        rendered_char_count=1_024,
        context_created_at=datetime(2026, 8, 6, tzinfo=UTC),
    ).model_dump(mode="json")


def _thread_record(*, agent_name: str = "soc-triage", queue_id: str = "REV-1") -> dict:
    return {
        "assistant_id": "lead_agent",
        "metadata": {
            "agent_name": agent_name,
            SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY: _binding(queue_id=queue_id),
        },
    }


def _review_ai_message(*, message_id: str, content: str) -> AIMessage:
    return AIMessage(
        id=message_id,
        content=content,
        additional_kwargs={SOC_LEAD_AGENT_REVIEW_CONTEXT_PROVENANCE_MESSAGE_KEY: _provenance()},
    )


@pytest.mark.asyncio
async def test_resolves_latest_terminal_soc_lead_agent_message_from_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_accessor(
        monkeypatch,
        [
            HumanMessage(id="human-1", content="Review this alert"),
            AIMessage(id="assistant-1", content="Intermediate answer"),
            HumanMessage(id="human-2", content="Give the conclusion"),
            _review_ai_message(
                message_id="assistant-2",
                content="  Verified conclusion from server state.  ",
            ),
        ],
    )

    resolved = await resolve_soc_lead_agent_message(
        _request(record=_thread_record()),
        thread_id="thread-soc-1",
        message_id="assistant-2",
        queue_id="REV-1",
    )

    assert resolved.text == "Verified conclusion from server state."
    assert resolved.message_id == "assistant-2"
    assert resolved.agent_name == "soc-triage"
    assert resolved.checkpoint_id == "checkpoint-7"
    assert resolved.context_provenance.context_hash == "a" * 64
    assert len(resolved.text_sha256) == 64


@pytest.mark.asyncio
async def test_rejects_stale_or_tool_call_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        AIMessage(id="assistant-old", content="Old conclusion"),
        AIMessage(
            id="assistant-tool",
            content="Calling lookup",
            tool_calls=[{"name": "lookup", "args": {}, "id": "call-1"}],
        ),
    ]
    _install_accessor(monkeypatch, messages)
    request = _request(record=_thread_record())

    with pytest.raises(SocLeadAgentMessageConflictError, match="latest visible"):
        await resolve_soc_lead_agent_message(
            request,
            thread_id="thread-soc-1",
            message_id="assistant-old",
            queue_id="REV-1",
        )
    with pytest.raises(SocLeadAgentMessageConflictError, match="tool-call"):
        await resolve_soc_lead_agent_message(
            request,
            thread_id="thread-soc-1",
            message_id="assistant-tool",
            queue_id="REV-1",
        )


@pytest.mark.asyncio
async def test_rejects_non_soc_thread_and_missing_current_branch_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_accessor(monkeypatch, [AIMessage(id="assistant-1", content="Conclusion")])

    with pytest.raises(SocLeadAgentMessageConflictError, match="SOC Lead Agent"):
        await resolve_soc_lead_agent_message(
            _request(record={"metadata": {"agent_name": "general-agent"}}),
            thread_id="thread-soc-1",
            message_id="assistant-1",
            queue_id="REV-1",
        )

    with pytest.raises(SocLeadAgentMessageNotFoundError, match="current thread branch"):
        await resolve_soc_lead_agent_message(
            _request(record=_thread_record()),
            thread_id="thread-soc-1",
            message_id="superseded-message",
            queue_id="REV-1",
        )


@pytest.mark.asyncio
async def test_rejects_message_without_server_review_context_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_accessor(
        monkeypatch,
        [AIMessage(id="assistant-1", content="Unbound conclusion")],
    )

    with pytest.raises(
        SocLeadAgentMessageConflictError,
        match="not produced with server-built SOC review context",
    ):
        await resolve_soc_lead_agent_message(
            _request(record=_thread_record()),
            thread_id="thread-soc-1",
            message_id="assistant-1",
            queue_id="REV-1",
        )


@pytest.mark.asyncio
async def test_rejects_route_queue_that_differs_from_thread_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_accessor(
        monkeypatch,
        [
            _review_ai_message(
                message_id="assistant-1",
                content="Conclusion for REV-1",
            )
        ],
    )

    with pytest.raises(SocLeadAgentMessageConflictError, match="not REV-2"):
        await resolve_soc_lead_agent_message(
            _request(record=_thread_record()),
            thread_id="thread-soc-1",
            message_id="assistant-1",
            queue_id="REV-2",
        )
