from __future__ import annotations

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.runtime import Runtime

from soc_agent import context_bridge
from soc_agent.context_bridge import (
    SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY,
    SOC_LEAD_AGENT_REVIEW_CONTEXT_PROVENANCE_MESSAGE_KEY,
)
from soc_agent.contracts import (
    SocLeadAgentReviewContextArtifact,
    SocLeadAgentReviewContextProvenance,
)
from soc_agent.middlewares.lead_agent_review_context import (
    SocLeadAgentReviewContextMiddleware,
)


class _RecordingModel(BaseChatModel):
    observed_messages: list[list[object]] = []

    @property
    def _llm_type(self) -> str:
        return "soc-review-context-recording-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed_messages.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(id="assistant-graph-1", content="reviewed"))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(
            messages,
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )


def _artifact() -> SocLeadAgentReviewContextArtifact:
    return SocLeadAgentReviewContextArtifact(
        artifact_id="LCTX-TEST",
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        context_hash="a" * 64,
        review={"status": "open"},
        analysis={"decision": {"verdict": "suspicious"}},
    )


def _runtime(*, agent_name: str = "soc-triage") -> Runtime:
    return Runtime(
        context={
            "agent_name": agent_name,
            "thread_id": "thread-soc-1",
            "run_id": "chat-run-1",
            SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY: _artifact().model_dump(mode="json"),
        }
    )


def test_middleware_injects_review_context_only_into_model_request() -> None:
    middleware = SocLeadAgentReviewContextMiddleware()
    original_messages = [
        SystemMessage(content="base system"),
        HumanMessage(content="investigate"),
    ]
    request = ModelRequest(
        model=None,
        messages=original_messages,
        tools=[],
        state={"messages": original_messages},
        runtime=_runtime(),
    )
    captured: list[ModelRequest] = []

    result = middleware.wrap_model_call(
        request,
        lambda prepared: captured.append(prepared) or "model-result",
    )

    assert result == "model-result"
    assert request.messages == original_messages
    prepared = captured[0]
    assert isinstance(prepared.messages[0], SystemMessage)
    assert isinstance(prepared.messages[1], SystemMessage)
    assert "SOC review context authority contract" in str(prepared.messages[1].content)
    assert isinstance(prepared.messages[2], HumanMessage)
    assert "<soc_review_context_artifact>" in str(prepared.messages[2].content)
    assert '"queue_id":"REV-1"' in str(prepared.messages[2].content)
    assert prepared.messages[2].additional_kwargs["hide_from_ui"] is True
    assert prepared.messages[-1] is original_messages[-1]


def test_middleware_stamps_exact_review_context_on_assistant_message() -> None:
    middleware = SocLeadAgentReviewContextMiddleware()
    message = AIMessage(id="assistant-1", content="conclusion")

    update = middleware.after_model({"messages": [message]}, _runtime())

    assert update is not None
    stamped = update["messages"][0]
    provenance = SocLeadAgentReviewContextProvenance.model_validate(stamped.additional_kwargs[SOC_LEAD_AGENT_REVIEW_CONTEXT_PROVENANCE_MESSAGE_KEY])
    assert provenance.queue_id == "REV-1"
    assert provenance.run_id == "RUN-1"
    assert provenance.alert_id == "ALT-1"
    assert provenance.context_hash == "a" * 64
    assert provenance.chat_thread_id == "thread-soc-1"
    assert provenance.chat_run_id == "chat-run-1"
    assert provenance.injection_mode == "transient_model_context"
    assert provenance.rendered_char_count > 0


def test_middleware_ignores_non_soc_agent() -> None:
    middleware = SocLeadAgentReviewContextMiddleware()
    message = AIMessage(id="assistant-1", content="answer")

    assert (
        middleware.after_model(
            {"messages": [message]},
            _runtime(agent_name="researcher"),
        )
        is None
    )


def test_middleware_fails_closed_when_projection_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = SocLeadAgentReviewContextMiddleware()
    request = ModelRequest(
        model=None,
        messages=[HumanMessage(content="investigate")],
        tools=[],
        state={},
        runtime=_runtime(),
    )
    monkeypatch.setattr(
        context_bridge,
        "SOC_LEAD_AGENT_REVIEW_CONTEXT_MAX_CHARS",
        10,
    )

    with pytest.raises(
        context_bridge.SocLeadAgentReviewContextTooLargeError,
        match="model-input limit",
    ):
        middleware.wrap_model_call(request, lambda prepared: prepared)


def test_runtime_context_reaches_middleware_in_real_agent_graph() -> None:
    model = _RecordingModel()
    graph = create_agent(
        model=model,
        tools=[],
        middleware=[SocLeadAgentReviewContextMiddleware()],
    )
    runtime = _runtime()

    result = graph.invoke(
        {"messages": [HumanMessage(content="investigate")]},
        context=dict(runtime.context),
    )

    assert len(model.observed_messages) == 1
    request_messages = model.observed_messages[0]
    hidden_context = [message for message in request_messages if isinstance(message, HumanMessage) and message.additional_kwargs.get("soc_lead_agent_review_context_data")]
    assert len(hidden_context) == 1
    assert '"queue_id":"REV-1"' in str(hidden_context[0].content)

    persisted_messages = result["messages"]
    assert all(message not in persisted_messages for message in hidden_context)
    final = persisted_messages[-1]
    assert isinstance(final, AIMessage)
    provenance = SocLeadAgentReviewContextProvenance.model_validate(final.additional_kwargs[SOC_LEAD_AGENT_REVIEW_CONTEXT_PROVENANCE_MESSAGE_KEY])
    assert provenance.queue_id == "REV-1"
    assert provenance.chat_thread_id == "thread-soc-1"
    assert provenance.chat_run_id == "chat-run-1"
