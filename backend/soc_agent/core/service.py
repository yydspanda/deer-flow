"""Stable public service entry points for SOC Agent use cases."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    ExtractionReport,
    InvestigationContext,
    NormalizationDriftReport,
    NormalizationDriftSample,
    NormalizationInspectionResult,
    NormalizationReport,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueuePriority,
    ReviewQueueStatus,
    ServiceRequestContext,
    SimilarAlertQuery,
    SocAgentActionResult,
    SocAgentApprovalRequest,
    SocAgentChatRequest,
    SocAgentChatResponse,
    SocAgentPermissionDecision,
    SocAgentRiskLevel,
    SocAgentRouteDecision,
    SocAgentStreamEvent,
    SocEvent,
    SocEventType,
    Verdict,
)
from soc_agent.core.runtime import analyze_alert, inspect_alert_normalization
from soc_agent.normalizers import load_mapping_config, normalize_alert_payload
from soc_agent.protocols import (
    AlertRepository,
    AlertSummaryRepository,
    AnalysisRuntime,
    DecisionAuditRepository,
    LLMAnalyzer,
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

    def __init__(self, *, analyzer: LLMAnalyzer | None = None) -> None:
        self._analyzer = analyzer

    def analyze(self, payload: Mapping[str, Any]) -> AnalysisRun:
        return analyze_alert(payload, analyzer=self._analyzer)


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


class SocNormalizationService:
    """Inspect-only normalization service for vendor onboarding and drift triage."""

    def __init__(self, *, repository: AlertRepository | None = None) -> None:
        self._repository = repository

    def inspect(
        self,
        payload: Mapping[str, Any],
        *,
        mapping_path: str | Path | None = None,
        mapping_config: Mapping[str, Any] | None = None,
    ) -> NormalizationInspectionResult:
        if mapping_path is not None and mapping_config is not None:
            raise SocServiceError("mapping_path and mapping_config cannot both be provided")
        loaded_mapping = load_mapping_config(mapping_path) if mapping_path is not None else mapping_config
        return inspect_alert_normalization(payload, mapping_config=loaded_mapping)

    def drift(
        self,
        samples: list[tuple[str, Mapping[str, Any]]],
        *,
        mapping_path: str | Path | None = None,
        mapping_config: Mapping[str, Any] | None = None,
    ) -> NormalizationDriftReport:
        if mapping_path is not None and mapping_config is not None:
            raise SocServiceError("mapping_path and mapping_config cannot both be provided")

        loaded_mapping = load_mapping_config(mapping_path) if mapping_path is not None else mapping_config
        sample_reports: list[NormalizationDriftSample] = []
        for sample_path, payload in samples:
            try:
                inspection = self.inspect(payload, mapping_config=loaded_mapping)
            except Exception as exc:  # noqa: BLE001 - preserve per-sample failures in batch report
                sample_reports.append(_drift_failure_sample(sample_path, str(exc)))
                continue

            sample_reports.append(
                _drift_sample_from_reports(
                    path=sample_path,
                    alert_id=inspection.alert.alert_id,
                    normalization=inspection.normalization_report,
                    extraction=inspection.extraction_report,
                )
            )

        return _normalization_drift_report(sample_reports)

    def drift_recent(self, *, limit: int = 50) -> NormalizationDriftReport:
        if self._repository is None:
            raise SocServiceNotImplementedError("drift_recent requires an AlertRepository")

        sample_reports: list[NormalizationDriftSample] = []
        for run in self._repository.list_runs(limit=limit):
            if run.normalization_report is None or run.extraction_report is None:
                sample_reports.append(
                    _drift_failure_sample(
                        f"run:{run.run_id}",
                        "run is missing normalization or extraction reports",
                        run_id=run.run_id,
                        alert_id=run.alert_id,
                    )
                )
                continue
            sample_reports.append(
                _drift_sample_from_reports(
                    path=f"run:{run.run_id}",
                    run_id=run.run_id,
                    alert_id=run.alert_id,
                    normalization=run.normalization_report,
                    extraction=run.extraction_report,
                )
            )

        return _normalization_drift_report(sample_reports)


def _normalization_drift_report(sample_reports: list[NormalizationDriftSample]) -> NormalizationDriftReport:
    adapter_counts: Counter[str] = Counter()
    source_type_counts: Counter[str] = Counter()
    missing_field_counts: Counter[str] = Counter()
    unmapped_field_counts: Counter[str] = Counter()
    entity_kind_counts: Counter[str] = Counter()
    missing_entity_kind_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()

    for sample in sample_reports:
        if sample.adapter:
            adapter_counts.update([sample.adapter])
        source_type_counts.update([sample.source_type.value])
        missing_field_counts.update(sample.missing_fields)
        unmapped_field_counts.update(sample.unmapped_fields)
        entity_kind_counts.update(sample.entity_counts)
        missing_entity_kind_counts.update(sample.missing_entity_kinds)
        warning_counts.update(sample.warnings)

    suspicious_samples = [sample for sample in sample_reports if sample.status == "failed" or sample.missing_fields or sample.unmapped_fields]

    success_count = sum(1 for sample in sample_reports if sample.status == "success")
    return NormalizationDriftReport(
        sample_count=len(sample_reports),
        success_count=success_count,
        failure_count=len(sample_reports) - success_count,
        adapter_counts=dict(adapter_counts),
        source_type_counts=dict(source_type_counts),
        missing_field_counts=dict(missing_field_counts),
        unmapped_field_counts=dict(unmapped_field_counts),
        entity_kind_counts=dict(entity_kind_counts),
        missing_entity_kind_counts=dict(missing_entity_kind_counts),
        warning_counts=dict(warning_counts),
        suspicious_samples=suspicious_samples,
        samples=sample_reports,
    )


def _drift_sample_from_reports(
    *,
    path: str,
    alert_id: str,
    normalization: NormalizationReport,
    extraction: ExtractionReport,
    run_id: str | None = None,
) -> NormalizationDriftSample:
    return NormalizationDriftSample(
        path=path,
        status="success",
        run_id=run_id,
        alert_id=alert_id,
        adapter=normalization.adapter,
        source_type=normalization.source_type,
        source_system=normalization.source_system,
        missing_fields=normalization.missing_fields,
        unmapped_fields=normalization.unmapped_fields,
        entity_counts=extraction.entity_counts,
        missing_entity_kinds=extraction.missing_entity_kinds,
        warnings=[*normalization.warnings, *extraction.warnings],
    )


def _drift_failure_sample(
    path: str,
    error: str,
    *,
    run_id: str | None = None,
    alert_id: str | None = None,
) -> NormalizationDriftSample:
    return NormalizationDriftSample(
        path=path,
        status="failed",
        run_id=run_id,
        alert_id=alert_id,
        warnings=[error],
        error=error,
    )


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

    def get_investigation_context(self, queue_id: str) -> InvestigationContext:
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("get_investigation_context requires a ReviewQueueRepository")
        if self._repository is None:
            raise SocServiceNotImplementedError("get_investigation_context requires an AlertRepository")

        item = self._review_queue_repository.get_review_item(queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {queue_id} not found")

        run = self._repository.get_run(item.run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {item.run_id} not found")

        summary = self._summary_repository.get_alert_summary(item.run_id) if self._summary_repository is not None else None
        audit_records = self._audit_repository.list_audit_records(item.run_id) if self._audit_repository is not None else []
        similar_alerts = self._summary_repository.find_similar_alert_summaries(_similar_alert_query_from_summary(summary)) if self._summary_repository is not None and summary is not None else []
        return InvestigationContext(
            queue_item=item,
            run=run,
            summary=summary,
            audit_records=audit_records,
            similar_alerts=similar_alerts,
        )


class SocMemoryService:
    """Facts and lessons service placeholder."""

    def list_facts(self) -> list[Any]:
        raise SocServiceNotImplementedError("memory store is planned after PostgreSQL persistence is implemented")


class SocDaemonService:
    """Kafka worker orchestration service placeholder."""

    def start(self) -> None:
        raise SocServiceNotImplementedError("daemon mode is planned for Phase 4")


class SocAgentChatService:
    """Interactive investigation service for TUI/Web/Channels.

    This Phase 1 version is intentionally deterministic. It establishes the
    DeerFlow-compatible stream contract and can load review context, but it does
    not run the future SOC Lead Agent or call LLM tools yet.
    """

    def __init__(
        self,
        *,
        review_service: SocReviewService | None = None,
        capability_router: SocAgentCapabilityRouter | None = None,
        action_dispatcher: SocAgentActionDispatcher | None = None,
    ) -> None:
        self._review_service = review_service
        self._capability_router = capability_router or SocAgentCapabilityRouter()
        self._action_dispatcher = action_dispatcher or SocAgentActionDispatcher(review_service=review_service)

    def stream(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> Iterator[SocAgentStreamEvent]:
        chat_request = _coerce_chat_request(request)
        request_context = context or ServiceRequestContext()
        thread_id = chat_request.thread_id or _new_chat_thread_id()

        yield SocAgentStreamEvent(
            type="values",
            data={
                "title": _chat_title(chat_request),
                "messages": [],
                "artifacts": [],
                "thread_id": thread_id,
            },
        )

        route_decision = self._capability_router.route(chat_request)
        yield _route_decision_event(route_decision)
        if not route_decision.allowed:
            yield _assistant_event(f"Route denied: {route_decision.reason}")
            yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})
            return

        permission_decision = self._action_dispatcher.check_permission(chat_request, route_decision, context=request_context)
        yield _permission_decision_event(permission_decision)
        if not permission_decision.allowed:
            if permission_decision.requires_human_approval:
                yield _approval_request_event(_approval_request_from_permission(permission_decision, context=request_context))
            yield _assistant_event(_permission_denied_message(permission_decision))
            yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})
            return

        action_result = self._action_dispatcher.dispatch(chat_request, route_decision, context=request_context, permission_decision=permission_decision)
        yield _action_result_event(action_result)
        if action_result.status != "success":
            yield _assistant_event(action_result.message)
            yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})
            return

        if action_result.action == "review.open_context":
            yield SocAgentStreamEvent(type="custom", data={"kind": "soc.review_context", **action_result.payload})
        yield _assistant_event(action_result.message)

        yield SocAgentStreamEvent(type="end", data={"usage": {}, "thread_id": thread_id})

    def send_message(
        self,
        request: SocAgentChatRequest | str,
        *,
        context: ServiceRequestContext | None = None,
    ) -> SocAgentChatResponse:
        events = list(self.stream(request, context=context))
        thread_id = _thread_id_from_events(events)
        return SocAgentChatResponse(
            thread_id=thread_id,
            events=events,
            final_text=_final_text_from_events(events),
        )


class SocAgentCapabilityRouter:
    """Deterministic whitelist router for SOC chat capabilities."""

    DEFAULT_ALLOWED_ROUTES = frozenset({"chat.freeform", "review.open_context"})

    def __init__(self, *, allowed_routes: set[str] | None = None) -> None:
        self._allowed_routes = frozenset(allowed_routes or self.DEFAULT_ALLOWED_ROUTES)

    def route(self, request: SocAgentChatRequest) -> SocAgentRouteDecision:
        route = _route_name(request)
        allowed = route in self._allowed_routes and (not request.allowed_routes or route in set(request.allowed_routes))
        if allowed:
            return SocAgentRouteDecision(
                route=route,
                allowed=True,
                reason=f"route {route} is allowed by whitelist",
                input_text=request.message,
            )
        return SocAgentRouteDecision(
            route=route,
            allowed=False,
            reason=f"route {route} is not allowed",
            input_text=request.message,
        )


class SocAgentActionPolicy:
    """Permission policy for routed SOC Agent service actions."""

    POLICY_VERSION = "soc.agent_action_policy.v1"
    READ_ONLY_ACTIONS = frozenset({"chat.ready_message", "review.open_context"})
    ANALYST_WRITE_ACTIONS = frozenset({"review.correct", "analysis.replay"})
    HIGH_RISK_ACTIONS = frozenset({"response.block_ip", "endpoint.isolate_host", "mcp.invoke"})

    def check(
        self,
        *,
        action: str,
        route: str,
        request: SocAgentChatRequest,
        context: ServiceRequestContext,
    ) -> SocAgentPermissionDecision:
        risk_level = self._risk_level(action)
        if risk_level is SocAgentRiskLevel.READ_ONLY:
            return self._decision(
                action=action,
                route=route,
                allowed=True,
                risk_level=risk_level,
                reason=f"action {action} is read-only",
                context=context,
            )
        if risk_level is SocAgentRiskLevel.ANALYST_WRITE:
            allowed = "analyst" in context.actor.roles
            reason = f"actor has analyst role for action {action}" if allowed else f"action {action} requires analyst role"
            return self._decision(
                action=action,
                route=route,
                allowed=allowed,
                risk_level=risk_level,
                reason=reason,
                context=context,
            )
        if risk_level is SocAgentRiskLevel.HIGH_RISK:
            return self._decision(
                action=action,
                route=route,
                allowed=False,
                risk_level=risk_level,
                reason=f"action {action} requires human approval",
                context=context,
                requires_human_approval=True,
            )
        return self._decision(
            action=action,
            route=route,
            allowed=False,
            risk_level=SocAgentRiskLevel.UNKNOWN,
            reason=f"action {action} is not registered in policy",
            context=context,
        )

    def _risk_level(self, action: str) -> SocAgentRiskLevel:
        if action in self.READ_ONLY_ACTIONS:
            return SocAgentRiskLevel.READ_ONLY
        if action in self.ANALYST_WRITE_ACTIONS:
            return SocAgentRiskLevel.ANALYST_WRITE
        if action in self.HIGH_RISK_ACTIONS:
            return SocAgentRiskLevel.HIGH_RISK
        return SocAgentRiskLevel.UNKNOWN

    def _decision(
        self,
        *,
        action: str,
        route: str,
        allowed: bool,
        risk_level: SocAgentRiskLevel,
        reason: str,
        context: ServiceRequestContext,
        requires_human_approval: bool = False,
    ) -> SocAgentPermissionDecision:
        return SocAgentPermissionDecision(
            route=route,
            action=action,
            allowed=allowed,
            risk_level=risk_level,
            reason=reason,
            requires_human_approval=requires_human_approval,
            approval_request_id=f"APR-{uuid4().hex[:12].upper()}" if requires_human_approval else None,
            policy_version=self.POLICY_VERSION,
            actor=context.actor,
        )


class SocAgentActionDispatcher:
    """Dispatch allowed SOC Agent routes to explicit service actions."""

    def __init__(self, *, review_service: SocReviewService | None = None, action_policy: SocAgentActionPolicy | None = None) -> None:
        self._review_service = review_service
        self._action_policy = action_policy or SocAgentActionPolicy()

    def check_permission(
        self,
        request: SocAgentChatRequest,
        route_decision: SocAgentRouteDecision,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentPermissionDecision:
        action = _action_name_for_route(route_decision.route)
        if not route_decision.allowed:
            return SocAgentPermissionDecision(
                route=route_decision.route,
                action=action,
                allowed=False,
                risk_level=SocAgentRiskLevel.UNKNOWN,
                reason=route_decision.reason,
                actor=context.actor,
            )
        return self._action_policy.check(action=action, route=route_decision.route, request=request, context=context)

    def dispatch(
        self,
        request: SocAgentChatRequest,
        route_decision: SocAgentRouteDecision,
        *,
        context: ServiceRequestContext,
        permission_decision: SocAgentPermissionDecision | None = None,
    ) -> SocAgentActionResult:
        if not route_decision.allowed:
            return SocAgentActionResult(
                route=route_decision.route,
                action="route.denied",
                status="denied",
                message=route_decision.reason,
            )
        permission = permission_decision or self.check_permission(request, route_decision, context=context)
        if not permission.allowed:
            return SocAgentActionResult(
                route=route_decision.route,
                action=permission.action,
                status="denied",
                message=permission.reason,
                requires_human_approval=permission.requires_human_approval,
            )
        if permission.action == "chat.ready_message":
            return SocAgentActionResult(
                route=route_decision.route,
                action=permission.action,
                status="success",
                message="SOC investigation chat is ready. Phase 1 supports deterministic review context loading; future SOC Lead Agent routing will attach skills, MCP tools, and bounded LLM reasoning here.",
            )
        if permission.action == "review.open_context":
            return self._open_review_context(request, route_decision=route_decision, context=context)
        return SocAgentActionResult(
            route=route_decision.route,
            action=permission.action,
            status="denied",
            message=f"action {permission.action} has no service action mapping",
        )

    def _open_review_context(
        self,
        request: SocAgentChatRequest,
        *,
        route_decision: SocAgentRouteDecision,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if self._review_service is None:
            raise SocServiceNotImplementedError("agent chat review context requires SocReviewService")
        if not request.queue_id:
            return SocAgentActionResult(
                route=route_decision.route,
                action="review.open_context",
                status="failed",
                message="review.open_context requires queue_id",
            )
        investigation_context = self._review_service.get_investigation_context(request.queue_id)
        payload = {
            "queue_id": investigation_context.queue_item.queue_id,
            "run_id": investigation_context.run.run_id,
            "alert_id": investigation_context.run.alert_id,
            "actor_surface": context.actor.surface.value,
        }
        return SocAgentActionResult(
            route=route_decision.route,
            action="review.open_context",
            status="success",
            message=_review_context_loaded_message(
                queue_id=investigation_context.queue_item.queue_id,
                run_id=investigation_context.run.run_id,
                alert_id=investigation_context.run.alert_id,
            ),
            payload=payload,
        )


def _coerce_chat_request(request: SocAgentChatRequest | str) -> SocAgentChatRequest:
    if isinstance(request, SocAgentChatRequest):
        return request
    return SocAgentChatRequest(message=request)


def _route_name(request: SocAgentChatRequest) -> str:
    if request.queue_id:
        return "review.open_context"
    if request.message.strip().startswith("/"):
        return "command.unknown"
    return "chat.freeform"


def _action_name_for_route(route: str) -> str:
    if route == "chat.freeform":
        return "chat.ready_message"
    if route == "review.open_context":
        return "review.open_context"
    if route == "command.unknown":
        return "command.unknown"
    if route in SocAgentActionPolicy.HIGH_RISK_ACTIONS:
        return route
    return "route.unsupported"


def _route_decision_event(decision: SocAgentRouteDecision) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.route_decision",
            "route": decision.route,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "requires_human_approval": decision.requires_human_approval,
        },
    )


def _permission_decision_event(decision: SocAgentPermissionDecision) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.permission_decision",
            "decision_id": decision.decision_id,
            "route": decision.route,
            "action": decision.action,
            "allowed": decision.allowed,
            "risk_level": decision.risk_level.value,
            "reason": decision.reason,
            "requires_human_approval": decision.requires_human_approval,
            "approval_request_id": decision.approval_request_id,
            "policy_version": decision.policy_version,
        },
    )


def _approval_request_from_permission(
    decision: SocAgentPermissionDecision,
    *,
    context: ServiceRequestContext,
) -> SocAgentApprovalRequest:
    return SocAgentApprovalRequest(
        approval_request_id=decision.approval_request_id or f"APR-{uuid4().hex[:12].upper()}",
        permission_decision_id=decision.decision_id,
        route=decision.route,
        action=decision.action,
        risk_level=decision.risk_level,
        reason=decision.reason,
        requested_by=decision.actor or context.actor,
    )


def _approval_request_event(request: SocAgentApprovalRequest) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.approval_request",
            "approval_request_id": request.approval_request_id,
            "permission_decision_id": request.permission_decision_id,
            "route": request.route,
            "action": request.action,
            "risk_level": request.risk_level.value,
            "reason": request.reason,
            "requested_by": request.requested_by.model_dump(mode="json"),
            "status": request.status,
            "created_at": request.created_at.isoformat(),
        },
    )


def _action_result_event(result: SocAgentActionResult) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="custom",
        data={
            "kind": "soc.action_result",
            "route": result.route,
            "action": result.action,
            "status": result.status,
            "message": result.message,
            "requires_human_approval": result.requires_human_approval,
        },
    )


def _permission_denied_message(decision: SocAgentPermissionDecision) -> str:
    if decision.requires_human_approval:
        return f"Action requires human approval: {decision.reason}"
    return f"Permission denied: {decision.reason}"


def _new_chat_thread_id() -> str:
    return f"SOC-TH-{uuid4().hex[:12].upper()}"


def _chat_title(request: SocAgentChatRequest) -> str:
    if request.queue_id:
        return f"SOC Review {request.queue_id}"
    if request.run_id:
        return f"SOC Run {request.run_id}"
    text = " ".join(request.message.split())
    if not text:
        return "SOC Investigation"
    return text[:60]


def _assistant_event(text: str) -> SocAgentStreamEvent:
    return SocAgentStreamEvent(
        type="messages-tuple",
        data={
            "type": "ai",
            "id": f"soc-ai-{uuid4().hex[:8]}",
            "content": text,
        },
    )


def _review_context_loaded_message(*, queue_id: str, run_id: str, alert_id: str) -> str:
    return f"Loaded review context {queue_id} for alert {alert_id} / run {run_id}. Next steps should be expressed as bounded SOC actions such as inspect evidence, compare similar alerts, record correction, or request human approval."


def _thread_id_from_events(events: list[SocAgentStreamEvent]) -> str:
    for event in events:
        thread_id = event.data.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return _new_chat_thread_id()


def _final_text_from_events(events: list[SocAgentStreamEvent]) -> str:
    parts: list[str] = []
    for event in events:
        if event.type != "messages-tuple":
            continue
        if event.data.get("type") != "ai":
            continue
        content = event.data.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)


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


def _similar_alert_query_from_summary(summary: AlertSummary) -> SimilarAlertQuery:
    return SimilarAlertQuery(
        run_id=summary.run_id,
        detection_key=summary.detection_key,
        rule_code=summary.rule_code,
        source_type=summary.source_type,
        category=summary.category,
        entity_keys=summary.entity_keys,
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

    if run.entities.mentions:
        return _dedupe([mention.key for mention in run.entities.mentions])

    values = [
        *(f"ip:{value}" for value in run.entities.ips),
        *(f"domain:{value}" for value in run.entities.domains),
        *(f"url:{value}" for value in run.entities.urls),
        *(f"process:{value}" for value in run.entities.processes),
        *(f"user:{value}" for value in run.entities.users),
        *(f"host:{value}" for value in run.entities.hosts),
        *(f"rule_code:{value}" for value in run.entities.rule_codes),
        *(f"rule_name:{value}" for value in run.entities.rule_names),
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
