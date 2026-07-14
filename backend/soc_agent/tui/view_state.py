"""Pure state model for the SOC review TUI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from soc_agent.contracts import (
    InvestigationContext,
    NormalizationMaintenanceIssue,
    ReviewQueueItem,
    SocAgentActionResult,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
)


@dataclass(frozen=True)
class Notice:
    text: str
    tone: Literal["info", "error"] = "info"


@dataclass(frozen=True)
class ReviewViewState:
    items: tuple[ReviewQueueItem, ...] = ()
    selected_queue_id: str | None = None
    context: InvestigationContext | None = None
    approval_requests: tuple[SocAgentApprovalRequest, ...] = ()
    selected_approval_request_id: str | None = None
    approval_request: SocAgentApprovalRequest | None = None
    approval_grant: SocAgentApprovalGrant | None = None
    approval_action_result: SocAgentActionResult | None = None
    normalization_issues: tuple[NormalizationMaintenanceIssue, ...] = ()
    notices: tuple[Notice, ...] = ()
    loading: bool = False


def initial_state() -> ReviewViewState:
    return ReviewViewState()


def set_items(state: ReviewViewState, items: list[ReviewQueueItem]) -> ReviewViewState:
    selected = state.selected_queue_id
    if selected and all(item.queue_id != selected for item in items):
        selected = None
    return replace(state, items=tuple(items), selected_queue_id=selected, loading=False)


def select_context(state: ReviewViewState, context: InvestigationContext) -> ReviewViewState:
    return replace(
        state,
        selected_queue_id=context.queue_item.queue_id,
        context=context,
        loading=False,
    )


def set_approval_requests(state: ReviewViewState, requests: list[SocAgentApprovalRequest]) -> ReviewViewState:
    selected = state.selected_approval_request_id
    if selected and all(request.approval_request_id != selected for request in requests):
        selected = None
    return replace(state, approval_requests=tuple(requests), selected_approval_request_id=selected, loading=False)


def select_approval_request(state: ReviewViewState, approval_request: SocAgentApprovalRequest) -> ReviewViewState:
    return replace(
        state,
        selected_approval_request_id=approval_request.approval_request_id,
        approval_request=approval_request,
        loading=False,
    )


def set_approval_grant(state: ReviewViewState, grant: SocAgentApprovalGrant) -> ReviewViewState:
    return replace(state, approval_grant=grant, loading=False)


def set_approval_action_result(state: ReviewViewState, result: SocAgentActionResult) -> ReviewViewState:
    return replace(state, approval_action_result=result, loading=False)


def set_normalization_issues(
    state: ReviewViewState,
    issues: list[NormalizationMaintenanceIssue],
) -> ReviewViewState:
    return replace(state, normalization_issues=tuple(issues), loading=False)


def add_notice(state: ReviewViewState, text: str, *, tone: Literal["info", "error"] = "info") -> ReviewViewState:
    notices = (*state.notices[-4:], Notice(text=text, tone=tone))
    return replace(state, notices=notices, loading=False)


def set_loading(state: ReviewViewState, loading: bool = True) -> ReviewViewState:
    return replace(state, loading=loading)
