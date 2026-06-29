"""Stable public service entry points for SOC Agent use cases."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from soc_agent.contracts import (
    ActorContext,
    AlertInput,
    AlertSourceType,
    AlertSummary,
    AnalysisRun,
    AnalysisRunStatus,
    AuditAction,
    CorrectionCommand,
    CorrectionRecord,
    Decision,
    DecisionAuditRecord,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueuePriority,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
    Verdict,
)
from soc_agent.core.runtime import analyze_alert
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.protocols import (
    AlertRepository,
    AlertSummaryRepository,
    AnalysisRuntime,
    DecisionAuditRepository,
    ReviewQueueRepository,
    SocEventSink,
)


class SocServiceError(RuntimeError):
    """Base error for service-layer failures."""


class SocServiceNotImplementedError(SocServiceError):
    """Raised when a planned service operation has no Phase 1 implementation."""


class SocServiceNotFoundError(SocServiceError):
    """Raised when a requested SOC resource does not exist."""


class DeterministicAnalysisRuntime:
    """Adapter that exposes the current deterministic runtime as a protocol."""

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun:
        return analyze_alert(payload)


class NoopEventSink:
    """Default event sink used until an entry adapter attaches subscribers."""

    def emit(self, event: SocEvent) -> None:
        return None


class SocAnalysisService:
    """Application service used by DeerFlow-aligned SOC entry adapters.

    TUI/headless CLI, Gateway API, Web UI, IM channels, and background ingestion
    call this service instead of directly assembling pipeline steps or touching
    repositories/adapters.
    """

    def __init__(
        self,
        *,
        runtime: AnalysisRuntime | None = None,
        repository: AlertRepository | None = None,
        summary_repository: AlertSummaryRepository | None = None,
        audit_repository: DecisionAuditRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._runtime = runtime or DeterministicAnalysisRuntime()
        self._repository = repository
        self._summary_repository = summary_repository
        self._audit_repository = audit_repository
        self._review_queue_repository = review_queue_repository
        self._event_sink = event_sink or NoopEventSink()

    def analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        request_context = context or ServiceRequestContext()
        return self._analyze(payload, context=request_context)

    def get_run(self, run_id: str) -> AnalysisRun | None:
        if self._repository is None:
            raise SocServiceNotImplementedError("get_run requires an AlertRepository")
        return self._repository.get_run(run_id)

    def replay(
        self,
        run_id: str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        if self._repository is None:
            raise SocServiceNotImplementedError("replay requires an AlertRepository")
        previous = self._repository.get_run(run_id)
        if previous is None:
            raise SocServiceNotFoundError(f"run {run_id} not found")
        if previous.input_payload is None:
            raise SocServiceNotImplementedError(f"run {run_id} has no replayable input payload")

        request_context = context or ServiceRequestContext()
        return self._analyze(previous.input_payload, context=request_context, replay_of_run_id=run_id)

    def _analyze(
        self,
        payload: Mapping[str, Any],
        *,
        context: ServiceRequestContext,
        replay_of_run_id: str | None = None,
    ) -> AnalysisRun:
        self._emit(
            SocEvent(
                event_type=SocEventType.ANALYSIS_REQUESTED,
                request_id=context.request_id,
                actor=context.actor,
                payload={
                    "surface": context.actor.surface.value,
                    "replay_of_run_id": replay_of_run_id,
                },
            )
        )

        run = self._runtime.analyze(payload)
        run.replay_of_run_id = replay_of_run_id
        if self._repository is not None:
            self._repository.save_run(run)
        summary = _alert_summary_from_run(run)
        if self._summary_repository is not None:
            self._summary_repository.save_alert_summary(summary)
        if self._review_queue_repository is not None:
            _upsert_review_queue_item(self._review_queue_repository, summary)
        if self._audit_repository is not None:
            self._audit_repository.save_audit_record(
                _analysis_audit_record(
                    run,
                    actor=context.actor,
                    action=AuditAction.REPLAY if replay_of_run_id else AuditAction.ANALYSIS,
                )
            )

        self._emit(
            SocEvent(
                event_type=_completion_event_type(run),
                request_id=context.request_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                actor=context.actor,
                payload={
                    "status": run.status.value,
                    "trace_id": context.trace_id,
                    "idempotency_key": context.idempotency_key,
                    "replay_of_run_id": replay_of_run_id,
                },
            )
        )
        return run

    def _emit(self, event: SocEvent) -> None:
        self._event_sink.emit(event)


class SocReviewService:
    """Review queue and correction service."""

    def __init__(
        self,
        *,
        repository: AlertRepository | None = None,
        summary_repository: AlertSummaryRepository | None = None,
        audit_repository: DecisionAuditRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._repository = repository
        self._summary_repository = summary_repository
        self._audit_repository = audit_repository
        self._review_queue_repository = review_queue_repository
        self._event_sink = event_sink or NoopEventSink()

    def correct(
        self,
        command: CorrectionCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AnalysisRun:
        if self._repository is None:
            raise SocServiceNotImplementedError("correct requires an AlertRepository")

        run = self._repository.get_run(command.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {command.run_id} not found")

        request_context = context or ServiceRequestContext()
        previous_verdict = _current_verdict(run)
        record = CorrectionRecord(
            run_id=run.run_id,
            previous_verdict=previous_verdict,
            corrected_verdict=command.corrected_verdict,
            reason=command.reason,
            corrected_confidence=command.corrected_confidence,
            actor=request_context.actor,
            evidence=command.evidence,
            candidate_knowledge_status="pending_review",
        )
        run.corrections.append(record)
        run.decision = Decision(
            verdict=command.corrected_verdict,
            confidence=command.corrected_confidence if command.corrected_confidence is not None else 1.0,
            suggested_action=run.decision.suggested_action if run.decision is not None else "manual correction recorded",
            needs_review=False,
            reason=command.reason,
            automation_allowed=False,
        )
        self._repository.save_run(run)
        if self._summary_repository is not None:
            self._summary_repository.save_alert_summary(_alert_summary_from_run(run))
        if self._review_queue_repository is not None:
            _close_open_review_item_for_run(
                self._review_queue_repository,
                run_id=run.run_id,
                actor=request_context.actor,
                reason=f"manual correction: {command.reason}",
            )
        if self._audit_repository is not None:
            self._audit_repository.save_audit_record(_correction_audit_record(run, record))
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.REVIEW_CORRECTED,
                request_id=request_context.request_id,
                run_id=run.run_id,
                alert_id=run.alert_id,
                actor=request_context.actor,
                payload={
                    "correction_id": record.correction_id,
                    "previous_verdict": previous_verdict.value if previous_verdict is not None else None,
                    "corrected_verdict": command.corrected_verdict.value,
                    "candidate_knowledge_status": record.candidate_knowledge_status,
                },
            )
        )
        return run

    def list_queue(
        self,
        *,
        status: ReviewQueueStatus | None = ReviewQueueStatus.OPEN,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("list_queue requires a ReviewQueueRepository")
        return self._review_queue_repository.list_review_items(status=status, limit=limit)

    def close_queue_item(
        self,
        command: ReviewQueueCloseCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> ReviewQueueItem:
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("close_queue_item requires a ReviewQueueRepository")

        item = self._review_queue_repository.get_review_item(command.queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {command.queue_id} not found")

        request_context = context or ServiceRequestContext()
        item.status = ReviewQueueStatus.CLOSED
        item.closed_at = _utc_now()
        item.closed_by = request_context.actor
        item.close_reason = command.reason
        item.updated_at = item.closed_at
        self._review_queue_repository.save_review_item(item)
        return item


class SocMemoryService:
    """Facts and lessons service placeholder."""

    def list_facts(self) -> list[Any]:
        raise SocServiceNotImplementedError("memory store is planned after PostgreSQL persistence is implemented")


class SocDaemonService:
    """Kafka worker orchestration service placeholder."""

    def start(self) -> None:
        raise SocServiceNotImplementedError("daemon mode is planned for Phase 4")


class SocAgentChatService:
    """Interactive investigation service placeholder for TUI/Web UI."""

    def send_message(self, *args: Any, **kwargs: Any) -> None:
        raise SocServiceNotImplementedError("agent chat is planned after review/replay primitives stabilize")


def _completion_event_type(run: AnalysisRun) -> SocEventType:
    if run.status is AnalysisRunStatus.FAILED:
        return SocEventType.ANALYSIS_FAILED
    return SocEventType.ANALYSIS_COMPLETED


def _current_verdict(run: AnalysisRun) -> Verdict | None:
    if run.decision is not None:
        return run.decision.verdict
    if run.analysis is not None:
        return run.analysis.verdict
    return None


def _current_confidence(run: AnalysisRun) -> float | None:
    if run.decision is not None:
        return run.decision.confidence
    if run.analysis is not None:
        return run.analysis.confidence
    return None


def _alert_summary_from_run(run: AnalysisRun) -> AlertSummary:
    alert = _normalized_alert_from_run(run)
    decision = run.decision
    analysis = run.analysis
    verdict = _current_verdict(run)
    confidence = _current_confidence(run)

    return AlertSummary(
        run_id=run.run_id,
        alert_id=run.alert_id,
        tenant_id=alert.tenant_id if alert is not None else None,
        source_type=alert.source.source_type if alert is not None else AlertSourceType.UNKNOWN,
        source_system=alert.source.source_system if alert is not None else None,
        detection_key=alert.detection.detection_key if alert is not None else None,
        rule_code=alert.detection.rule_code if alert is not None else None,
        rule_name=alert.detection.rule_name if alert is not None else None,
        severity=alert.classification.severity if alert is not None else None,
        category=alert.classification.category if alert is not None else None,
        entity_keys=_entity_keys(run),
        status=run.status,
        verdict=verdict,
        confidence=confidence,
        needs_review=decision.needs_review if decision is not None else run.status is AnalysisRunStatus.NEEDS_REVIEW,
        summary=analysis.summary if analysis is not None else None,
        recommended_action=decision.suggested_action if decision is not None else None,
        input_hash=run.input_hash,
        replay_of_run_id=run.replay_of_run_id,
        created_at=run.started_at,
        updated_at=run.ended_at or run.started_at,
    )


def _normalized_alert_from_run(run: AnalysisRun) -> AlertInput | None:
    if run.input_payload is None:
        return None
    try:
        return normalize_alert_payload(run.input_payload)
    except Exception:  # noqa: BLE001 - summary generation should preserve failed runs
        return None


def _entity_keys(run: AnalysisRun) -> list[str]:
    if run.entities is None:
        return []

    values = [
        *(f"ip:{value}" for value in run.entities.ips),
        *(f"domain:{value}" for value in run.entities.domains),
        *(f"url:{value}" for value in run.entities.urls),
        *(f"process:{value}" for value in run.entities.processes),
        *(f"user:{value}" for value in run.entities.users),
        *(f"host:{value}" for value in run.entities.hosts),
        *(f"rule:{value}" for value in run.entities.rules if value),
    ]
    return _dedupe(values)


def _upsert_review_queue_item(repository: ReviewQueueRepository, summary: AlertSummary) -> None:
    reason = _review_reason(summary)
    if reason is None:
        return

    existing = repository.get_open_review_item_by_run(summary.run_id)
    item = existing or ReviewQueueItem(
        run_id=summary.run_id,
        alert_id=summary.alert_id,
        reason=reason,
    )
    item.tenant_id = summary.tenant_id
    item.priority = _review_priority(summary)
    item.reason = reason
    item.source_type = summary.source_type
    item.source_system = summary.source_system
    item.rule_code = summary.rule_code
    item.rule_name = summary.rule_name
    item.severity = summary.severity
    item.category = summary.category
    item.verdict = summary.verdict
    item.confidence = summary.confidence
    item.entity_keys = summary.entity_keys
    item.summary = summary.summary
    item.updated_at = _utc_now()
    repository.save_review_item(item)


def _close_open_review_item_for_run(
    repository: ReviewQueueRepository,
    *,
    run_id: str,
    actor: ActorContext,
    reason: str,
) -> None:
    item = repository.get_open_review_item_by_run(run_id)
    if item is None:
        return
    item.status = ReviewQueueStatus.CLOSED
    item.closed_at = _utc_now()
    item.closed_by = actor
    item.close_reason = reason
    item.updated_at = item.closed_at
    repository.save_review_item(item)


def _review_reason(summary: AlertSummary) -> str | None:
    if summary.needs_review:
        return "summary.needs_review"
    if summary.confidence is not None and summary.confidence < 0.75:
        return "low_confidence"
    if summary.verdict in {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW, Verdict.SUSPICIOUS}:
        return "uncertain_verdict"
    if _severity_level(summary.severity) >= 2:
        return "high_severity"
    return None


def _review_priority(summary: AlertSummary) -> ReviewQueuePriority:
    if _severity_level(summary.severity) >= 2 or summary.verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return ReviewQueuePriority.HIGH
    if summary.confidence is not None and summary.confidence < 0.6:
        return ReviewQueuePriority.HIGH
    if summary.needs_review:
        return ReviewQueuePriority.MEDIUM
    return ReviewQueuePriority.LOW


def _severity_level(value: str | None) -> int:
    if value is None:
        return 0
    normalized = value.strip().lower()
    if normalized in {"critical", "high", "高危", "严重"}:
        return 2
    if normalized in {"medium", "中危"}:
        return 1
    return 0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _analysis_audit_record(run: AnalysisRun, *, actor: ActorContext, action: AuditAction) -> DecisionAuditRecord:
    return DecisionAuditRecord(
        action=action,
        run_id=run.run_id,
        alert_id=run.alert_id,
        actor=actor,
        input_hash=run.input_hash,
        final_verdict=_current_verdict(run),
        confidence=_current_confidence(run),
        replay_of_run_id=run.replay_of_run_id,
        payload={
            "status": run.status.value,
            "pipeline_version": run.pipeline_version,
            "model_name": run.model_name,
            "prompt_version": run.prompt_version,
            "step_count": len(run.steps),
        },
    )


def _correction_audit_record(run: AnalysisRun, record: CorrectionRecord) -> DecisionAuditRecord:
    return DecisionAuditRecord(
        action=AuditAction.CORRECTION,
        run_id=run.run_id,
        alert_id=run.alert_id,
        actor=record.actor,
        input_hash=run.input_hash,
        previous_verdict=record.previous_verdict,
        final_verdict=record.corrected_verdict,
        confidence=record.corrected_confidence,
        replay_of_run_id=run.replay_of_run_id,
        correction_id=record.correction_id,
        payload={
            "reason": record.reason,
            "candidate_knowledge_status": record.candidate_knowledge_status,
            "evidence_count": len(record.evidence),
            "automation_allowed": False,
        },
    )
