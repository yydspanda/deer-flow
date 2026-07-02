"""Textual ReviewQueue workbench for SOC Agent."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from deerflow.tui.theme import THEME
from deerflow.tui.widgets.composer import ComposerInput
from soc_agent.contracts import (
    ActorContext,
    CorrectionCommand,
    EntrySurface,
    ReviewQueueCloseCommand,
    ReviewQueueStatus,
    ServiceRequestContext,
    Verdict,
)
from soc_agent.core import SocReviewService, SocServiceError
from soc_agent.tui.command_registry import filter_commands, resolve
from soc_agent.tui.render import render_header, render_main, render_palette, render_status
from soc_agent.tui.view_state import add_notice, initial_state, select_context, set_items, set_loading

_HELP_TEXT = "Commands: /refresh  /open REV-...  /close REV-... reason  /correct RUN-... verdict reason  /quit"


class SocReviewTUI(App):
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
    #main {{
        width: 100%;
        height: auto;
    }}
    #status {{
        height: 1;
        padding: 0 1;
        background: {THEME.panel};
        color: {THEME.muted};
    }}
    #palette {{
        height: auto;
        max-height: 10;
        margin: 0 1;
        padding: 0 1;
        background: {THEME.panel};
        border: round {THEME.border};
        display: none;
    }}
    #palette.open {{
        display: block;
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
        Binding("down", "nav_down", show=False, priority=True),
        Binding("up", "nav_up", show=False, priority=True),
        Binding("tab", "palette_complete", show=False, priority=True),
        Binding("enter", "palette_accept", show=False, priority=True),
        Binding("escape", "escape", show=False, priority=True),
    ]

    def __init__(self, service: SocReviewService, *, database_label: str = "") -> None:
        super().__init__()
        self.service = service
        self.database_label = database_label
        self.state = initial_state()
        self._palette_open = False
        self._palette_items = []
        self._palette_index = 0

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with VerticalScroll(id="scroll"):
            yield Static(id="main")
        yield Static(id="status")
        yield Static(id="palette")
        yield ComposerInput(placeholder="SOC review command...   ( / for commands )", id="composer")

    def on_mount(self) -> None:
        self._refresh_all()
        self._load_queue()
        self.query_one("#composer", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self._close_palette()
        if text:
            self._handle_submit(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        if value.startswith("/") and " " not in value:
            self._palette_index = 0
            self._open_palette(filter_commands(value[1:]))
        else:
            self._close_palette()

    def _handle_submit(self, text: str) -> None:
        resolution = resolve(text)
        if resolution.kind == "unknown":
            self._notice(f"Unknown command {resolution.name!r}. Try /help.", tone="error")
            return
        self._handle_builtin(resolution.name, resolution.args)

    def _handle_builtin(self, name: str, args: str) -> None:
        if name == "quit":
            self.exit()
        elif name == "help":
            self._notice(_HELP_TEXT)
        elif name == "refresh":
            self._load_queue()
        elif name == "open":
            self._open_context(args)
        elif name == "close":
            self._close_item(args)
        elif name == "correct":
            self._correct_run(args)
        else:
            self._notice(f"/{name} is not available.", tone="error")

    def _load_queue(self) -> None:
        self.state = set_loading(self.state)
        self._refresh_status()
        try:
            items = self.service.list_queue(status=ReviewQueueStatus.OPEN, limit=50)
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = set_items(self.state, items)
        self._refresh_all()

    def _open_context(self, queue_id: str) -> None:
        queue_id = queue_id.strip()
        if not queue_id:
            self._notice("Usage: /open REV-...", tone="error")
            return
        try:
            context = self.service.get_investigation_context(queue_id)
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self.state = select_context(self.state, context)
        self._refresh_all()

    def _close_item(self, args: str) -> None:
        queue_id, _, reason = args.partition(" ")
        if not queue_id or not reason.strip():
            self._notice("Usage: /close REV-... reason", tone="error")
            return
        try:
            self.service.close_queue_item(
                ReviewQueueCloseCommand(queue_id=queue_id, reason=reason.strip()),
                context=_tui_request_context(),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self._notice(f"Closed {queue_id}.")
        self._load_queue()

    def _correct_run(self, args: str) -> None:
        run_id, verdict_value, reason = _parse_correct_args(args)
        if not run_id or not verdict_value or not reason:
            self._notice("Usage: /correct RUN-... verdict reason", tone="error")
            return
        try:
            verdict = Verdict(verdict_value)
        except ValueError:
            self._notice(f"Unknown verdict {verdict_value!r}.", tone="error")
            return
        try:
            self.service.correct(
                CorrectionCommand(run_id=run_id, corrected_verdict=verdict, reason=reason),
                context=_tui_request_context(),
            )
        except SocServiceError as exc:
            self._notice(str(exc), tone="error")
            return
        self._notice(f"Corrected {run_id} -> {verdict.value}.")
        self._load_queue()

    def _notice(self, text: str, *, tone: str = "info") -> None:
        self.state = add_notice(self.state, text, tone="error" if tone == "error" else "info")
        self._refresh_all()

    def _refresh_all(self) -> None:
        self.query_one("#header", Static).update(render_header(database_label=self.database_label))
        self.query_one("#main", Static).update(render_main(self.state))
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(render_status(self.state))

    def check_action(self, action: str, parameters):  # noqa: D401 - Textual hook
        custom = {"nav_up", "nav_down", "palette_complete", "palette_accept", "escape"}
        if action in custom:
            if action == "palette_accept":
                return True if self._palette_open else None
            return True
        return True

    def action_nav_down(self) -> None:
        if self._palette_open and self._palette_items:
            self._palette_index = min(self._palette_index + 1, len(self._palette_items) - 1)
            self._render_palette()

    def action_nav_up(self) -> None:
        if self._palette_open and self._palette_items:
            self._palette_index = max(self._palette_index - 1, 0)
            self._render_palette()

    def action_palette_complete(self) -> None:
        if self._palette_open:
            self._fill_from_palette()

    def action_palette_accept(self) -> None:
        if self._palette_open:
            item = self._current_palette_item()
            if item is not None:
                self.query_one("#composer", Input).value = ""
                self._close_palette()
                self._handle_submit(f"/{item.name}")

    def action_escape(self) -> None:
        self._close_palette()

    def action_redraw(self) -> None:
        self._refresh_all()

    def _open_palette(self, items) -> None:
        if not items:
            self._close_palette()
            return
        self._palette_items = items
        self._palette_index = min(self._palette_index, len(items) - 1)
        self._palette_open = True
        self.query_one("#palette", Static).add_class("open")
        self._render_palette()

    def _close_palette(self) -> None:
        if not self._palette_open and not self._palette_items:
            return
        self._palette_open = False
        self._palette_items = []
        self._palette_index = 0
        self.query_one("#palette", Static).remove_class("open")

    def _render_palette(self) -> None:
        self.query_one("#palette", Static).update(render_palette(self._palette_items, self._palette_index))

    def _current_palette_item(self):
        if 0 <= self._palette_index < len(self._palette_items):
            return self._palette_items[self._palette_index]
        return None

    def _fill_from_palette(self) -> None:
        item = self._current_palette_item()
        if item is None:
            return
        composer = self.query_one("#composer", Input)
        composer.value = f"/{item.name} "
        composer.cursor_position = len(composer.value)
        self._close_palette()


def _parse_correct_args(args: str) -> tuple[str, str, str]:
    run_id, _, rest = args.strip().partition(" ")
    verdict, _, reason = rest.strip().partition(" ")
    return run_id.strip(), verdict.strip(), reason.strip()


def _tui_request_context() -> ServiceRequestContext:
    return ServiceRequestContext(actor=ActorContext(actor_id="soc-review-tui", surface=EntrySurface.TUI))
