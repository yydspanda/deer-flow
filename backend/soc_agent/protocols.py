"""Public protocols for replaceable SOC Agent dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from soc_agent.contracts import (
    AlertInput,
    AlertSummary,
    AnalysisNodeOutput,
    AnalysisRun,
    DecisionAuditRecord,
    InvestigationEvidence,
    LLMAnalysisRequest,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocAgentActionAdapterDescriptor,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocEvent,
    SocExternalDispositionRecord,
    SocMemoryCandidate,
    SocMemoryCandidateStatus,
    SocMemoryRecord,
    SocMemoryRecordStatus,
)


class AlertNormalizer(Protocol):
    """Convert a loose source payload into canonical alert input."""

    def __call__(self, payload: Mapping[str, Any]) -> AlertInput: ...


class AnalysisRuntime(Protocol):
    """Run the deterministic analysis pipeline."""

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun: ...


class LLMAnalyzer(Protocol):
    """Bounded LLM analysis node used behind a fixed runtime step."""

    step_name: str

    def analyze(self, request: LLMAnalysisRequest) -> AnalysisNodeOutput: ...


class AlertRepository(Protocol):
    """Persistence boundary for analysis runs and alert summaries."""

    def save_run(self, run: AnalysisRun) -> None: ...

    def get_run(self, run_id: str) -> AnalysisRun | None: ...

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]: ...


class DecisionAuditRepository(Protocol):
    """Persistence boundary for decision audit records."""

    def save_audit_record(self, record: DecisionAuditRecord) -> None: ...

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]: ...

    def find_audit_record_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        action: str | None = None,
    ) -> DecisionAuditRecord | None: ...


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


class InvestigationEvidenceRepository(Protocol):
    """Persistence boundary for investigation evidence produced by safe actions."""

    def save_evidence(self, evidence: InvestigationEvidence) -> None: ...

    def list_evidence(
        self,
        *,
        queue_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[InvestigationEvidence]: ...


class MemoryCandidateRepository(Protocol):
    """Persistence boundary for SOC memory candidates."""

    def save_memory_candidate(self, candidate: SocMemoryCandidate) -> None: ...

    def get_memory_candidate(self, candidate_id: str) -> SocMemoryCandidate | None: ...

    def find_memory_candidate_by_idempotency_key(self, idempotency_key: str) -> SocMemoryCandidate | None: ...

    def list_memory_candidates(
        self,
        *,
        status: SocMemoryCandidateStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryCandidate]: ...


class MemoryRecordRepository(Protocol):
    """Persistence boundary for confirmed SOC memory records."""

    def save_memory_record(self, record: SocMemoryRecord) -> None: ...

    def get_memory_record(self, memory_id: str) -> SocMemoryRecord | None: ...

    def get_memory_record_by_candidate_id(self, candidate_id: str) -> SocMemoryRecord | None: ...

    def list_memory_records(
        self,
        *,
        status: SocMemoryRecordStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        source_candidate_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryRecord]: ...


class SocExternalDispositionRepository(Protocol):
    """Persistence boundary for external disposition feedback events."""

    def save_external_disposition(self, record: SocExternalDispositionRecord) -> None: ...

    def find_external_disposition_by_idempotency_key(self, idempotency_key: str) -> SocExternalDispositionRecord | None: ...

    def list_external_dispositions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        external_system: str | None = None,
        external_case_id: str | None = None,
        limit: int = 50,
    ) -> list[SocExternalDispositionRecord]: ...


class SocAgentApprovalGrantRepository(Protocol):
    """Persistence boundary for approved high-risk action grants."""

    def save_approval_grant(self, grant: SocAgentApprovalGrant) -> None: ...

    def get_approval_grant(self, approval_grant_id: str) -> SocAgentApprovalGrant | None: ...

    def get_approval_grant_by_token(self, execution_token_id: str) -> SocAgentApprovalGrant | None: ...


class SocAgentApprovalRequestRepository(Protocol):
    """Persistence boundary for pending high-risk action approval requests."""

    def save_approval_request(self, approval_request: SocAgentApprovalRequest) -> None: ...

    def get_approval_request(self, approval_request_id: str) -> SocAgentApprovalRequest | None: ...

    def list_approval_requests(
        self,
        *,
        status: str | None = "pending",
        limit: int = 50,
    ) -> list[SocAgentApprovalRequest]: ...


class SocActionAdapter(Protocol):
    """Replaceable adapter for approved SOC response actions."""

    descriptor: SocAgentActionAdapterDescriptor

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...


class SocActionAdapterRegistryPort(Protocol):
    """Allowlisted registry boundary for approved SOC response action adapters."""

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...

    def preflight_execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult: ...


class SocEventSink(Protocol):
    """Event boundary for TUI/CLI progress, API SSE, channels, daemon logs, and audit."""

    def emit(self, event: SocEvent) -> None: ...
