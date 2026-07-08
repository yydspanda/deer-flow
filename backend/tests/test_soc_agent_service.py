from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.actions.adapters import (
    DryRunOnlySocActionAdapter,
    InMemoryAssetLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    SocActionAdapterRegistry,
)
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertSourceType,
    AlertSummary,
    AnalysisRun,
    AnalysisRunStatus,
    AuditAction,
    CorrectionCommand,
    CorrelationQuery,
    DecisionAuditRecord,
    EntrySurface,
    InvestigationEvidence,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocAgentActionAdapterDescriptor,
    SocAgentActionResult,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentApprovedActionCommand,
    SocAgentChatRequest,
    SocAgentPermissionDecision,
    SocAgentRiskLevel,
    SocAgentRouteDecision,
    SocAssetLookupRecord,
    SocDaemonMessage,
    SocDomainFinding,
    SocDomainFindingDisposition,
    SocDomainFindingSeverity,
    SocDomainName,
    SocDomainTriageResult,
    SocEvent,
    SocEventType,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryQuery,
    SocMemoryRecordStatus,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.core import (
    DeterministicAnalysisRuntime,
    InMemoryInvestigationEvidenceRepository,
    SocAgentActionDispatcher,
    SocAgentActionPolicy,
    SocAgentApprovalService,
    SocAgentCapabilityRouter,
    SocAgentChatService,
    SocAnalysisService,
    SocCorrelationService,
    SocDaemonService,
    SocMemoryService,
    SocNormalizationService,
    SocReviewService,
    SocServiceError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)
from soc_agent.memory import InMemoryMemoryCandidateRepository, SocMemoryCandidateSourceBridge

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[SocEvent] = []

    def emit(self, event: SocEvent) -> None:
        self.events.append(event)


class InMemoryAlertRepository:
    def __init__(self) -> None:
        self.runs: dict[str, AnalysisRun] = {}

    def save_run(self, run: AnalysisRun) -> None:
        self.runs[run.run_id] = run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]:
        return list(self.runs.values())[-limit:][::-1]


class CountingRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self._runtime = DeterministicAnalysisRuntime()

    def analyze(self, payload: dict) -> AnalysisRun:
        self.calls += 1
        return self._runtime.analyze(payload)


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.records: list[DecisionAuditRecord] = []

    def save_audit_record(self, record: DecisionAuditRecord) -> None:
        self.records.append(record)

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]:
        return [record for record in self.records if record.run_id == run_id]

    def find_audit_record_by_idempotency_key(
        self,
        idempotency_key: str,
        *,
        action: str | None = None,
    ) -> DecisionAuditRecord | None:
        for record in reversed(self.records):
            if record.payload.get("idempotency_key") != idempotency_key:
                continue
            if action is not None and record.action.value != action:
                continue
            return record
        return None


class InMemorySummaryRepository:
    def __init__(self) -> None:
        self.summaries: dict[str, AlertSummary] = {}

    def save_alert_summary(self, summary: AlertSummary) -> None:
        self.summaries[summary.run_id] = summary

    def get_alert_summary(self, run_id: str) -> AlertSummary | None:
        return self.summaries.get(run_id)

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]:
        return list(self.summaries.values())[:limit]

    def find_similar_alert_summaries(self, query: SimilarAlertQuery) -> list[SimilarAlertMatch]:
        matches: list[SimilarAlertMatch] = []
        for summary in self.summaries.values():
            if summary.run_id == query.run_id:
                continue
            score = 0.0
            reasons: list[str] = []
            if query.detection_key and summary.detection_key == query.detection_key:
                score += 50
                reasons.append(f"detection_key:{query.detection_key}")
            if query.rule_code and summary.rule_code == query.rule_code:
                score += 40
                reasons.append(f"rule_code:{query.rule_code}")
            shared_entity_keys = sorted(set(query.entity_keys).intersection(summary.entity_keys))
            if shared_entity_keys:
                score += min(len(shared_entity_keys) * 15, 60)
                reasons.extend(f"entity_key:{value}" for value in shared_entity_keys[:10])
            if score:
                matches.append(SimilarAlertMatch(summary=summary, score=score, matched_reasons=reasons))
        return sorted(matches, key=lambda item: item.score, reverse=True)[: query.limit]


class InMemoryReviewQueueRepository:
    def __init__(self) -> None:
        self.items: dict[str, ReviewQueueItem] = {}

    def save_review_item(self, item: ReviewQueueItem) -> None:
        self.items[item.queue_id] = item

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        return self.items.get(queue_id)

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None:
        for item in self.items.values():
            if item.run_id == run_id and item.status == ReviewQueueStatus.OPEN:
                return item
        return None

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = None,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        items = list(self.items.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        return items[:limit]


class InMemoryApprovalGrantRepository:
    def __init__(self) -> None:
        self.grants: dict[str, SocAgentApprovalGrant] = {}
        self.requests: dict[str, SocAgentApprovalRequest] = {}

    def save_approval_grant(self, grant: SocAgentApprovalGrant) -> None:
        self.grants[grant.approval_grant_id] = grant

    def get_approval_grant(self, approval_grant_id: str) -> SocAgentApprovalGrant | None:
        return self.grants.get(approval_grant_id)

    def get_approval_grant_by_token(self, execution_token_id: str) -> SocAgentApprovalGrant | None:
        for grant in self.grants.values():
            if grant.execution_token_id == execution_token_id:
                return grant
        return None

    def save_approval_request(self, approval_request: SocAgentApprovalRequest) -> None:
        self.requests[approval_request.approval_request_id] = approval_request

    def get_approval_request(self, approval_request_id: str) -> SocAgentApprovalRequest | None:
        return self.requests.get(approval_request_id)

    def list_approval_requests(
        self,
        *,
        status: str | None = "pending",
        limit: int = 50,
    ) -> list[SocAgentApprovalRequest]:
        requests = list(self.requests.values())
        if status is not None:
            requests = [request for request in requests if request.status == status]
        return requests[:limit]


class _HighRiskRouter:
    def route(self, request: SocAgentChatRequest) -> SocAgentRouteDecision:
        return SocAgentRouteDecision(route="response.block_ip", allowed=True, reason="test high-risk route")


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _approval_request() -> SocAgentApprovalRequest:
    return SocAgentApprovalRequest(
        approval_request_id="APR-TEST-001",
        permission_decision_id="PERM-TEST-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="action response.block_ip requires human approval",
        requested_by=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI, roles=["analyst"]),
    )


class _ExecutableActionAdapter:
    def __init__(self, descriptor: SocAgentActionAdapterDescriptor) -> None:
        self.descriptor = descriptor
        self.execute_calls = 0

    def dry_run(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="test dry-run",
            payload={"adapter_id": self.descriptor.adapter_id},
        )

    def execute(
        self,
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        self.execute_calls += 1
        raise AssertionError("execute preflight must not call adapter.execute")


def _block_ip_adapter_descriptor(*, execute_supported: bool = False) -> SocAgentActionAdapterDescriptor:
    return SocAgentActionAdapterDescriptor(
        adapter_id="test-block-ip",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        adapter_kind="mcp",
        external_side_effect="write",
        execute_supported=execute_supported,
        required_payload_fields=["ip", "duration_seconds"],
        required_context_refs=["queue_id", "run_id"],
        description="Test block-ip adapter descriptor.",
    )


def _asset_lookup_record() -> SocAssetLookupRecord:
    return SocAssetLookupRecord(
        asset_key="srv-payments-01",
        asset_id="asset-001",
        hostname="srv-payments-01",
        primary_ip="10.10.1.5",
        owner="payments-sre",
        business_unit="payments",
        environment="prod",
        criticality="critical",
        source="unit-test",
    )


def test_analysis_service_emits_events_and_saves_run() -> None:
    sink = RecordingEventSink()
    repository = InMemoryAlertRepository()
    service = SocAnalysisService(repository=repository, event_sink=sink)
    context = ServiceRequestContext(
        request_id="REQ-TEST-001",
        actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI),
        trace_id="trace-001",
        idempotency_key="idem-001",
    )

    run = service.analyze(_sample("approved_scanner.json"), context=context)

    assert repository.get_run(run.run_id) == run
    assert [event.event_type for event in sink.events] == [
        SocEventType.ANALYSIS_REQUESTED,
        SocEventType.ANALYSIS_COMPLETED,
    ]
    assert sink.events[0].request_id == "REQ-TEST-001"
    assert sink.events[0].actor.surface == EntrySurface.TUI
    assert sink.events[1].run_id == run.run_id
    assert sink.events[1].payload["idempotency_key"] == "idem-001"


def test_analysis_service_writes_decision_audit_record() -> None:
    audit_repository = InMemoryAuditRepository()
    service = SocAnalysisService(
        repository=InMemoryAlertRepository(),
        audit_repository=audit_repository,
    )

    run = service.analyze(_sample("approved_scanner.json"))

    assert len(audit_repository.records) == 1
    record = audit_repository.records[0]
    assert record.action == AuditAction.ANALYSIS
    assert record.run_id == run.run_id
    assert record.alert_id == run.alert_id
    assert record.input_hash == run.input_hash
    assert record.final_verdict == Verdict.FALSE_POSITIVE
    assert record.payload["step_count"] == len(run.steps)


