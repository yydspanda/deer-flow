"""Launch helpers for SOC TUI surfaces."""

from __future__ import annotations

from soc_agent.core import SocReviewService


def run_review_tui(service: SocReviewService, *, database_label: str = "") -> None:
    try:
        from soc_agent.tui.app import SocReviewTUI
    except ImportError as exc:
        raise RuntimeError("SOC review TUI requires Textual. Install the backend dev dependencies or deerflow-harness[tui].") from exc

    SocReviewTUI(service, database_label=database_label).run()
