from __future__ import annotations

import json

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.subagents.status_contract import make_subagent_additional_kwargs
from soc_agent.context_bridge import SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY
from soc_agent.contracts import (
    SocLeadAgentReviewContextArtifact,
    SocSpecialistDelegationContext,
    SocSpecialistDelegationProvenance,
)
from soc_agent.middlewares.lead_agent_delegation import (
    SOC_SPECIALIST_DELEGATION_GUARD_KEY,
    SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY,
    SocLeadAgentDelegationMiddleware,
    SocSpecialistDelegationContextTooLargeError,
)
from soc_agent.subagents import (
    SOC_ENDPOINT_SPECIALIST_NAME,
    SOC_NETWORK_SPECIALIST_NAME,
    SOC_WEB_SPECIALIST_NAME,
)


def _artifact(*, review: dict[str, object] | None = None) -> SocLeadAgentReviewContextArtifact:
    return SocLeadAgentReviewContextArtifact(
        artifact_id="LCTX-TEST",
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        context_hash="a" * 64,
        review=review or {"status": "open", "summary": "network alert"},
        analysis={"decision": {"verdict": "suspicious"}},
        fact_context={"selected_input_path": "raw.message"},
        instructions=["trusted service instruction"],
    )


def _context(
    *,
    agent_name: str = "soc-triage",
    artifact: SocLeadAgentReviewContextArtifact | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "agent_name": agent_name,
        "thread_id": "thread-1",
        "run_id": "chat-run-1",
    }
    if artifact is not None:
        context[SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY] = artifact.model_dump(mode="json")
    return context


def _task_call(
    call_id: str,
    specialist: str,
    *,
    prompt: str = "Assess the competing network-direction hypotheses.",
    description: str = "network second opinion",
) -> dict[str, object]:
    return {
        "id": call_id,
        "name": "task",
        "args": {
            "description": description,
            "prompt": prompt,
            "subagent_type": specialist,
        },
        "type": "tool_call",
    }


def _tool_request(
    call: dict[str, object],
    *,
    context: dict[str, object],
    state: dict[str, object] | None = None,
) -> ToolCallRequest:
    runtime = ToolRuntime(
        state=state or {},
        context=context,
        config={"configurable": {}},
        stream_writer=lambda _: None,
        tools=[],
        tool_call_id=str(call["id"]),
        store=None,
    )
    return ToolCallRequest(
        tool_call=call,
        tool=None,
        state=runtime.state,
        runtime=runtime,
    )


def _completed_result(call_id: str, content: str = "bounded specialist assessment") -> Command:
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=call_id,
                    name="task",
                    additional_kwargs=make_subagent_additional_kwargs(
                        "completed",
                        result=content,
                    ),
                )
            ]
        }
    )


def _delegation_context_from_prompt(prompt: str) -> SocSpecialistDelegationContext:
    payload = prompt.split("<soc_specialist_delegation_context>\n", 1)[1].split(
        "\n</soc_specialist_delegation_context>",
        1,
    )[0]
    return SocSpecialistDelegationContext.model_validate(json.loads(payload))


def test_after_model_removes_tasks_without_server_bound_review_context() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    message = AIMessage(
        id="assistant-1",
        content="delegating",
        tool_calls=[_task_call("call-1", SOC_NETWORK_SPECIALIST_NAME)],
    )

    update = middleware.after_model(
        {"messages": [message], "delegations": []},
        Runtime(context=_context()),
    )

    assert update is not None
    guarded = update["messages"][0]
    assert guarded.tool_calls == []
    guard = guarded.additional_kwargs[SOC_SPECIALIST_DELEGATION_GUARD_KEY]
    assert guard["reason_counts"] == {"missing_trusted_review_context": 1}