def test_analysis_service_reuses_existing_run_for_same_idempotency_key() -> None:
    repository = InMemoryAlertRepository()
    audit_repository = InMemoryAuditRepository()
    summary_repository = InMemorySummaryRepository()
    review_repository = InMemoryReviewQueueRepository()
    runtime = CountingRuntime()
    sink = RecordingEventSink()
    service = SocAnalysisService(
        runtime=runtime,
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
        event_sink=sink,
    )
    context = ServiceRequestContext(idempotency_key="kafka:soc.alerts.raw.v1:0:42")

    first = service.analyze(_sample("pingan_legacy_apt.json"), context=context)
    second = service.analyze(_sample("pingan_legacy_apt.json"), context=context)

    assert second == first
    assert runtime.calls == 1
    assert list(repository.runs) == [first.run_id]
    assert list(summary_repository.summaries) == [first.run_id]
    assert len(review_repository.items) == 1
    assert len(audit_repository.records) == 1
    assert audit_repository.records[0].payload["idempotency_key"] == "kafka:soc.alerts.raw.v1:0:42"
    assert sink.events[-1].payload["idempotent_replay"] is True


def test_analysis_service_writes_alert_summary() -> None:
    summary_repository = InMemorySummaryRepository()
    service = SocAnalysisService(
        repository=InMemoryAlertRepository(),
        summary_repository=summary_repository,
    )

    run = service.analyze(_sample("approved_scanner.json"))

    summary = summary_repository.get_alert_summary(run.run_id)
    assert summary is not None
    assert summary.run_id == run.run_id
    assert summary.alert_id == "ALT-SAMPLE-FP-001"
    assert summary.verdict == Verdict.FALSE_POSITIVE
    assert summary.needs_review is False
    assert summary.detection_key == "sample-edr:rule_code:edr-scan-001"
    assert "ip:10.0.1.10" in summary.entity_keys


def test_correlation_service_returns_matches_and_reusable_evidence() -> None:
    summary_repository = InMemorySummaryRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    subject = AlertSummary(
        run_id="RUN-CURRENT",
        alert_id="ALT-CURRENT",
        detection_key="edr:credential-access",
        rule_code="EDR-CRED-001",
        category="credential_access",
        entity_keys=["host:workstation-1", "user:alice"],
        status=AnalysisRunStatus.NEEDS_REVIEW,
        verdict=Verdict.SUSPICIOUS,
        needs_review=True,
    )
    related = AlertSummary(
        run_id="RUN-RELATED",
        alert_id="ALT-RELATED",
        detection_key="edr:credential-access",
        rule_code="EDR-CRED-001",
        category="credential_access",
        entity_keys=["host:workstation-1", "user:bob"],
        status=AnalysisRunStatus.SUCCESS,
        verdict=Verdict.FALSE_POSITIVE,
        needs_review=False,
    )
    unrelated = AlertSummary(
        run_id="RUN-UNRELATED",
        alert_id="ALT-UNRELATED",
        detection_key="waf:sqli",
        entity_keys=["ip:203.0.113.8"],
        status=AnalysisRunStatus.SUCCESS,
        verdict=Verdict.TRUE_POSITIVE,
    )
    for summary in (subject, related, unrelated):
        summary_repository.save_alert_summary(summary)
    evidence_repository.save_evidence(
        InvestigationEvidence(
            evidence_id="EVI-RELATED-1",
            route="endpoint.process_tree.lookup",
            action="endpoint.process_tree.lookup",
            status="success",
            message="Process tree lookup completed.",
            result_payload={"root_process": "browser.exe", "risk": "low"},
            run_id=related.run_id,
            alert_id=related.alert_id,
        )
    )

    result = SocCorrelationService(
        summary_repository=summary_repository,
        evidence_repository=evidence_repository,
    ).correlate(CorrelationQuery(run_id=subject.run_id, limit=5))

    assert result.subject_summary == subject
    assert result.reusable_evidence_count == 1
    assert [match.summary.run_id for match in result.matches] == ["RUN-RELATED"]
    match = result.matches[0]
    assert "detection_key:edr:credential-access" in match.matched_reasons
    assert "rule_code:EDR-CRED-001" in match.matched_reasons
    assert "entity_key:host:workstation-1" in match.matched_reasons
    assert match.reusable_evidence[0].evidence_id == "EVI-RELATED-1"
    assert match.reusable_evidence[0].result_payload["risk"] == "low"


def test_correlation_service_can_disable_reusable_evidence_loading() -> None:
    summary_repository = InMemorySummaryRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    subject = AlertSummary(
        run_id="RUN-CURRENT",
        alert_id="ALT-CURRENT",
        detection_key="apt:callback",
        entity_keys=["ip:10.0.0.5"],
        status=AnalysisRunStatus.NEEDS_REVIEW,
    )
    related = AlertSummary(
        run_id="RUN-RELATED",
        alert_id="ALT-RELATED",
        detection_key="apt:callback",
        entity_keys=["ip:10.0.0.5"],
        status=AnalysisRunStatus.SUCCESS,
    )
    summary_repository.save_alert_summary(subject)
    summary_repository.save_alert_summary(related)
    evidence_repository.save_evidence(
        InvestigationEvidence(
            route="asset.locate",
            action="asset.locate",
            status="success",
            message="Asset located.",
            run_id=related.run_id,
            alert_id=related.alert_id,
        )
    )

    result = SocCorrelationService(
        summary_repository=summary_repository,
        evidence_repository=evidence_repository,
    ).correlate(CorrelationQuery(run_id=subject.run_id, evidence_limit_per_match=0))

    assert len(result.matches) == 1
    assert result.reusable_evidence_count == 0
    assert result.matches[0].reusable_evidence == []


def test_analysis_service_enqueues_review_item_from_summary() -> None:
    review_repository = InMemoryReviewQueueRepository()
    run = SocAnalysisService(
        repository=InMemoryAlertRepository(),
        review_queue_repository=review_repository,
    ).analyze(_sample("pingan_legacy_apt.json"))

    items = review_repository.list_review_items()
    assert len(items) == 1
    item = items[0]
    assert item.run_id == run.run_id
    assert item.alert_id == "2026494"
    assert item.status == ReviewQueueStatus.OPEN
    assert item.priority.value == "high"
    assert item.reason == "summary.needs_review"
    assert item.rule_code == "RPAADM_002635"
    assert "ip:30.180.248.178" in item.entity_keys


def test_analysis_service_get_run_requires_repository() -> None:
    service = SocAnalysisService()

    with pytest.raises(SocServiceNotImplementedError):
        service.get_run("RUN-UNKNOWN")


def test_analysis_service_replays_saved_run_as_new_run() -> None:
    sink = RecordingEventSink()
    repository = InMemoryAlertRepository()
    service = SocAnalysisService(repository=repository, event_sink=sink)

    original = service.analyze(_sample("approved_scanner.json"))
    replayed = service.replay(original.run_id)

    assert replayed.run_id != original.run_id
    assert replayed.replay_of_run_id == original.run_id
    assert replayed.input_payload == original.input_payload
    assert repository.get_run(original.run_id) == original
    assert repository.get_run(replayed.run_id) == replayed
    assert sink.events[-2].payload["replay_of_run_id"] == original.run_id
    assert sink.events[-1].payload["replay_of_run_id"] == original.run_id


def test_analysis_service_replay_requires_existing_run() -> None:
    service = SocAnalysisService(repository=InMemoryAlertRepository())

    with pytest.raises(SocServiceNotFoundError):
        service.replay("RUN-UNKNOWN")


def test_normalization_service_aggregates_recent_persisted_runs() -> None:
    repository = InMemoryAlertRepository()
    analysis_service = SocAnalysisService(repository=repository)
    approved = analysis_service.analyze(_sample("approved_scanner.json"))
    missing = analysis_service.analyze(_sample("missing_fields.json"))

    report = SocNormalizationService(repository=repository).drift_recent(limit=1)

    assert report.sample_count == 1
    assert report.success_count == 1
    assert report.samples[0].run_id == missing.run_id
    assert report.samples[0].path == f"run:{missing.run_id}"
    assert report.samples[0].alert_id == missing.alert_id
    assert report.missing_field_counts["detection.rule_code_or_name"] == 1
    assert approved.run_id not in {sample.run_id for sample in report.samples}


def test_review_service_corrects_run_and_emits_event() -> None:
    sink = RecordingEventSink()
    repository = InMemoryAlertRepository()
    analysis_service = SocAnalysisService(repository=repository)
    run = analysis_service.analyze(_sample("approved_scanner.json"))
    service = SocReviewService(repository=repository, event_sink=sink)

    corrected = service.correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.TRUE_POSITIVE,
            corrected_confidence=0.9,
            reason="Analyst found malicious follow-up activity.",
        ),
        context=ServiceRequestContext(
            request_id="REQ-CORRECT-001",
            actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.CLI),
        ),
    )

    assert corrected.decision is not None
    assert corrected.decision.verdict == Verdict.TRUE_POSITIVE
    assert corrected.decision.confidence == 0.9
    assert corrected.decision.automation_allowed is False
    assert len(corrected.corrections) == 1
    assert corrected.corrections[0].previous_verdict == Verdict.FALSE_POSITIVE
    assert corrected.corrections[0].candidate_knowledge_status == "pending_review"
    assert repository.get_run(run.run_id) == corrected
    assert sink.events[0].event_type == SocEventType.REVIEW_CORRECTED
    assert sink.events[0].payload["corrected_verdict"] == "true_positive"


