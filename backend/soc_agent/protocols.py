"""Public protocols for replaceable SOC Agent dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from soc_agent.contracts import (
    AlertInput,
    AlertSummary,
    AnalysisResult,
    AnalysisRun,
    DecisionAuditRecord,
    ReviewQueueItem,
    ReviewQueueStatus,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocEvent,
)


class AlertNormalizer(Protocol):
    """Convert a loose source payload into canonical alert input."""

    def __call__(self, payload: Mapping[str, Any]) -> AlertInput: ...


class AnalysisRuntime(Protocol):
    """Run the deterministic analysis pipeline."""

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun: ...


class LLMAnalyzer(Protocol):
    """Bounded LLM analysis node used behind a fixed runtime step."""

    def analyze(self, alert: AlertInput) -> AnalysisResult: ...


class AlertRepository(Protocol):
    """Persistence boundary for analysis runs and alert summaries."""

    def save_run(self, run: AnalysisRun) -> None: ...

    def get_run(self, run_id: str) -> AnalysisRun | None: ...

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]: ...


class DecisionAuditRepository(Protocol):
    """Persistence boundary for decision audit records."""

    def save_audit_record(self, record: DecisionAuditRecord) -> None: ...

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]: ...


class AlertSummaryRepository(Protocol):
    """Persistence boundary for queryable alert summaries."""

    def save_alert_summary(self, summary: AlertSummary) -> None: ...

    def get_alert_summary(self, run_id: str) -> AlertSummary | None: ...

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]: ...

    def find_similar_alert_summaries(self, query: SimilarAlertQuery) -> list[SimilarAlertMatch]: ...


class ReviewQueueRepository(Protocol):
    """Persistence boundary for human review queue items."""

    def save_review_item(self, item: ReviewQueueItem) -> None: ...

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None: ...

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None: ...

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = None,
        limit: int = 50,
    ) -> list[ReviewQueueItem]: ...


class SocEventSink(Protocol):
    """Event boundary for TUI/CLI progress, API SSE, channels, daemon logs, and audit."""

    def emit(self, event: SocEvent) -> None: ...
