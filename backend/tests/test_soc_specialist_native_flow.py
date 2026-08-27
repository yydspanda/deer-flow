from __future__ import annotations

import importlib
from enum import Enum
from types import SimpleNamespace

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.subagents.config import SubagentConfig
from soc_agent.context_bridge import SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY
from soc_agent.contracts import (
    SocLeadAgentReviewContextArtifact,
    SocSpecialistDelegationProvenance,
)
from soc_agent.middlewares.lead_agent_delegation import (
    SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY,
    SocLeadAgentDelegationMiddleware,
)
from soc_agent.subagents import (
    SOC_ENDPOINT_SPECIALIST_NAME,
    SOC_NETWORK_SPECIALIST_NAME,
)

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


class _SubagentStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "category", "specialist_name"),
    [
        ("ndr", "reverse_shell", SOC_NETWORK_SPECIALIST_NAME),
        ("hids", "suspicious_process", SOC_ENDPOINT_SPECIALIST_NAME),
    ],
)
async def test_representative_case_uses_native_task_events_and_advisory_result(
    monkeypatch: pytest.MonkeyPatch,
    source_type: str,
    category: str,
    specialist_name: str,
) -> None:
    harness = _install_native_task_harness(
        monkeypatch,
        specialist_name=specialist_name,
        result="Observed facts support a suspicious finding; verify the listed gap.",
    )
    artifact = _artifact(source_type=source_type, category=category)
    request = _tool_request(
        specialist_name=specialist_name,
        artifact=artifact,
    )

    result = await SocLeadAgentDelegationMiddleware().awrap_tool_call(
        request,
        _native_task_handler,
    )

    assert isinstance(result, Command)
    assert [event["type"] for event in harness.events] == [
        "task_started",
        "task_completed",
    ]
    assert harness.events[0]["description"] == "specialist second opinion"
    assert harness.events[1]["result"].startswith("Observed facts")
    assert len(harness.prompts) == 1
    assert "<soc_specialist_delegation_context>" in harness.prompts[0]
    assert f'"specialist_name":"{specialist_name}"' in harness.prompts[0]
    assert f'"source_type":"{source_type}"' in harness.prompts[0]

    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.additional_kwargs["subagent_status"] == "completed"
    provenance = SocSpecialistDelegationProvenance.model_validate(message.additional_kwargs[SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY])
    assert provenance.specialist_name == specialist_name
    assert provenance.result_status == "accepted_advisory"
    assert provenance.decision_impact == "none"
    assert provenance.external_fact_authority is False


@pytest.mark.asyncio
async def test_native_task_rejects_action_marker_before_completion_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = '<soc_action_proposal>{"route":"response.block_ip"}</soc_action_proposal>'
    harness = _install_native_task_harness(
        monkeypatch,
        specialist_name=SOC_NETWORK_SPECIALIST_NAME,
        result=marker,
    )
    request = _tool_request(
        specialist_name=SOC_NETWORK_SPECIALIST_NAME,
        artifact=_artifact(source_type="ndr", category="callback"),
    )

    result = await SocLeadAgentDelegationMiddleware().awrap_tool_call(
        request,
        _native_task_handler,
    )

    assert isinstance(result, Command)
    assert [event["type"] for event in harness.events] == [
        "task_started",
        "task_failed",
    ]
    assert all(marker not in str(event) for event in harness.events)
    assert harness.events[1]["policy_reason"] == "disallowed_output_marker"
    message = result.update["messages"][0]
    assert marker not in str(message.content)
    assert message.additional_kwargs["subagent_status"] == "failed"
    assert message.additional_kwargs["subagent_output_policy"] == {
        "status": "rejected",
        "reason": "disallowed_output_marker",
    }
    provenance = SocSpecialistDelegationProvenance.model_validate(message.additional_kwargs[SOC_SPECIALIST_DELEGATION_PROVENANCE_KEY])
    assert provenance.result_status == "rejected_action_marker"