def test_review_service_correct_writes_decision_audit_record() -> None:
    audit_repository = InMemoryAuditRepository()
    repository = InMemoryAlertRepository()
    run = SocAnalysisService(repository=repository).analyze(_sample("approved_scanner.json"))

    corrected = SocReviewService(repository=repository, audit_repository=audit_repository).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.TRUE_POSITIVE,
            reason="Manual correction",
        )
    )

    assert len(audit_repository.records) == 1
    record = audit_repository.records[0]
    assert record.action == AuditAction.CORRECTION
    assert record.run_id == corrected.run_id
    assert record.previous_verdict == Verdict.FALSE_POSITIVE
    assert record.final_verdict == Verdict.TRUE_POSITIVE
    assert record.correction_id == corrected.corrections[0].correction_id
    assert record.payload["candidate_knowledge_status"] == "pending_review"


def test_review_service_correct_updates_alert_summary() -> None:
    repository = InMemoryAlertRepository()
    summary_repository = InMemorySummaryRepository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=summary_repository,
    ).analyze(_sample("approved_scanner.json"))

    corrected = SocReviewService(
        repository=repository,
        summary_repository=summary_repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.TRUE_POSITIVE,
            reason="Manual correction",
        )
    )

    summary = summary_repository.get_alert_summary(corrected.run_id)
    assert summary is not None
    assert summary.verdict == Verdict.TRUE_POSITIVE
    assert summary.confidence == 1.0
    assert summary.needs_review is False
    assert summary.summary == corrected.analysis.summary


def test_review_service_correct_closes_open_review_queue_item() -> None:
    repository = InMemoryAlertRepository()
    review_repository = InMemoryReviewQueueRepository()
    run = SocAnalysisService(
        repository=repository,
        review_queue_repository=review_repository,
    ).analyze(_sample("pingan_legacy_edr.json"))
    open_item = review_repository.get_open_review_item_by_run(run.run_id)
    assert open_item is not None

    SocReviewService(
        repository=repository,
        review_queue_repository=review_repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.FALSE_POSITIVE,
            reason="Analyst confirmed authorized activity.",
        )
    )

    closed = review_repository.get_review_item(open_item.queue_id)
    assert closed is not None
    assert closed.status == ReviewQueueStatus.CLOSED
    assert closed.close_reason == "manual correction: Analyst confirmed authorized activity."
    assert closed.closed_by is not None


def test_review_service_correct_proposes_pending_memory_candidate() -> None:
    repository = InMemoryAlertRepository()
    review_repository = InMemoryReviewQueueRepository()
    audit_repository = InMemoryAuditRepository()
    memory_repository = InMemoryMemoryCandidateRepository()
    run = SocAnalysisService(
        repository=repository,
        review_queue_repository=review_repository,
    ).analyze(_sample("pingan_legacy_edr.json"))
    open_item = review_repository.get_open_review_item_by_run(run.run_id)
    assert open_item is not None

    corrected = SocReviewService(
        repository=repository,
        review_queue_repository=review_repository,
        audit_repository=audit_repository,
        memory_candidate_repository=memory_repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.FALSE_POSITIVE,
            corrected_confidence=0.88,
            reason="Analyst confirmed this EDR activity was authorized maintenance.",
        ),
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI),
        ),
    )

    correction = corrected.corrections[0]
    assert correction.candidate_knowledge_status == "pending_review"
    assert correction.memory_candidate_id is not None
    candidate = memory_repository.get_memory_candidate(correction.memory_candidate_id)
    assert candidate is not None
    assert candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert candidate.candidate_type is SocMemoryCandidateType.BENIGN_PATTERN
    assert candidate.source.source_type is SocMemoryCandidateSourceType.CORRECTION
    assert candidate.source.run_id == run.run_id
    assert candidate.source.alert_id == run.alert_id
    assert candidate.source.queue_id == open_item.queue_id
    assert candidate.source.correction_id == correction.correction_id
    assert candidate.idempotency_key == f"memory_candidate:correction:{correction.correction_id}"
    assert "correction" in candidate.facets["candidate_source"]
    assert "false_positive" in candidate.facets["corrected_verdict"]
    assert audit_repository.records[0].payload["memory_candidate_id"] == correction.memory_candidate_id


def test_memory_source_bridge_proposes_domain_finding_candidate_idempotently() -> None:
    memory_repository = InMemoryMemoryCandidateRepository()
    bridge = SocMemoryCandidateSourceBridge(SocMemoryService(candidate_repository=memory_repository))
    finding = SocDomainFinding(
        domain=SocDomainName.EDR,
        title="Reverse shell behavior candidate",
        summary="Endpoint command line and outbound connection resemble a reverse shell.",
        severity=SocDomainFindingSeverity.HIGH,
        disposition=SocDomainFindingDisposition.SUSPICIOUS,
        confidence=0.81,
        evidence_refs=["EVI-PROC-1"],
        skill_names=["soc-endpoint-triage"],
        recommendations=["Confirm process ancestry and remote endpoint before containment."],
    )
    result = SocDomainTriageResult(
        request_id="DTR-SCENARIO-1",
        run_id="RUN-SCENARIO-1",
        alert_id="ALT-SCENARIO-1",
        domain=SocDomainName.EDR,
        handler_id="soc.domain.edr.v1",
        findings=[finding],
        evidence_ref_count=1,
    )

    first = bridge.propose_from_domain_triage_result(
        result,
        queue_id="REV-SCENARIO-1",
        tenant_id="tenant-a",
    )
    second = bridge.propose_from_domain_triage_result(
        result,
        queue_id="REV-SCENARIO-1",
        tenant_id="tenant-a",
    )

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].candidate_id == second[0].candidate_id
    candidate = first[0]
    assert candidate.candidate_type is SocMemoryCandidateType.DETECTION_LESSON
    assert candidate.source.source_type is SocMemoryCandidateSourceType.DOMAIN_FINDING
    assert candidate.source.run_id == "RUN-SCENARIO-1"
    assert candidate.source.alert_id == "ALT-SCENARIO-1"
    assert candidate.source.queue_id == "REV-SCENARIO-1"
    assert "EVI-PROC-1" in candidate.evidence_refs
    assert "domain_finding" in candidate.facets["candidate_source"]
    assert "edr" in candidate.facets["domain"]


def test_review_service_lists_and_closes_queue_item() -> None:
    review_repository = InMemoryReviewQueueRepository()
    run = SocAnalysisService(
        repository=InMemoryAlertRepository(),
        review_queue_repository=review_repository,
    ).analyze(_sample("pingan_legacy_edr.json"))
    item = review_repository.get_open_review_item_by_run(run.run_id)
    assert item is not None

    service = SocReviewService(review_queue_repository=review_repository)
    assert service.list_queue() == [item]

    closed = service.close_queue_item(ReviewQueueCloseCommand(queue_id=item.queue_id, reason="Reviewed in queue"))
    assert closed.status == ReviewQueueStatus.CLOSED
    assert closed.close_reason == "Reviewed in queue"
    assert service.list_queue() == []
    assert service.list_queue(status=ReviewQueueStatus.CLOSED) == [closed]


def test_review_service_gets_investigation_context() -> None:
    repository = InMemoryAlertRepository()
    summary_repository = InMemorySummaryRepository()
    audit_repository = InMemoryAuditRepository()
    review_repository = InMemoryReviewQueueRepository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
    ).analyze(_sample("pingan_legacy_apt.json"))
    item = review_repository.get_open_review_item_by_run(run.run_id)
    assert item is not None

    context = SocReviewService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
    ).get_investigation_context(item.queue_id)

    assert context.queue_item == item
    assert context.run == run
    assert context.summary is not None
    assert context.summary.run_id == run.run_id
    assert context.summary.alert_id == "2026494"
    assert context.audit_records[0].action == AuditAction.ANALYSIS
    assert context.audit_records[0].run_id == run.run_id


def test_review_service_context_includes_similar_alerts() -> None:
    repository = InMemoryAlertRepository()
    summary_repository = InMemorySummaryRepository()
    audit_repository = InMemoryAuditRepository()
    review_repository = InMemoryReviewQueueRepository()
    service = SocAnalysisService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
    )
    similar_run = service.analyze(_sample("pingan_legacy_apt.json"))
    current_run = service.analyze(_sample("pingan_legacy_apt.json"))
    item = review_repository.get_open_review_item_by_run(current_run.run_id)
    assert item is not None

    context = SocReviewService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
    ).get_investigation_context(item.queue_id)

    assert context.similar_alerts
    match = context.similar_alerts[0]
    assert match.summary.run_id == similar_run.run_id
    assert match.score >= 90
    assert "rule_code:RPAADM_002635" in match.matched_reasons
    assert "entity_key:ip:30.180.248.178" in match.matched_reasons
    assert context.correlation_result is not None
    assert context.correlation_result.matches[0].summary.run_id == similar_run.run_id
    assert context.domain_triage_results
    assert context.domain_triage_results[0].findings
    assert context.investigation_view is not None
    assert context.investigation_view.counts["correlation_matches"] == len(context.correlation_result.matches)
    assert context.investigation_view.counts["domain_findings"] >= 1
    timeline_kinds = {item.kind for item in context.investigation_view.evidence_timeline}
    assert {"analysis", "decision", "correlation", "domain_finding"}.issubset(timeline_kinds)


