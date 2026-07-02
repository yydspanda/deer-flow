"""Launch helpers for SOC TUI surfaces."""

from __future__ import annotations

from soc_agent.core import SocAgentChatService, SocReviewService


def run_review_tui(service: SocReviewService, *, database_label: str = "") -> None:
    try:
        from soc_agent.tui.app import SocReviewTUI
    except ImportError as exc:
        raise RuntimeError("SOC review TUI requires Textual. Install the backend dev dependencies or deerflow-harness[tui].") from exc

    SocReviewTUI(service, database_label=database_label).run()


def run_chat_tui(
    service: SocAgentChatService,
    *,
    initial_queue_id: str | None = None,
    initial_message: str | None = None,
) -> None:
    try:
        from soc_agent.tui.chat_app import SocAgentChatTUI
    except ImportError as exc:
        raise RuntimeError("SOC agent chat TUI requires Textual. Install the backend dev dependencies or deerflow-harness[tui].") from exc

    SocAgentChatTUI(
        service,
        initial_queue_id=initial_queue_id,
        initial_message=initial_message,
    ).run()
