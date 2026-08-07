from __future__ import annotations

from rich.console import Console

from deerflow.tui.view_state import AssistantRow, SystemMessage, ViewState
from soc_agent.contracts import (
    EntrySurface,
    ReviewNoteOrigin,
    SocAgentStreamEvent,
)
from soc_agent.tui.chat_app import (
    _accepted_lead_agent_note_command,
    _chat_help_text,
    _chat_request_from_text,
    _thread_label,
    _tui_request_context,
    render_chat_header,
)
from soc_agent.tui.chat_runtime import translate


def test_soc_chat_app_builds_plain_chat_request() -> None:
    request = _chat_request_from_text("triage this", thread_id="thread-1")

    assert request.message == "triage this"
    assert request.thread_id == "thread-1"
    assert request.queue_id is None


def test_soc_chat_app_builds_open_queue_request() -> None:
    request = _chat_request_from_text("/open REV-1", thread_id="thread-1")

    assert request.message == "/open REV-1"
    assert request.thread_id == "thread-1"
    assert request.queue_id == "REV-1"


def test_soc_chat_app_can_attach_initial_queue_to_plain_message() -> None:
    request = _chat_request_from_text("continue investigation", thread_id="thread-1", queue_id="REV-1")

    assert request.message == "continue investigation"
    assert request.thread_id == "thread-1"
    assert request.queue_id == "REV-1"


def test_soc_chat_app_tui_context_marks_surface() -> None:
    context = _tui_request_context()

    assert context.actor.actor_id == "soc-agent-tui"
    assert context.actor.surface is EntrySurface.TUI


def test_soc_chat_app_exposes_conclusion_acceptance_only_in_lead_agent_mode() -> None:
    assert "/accept-conclusion" not in _chat_help_text(lead_agent_mode=False)
    assert "/accept-conclusion" in _chat_help_text(lead_agent_mode=True)


def test_soc_tui_translates_native_specialist_task_lifecycle() -> None:
    started = translate(
        SocAgentStreamEvent(
            type="custom",
            data={
                "type": "task_started",
                "task_id": "task-1",
                "description": "network second opinion",
                "model_name": "deepseek-v4-flash",
            },
        )
    )
    running = translate(
        SocAgentStreamEvent(
            type="custom",
            data={
                "type": "task_running",
                "task_id": "task-1",
                "message_index": 1,
                "total_messages": 2,
                "usage": {"total_tokens": 123},
            },
        )
    )
    timed_out = translate(
        SocAgentStreamEvent(
            type="custom",
            data={
                "type": "task_timed_out",
                "task_id": "task-1",
                "error": "timeout",
            },
        )
    )

    assert isinstance(started[0], SystemMessage)
    assert "network second opinion" in started[0].text
    assert "model=deepseek-v4-flash" in started[0].text
    assert "progress=1/2" in running[0].text
    assert "tokens=123" in running[0].text
    assert timed_out[0].tone == "error"
    assert "timeout" in timed_out[0].text


def test_soc_chat_app_builds_explicit_lead_agent_acceptance_from_last_message() -> None:
    state = ViewState(
        rows=(
            AssistantRow(id="assistant-old", text="Old conclusion"),
            AssistantRow(id="assistant-current", text="Verified reusable conclusion"),
        )
    )

    command = _accepted_lead_agent_note_command(
        state,
        queue_id="REV-1",
        thread_id="SOC-TUI-1",
        acceptance_reason="Analyst checked the raw evidence.",
    )

    assert command.origin is ReviewNoteOrigin.ACCEPTED_LEAD_AGENT_CONCLUSION
    assert command.queue_id == "REV-1"
    assert command.note == "Verified reusable conclusion"
    assert command.source_thread_id == "SOC-TUI-1"
    assert command.source_message_id == "assistant-current"
    assert command.acceptance_reason == "Analyst checked the raw evidence."


def test_soc_chat_app_requires_queue_reason_and_stable_message_for_acceptance() -> None:
    state = ViewState(rows=(AssistantRow(id=None, text="Conclusion without an id"),))

    for kwargs, error in (
        ({"queue_id": None, "thread_id": "SOC-TUI-1", "acceptance_reason": "valid"}, "Open a ReviewQueue"),
        ({"queue_id": "REV-1", "thread_id": "SOC-TUI-1", "acceptance_reason": ""}, "Acceptance reason"),
        ({"queue_id": "REV-1", "thread_id": "SOC-TUI-1", "acceptance_reason": "valid"}, "stable message identity"),
    ):
        try:
            _accepted_lead_agent_note_command(state, **kwargs)
        except ValueError as exc:
            assert error in str(exc)
        else:
            raise AssertionError("expected acceptance command validation to fail")


def test_soc_chat_app_thread_label() -> None:
    assert _thread_label(None) == "new thread"
    assert _thread_label("thread-1") == "thread-1"


def test_soc_chat_app_header_renders_soc_agent_label() -> None:
    console = Console(record=True, width=80)
    console.print(render_chat_header(thread_label="thread-1"))

    text = console.export_text()
    assert "SOC Agent" in text
    assert "thread-1" in text
