"""Rich renderers for the SOC review TUI."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from deerflow.tui.theme import THEME
from soc_agent.contracts import InvestigationContext, ReviewQueueItem
from soc_agent.tui.command_registry import Command
from soc_agent.tui.view_state import ReviewViewState


def render_header(*, database_label: str) -> Text:
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(" SOC Review ", style=f"bold {THEME.bg} on {THEME.primary}")
    text.append("  ")
    text.append(database_label or "database", style=THEME.dim)
    text.append("  |  ", style=THEME.dim)
    text.append("ReviewQueue")
    return text


def render_status(state: ReviewViewState) -> Text:
    text = Text(no_wrap=True, overflow="ellipsis")
    if state.loading:
        text.append("* loading", style=f"bold {THEME.warning}")
    else:
        text.append("* ready", style=f"bold {THEME.accent}")
    text.append("   ")
    text.append(f"{len(state.items)} open", style=THEME.muted)
    if state.selected_queue_id:
        text.append("   ")
        text.append(state.selected_queue_id, style=THEME.primary)
    text.append("   /help", style=THEME.dim)
    return text


def render_main(state: ReviewViewState) -> RenderableType:
    blocks: list[RenderableType] = [render_queue_table(state.items, selected_queue_id=state.selected_queue_id)]
    if state.context is not None:
        blocks.append(Text(""))
        blocks.append(render_context(state.context))
    if state.notices:
        blocks.append(Text(""))
        blocks.extend(_render_notice(notice.text, notice.tone) for notice in state.notices)
    return Group(*blocks)


def render_queue_table(items: tuple[ReviewQueueItem, ...], *, selected_queue_id: str | None = None) -> Table:
    table = Table(expand=True)
    table.add_column("Queue", style=THEME.primary, no_wrap=True)
    table.add_column("Priority", no_wrap=True)
    table.add_column("Alert", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Rule")
    table.add_column("Verdict", no_wrap=True)
    table.add_column("Conf", justify="right", no_wrap=True)
    table.add_column("Summary")

    if not items:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "No open review items.")
        return table

    for item in items:
        marker = ">" if item.queue_id == selected_queue_id else " "
        confidence = f"{item.confidence:.2f}" if item.confidence is not None else "-"
        table.add_row(
            f"{marker} {item.queue_id}",
            item.priority.value,
            item.alert_id,
            item.source_type.value,
            item.rule_code or item.rule_name or "-",
            item.verdict.value if item.verdict is not None else "-",
            confidence,
            item.summary or "",
        )
    return table


def render_context(context: InvestigationContext) -> RenderableType:
    run = context.run
    table = Table.grid(expand=True)
    table.add_column(width=20, style=THEME.dim)
    table.add_column(ratio=1)
    table.add_row("run_id", run.run_id)
    table.add_row("alert_id", run.alert_id)
    table.add_row("status", run.status.value)
    if run.analysis is not None:
        table.add_row("analysis", f"{run.analysis.verdict.value} / {run.analysis.confidence:.2f}")
        table.add_row("summary", run.analysis.summary)
        table.add_row("reason", run.analysis.reason)
    if run.decision is not None:
        table.add_row("decision", f"{run.decision.verdict.value} / review={run.decision.needs_review}")
    if run.fact_reconstruction is not None:
        conflicts = ", ".join(item.conflict_type for item in run.fact_reconstruction.conflict_reports) or "none"
        table.add_row("conflicts", conflicts)
    if context.similar_alerts:
        table.add_row("similar", ", ".join(f"{match.summary.alert_id}:{match.score:.0f}" for match in context.similar_alerts[:5]))
    return Group(Text("Investigation Context", style=f"bold {THEME.primary}"), table)


def render_palette(items: list[Command], index: int, limit: int = 8) -> RenderableType:
    if not items:
        return Text("")
    index = max(0, min(index, len(items) - 1))
    window = items[:limit]
    lines: list[RenderableType] = []
    for i, command in enumerate(window):
        selected = i == index
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append("> " if selected else "  ", style=THEME.primary)
        line.append(f"/{command.name}", style=(f"bold {THEME.primary}" if selected else THEME.text))
        line.append("  ")
        line.append(command.description, style=THEME.dim)
        lines.append(line)
    return Group(*lines)


def _render_notice(text: str, tone: str) -> Text:
    style = THEME.error if tone == "error" else THEME.dim
    return Text(f"- {text}", style=f"italic {style}")
