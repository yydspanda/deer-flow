"""Textual chat workbench for the SOC Agent."""

from __future__ import annotations

from functools import partial
from uuid import uuid4

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from deerflow.tui.render import render_status, render_transcript
from deerflow.tui.theme import THEME
from deerflow.tui.view_state import RunEnded, RunStarted, SystemMessage, UserSubmitted, initial_state, reduce
from deerflow.tui.widgets.composer import ComposerInput
from soc_agent.contracts import ActorContext, EntrySurface, ServiceRequestContext, SocAgentChatRequest
from soc_agent.core import SocAgentChatService
from soc_agent.tui.chat_runtime import stream_actions

_HELP_TEXT = "Commands: /open REV-...  /help  /quit"


class SocAgentChatTUI(App):
    CSS = f"""
    Screen {{
        background: {THEME.bg};
        color: {THEME.text};
    }}
    #header {{
        height: 1;
        padding: 0 1;
        background: {THEME.panel};
    }}
    #scroll {{
        height: 1fr;
        padding: 1 2;
        background: {THEME.bg};
        scrollbar-size-vertical: 1;
    }}
    #transcript {{
        width: 100%;
        height: auto;
    }}
    #status {{
        height: 1;
        padding: 0 1;
        background: {THEME.panel};
        color: {THEME.muted};
    }}
    #composer {{
        height: 3;
        margin: 0 1 1 1;
        border: round {THEME.border};
        background: {THEME.panel};
    }}
    #composer:focus {{
        border: round {THEME.primary};
    }}
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True, show=True),
        Binding("ctrl+l", "redraw", "Redraw", show=False),
        Binding("escape", "escape", show=False, priority=True),
    ]

    def __init__(
        self,
        service: SocAgentChatService,
        *,
        initial_queue_id: str | None = None,
        initial_message: str | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.initial_queue_id = initial_queue_id
        self.initial_message = initial_message
        self.state = initial_state()
        self._thread_id: str | None = None
        self._streaming = False
        self._cancelled = False
        self._transcript_dirty = False

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with VerticalScroll(id="scroll"):
            yield Static(id="transcript")
        yield Static(id="status")
        yield ComposerInput(placeholder="Message SOC Agent...   (/open REV-...)", id="composer")

    def on_mount(self) -> None:
        self._refresh_all()
        self.set_interval(0.06, self._flush_transcript)
        self.query_one("#composer", Input).focus()
        if self.initial_queue_id or self.initial_message:
            text = self.initial_message or f"/open {self.initial_queue_id}"
            self._send_to_agent(text, queue_id=self.initial_queue_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        self._handle_submit(text)

    def _handle_submit(self, text: str) -> None:
        if text == "/quit":
            self.exit()
            return
        if text == "/help":
            self._dispatch(SystemMessage(_HELP_TEXT))
            return
        self._send_to_agent(text)

    def _send_to_agent(self, text: str, *, queue_id: str | None = None) -> None:
        if self._streaming:
            self._dispatch(SystemMessage("Still working; wait for the current run to finish."))
            return
        if self._thread_id is None:
            self._thread_id = f"SOC-TUI-{uuid4().hex[:12].upper()}"
            self._refresh_header()
        self._cancelled = False
        request = _chat_request_from_text(text, thread_id=self._thread_id, queue_id=queue_id)
        self._dispatch(UserSubmitted(text))
        self.run_worker(
            partial(self._stream_worker, request),
            thread=True,
            exclusive=True,
            group="soc-agent-chat",
        )

    def _stream_worker(self, request: SocAgentChatRequest) -> None:
        for action in stream_actions(self.service, request, context=_tui_request_context()):
            if self._cancelled:
                break
            self.call_from_thread(self._on_action, action)

    def _on_action(self, action) -> None:
        self._dispatch(action)

    def _dispatch(self, action) -> None:
        self.state = reduce(self.state, action)
        if isinstance(action, RunStarted):
            self._streaming = True
            self._transcript_dirty = True
        elif isinstance(action, RunEnded):
            self._streaming = False
            self._transcript_dirty = False
            self._refresh_transcript()
        else:
            self._transcript_dirty = True
        self._refresh_status()

    def _flush_transcript(self) -> None:
        if self._transcript_dirty:
            self._transcript_dirty = False
            self._refresh_transcript()

    def _refresh_all(self) -> None:
        self._refresh_header()
        self._refresh_transcript()
        self._refresh_status()

    def _refresh_header(self) -> None:
        self.query_one("#header", Static).update(render_chat_header(thread_label=_thread_label(self._thread_id)))

    def _refresh_transcript(self) -> None:
        self.query_one("#transcript", Static).update(render_transcript(self.state))

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(
            render_status(
                self.state,
                model="soc-agent",
                thread_label=_thread_label(self._thread_id),
                spinner="*",
            )
        )

    def action_escape(self) -> None:
        if self._streaming:
            self._cancelled = True
            self._dispatch(SystemMessage("Interrupted local stream."))

    def action_redraw(self) -> None:
        self._refresh_all()


def render_chat_header(*, thread_label: str) -> Text:
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(" SOC Agent ", style=f"bold {THEME.bg} on {THEME.primary}")
    text.append("  ")
    text.append("chat", style=f"bold {THEME.primary}")
    text.append("  |  ", style=THEME.dim)
    text.append(thread_label, style=THEME.muted)
    return text


def _chat_request_from_text(text: str, *, thread_id: str | None, queue_id: str | None = None) -> SocAgentChatRequest:
    stripped = text.strip()
    resolved_queue_id = queue_id.strip() if queue_id else None
    if stripped.startswith("/open "):
        resolved_queue_id = stripped.removeprefix("/open ").strip() or resolved_queue_id
    return SocAgentChatRequest(message=stripped, thread_id=thread_id, queue_id=resolved_queue_id)


def _thread_label(thread_id: str | None) -> str:
    if not thread_id:
        return "new thread"
    return thread_id


def _tui_request_context() -> ServiceRequestContext:
    return ServiceRequestContext(actor=ActorContext(actor_id="soc-agent-tui", surface=EntrySurface.TUI))
