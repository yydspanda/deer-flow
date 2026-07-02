from __future__ import annotations

from rich.console import Console

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    EntrySurface,
    InvestigationContext,
    ReviewQueueItem,
    ReviewQueuePriority,
    ReviewQueueStatus,
)
from soc_agent.tui.app import _parse_correct_args, _tui_request_context
from soc_agent.tui.command_registry import filter_commands, resolve
from soc_agent.tui.render import render_main
from soc_agent.tui.view_state import add_notice, initial_state, select_context, set_items


def test_soc_review_tui_command_registry_filters_and_resolves() -> None:
    commands = filter_commands("cl")

    assert [command.name for command in commands][:1] == ["close"]
    assert resolve("/open REV-1").kind == "builtin"
    assert resolve("/open REV-1").name == "open"
    assert resolve("/open REV-1").args == "REV-1"
    assert resolve("open REV-1").kind == "unknown"


def test_soc_review_tui_view_state_tracks_items_context_and_notices() -> None:
    item = ReviewQueueItem(
        queue_id="REV-TEST",
        run_id="RUN-TEST",
        alert_id="ALT-TEST",
        status=ReviewQueueStatus.OPEN,
        priority=ReviewQueuePriority.HIGH,
        reason="summary.needs_review",
    )
    run = AnalysisRun(run_id="RUN-TEST", alert_id="ALT-TEST", status=AnalysisRunStatus.NEEDS_REVIEW)
    context = InvestigationContext(queue_item=item, run=run)

    state = set_items(initial_state(), [item])
    state = select_context(state, context)
    state = add_notice(state, "loaded")

    assert state.items == (item,)
    assert state.selected_queue_id == "REV-TEST"
    assert state.context == context
    assert state.notices[-1].text == "loaded"


def test_soc_review_tui_render_includes_queue_and_context() -> None:
    item = ReviewQueueItem(
        queue_id="REV-TEST",
        run_id="RUN-TEST",
        alert_id="ALT-TEST",
        reason="summary.needs_review",
        summary="需要复核",
    )
    run = AnalysisRun(run_id="RUN-TEST", alert_id="ALT-TEST", status=AnalysisRunStatus.NEEDS_REVIEW)
    state = select_context(set_items(initial_state(), [item]), InvestigationContext(queue_item=item, run=run))

    console = Console(record=True, width=120)
    console.print(render_main(state))
    text = console.export_text()

    assert "REV-TEST" in text
    assert "ALT-TEST" in text
    assert "Investigation Context" in text
    assert "RUN-TEST" in text


def test_soc_review_tui_parse_correct_args() -> None:
    assert _parse_correct_args("RUN-1 false_positive 分析师确认") == (
        "RUN-1",
        "false_positive",
        "分析师确认",
    )


def test_soc_review_tui_request_context_marks_tui_surface() -> None:
    context = _tui_request_context()

    assert context.actor.actor_id == "soc-review-tui"
    assert context.actor.surface is EntrySurface.TUI
