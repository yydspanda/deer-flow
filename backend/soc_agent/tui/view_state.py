"""Pure state model for the SOC review TUI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from soc_agent.contracts import InvestigationContext, ReviewQueueItem


@dataclass(frozen=True)
class Notice:
    text: str
    tone: Literal["info", "error"] = "info"


@dataclass(frozen=True)
class ReviewViewState:
    items: tuple[ReviewQueueItem, ...] = ()
    selected_queue_id: str | None = None
    context: InvestigationContext | None = None
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


def add_notice(state: ReviewViewState, text: str, *, tone: Literal["info", "error"] = "info") -> ReviewViewState:
    notices = (*state.notices[-4:], Notice(text=text, tone=tone))
    return replace(state, notices=notices, loading=False)


def set_loading(state: ReviewViewState, loading: bool = True) -> ReviewViewState:
    return replace(state, loading=loading)