def test_review_service_context_requires_existing_queue_item() -> None:
    with pytest.raises(SocServiceNotFoundError):
        SocReviewService(
            repository=InMemoryAlertRepository(),
            review_queue_repository=InMemoryReviewQueueRepository(),
        ).get_investigation_context("REV-UNKNOWN")


def test_agent_chat_service_streams_deerflow_like_events() -> None:
    service = SocAgentChatService()

    events = list(service.stream(SocAgentChatRequest(message="triage this alert", thread_id="soc-thread-1")))

    assert [event.type for event in events] == ["values", "custom", "custom", "custom", "messages-tuple", "end"]
    assert events[0].data["title"] == "triage this alert"
    assert events[0].data["thread_id"] == "soc-thread-1"
    assert events[0].data["artifacts"] == []
    assert events[1].data["kind"] == "soc.route_decision"
    assert events[1].data["route"] == "chat.freeform"
    assert events[1].data["allowed"] is True
    assert events[2].data["kind"] == "soc.permission_decision"
    assert events[2].data["action"] == "chat.ready_message"
    assert events[2].data["risk_level"] == "read_only"
    assert events[2].data["allowed"] is True
    assert events[3].data["kind"] == "soc.action_result"
    assert events[3].data["action"] == "chat.ready_message"
    assert events[3].data["status"] == "success"
    assert events[4].data["type"] == "ai"
    assert "deterministic review context loading" in events[4].data["content"]
    assert events[-1].data["thread_id"] == "soc-thread-1"


def test_agent_chat_service_materializes_response_from_same_stream() -> None:
    response = SocAgentChatService().send_message("hello soc")

    assert response.thread_id.startswith("SOC-TH-")
    assert [event.type for event in response.events] == ["values", "custom", "custom", "custom", "messages-tuple", "end"]
    assert "SOC investigation chat is ready" in response.final_text


def test_agent_chat_service_loads_review_context() -> None:
    repository = InMemoryAlertRepository()
    summary_repository = InMemorySummaryRepository()
    audit_repository = InMemoryAuditRepository()
    review_repository = InMemoryReviewQueueRepository()
    analysis_service = SocAnalysisService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
    )
    run = analysis_service.analyze(_sample("pingan_legacy_apt.json"))
    item = review_repository.get_open_review_item_by_run(run.run_id)
    assert item is not None
    review_service = SocReviewService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
    )

    events = list(
        SocAgentChatService(review_service=review_service).stream(
            SocAgentChatRequest(message="open queue", queue_id=item.queue_id),
            context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI)),
        )
    )

    assert [event.type for event in events] == ["values", "custom", "custom", "custom", "custom", "custom", "messages-tuple", "end"]
    assert events[0].data["title"] == f"SOC Review {item.queue_id}"
    assert events[1].data["kind"] == "soc.route_decision"
    assert events[1].data["route"] == "review.open_context"
    assert events[1].data["allowed"] is True
    assert events[2].data["kind"] == "soc.permission_decision"
    assert events[2].data["action"] == "review.open_context"
    assert events[2].data["risk_level"] == "read_only"
    assert events[2].data["allowed"] is True
    assert events[3].data["kind"] == "soc.action_result"
    assert events[3].data["action"] == "review.open_context"
    assert events[3].data["status"] == "success"
    assert events[4].data == {
        "kind": "soc.review_context",
        "queue_id": item.queue_id,
        "run_id": run.run_id,
        "alert_id": run.alert_id,
        "actor_surface": "tui",
    }
    assert events[5].data["kind"] == "soc.skill_context"
    assert events[5].data["selected_skills"]
    assert events[5].data["total_token_budget"] > 0
    assert f"Loaded review context {item.queue_id}" in events[6].data["content"]


def test_agent_chat_service_denies_unlisted_route() -> None:
    events = list(SocAgentChatService(capability_router=SocAgentCapabilityRouter(allowed_routes={"chat.freeform"})).stream(SocAgentChatRequest(message="open", queue_id="REV-1")))

    assert [event.type for event in events] == ["values", "custom", "messages-tuple", "end"]
    assert events[1].data["kind"] == "soc.route_decision"
    assert events[1].data["route"] == "review.open_context"
    assert events[1].data["allowed"] is False
    assert "Route denied" in events[2].data["content"]


def test_agent_chat_service_does_not_allow_asset_lookup_by_default() -> None:
    request = SocAgentChatRequest(
        message="lookup asset",
        metadata={
            "soc_route": "asset.lookup",
            "action_payload": {"asset_key": "10.10.1.5"},
        },
    )

    events = list(SocAgentChatService().stream(request))

    assert [event.type for event in events] == ["values", "custom", "messages-tuple", "end"]
    assert events[1].data["kind"] == "soc.route_decision"
    assert events[1].data["route"] == "asset.lookup"
    assert events[1].data["allowed"] is False
    assert "Route denied" in events[2].data["content"]


def test_agent_action_policy_treats_asset_locate_as_read_only() -> None:
    decision = SocAgentActionPolicy().check(
        action="asset.locate",
        route="asset.locate",
        request=SocAgentChatRequest(message="locate asset"),
        context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI)),
    )

    assert decision.allowed is True
    assert decision.risk_level is SocAgentRiskLevel.READ_ONLY
    assert decision.requires_human_approval is False

    process_tree_decision = SocAgentActionPolicy().check(
        action="endpoint.process_tree.lookup",
        route="endpoint.process_tree.lookup",
        request=SocAgentChatRequest(message="lookup process tree"),
        context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI)),
    )
    assert process_tree_decision.allowed is True
    assert process_tree_decision.risk_level is SocAgentRiskLevel.READ_ONLY
    assert process_tree_decision.requires_human_approval is False

    for action in (
        "host.event_context.lookup",
        "threat_intel.ip_reputation.lookup",
        "security_tag.lookup",
    ):
        decision = SocAgentActionPolicy().check(
            action=action,
            route=action,
            request=SocAgentChatRequest(message=f"lookup {action}"),
            context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI)),
        )
        assert decision.allowed is True
        assert decision.risk_level is SocAgentRiskLevel.READ_ONLY
        assert decision.requires_human_approval is False


def test_agent_chat_service_dispatches_explicit_read_only_asset_lookup_adapter() -> None:
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[_asset_lookup_record()])])
    service = SocAgentChatService(
        capability_router=SocAgentCapabilityRouter(allowed_routes={"asset.lookup"}),
        action_dispatcher=SocAgentActionDispatcher(action_adapter_registry=registry),
    )
    request = SocAgentChatRequest(
        message="lookup asset",
        metadata={
            "soc_route": "asset.lookup",
            "action_payload": {"asset_key": "10.10.1.5"},
        },
    )

    events = list(
        service.stream(
            request,
            context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI)),
        )
    )

    assert [event.type for event in events] == ["values", "custom", "custom", "custom", "messages-tuple", "end"]
    assert events[1].data["kind"] == "soc.route_decision"
    assert events[1].data["route"] == "asset.lookup"
    assert events[1].data["allowed"] is True
    assert events[2].data["kind"] == "soc.permission_decision"
    assert events[2].data["action"] == "asset.lookup"
    assert events[2].data["risk_level"] == "read_only"
    assert events[2].data["allowed"] is True
    assert events[3].data["kind"] == "soc.action_result"
    assert events[3].data["action"] == "asset.lookup"
    assert events[3].data["status"] == "success"
    assert events[3].data["payload"]["asset_found"] is True
    assert events[3].data["payload"]["asset_record"]["asset_id"] == "asset-001"
    assert events[3].data["payload"]["external_side_effect"] == "read"
    assert "Asset lookup completed" in events[4].data["content"]


def test_read_only_action_result_can_be_recorded_as_investigation_evidence() -> None:
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[_asset_lookup_record()])])
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    service = SocAgentChatService(
        capability_router=SocAgentCapabilityRouter(allowed_routes={"asset.lookup"}),
        action_dispatcher=SocAgentActionDispatcher(
            action_adapter_registry=registry,
            evidence_repository=evidence_repository,
        ),
    )
    request = SocAgentChatRequest(
        message="lookup asset",
        thread_id="SOC-THREAD-1",
        metadata={
            "soc_route": "asset.lookup",
            "action_payload": {
                "asset_key": "10.10.1.5",
                "context_refs": {
                    "queue_id": "REV-1",
                    "run_id": "RUN-1",
                    "alert_id": "ALT-1",
                    "proposal_id": "PROP-1",
                    "context_hash": "ctx-hash",
                },
            },
        },
    )

    events = list(
        service.stream(
            request,
            context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI)),
        )
    )

    action_payload = events[3].data["payload"]
    assert action_payload["evidence_id"].startswith("EVI-")
    evidence = evidence_repository.list_evidence(queue_id="REV-1")
    assert len(evidence) == 1
    assert evidence[0].evidence_id == action_payload["evidence_id"]
    assert evidence[0].route == "asset.lookup"
    assert evidence[0].action == "asset.lookup"
    assert evidence[0].status == "success"
    assert evidence[0].queue_id == "REV-1"
    assert evidence[0].run_id == "RUN-1"
    assert evidence[0].alert_id == "ALT-1"
    assert evidence[0].thread_id == "SOC-THREAD-1"
    assert evidence[0].source_proposal_id == "PROP-1"
    assert evidence[0].context_hash == "ctx-hash"
    assert evidence[0].actor is not None
    assert evidence[0].actor.actor_id == "analyst-1"
    assert evidence[0].result_payload["asset_record"]["asset_id"] == "asset-001"


