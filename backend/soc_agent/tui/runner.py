"""Launch helpers for SOC TUI surfaces."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from soc_agent.contracts import ServiceRequestContext, SocAgentChatRequest, SocAgentStreamEvent
from soc_agent.core import (
    SocAgentApprovalService,
    SocDispositionEvaluationService,
    SocNormalizationMaintenanceService,
    SocReviewService,
)


class SocChatServiceLike(Protocol):
    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]: ...


def run_review_tui(
    service: SocReviewService,
    *,
    approval_service: SocAgentApprovalService | None = None,
    normalization_service: SocNormalizationMaintenanceService | None = None,
    disposition_evaluation_service: SocDispositionEvaluationService | None = None,
    actor_id: str = "soc-review-tui",
    database_label: str = "",
) -> None:
    try:
        from soc_agent.tui.app import SocReviewTUI
    except ImportError as exc:
        raise RuntimeError("SOC review TUI requires Textual. Install the backend dev dependencies or deerflow-harness[tui].") from exc

    SocReviewTUI(
        service,
        approval_service=approval_service,
        normalization_service=normalization_service,
        disposition_evaluation_service=disposition_evaluation_service,
        actor_id=actor_id,
        database_label=database_label,
    ).run()


def run_chat_tui(
    service: SocChatServiceLike,
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