def test_after_model_allows_only_distinct_whitelisted_specialists_within_budget() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    message = AIMessage(
        id="assistant-1",
        content="delegating",
        tool_calls=[
            _task_call("call-1", SOC_NETWORK_SPECIALIST_NAME),
            _task_call("call-2", SOC_NETWORK_SPECIALIST_NAME),
            _task_call("call-3", "general-purpose"),
            _task_call("call-4", SOC_ENDPOINT_SPECIALIST_NAME),
            _task_call("call-5", SOC_WEB_SPECIALIST_NAME),
            {
                "id": "call-read",
                "name": "read_file",
                "args": {"file_path": "evidence.txt"},
                "type": "tool_call",
            },
        ],
    )

    update = middleware.after_model(
        {"messages": [message], "delegations": []},
        Runtime(context=_context(artifact=_artifact())),
    )

    assert update is not None
    guarded = update["messages"][0]
    assert [call["id"] for call in guarded.tool_calls] == [
        "call-1",
        "call-4",
        "call-read",
    ]
    guard = guarded.additional_kwargs[SOC_SPECIALIST_DELEGATION_GUARD_KEY]
    assert guard["accepted_task_call_count"] == 2
    assert guard["reason_counts"] == {
        "duplicate_specialist": 1,
        "per_run_limit_reached": 1,
        "specialist_not_allowed": 1,
    }


def test_after_model_rejects_unbounded_lead_agent_task() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    message = AIMessage(
        id="assistant-1",
        content="delegating",
        tool_calls=[
            _task_call(
                "call-large",
                SOC_NETWORK_SPECIALIST_NAME,
                prompt="x" * 1_201,
            )
        ],
    )

    update = middleware.after_model(
        {"messages": [message], "delegations": []},
        Runtime(context=_context(artifact=_artifact())),
    )

    assert update is not None
    guarded = update["messages"][0]
    assert guarded.tool_calls == []
    assert guarded.additional_kwargs[SOC_SPECIALIST_DELEGATION_GUARD_KEY]["reason_counts"] == {"prompt_too_large": 1}
    assert "Retry at most once" in str(guarded.content)
    assert "Do not claim delegation completed" in str(guarded.content)


def test_after_model_counts_only_prior_soc_delegations_from_current_run() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    message = AIMessage(
        id="assistant-1",
        content="delegating",
        tool_calls=[
            _task_call("new-network", SOC_NETWORK_SPECIALIST_NAME),
            _task_call("new-endpoint", SOC_ENDPOINT_SPECIALIST_NAME),
        ],
    )
    delegations = [
        {
            "id": "prior-web",
            "run_id": "chat-run-1",
            "subagent_type": SOC_WEB_SPECIALIST_NAME,
        },
        {
            "id": "prior-old-run",
            "run_id": "old-run",
            "subagent_type": SOC_NETWORK_SPECIALIST_NAME,
        },
        {
            "id": "prior-general",
            "run_id": "chat-run-1",
            "subagent_type": "general-purpose",
        },
    ]

    update = middleware.after_model(
        {"messages": [message], "delegations": delegations},
        Runtime(context=_context(artifact=_artifact())),
    )

    assert update is not None
    guarded = update["messages"][0]
    assert [call["id"] for call in guarded.tool_calls] == ["new-network"]
    assert guarded.additional_kwargs[SOC_SPECIALIST_DELEGATION_GUARD_KEY]["prior_delegation_count"] == 1


def test_after_model_does_not_repeat_a_specialist_used_in_current_run() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    message = AIMessage(
        id="assistant-1",
        content="delegating",
        tool_calls=[
            _task_call("repeat-network", SOC_NETWORK_SPECIALIST_NAME),
            _task_call("new-endpoint", SOC_ENDPOINT_SPECIALIST_NAME),
        ],
    )
    delegations = [
        {
            "id": "prior-network",
            "run_id": "chat-run-1",
            "subagent_type": SOC_NETWORK_SPECIALIST_NAME,
        }
    ]

    update = middleware.after_model(
        {"messages": [message], "delegations": delegations},
        Runtime(context=_context(artifact=_artifact())),
    )

    assert update is not None
    guarded = update["messages"][0]
    assert [call["id"] for call in guarded.tool_calls] == ["new-endpoint"]
    guard = guarded.additional_kwargs[SOC_SPECIALIST_DELEGATION_GUARD_KEY]
    assert guard["reason_counts"] == {"duplicate_specialist": 1}