def test_pa07_security_tag_read_only_action_records_investigation_evidence() -> None:
    registry = SocActionAdapterRegistry([InMemorySecurityTagLookupActionAdapter()])
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    service = SocAgentChatService(
        capability_router=SocAgentCapabilityRouter(allowed_routes={"security_tag.lookup"}),
        action_dispatcher=SocAgentActionDispatcher(
            action_adapter_registry=registry,
            evidence_repository=evidence_repository,
        ),
    )
    request = SocAgentChatRequest(
        message="lookup security tag",
        metadata={
            "soc_route": "security_tag.lookup",
            "action_payload": {
                "entity_key": "host:web-01",
                "context_refs": {"queue_id": "REV-1", "run_id": "RUN-1", "alert_id": "ALT-1"},
            },
        },
    )

    events = list(
        service.stream(
            request,
            context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI)),
        )
    )

    action_payload = events[3].data["payload"]
    assert action_payload["evidence_id"].startswith("EVI-")
    assert action_payload["security_tag_found"] is True
    evidence = evidence_repository.list_evidence(queue_id="REV-1")
    assert len(evidence) == 1
    assert evidence[0].action == "security_tag.lookup"
    assert evidence[0].status == "success"
    assert evidence[0].result_payload["has_active"] is True
    assert evidence[0].result_payload["security_tag"]["labels"] == ["authorized_maintenance"]


def test_review_service_context_includes_action_evidence() -> None:
    repository = InMemoryAlertRepository()
    review_repository = InMemoryReviewQueueRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    run = AnalysisRun(run_id="RUN-1", alert_id="ALT-1", status=AnalysisRunStatus.NEEDS_REVIEW)
    item = ReviewQueueItem(
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        reason="low_confidence",
        source_type=AlertSourceType.EDR,
        summary="Needs review.",
    )
    repository.save_run(run)
    review_repository.save_review_item(item)
    evidence_repository.save_evidence(
        InvestigationEvidence(
            route="asset.locate",
            action="asset.locate",
            status="success",
            message="Asset location completed.",
            result_payload={"mcp_result": {"company_code": "PA011", "mocked": True}},
            queue_id="REV-1",
            run_id="RUN-1",
            alert_id="ALT-1",
            thread_id="SOC-THREAD-1",
        )
    )

    context = SocReviewService(
        repository=repository,
        review_queue_repository=review_repository,
        evidence_repository=evidence_repository,
    ).get_investigation_context("REV-1")

    assert len(context.action_evidence) == 1
    assert context.action_evidence[0].action == "asset.locate"
    assert context.action_evidence[0].result_payload["mcp_result"]["company_code"] == "PA011"
    assert context.investigation_view is not None
    assert context.investigation_view.counts["action_evidence"] == 1
    assert any(item.kind == "read_only_evidence" and item.source_id == context.action_evidence[0].evidence_id for item in context.investigation_view.evidence_timeline)


def test_agent_chat_service_requires_review_service_for_queue_context() -> None:
    with pytest.raises(SocServiceNotImplementedError):
        list(SocAgentChatService().stream(SocAgentChatRequest(message="open", queue_id="REV-1")))


def test_agent_chat_service_emits_approval_request_for_high_risk_action() -> None:
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI))

    events = list(SocAgentChatService(capability_router=_HighRiskRouter()).stream("block this ip", context=context))

    assert [event.type for event in events] == ["values", "custom", "custom", "custom", "messages-tuple", "end"]
    assert events[1].data["kind"] == "soc.route_decision"
    assert events[1].data["route"] == "response.block_ip"
    assert events[2].data["kind"] == "soc.permission_decision"
    assert events[2].data["action"] == "response.block_ip"
    assert events[2].data["allowed"] is False
    assert events[2].data["requires_human_approval"] is True
    assert events[2].data["approval_request_id"].startswith("APR-")
    assert events[3].data["kind"] == "soc.approval_request"
    assert events[3].data["approval_request_id"] == events[2].data["approval_request_id"]
    assert events[3].data["permission_decision_id"] == events[2].data["decision_id"]
    assert events[3].data["status"] == "pending"
    assert events[3].data["requested_by"]["actor_id"] == "analyst-1"
    assert "Action requires human approval" in events[4].data["content"]


def test_agent_chat_service_persists_approval_request_to_inbox() -> None:
    repository = InMemoryApprovalGrantRepository()
    approval_service = SocAgentApprovalService(request_repository=repository)
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", surface=EntrySurface.TUI))

    events = list(
        SocAgentChatService(
            capability_router=_HighRiskRouter(),
            approval_service=approval_service,
        ).stream("block this ip", context=context)
    )

    approval_event = events[3]
    approval_request_id = approval_event.data["approval_request_id"]
    saved = repository.get_approval_request(approval_request_id)
    assert saved is not None
    assert saved.approval_request_id == approval_request_id
    assert saved.permission_decision_id == approval_event.data["permission_decision_id"]
    assert saved.action == "response.block_ip"
    assert saved.requested_by.actor_id == "analyst-1"


def test_agent_action_dispatcher_maps_chat_route() -> None:
    decision = SocAgentCapabilityRouter().route(SocAgentChatRequest(message="hello"))
    result = SocAgentActionDispatcher().dispatch(SocAgentChatRequest(message="hello"), decision, context=ServiceRequestContext())

    assert result.route == "chat.freeform"
    assert result.action == "chat.ready_message"
    assert result.status == "success"


def test_agent_action_dispatcher_denies_without_permission() -> None:
    decision = SocAgentRouteDecision(route="response.block_ip", allowed=True, reason="test")
    permission = SocAgentPermissionDecision(
        route="response.block_ip",
        action="response.block_ip",
        allowed=False,
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="approval required",
        requires_human_approval=True,
    )

    result = SocAgentActionDispatcher().dispatch(
        SocAgentChatRequest(message="block ip"),
        decision,
        context=ServiceRequestContext(),
        permission_decision=permission,
    )

    assert result.action == "response.block_ip"
    assert result.status == "denied"
    assert result.requires_human_approval is True


def test_agent_action_dispatcher_rejects_missing_queue_id() -> None:
    decision = SocAgentRouteDecision(route="review.open_context", allowed=True, reason="test")
    result = SocAgentActionDispatcher(review_service=SocReviewService()).dispatch(
        SocAgentChatRequest(message="open"),
        decision,
        context=ServiceRequestContext(),
    )

    assert result.action == "review.open_context"
    assert result.status == "failed"
    assert "queue_id" in result.message


def test_agent_action_policy_allows_read_only_actions() -> None:
    decision = SocAgentActionPolicy().check(
        action="review.open_context",
        route="review.open_context",
        request=SocAgentChatRequest(message="open"),
        context=ServiceRequestContext(),
    )

    assert decision.allowed is True
    assert decision.risk_level is SocAgentRiskLevel.READ_ONLY
    assert decision.requires_human_approval is False


def test_agent_action_policy_allows_asset_lookup_as_read_only() -> None:
    decision = SocAgentActionPolicy().check(
        action="asset.lookup",
        route="asset.lookup",
        request=SocAgentChatRequest(message="lookup asset"),
        context=ServiceRequestContext(),
    )

    assert decision.allowed is True
    assert decision.risk_level is SocAgentRiskLevel.READ_ONLY
    assert decision.requires_human_approval is False


def test_agent_action_policy_requires_analyst_role_for_write_actions() -> None:
    denied = SocAgentActionPolicy().check(
        action="review.correct",
        route="review.correct",
        request=SocAgentChatRequest(message="correct"),
        context=ServiceRequestContext(),
    )
    allowed = SocAgentActionPolicy().check(
        action="review.correct",
        route="review.correct",
        request=SocAgentChatRequest(message="correct"),
        context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", roles=["analyst"])),
    )

    assert denied.allowed is False
    assert denied.risk_level is SocAgentRiskLevel.ANALYST_WRITE
    assert allowed.allowed is True
    assert allowed.risk_level is SocAgentRiskLevel.ANALYST_WRITE


def test_agent_action_policy_blocks_high_risk_actions_for_human_approval() -> None:
    decision = SocAgentActionPolicy().check(
        action="response.block_ip",
        route="response.block_ip",
        request=SocAgentChatRequest(message="block ip"),
        context=ServiceRequestContext(),
    )

    assert decision.allowed is False
    assert decision.risk_level is SocAgentRiskLevel.HIGH_RISK
    assert decision.requires_human_approval is True
    assert decision.approval_request_id is not None
    assert decision.approval_request_id.startswith("APR-")


def test_agent_action_policy_denies_unknown_actions() -> None:
    decision = SocAgentActionPolicy().check(
        action="unknown.action",
        route="unknown.route",
        request=SocAgentChatRequest(message="unknown"),
        context=ServiceRequestContext(),
    )

    assert decision.allowed is False
    assert decision.risk_level is SocAgentRiskLevel.UNKNOWN