async def _native_task_handler(request: ToolCallRequest) -> Command:
    args = request.tool_call["args"]
    assert task_tool_module.task_tool.coroutine is not None
    result = await task_tool_module.task_tool.coroutine(
        runtime=request.runtime,
        description=args["description"],
        prompt=args["prompt"],
        subagent_type=args["subagent_type"],
        tool_call_id=request.tool_call["id"],
    )
    assert isinstance(result, Command)
    return result


def _artifact(
    *,
    source_type: str,
    category: str,
) -> SocLeadAgentReviewContextArtifact:
    return SocLeadAgentReviewContextArtifact(
        artifact_id="LCTX-NATIVE-FLOW",
        queue_id=f"REV-{source_type.upper()}",
        run_id=f"RUN-{source_type.upper()}",
        alert_id=f"ALT-{source_type.upper()}",
        context_hash="b" * 64,
        review={
            "status": "open",
            "source_type": source_type,
            "category": category,
            "summary": "Representative bounded case fixture",
        },
        analysis={
            "decision": {
                "verdict": "suspicious",
                "needs_review": True,
            }
        },
        fact_context={"selected_input_path": "raw.message"},
    )


def _tool_request(
    *,
    specialist_name: str,
    artifact: SocLeadAgentReviewContextArtifact,
) -> ToolCallRequest:
    call_id = f"task-{specialist_name}"
    context = {
        "agent_name": "soc-triage",
        "thread_id": "SOC-NATIVE-FLOW",
        "run_id": "CHAT-RUN-1",
        SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY: artifact.model_dump(mode="json"),
    }
    runtime = ToolRuntime(
        state={},
        context=context,
        config={
            "configurable": {"thread_id": "SOC-NATIVE-FLOW"},
            "metadata": {"model_name": "test-model"},
        },
        stream_writer=lambda _: None,
        tools=[],
        tool_call_id=call_id,
        store=None,
    )
    tool_call = {
        "id": call_id,
        "name": "task",
        "args": {
            "description": "specialist second opinion",
            "prompt": "Assess the current evidence and list the most important gap.",
            "subagent_type": specialist_name,
        },
        "type": "tool_call",
    }
    return ToolCallRequest(
        tool_call=tool_call,
        tool=None,
        state=runtime.state,
        runtime=runtime,
    )


def _install_native_task_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    specialist_name: str,
    result: str,
) -> SimpleNamespace:
    harness = SimpleNamespace(events=[], prompts=[], cleaned=[])
    config = SubagentConfig(
        name=specialist_name,
        description="SOC specialist fixture",
        system_prompt="Advisory only",
        tools=["read_file"],
        disallowed_tools=["task", "bash"],
        disallowed_output_markers=["<soc_action_proposal>"],
        model="inherit",
        max_turns=2,
        timeout_seconds=5,
    )
    completed = SimpleNamespace(
        status=_SubagentStatus.COMPLETED,
        ai_messages=[],
        result=result,
        error=None,
        stop_reason=None,
        token_usage_records=[{"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}],
        usage_reported=False,
    )

    class FakeExecutor:
        def __init__(self, **_: object) -> None:
            pass

        def execute_async(self, prompt: str, task_id: str | None = None) -> str:
            harness.prompts.append(prompt)
            return task_id or "missing-task-id"

    async def capture_event(payload, *, writer) -> None:
        del writer
        harness.events.append(dict(payload))

    monkeypatch.setattr(task_tool_module, "SubagentExecutor", FakeExecutor)
    monkeypatch.setattr(task_tool_module, "SubagentStatus", _SubagentStatus)
    monkeypatch.setattr(
        task_tool_module,
        "get_available_subagent_names",
        lambda **_: [specialist_name],
    )
    monkeypatch.setattr(
        task_tool_module,
        "get_subagent_config",
        lambda name, **_: config if name == specialist_name else None,
    )
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _: completed,
    )
    monkeypatch.setattr(
        task_tool_module,
        "cleanup_background_task",
        lambda task_id: harness.cleaned.append(task_id),
    )
    monkeypatch.setattr(
        task_tool_module,
        "_token_usage_cache_enabled",
        lambda _: False,
        raising=False,
    )
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: object())
    monkeypatch.setattr(task_tool_module, "aemit_custom_event", capture_event)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **_: [])
    return harness