def test_wrap_tool_call_replaces_model_prompt_with_server_projection_and_stamps_result() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    call = _task_call("call-1", SOC_NETWORK_SPECIALIST_NAME)
    request = _tool_request(
        call,
        context=_context(artifact=_artifact()),
    )
    captured: list[ToolCallRequest] = []

    result = middleware.wrap_tool_call(
        request,
        lambda prepared: captured.append(prepared) or _completed_result("call-1"),
    )

    assert isinstance(result, Command)
    assert request.tool_call["args"]["prompt"] == ("Assess the competing network-direction hypotheses.")
    prepared_prompt = captured[0].tool_call["args"]["prompt"]
    assert isinstance(prepared_prompt, str)
    delegation = _delegation_context_from_prompt(prepared_prompt)
    assert delegation.context_source == "soc_review_service"
    assert delegation.specialist_name == SOC_NETWORK_SPECIALIST_NAME
    assert delegation.queue_id == "REV-1"
    assert delegation.lead_agent_task == ("Assess the competing network-direction hypotheses.")
    assert delegation.evidence_context["review"]["status"] == "open"
    assert "instructions" not in delegation.evidence_context
    messages = result.update["messages"]
    provenance = SocSpecialistDelegationProvenance.model_validate(messages[0].additional_kwargs[SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY])
    assert provenance.result_status == "accepted_advisory"
    assert provenance.decision_impact == "none"
    assert provenance.action_allowed is False
    assert provenance.projection_hash == delegation.projection_hash


def test_same_case_task_keeps_stable_delegation_identity_across_replay() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    artifact = _artifact()

    first = _capture_delegation(
        middleware,
        call=_task_call("call-first", SOC_NETWORK_SPECIALIST_NAME),
        context={
            **_context(artifact=artifact),
            "run_id": "chat-run-first",
        },
    )
    replay = _capture_delegation(
        middleware,
        call=_task_call("call-replay", SOC_NETWORK_SPECIALIST_NAME),
        context={
            **_context(artifact=artifact),
            "run_id": "chat-run-replay",
        },
    )

    assert first.delegation_id == replay.delegation_id
    assert first.projection_hash == replay.projection_hash
    assert first.chat_run_id == "chat-run-first"
    assert replay.chat_run_id == "chat-run-replay"
    assert first.tool_call_id == "call-first"
    assert replay.tool_call_id == "call-replay"


def test_wrap_tool_call_rejects_specialist_action_marker() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    call = _task_call("call-1", SOC_ENDPOINT_SPECIALIST_NAME)
    request = _tool_request(
        call,
        context=_context(artifact=_artifact()),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda _: _completed_result(
            "call-1",
            '<soc_action_proposal>{"route":"response.block_ip"}</soc_action_proposal>',
        ),
    )

    assert isinstance(result, Command)
    message = result.update["messages"][0]
    assert message.status == "error"
    assert "<soc_action_proposal>" not in str(message.content)
    assert message.additional_kwargs["subagent_status"] == "failed"
    provenance = SocSpecialistDelegationProvenance.model_validate(message.additional_kwargs[SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY])
    assert provenance.result_status == "rejected_action_marker"


def test_wrap_tool_call_marks_capped_partial_result_as_execution_failed() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    request = _tool_request(
        _task_call("call-capped", SOC_NETWORK_SPECIALIST_NAME),
        context=_context(artifact=_artifact()),
    )
    partial = "I will inspect the available guidance before reaching a conclusion."

    result = middleware.wrap_tool_call(
        request,
        lambda _: Command(
            update={
                "messages": [
                    ToolMessage(
                        content=partial,
                        tool_call_id="call-capped",
                        name="task",
                        additional_kwargs=make_subagent_additional_kwargs(
                            "completed",
                            result=partial,
                            stop_reason="turn_capped",
                        ),
                    )
                ]
            }
        ),
    )

    assert isinstance(result, Command)
    message = result.update["messages"][0]
    provenance = SocSpecialistDelegationProvenance.model_validate(message.additional_kwargs[SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY])
    assert provenance.result_status == "execution_failed"