def test_agent_approval_service_creates_one_time_grant_for_approver() -> None:
    repository = InMemoryApprovalGrantRepository()
    context = ServiceRequestContext(
        actor=ActorContext(actor_id="approver-1", surface=EntrySurface.TUI, roles=["soc_approver"]),
        idempotency_key="idem-approval-1",
    )

    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=context,
        reason="Analyst verified emergency containment scope.",
        expires_in_seconds=300,
    )

    assert grant.approval_grant_id.startswith("APG-")
    assert grant.execution_token_id.startswith("SAT-")
    assert grant.approval_request_id == "APR-TEST-001"
    assert grant.permission_decision_id == "PERM-TEST-001"
    assert grant.action == "response.block_ip"
    assert grant.risk_level is SocAgentRiskLevel.HIGH_RISK
    assert grant.requested_by.actor_id == "analyst-1"
    assert grant.approved_by.actor_id == "approver-1"
    assert grant.approval_reason == "Analyst verified emergency containment scope."
    assert grant.idempotency_key == "idem-approval-1"
    assert grant.single_use is True
    assert grant.status == "approved"
    assert grant.expires_at > grant.approved_at
    assert repository.get_approval_grant(grant.approval_grant_id) == grant
    assert repository.get_approval_grant_by_token(grant.execution_token_id) == grant


def test_agent_approval_service_persists_approval_request_in_inbox() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    approval_request = _approval_request()

    submitted = service.submit_request(approval_request)
    listed = service.list_requests()
    fetched = service.get_request(approval_request.approval_request_id)

    assert submitted == approval_request
    assert listed == [approval_request]
    assert fetched == approval_request


def test_agent_approval_service_approve_saves_request_when_repository_available() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    approval_request = _approval_request()

    grant = service.approve(
        approval_request,
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )

    assert repository.get_approval_request(approval_request.approval_request_id) == approval_request
    assert repository.get_approval_grant(grant.approval_grant_id) == grant


def test_agent_approval_service_request_inbox_requires_repository() -> None:
    service = SocAgentApprovalService()

    with pytest.raises(SocServiceNotImplementedError, match="SocAgentApprovalRequestRepository"):
        service.submit_request(_approval_request())
    with pytest.raises(SocServiceNotImplementedError, match="SocAgentApprovalRequestRepository"):
        service.list_requests()
    with pytest.raises(SocServiceNotImplementedError, match="SocAgentApprovalRequestRepository"):
        service.get_request("APR-MISSING")


def test_agent_approval_service_request_inbox_maps_missing_request() -> None:
    service = SocAgentApprovalService(request_repository=InMemoryApprovalGrantRepository())

    with pytest.raises(SocServiceNotFoundError, match="not found"):
        service.get_request("APR-MISSING")


def test_daemon_service_submits_approval_request_to_shared_inbox() -> None:
    repository = InMemoryApprovalGrantRepository()
    approval_service = SocAgentApprovalService(request_repository=repository)
    approval_request = _approval_request()

    submitted = SocDaemonService(approval_service=approval_service).submit_approval_request(approval_request)

    assert submitted == approval_request
    assert repository.get_approval_request(approval_request.approval_request_id) == approval_request


def test_daemon_service_processes_alert_message_through_analysis_service() -> None:
    repository = InMemoryAlertRepository()
    sink = RecordingEventSink()
    analysis_service = SocAnalysisService(repository=repository, event_sink=sink)
    message = SocDaemonMessage(
        message_id="SDM-ALERT-001",
        kind="alert",
        payload=_sample("approved_scanner.json"),
        topic="soc.alerts",
        partition=1,
        offset=42,
        key="alert-key-1",
    )

    result = SocDaemonService(analysis_service=analysis_service).process_message(message)

    assert result.status == "processed"
    assert result.kind == "alert"
    assert result.run_id is not None
    assert repository.get_run(result.run_id) is not None
    assert result.payload["idempotency_key"] == "kafka:soc.alerts:1:42"
    assert sink.events[0].actor.actor_type is ActorType.SERVICE
    assert sink.events[0].actor.surface is EntrySurface.DAEMON
    assert sink.events[0].request_id.startswith("REQ-")
    assert sink.events[1].payload["idempotency_key"] == "kafka:soc.alerts:1:42"


def test_daemon_service_processes_approval_request_message_to_shared_inbox() -> None:
    repository = InMemoryApprovalGrantRepository()
    approval_service = SocAgentApprovalService(request_repository=repository)
    approval_request = _approval_request()
    message = SocDaemonMessage(
        message_id="SDM-APPROVAL-001",
        kind="approval_request",
        payload=approval_request.model_dump(mode="json"),
        topic="soc.approvals",
        partition=0,
        offset=7,
    )

    result = SocDaemonService(approval_service=approval_service).process_message(message)

    assert result.status == "processed"
    assert result.kind == "approval_request"
    assert result.approval_request_id == approval_request.approval_request_id
    assert result.payload["idempotency_key"] == "kafka:soc.approvals:0:7"
    assert repository.get_approval_request(approval_request.approval_request_id) == approval_request


def test_daemon_service_process_alert_message_requires_analysis_service() -> None:
    with pytest.raises(SocServiceNotImplementedError, match="SocAnalysisService"):
        SocDaemonService().process_message(SocDaemonMessage(kind="alert", payload=_sample("approved_scanner.json")))


def test_daemon_service_submit_approval_request_requires_service() -> None:
    with pytest.raises(SocServiceNotImplementedError, match="SocAgentApprovalService"):
        SocDaemonService().submit_approval_request(_approval_request())


def test_agent_approval_service_dry_runs_approved_action_without_side_effect() -> None:
    repository = InMemoryApprovalGrantRepository()
    approver_context = ServiceRequestContext(
        actor=ActorContext(actor_id="approver-1", surface=EntrySurface.TUI, roles=["soc_approver"]),
        idempotency_key="idem-approval-1",
    )
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=approver_context,
        reason="approved containment scope",
        expires_in_seconds=300,
    )
    operator_context = ServiceRequestContext(
        actor=ActorContext(actor_id="analyst-2", surface=EntrySurface.TUI, roles=["analyst"]),
        idempotency_key="idem-dry-run-1",
    )

    result = SocAgentApprovalService(grant_repository=repository).dry_run_approved_action(
        SocAgentApprovedActionCommand(
            execution_token_id=grant.execution_token_id,
            route="response.block_ip",
            action="response.block_ip",
            payload={"ip": "203.0.113.8"},
        ),
        context=operator_context,
    )

    assert result.status == "success"
    assert result.route == "response.block_ip"
    assert result.action == "response.block_ip"
    assert result.payload["dry_run"] is True
    assert result.payload["approval_grant_id"] == grant.approval_grant_id
    assert result.payload["approved_by"]["actor_id"] == "approver-1"
    assert result.payload["executed_by"]["actor_id"] == "analyst-2"
    assert "no external side effect" in result.message
    assert repository.get_approval_grant(grant.approval_grant_id).status == "approved"


def test_agent_approval_service_dry_run_uses_action_adapter_registry_payload() -> None:
    repository = InMemoryApprovalGrantRepository()
    approval_request = _approval_request().model_copy(
        update={
            "action_payload": {"ip": "203.0.113.8", "duration_seconds": 900},
            "context_refs": {"queue_id": "REV-TEST-001", "run_id": "RUN-TEST-001"},
        }
    )
    grant = SocAgentApprovalService(grant_repository=repository, request_repository=repository).approve(
        approval_request,
        context=ServiceRequestContext(actor=ActorContext(actor_id="approver-1", surface=EntrySurface.TUI, roles=["soc_approver"])),
        reason="approved containment scope",
    )
    registry = SocActionAdapterRegistry([DryRunOnlySocActionAdapter(_block_ip_adapter_descriptor())])
    service = SocAgentApprovalService(
        grant_repository=repository,
        request_repository=repository,
        action_adapter_registry=registry,
    )

    result = service.dry_run_approved_action(
        SocAgentApprovedActionCommand(
            execution_token_id=grant.execution_token_id,
            route="response.block_ip",
            action="response.block_ip",
        ),
        context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-2", surface=EntrySurface.TUI, roles=["analyst"])),
    )

    assert result.status == "success"
    assert result.payload["adapter_validated"] is True
    assert result.payload["adapter_id"] == "test-block-ip"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["approval_grant_id"] == grant.approval_grant_id
    assert result.payload["payload"]["ip"] == "203.0.113.8"
    assert result.payload["payload"]["context_refs"]["queue_id"] == "REV-TEST-001"
    assert repository.get_approval_grant(grant.approval_grant_id).status == "approved"


def test_agent_approval_service_dry_run_maps_adapter_validation_error() -> None:
    repository = InMemoryApprovalGrantRepository()
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=ServiceRequestContext(actor=ActorContext(actor_id="approver-1", roles=["soc_approver"])),
        reason="approved containment scope",
    )
    service = SocAgentApprovalService(
        grant_repository=repository,
        action_adapter_registry=SocActionAdapterRegistry([DryRunOnlySocActionAdapter(_block_ip_adapter_descriptor())]),
    )

    with pytest.raises(SocServiceError, match="adapter validation failed"):
        service.dry_run_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id=grant.execution_token_id,
                route="response.block_ip",
                action="response.block_ip",
                payload={"ip": "203.0.113.8"},
            ),
            context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-2", surface=EntrySurface.TUI, roles=["analyst"])),
        )


