from __future__ import annotations

from rich.console import Console

from soc_agent.contracts import EntrySurface
from soc_agent.tui.chat_app import _chat_request_from_text, _thread_label, _tui_request_context, render_chat_header


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


def test_soc_chat_app_thread_label() -> None:
    assert _thread_label(None) == "new thread"
    assert _thread_label("thread-1") == "thread-1"


def test_soc_chat_app_header_renders_soc_agent_label() -> None:
    console = Console(record=True, width=80)
    console.print(render_chat_header(thread_label="thread-1"))

    text = console.export_text()
    assert "SOC Agent" in text
    assert "thread-1" in text