def test_wrap_tool_call_blocks_direct_unknown_specialist_without_calling_handler() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    request = _tool_request(
        _task_call("call-1", "bash"),
        context=_context(artifact=_artifact()),
    )
    called = False

    def handler(_: ToolCallRequest) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="unexpected", tool_call_id="call-1")

    result = middleware.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert called is False
    assert result.status == "error"
    assert "specialist_not_allowed" in str(result.content)


@pytest.mark.asyncio
async def test_async_wrap_uses_same_projection_and_provenance() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    request = _tool_request(
        _task_call("call-1", SOC_WEB_SPECIALIST_NAME),
        context=_context(artifact=_artifact()),
    )
    captured: list[ToolCallRequest] = []

    async def handler(prepared: ToolCallRequest) -> Command:
        captured.append(prepared)
        return _completed_result("call-1")

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, Command)
    assert _delegation_context_from_prompt(captured[0].tool_call["args"]["prompt"]).specialist_name == SOC_WEB_SPECIALIST_NAME
    assert SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY in result.update["messages"][0].additional_kwargs


def test_non_soc_agent_task_is_unchanged() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    call = _task_call("call-1", "general-purpose")
    request = _tool_request(
        call,
        context=_context(agent_name="researcher"),
    )
    original = ToolMessage(content="ok", tool_call_id="call-1")

    result = middleware.wrap_tool_call(request, lambda prepared: original)

    assert result is original


def test_oversized_server_projection_fails_closed() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    request = _tool_request(
        _task_call("call-1", SOC_NETWORK_SPECIALIST_NAME),
        context=_context(artifact=_artifact(review={"summary": "x" * 33_000})),
    )

    with pytest.raises(
        SocSpecialistDelegationContextTooLargeError,
        match="model-input limit",
    ):
        middleware.wrap_tool_call(
            request,
            _unexpected_handler,
        )


def test_large_timeline_is_compacted_into_bounded_domain_findings() -> None:
    middleware = SocLeadAgentDelegationMiddleware()
    artifact = _artifact()
    artifact = artifact.model_copy(
        update={
            "investigation_view": {
                "runtime_verdict": "suspicious",
                "primary_summary": "current conclusion",
                "counts": {"timeline_items": 2},
                "timeline": [
                    {
                        "kind": "domain_finding",
                        "title": "Reverse shell",
                        "summary": "bounded finding",
                        "status": "suspicious",
                        "severity": "high",
                        "source_id": "DFN-1",
                        "payload": {
                            "scenario_key": "execution.reverse_shell",
                            "confidence": 0.8,
                            "evidence_profile": {
                                "used_sources": ["raw_log"],
                                "gaps": ["endpoint confirmation"],
                            },
                            "current_conclusion": {
                                "summary": "reverse shell is plausible",
                                "automation_allowed": False,
                            },
                            "evidence_refs": ["EVI-" + ("x" * 40_000)],
                            "human_checklist": ["verify endpoint telemetry"],
                        },
                    },
                    {
                        "kind": "read_only_evidence",
                        "payload": {"raw": "y" * 40_000},
                    },
                ],
            }
        }
    )
    captured: list[ToolCallRequest] = []

    result = middleware.wrap_tool_call(
        _tool_request(
            _task_call("call-compact", SOC_NETWORK_SPECIALIST_NAME),
            context=_context(artifact=artifact),
        ),
        lambda prepared: captured.append(prepared) or _completed_result("call-compact"),
    )

    assert isinstance(result, Command)
    prompt = captured[0].tool_call["args"]["prompt"]
    assert isinstance(prompt, str)
    assert len(prompt) < 32_000
    delegation = _delegation_context_from_prompt(prompt)
    assert "timeline" not in delegation.evidence_context["investigation_view"]
    assert delegation.evidence_context["domain_findings"] == [
        {
            "title": "Reverse shell",
            "summary": "bounded finding",
            "status": "suspicious",
            "severity": "high",
            "source_id": "DFN-1",
            "scenario_key": "execution.reverse_shell",
            "confidence": 0.8,
            "current_conclusion": {
                "summary": "reverse shell is plausible",
                "automation_allowed": False,
            },
            "used_sources": ["raw_log"],
            "evidence_gaps": ["endpoint confirmation"],
            "human_checklist": ["verify endpoint telemetry"],
        }
    ]