def test_agent_approval_service_dry_run_maps_missing_adapter_error() -> None:
    repository = InMemoryApprovalGrantRepository()
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=ServiceRequestContext(actor=ActorContext(actor_id="approver-1", roles=["soc_approver"])),
        reason="approved containment scope",
    )
    service = SocAgentApprovalService(
        grant_repository=repository,
        action_adapter_registry=SocActionAdapterRegistry(),
    )

    with pytest.raises(SocServiceError, match="no action adapter registered"):
        service.dry_run_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id=grant.execution_token_id,
                route="response.block_ip",
                action="response.block_ip",
                payload={"ip": "203.0.113.8", "duration_seconds": 900},
            ),
            context=ServiceRequestContext(actor=ActorContext(actor_id="analyst-2", surface=EntrySurface.TUI, roles=["analyst"])),
        )


def test_agent_approval_service_dry_run_requires_repository() -> None:
    with pytest.raises(SocServiceNotImplementedError, match="SocAgentApprovalGrantRepository"):
        SocAgentApprovalService().dry_run_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id="SAT-UNKNOWN",
                route="response.block_ip",
                action="response.block_ip",
            ),
            context=ServiceRequestContext(),
        )


def test_agent_approval_service_execute_consumes_token_and_is_idempotent() -> None:
    repository = InMemoryApprovalGrantRepository()
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )
    service = SocAgentApprovalService(grant_repository=repository)
    context = ServiceRequestContext(
        actor=ActorContext(actor_id="analyst-2", surface=EntrySurface.TUI, roles=["analyst"]),
        idempotency_key="idem-execute-1",
    )
    command = SocAgentApprovedActionCommand(
        execution_token_id=grant.execution_token_id,
        route="response.block_ip",
        action="response.block_ip",
        dry_run=False,
        payload={"ip": "203.0.113.8"},
    )

    result = service.execute_approved_action(command, context=context)
    replay = service.execute_approved_action(command, context=context)
    consumed = repository.get_approval_grant(grant.approval_grant_id)

    assert result == replay
    assert result.status == "success"
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["payload"] == {"ip": "203.0.113.8"}
    assert consumed.status == "consumed"
    assert consumed.consumed_by.actor_id == "analyst-2"
    assert consumed.consume_idempotency_key == "idem-execute-1"
    assert consumed.execution_result_id == result.payload["execution_result_id"]
    assert consumed.execution_result_payload == result.model_dump(mode="json")


def test_agent_approval_service_execute_preflights_adapter_before_consuming_token() -> None:
    repository = InMemoryApprovalGrantRepository()
    approval_request = _approval_request().model_copy(
        update={
            "action_payload": {"ip": "203.0.113.8", "duration_seconds": 900},
            "context_refs": {"queue_id": "REV-TEST-001", "run_id": "RUN-TEST-001"},
        }
    )
    grant = SocAgentApprovalService(grant_repository=repository, request_repository=repository).approve(
        approval_request,
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )
    adapter = _ExecutableActionAdapter(_block_ip_adapter_descriptor(execute_supported=True))
    service = SocAgentApprovalService(
        grant_repository=repository,
        request_repository=repository,
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
    )

    result = service.execute_approved_action(
        SocAgentApprovedActionCommand(
            execution_token_id=grant.execution_token_id,
            route="response.block_ip",
            action="response.block_ip",
            dry_run=False,
        ),
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="analyst-2", surface=EntrySurface.TUI, roles=["analyst"]),
            idempotency_key="idem-execute-preflight-1",
        ),
    )

    stored = repository.get_approval_grant(grant.approval_grant_id)
    assert adapter.execute_calls == 0
    assert result.status == "success"
    assert result.payload["adapter_preflight_validated"] is True
    assert result.payload["adapter_id"] == "test-block-ip"
    assert result.payload["preflight_only"] is True
    assert result.payload["external_side_effect"] == "not_executed"
    assert result.payload["payload"]["ip"] == "203.0.113.8"
    assert result.payload["payload"]["context_refs"]["run_id"] == "RUN-TEST-001"
    assert stored.status == "consumed"
    assert stored.execution_result_payload == result.model_dump(mode="json")


def test_agent_approval_service_execute_preflight_failure_does_not_consume_token() -> None:
    repository = InMemoryApprovalGrantRepository()
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )
    service = SocAgentApprovalService(
        grant_repository=repository,
        action_adapter_registry=SocActionAdapterRegistry([DryRunOnlySocActionAdapter(_block_ip_adapter_descriptor())]),
    )

    with pytest.raises(SocServiceError, match="does not support execute"):
        service.execute_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id=grant.execution_token_id,
                route="response.block_ip",
                action="response.block_ip",
                dry_run=False,
                payload={
                    "ip": "203.0.113.8",
                    "duration_seconds": 900,
                    "context_refs": {"queue_id": "REV-TEST-001", "run_id": "RUN-TEST-001"},
                },
            ),
            context=ServiceRequestContext(
                actor=ActorContext(actor_id="analyst-2", surface=EntrySurface.TUI, roles=["analyst"]),
                idempotency_key="idem-execute-preflight-fail-1",
            ),
        )

    stored = repository.get_approval_grant(grant.approval_grant_id)
    assert stored.status == "approved"
    assert stored.execution_result_payload is None


def test_agent_approval_service_execute_rejects_consumed_token_with_different_idempotency() -> None:
    repository = InMemoryApprovalGrantRepository()
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )
    service = SocAgentApprovalService(grant_repository=repository)
    command = SocAgentApprovedActionCommand(
        execution_token_id=grant.execution_token_id,
        route="response.block_ip",
        action="response.block_ip",
        dry_run=False,
    )

    service.execute_approved_action(command, context=ServiceRequestContext(idempotency_key="idem-1"))

    with pytest.raises(SocServiceError, match="already been consumed"):
        service.execute_approved_action(command, context=ServiceRequestContext(idempotency_key="idem-2"))


def test_agent_approval_service_execute_requires_non_dry_run_and_idempotency() -> None:
    repository = InMemoryApprovalGrantRepository()
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )
    service = SocAgentApprovalService(grant_repository=repository)

    with pytest.raises(SocServiceError, match="dry_run=false"):
        service.execute_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id=grant.execution_token_id,
                route="response.block_ip",
                action="response.block_ip",
            ),
            context=ServiceRequestContext(idempotency_key="idem-1"),
        )

    with pytest.raises(SocServiceError, match="idempotency_key"):
        service.execute_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id=grant.execution_token_id,
                route="response.block_ip",
                action="response.block_ip",
                dry_run=False,
            ),
            context=ServiceRequestContext(),
        )


def test_agent_approval_service_dry_run_rejects_mismatched_action() -> None:
    repository = InMemoryApprovalGrantRepository()
    grant = SocAgentApprovalService(grant_repository=repository).approve(
        _approval_request(),
        context=ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"])),
        reason="approved containment scope",
    )

    with pytest.raises(SocServiceError, match="action"):
        SocAgentApprovalService(grant_repository=repository).dry_run_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id=grant.execution_token_id,
                route="response.block_ip",
                action="endpoint.isolate_host",
            ),
            context=ServiceRequestContext(),
        )


def test_agent_approval_service_rejects_non_approver() -> None:
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst-1", roles=["analyst"]))

    with pytest.raises(SocServiceError, match="soc_approver"):
        SocAgentApprovalService().approve(_approval_request(), context=context, reason="approve")


def test_agent_approval_service_requires_valid_reason_and_expiry() -> None:
    context = ServiceRequestContext(actor=ActorContext(actor_id="admin-1", roles=["soc_admin"]))
    service = SocAgentApprovalService()

    with pytest.raises(SocServiceError, match="reason"):
        service.approve(_approval_request(), context=context, reason=" ")
    with pytest.raises(SocServiceError, match="expiry"):
        service.approve(_approval_request(), context=context, reason="valid reason", expires_in_seconds=0)


def test_review_service_correct_requires_repository() -> None:
    with pytest.raises(SocServiceNotImplementedError):
        SocReviewService().correct(
            CorrectionCommand(
                run_id="RUN-UNKNOWN",
                corrected_verdict=Verdict.FALSE_POSITIVE,
                reason="manual correction",
            )
        )


def test_memory_service_requires_candidate_repository() -> None:
    with pytest.raises(SocServiceNotImplementedError, match="MemoryCandidateRepository"):
        SocMemoryService().propose_candidate(_pingan_memory_candidate_command())


