"""Public protocols for replaceable SOC Agent dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from soc_agent.contracts import AlertInput, AnalysisResult, AnalysisRun, DecisionAuditRecord, SocEvent


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


class DecisionAuditRepository(Protocol):
    """Persistence boundary for decision audit records."""

    def save_audit_record(self, record: DecisionAuditRecord) -> None: ...

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]: ...


class SocEventSink(Protocol):
    """Event boundary for TUI/CLI progress, API SSE, channels, daemon logs, and audit."""

    def emit(self, event: SocEvent) -> None: ...