def test_projection_filters_specialist_guidance_and_compacts_action_evidence() -> None:
    artifact_payload = _artifact().model_dump(mode="json")
    artifact_payload["skill_context"] = {
        "selected_skills": [
            {
                "skill_name": "soc-network-apt-triage",
                "reason": "network source",
                "confidence": 0.8,
                "matched_fields": ["source_type"],
                "guidance": "network runtime guidance",
                "guidance_source": "references/runtime-guidance.md",
                "guidance_hash": "a" * 64,
                "package_hash": "b" * 64,
                "estimated_token_count": 8,
            },
            {
                "skill_name": "soc-web-application-triage",
                "reason": "http evidence",
                "confidence": 0.7,
                "matched_fields": ["http"],
                "guidance": "web runtime guidance",
                "guidance_source": "references/runtime-guidance.md",
                "guidance_hash": "c" * 64,
                "package_hash": "d" * 64,
                "estimated_token_count": 8,
            },
        ],
        "total_token_budget": 480,
        "total_estimated_token_count": 16,
    }
    artifact_payload["action_evidence"] = [
        {
            "evidence_id": "EVI-1",
            "route": "asset.locate",
            "action": "asset.locate",
            "status": "success",
            "message": "lookup completed",
            "actor": {"actor_id": "internal-service"},
            "result_payload": {
                "adapter_id": "asset-adapter",
                "adapter_kind": "mcp",
                "idempotency_key": "do-not-project",
                "mcp_result": {
                    "query": "10.0.0.1",
                    "found": False,
                    "mocked": True,
                    "provider_mode": "fake",
                },
            },
        }
    ]
    artifact = SocLeadAgentReviewContextArtifact.model_validate(artifact_payload)

    delegation = _capture_delegation(
        SocLeadAgentDelegationMiddleware(),
        call=_task_call("call-projection", SOC_NETWORK_SPECIALIST_NAME),
        context=_context(artifact=artifact),
    )

    selected = delegation.evidence_context["skill_selection"]["selected_skills"]
    assert [item["skill_name"] for item in selected] == ["soc-network-apt-triage"]
    assert selected[0]["guidance"] == "network runtime guidance"
    evidence = delegation.evidence_context["action_evidence"][0]
    assert evidence["adapter"] == {
        "adapter_id": "asset-adapter",
        "adapter_kind": "mcp",
    }
    assert evidence["result"] == {
        "query": "10.0.0.1",
        "found": False,
        "mocked": True,
        "provider_mode": "fake",
    }
    assert "actor" not in evidence
    assert "idempotency_key" not in str(evidence)


def _unexpected_handler(_: ToolCallRequest) -> ToolMessage:
    raise AssertionError("handler must not run")


def _capture_delegation(
    middleware: SocLeadAgentDelegationMiddleware,
    *,
    call: dict[str, object],
    context: dict[str, object],
) -> SocSpecialistDelegationContext:
    captured: list[ToolCallRequest] = []
    result = middleware.wrap_tool_call(
        _tool_request(call, context=context),
        lambda prepared: captured.append(prepared) or _completed_result(str(call["id"])),
    )
    assert isinstance(result, Command)
    return _delegation_context_from_prompt(captured[0].tool_call["args"]["prompt"])