def test_memory_service_proposes_pingan_candidate_as_pending_review() -> None:
    repository = InMemoryMemoryCandidateRepository()
    event_sink = RecordingEventSink()
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="analyst-1",
            surface=EntrySurface.TUI,
        )
    )
    service = SocMemoryService(candidate_repository=repository, event_sink=event_sink)

    candidate = service.propose_candidate(_pingan_memory_candidate_command(), context=context)

    assert candidate.status == SocMemoryCandidateStatus.PENDING_REVIEW
    assert candidate.runtime_decision_allowed is False
    assert candidate.review_required is True
    assert candidate.tenant_scope == "pingan"
    assert candidate.idempotency_key == "memory:pingan:hids:host-context:demo"
    assert candidate.confidence == 0.7
    assert candidate.facets == {
        "tenant": ["pingan"],
        "domain": ["hids"],
        "capability_card": ["PA-HIDS-001"],
    }
    assert candidate.review_owner == "soc_analyst"
    assert candidate.source.source_surface == EntrySurface.TUI
    assert candidate.source.source_doc == "pingan_docs/hids-alert-assess-flow.md"
    assert candidate.source.capability_card_id == "PA-HIDS-001"
    assert candidate.evidence_refs == ["EVI-HOST-CONTEXT-1"]
    assert candidate.validity.notes == "Only applies to PingAn HIDS host-event context until reviewed."
    assert candidate.proposed_by == context.actor
    assert candidate.metadata["request_id"] == context.request_id
    assert service.get_candidate(candidate.candidate_id) == candidate
    assert service.propose_candidate(_pingan_memory_candidate_command(), context=context) == candidate

    assert event_sink.events[-1].event_type == SocEventType.MEMORY_UPDATED
    assert event_sink.events[-1].payload["operation"] == "memory_candidate.proposed"
    assert event_sink.events[-1].payload["candidate_status"] == "pending_review"
    assert event_sink.events[-1].payload["runtime_decision_allowed"] is False


def test_memory_service_lists_candidates_by_status_and_tenant_scope() -> None:
    service = SocMemoryService(candidate_repository=InMemoryMemoryCandidateRepository())
    pingan = service.propose_candidate(_pingan_memory_candidate_command())
    service.propose_candidate(
        SocMemoryCandidateCreateCommand(
            candidate_type=SocMemoryCandidateType.PROCEDURE,
            target_artifact=SocMemoryTargetArtifact.PUBLIC_SKILL,
            summary="Generic endpoint triage lesson",
            content="Review parent process, command line, user, persistence, and network activity together.",
            tenant_scope="global",
            source=SocMemoryCandidateSource(
                source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
                source_id="manual-endpoint-triage-1",
            ),
            evidence_refs=["manual:analyst-note:endpoint-triage"],
            validity=SocMemoryCandidateValidity(notes="Generic triage guidance pending skill review."),
        )
    )

    candidates = service.list_candidates(
        status=SocMemoryCandidateStatus.PENDING_REVIEW,
        tenant_scope="pingan",
        run_id="RUN-HIDS-DEMO",
        alert_id="ALERT-HIDS-DEMO",
    )

    assert candidates == [pingan]


def test_memory_service_confirms_candidate_into_retrieval_disabled_record() -> None:
    repository = InMemoryMemoryCandidateRepository()
    event_sink = RecordingEventSink()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository, event_sink=event_sink)
    candidate = service.propose_candidate(_pingan_memory_candidate_command())

    result = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Analyst verified this lesson against evidence.",
        ),
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="analyst-1", actor_type=ActorType.USER, surface=EntrySurface.CLI),
        ),
    )

    assert result.previous_status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert result.candidate.status is SocMemoryCandidateStatus.CONFIRMED
    assert result.candidate.reviewed_by is not None
    assert result.memory_record is not None
    assert result.memory_record.source_candidate_id == candidate.candidate_id
    assert result.memory_record.status is SocMemoryRecordStatus.CONFIRMED
    assert result.memory_record.retrieval_enabled is False
    assert result.memory_record.content_hash.startswith("sha256:")
    assert repository.get_memory_record_by_candidate_id(candidate.candidate_id) == result.memory_record
    assert event_sink.events[-1].payload["operation"] == "memory_candidate.reviewed"
    assert event_sink.events[-1].payload["memory_id"] == result.memory_record.memory_id


def test_memory_service_rejects_candidate_without_record() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository)
    candidate = service.propose_candidate(_pingan_memory_candidate_command())

    result = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.REJECT,
            reason="Evidence did not support this candidate.",
        )
    )

    assert result.candidate.status is SocMemoryCandidateStatus.REJECTED
    assert result.memory_record is None
    assert repository.get_memory_record_by_candidate_id(candidate.candidate_id) is None


def test_memory_service_deprecates_confirmed_record() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository)
    candidate = service.propose_candidate(_pingan_memory_candidate_command())
    confirmed = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Confirmed for current tenant.",
        )
    )
    assert confirmed.memory_record is not None

    deprecated = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.DEPRECATE,
            reason="Superseded by newer guidance.",
        )
    )

    assert deprecated.candidate.status is SocMemoryCandidateStatus.DEPRECATED
    assert deprecated.memory_record is not None
    assert deprecated.memory_record.status is SocMemoryRecordStatus.DEPRECATED
    assert deprecated.memory_record.deprecation_reason == "Superseded by newer guidance."


def test_memory_service_retrieval_requires_enabled_confirmed_records() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository)
    candidate = service.propose_candidate(_pingan_memory_candidate_command())
    confirmed = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Confirmed for retrieval gate test.",
        )
    )
    assert confirmed.memory_record is not None

    query = SocMemoryQuery(
        facets={"domain": ["hids"], "capability_card": ["PA-HIDS-001"]},
        text_terms=["authorized"],
    )
    disabled_result = service.find_relevant_records(query)

    assert disabled_result.matches == []
    assert disabled_result.skipped_retrieval_disabled == 1

    enabled_record = confirmed.memory_record.model_copy(update={"retrieval_enabled": True})
    repository.save_memory_record(enabled_record)

    enabled_result = service.find_relevant_records(query)

    assert enabled_result.returned_count == 1
    match = enabled_result.matches[0]
    assert match.memory_id == enabled_record.memory_id
    assert match.retrieval_enabled is True
    assert match.score > 1
    assert "facet:domain=hids" in match.match_reasons
    assert match.content_hash == enabled_record.content_hash
    assert enabled_result.total_token_estimate == match.token_estimate


def test_review_context_includes_relevant_memory_result() -> None:
    repository = InMemoryAlertRepository()
    summary_repository = InMemorySummaryRepository()
    audit_repository = InMemoryAuditRepository()
    review_repository = InMemoryReviewQueueRepository()
    memory_repository = InMemoryMemoryCandidateRepository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
    ).analyze(_sample("pingan_legacy_apt.json"))
    item = review_repository.get_open_review_item_by_run(run.run_id)
    assert item is not None

    memory_service = SocMemoryService(candidate_repository=memory_repository, record_repository=memory_repository)
    candidate = memory_service.propose_candidate(
        SocMemoryCandidateCreateCommand(
            candidate_type=SocMemoryCandidateType.PROCEDURE,
            target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
            summary="APT direction reconstruction",
            content="For this APT rule, reconstruct direction from raw message and internal asset role before suppressing.",
            tenant_scope="global",
            source=SocMemoryCandidateSource(
                source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
                run_id=run.run_id,
                alert_id=run.alert_id,
                queue_id=item.queue_id,
            ),
            evidence_refs=["manual:apt-direction"],
            validity=SocMemoryCandidateValidity(notes="Applies to APT direction reconstruction demos."),
            facets={
                "source_type": ["apt"],
                "rule_code": [item.rule_code or ""],
                "entity": item.entity_keys,
            },
        )
    )
    confirmed = memory_service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Analyst confirmed this APT procedure.",
        )
    )
    assert confirmed.memory_record is not None
    memory_repository.save_memory_record(confirmed.memory_record.model_copy(update={"retrieval_enabled": True}))

    context = SocReviewService(
        repository=repository,
        summary_repository=summary_repository,
        audit_repository=audit_repository,
        review_queue_repository=review_repository,
        memory_record_repository=memory_repository,
    ).get_investigation_context(item.queue_id)

    assert context.relevant_memories is not None
    assert context.relevant_memories.returned_count == 1
    assert context.relevant_memories.matches[0].record.summary == "APT direction reconstruction"
    assert context.investigation_view is not None
    assert context.investigation_view.counts["relevant_memories"] == 1
    assert any(item.kind == "relevant_memory" for item in context.investigation_view.evidence_timeline)


def _pingan_memory_candidate_command() -> SocMemoryCandidateCreateCommand:
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="PingAn HIDS authorized operation candidate",
        content=("PingAn HIDS host-event context may indicate authorized operations when the host event lookup returns a trusted maintenance tag. This is a tenant candidate and must be reviewed before affecting future decisions."),
        tenant_scope="pingan",
        tenant_id="pingan",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.PINGAN_DOC,
            source_doc="pingan_docs/hids-alert-assess-flow.md",
            source_section="host event context",
            capability_card_id="PA-HIDS-001",
            run_id="RUN-HIDS-DEMO",
            alert_id="ALERT-HIDS-DEMO",
            eval_sample_id="pingan-hids-action-evidence",
        ),
        evidence_refs=["EVI-HOST-CONTEXT-1"],
        validity=SocMemoryCandidateValidity(
            notes="Only applies to PingAn HIDS host-event context until reviewed.",
        ),
        idempotency_key="memory:pingan:hids:host-context:demo",
        confidence=0.7,
        facets={
            "tenant": ["pingan"],
            "domain": ["hids"],
            "capability_card": ["PA-HIDS-001"],
        },
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc_analyst",
        labels=["pingan", "hids", "candidate-only"],
        metadata={"source_backlog_id": "PA-09"},
    )


def test_planned_services_fail_fast_until_implemented() -> None:
    with pytest.raises(SocServiceNotImplementedError):
        SocMemoryService().list_facts()
    with pytest.raises(SocServiceNotImplementedError):
        SocDaemonService().start()
