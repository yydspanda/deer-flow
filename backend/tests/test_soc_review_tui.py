from __future__ import annotations

from rich.console import Console

from soc_agent.contracts import (
    ActorContext,
    AnalysisRun,
    AnalysisRunStatus,
    EntrySurface,
    InvestigationContext,
    ReviewQueueItem,
    ReviewQueuePriority,
    ReviewQueueStatus,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentRiskLevel,
)
from soc_agent.tui.app import _parse_correct_args, _tui_approval_context, _tui_request_context
from soc_agent.tui.command_registry import filter_commands, resolve
from soc_agent.tui.render import render_main
from soc_agent.tui.view_state import (
    add_notice,
    initial_state,
    select_approval_request,
    select_context,
    set_approval_grant,
    set_approval_requests,
    set_items,
)


def test_soc_review_tui_command_registry_filters_and_resolves() -> None:
    commands = filter_commands("cl")

    assert [command.name for command in commands][:1] == ["close"]
    assert resolve("/open REV-1").kind == "builtin"
    assert resolve("/open REV-1").name == "open"
    assert resolve("/open REV-1").args == "REV-1"
    assert resolve("/approvals").kind == "builtin"
    assert resolve("/approval APR-1").args == "APR-1"
    assert resolve("/approve APR-1 reason").args == "APR-1 reason"
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


def test_soc_review_tui_view_state_tracks_approval_request_and_grant() -> None:
    approval_request = _approval_request()
    approval_grant = _approval_grant(approval_request)

    state = set_approval_requests(initial_state(), [approval_request])
    state = select_approval_request(state, approval_request)
    state = set_approval_grant(state, approval_grant)

    assert state.approval_requests == (approval_request,)
    assert state.selected_approval_request_id == "APR-TUI-001"
    assert state.approval_request == approval_request
    assert state.approval_grant == approval_grant


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


def test_soc_review_tui_render_includes_approval_inbox_and_grant() -> None:
    approval_request = _approval_request()
    approval_grant = _approval_grant(approval_request)
    state = set_approval_grant(select_approval_request(set_approval_requests(initial_state(), [approval_request]), approval_request), approval_grant)

    console = Console(record=True, width=140)
    console.print(render_main(state))
    text = console.export_text()

    assert "APR-TUI-001" in text
    assert "response.block_ip" in text
    assert "Approval Request" in text
    assert "SAT-TUI-001" in text


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


def test_soc_review_tui_approval_context_marks_approver_role() -> None:
    context = _tui_approval_context()

    assert context.actor.actor_id == "soc-review-tui"
    assert context.actor.surface is EntrySurface.TUI
    assert context.actor.roles == ["soc_approver"]


def _approval_request() -> SocAgentApprovalRequest:
    return SocAgentApprovalRequest(
        approval_request_id="APR-TUI-001",
        permission_decision_id="PERM-TUI-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="Action requires human approval",
        requested_by=ActorContext(actor_id="soc-agent-tui", surface=EntrySurface.TUI),
    )


def _approval_grant(approval_request: SocAgentApprovalRequest) -> SocAgentApprovalGrant:
    return SocAgentApprovalGrant(
        approval_grant_id="APG-TUI-001",
        execution_token_id="SAT-TUI-001",
        approval_request_id=approval_request.approval_request_id,
        permission_decision_id=approval_request.permission_decision_id,
        route=approval_request.route,
        action=approval_request.action,
        risk_level=approval_request.risk_level,
        requested_by=approval_request.requested_by,
        approved_by=ActorContext(actor_id="soc-review-tui", surface=EntrySurface.TUI, roles=["soc_approver"]),
        approval_reason="approved in tui",
        expires_at=approval_request.created_at,
    )
