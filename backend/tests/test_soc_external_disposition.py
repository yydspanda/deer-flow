from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    ActorType,
    AlertSourceType,
    AnalysisRun,
    AnalysisRunStatus,
    AuditAction,
    AuthorizationFactRef,
    DecisionAuditRecord,
    DecisionConfidenceSource,
    EntrySurface,
    GovernedContextFactStatus,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocDetectionTruthSnapshot,
    SocDispositionOutcomeCommand,
    SocDispositionOutcomeSource,
    SocDispositionOutcomeStatus,
    SocDispositionProposalReasonCode,
    SocDispositionProposalRecord,
    SocEvent,
    SocExternalDispositionAdapterConfig,
    SocExternalDispositionApplyStatus,
    SocExternalDispositionCanonicalStatus,
    SocExternalDispositionMappingConfig,
    SocExternalDispositionStatusMapping,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import (
    SocDispositionEvaluationService,
    SocExternalDispositionService,
    SocMemoryService,
    SocServiceAuthorizationError,
    SocServiceNotImplementedError,
)
from soc_agent.disposition import (
    InMemoryDispositionEvaluationRepository,
    InMemoryDispositionProposalRepository,
)
from soc_agent.external_disposition import (
    InMemoryExternalDispositionRepository,
    build_external_disposition_event,
    build_external_disposition_idempotency_key,
    resolve_external_disposition_status,
)
from soc_agent.memory import InMemoryMemoryCandidateRepository

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "external_disposition"


def _external_adapter_context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="external-disposition-test-adapter",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.API,
            roles=["external_disposition_adapter"],
            auth_source=ActorAuthSource.EXTERNAL_ADAPTER,
        )
    )


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
        return list(self.runs.values())[:limit]


class InMemoryReviewQueueRepository:
    def __init__(self) -> None:
        self.items: dict[str, ReviewQueueItem] = {}

    def save_review_item(self, item: ReviewQueueItem) -> None:
        self.items[item.queue_id] = item

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        return self.items.get(queue_id)

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None:
        for item in self.items.values():
            if item.run_id == run_id and item.status is ReviewQueueStatus.OPEN:
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
            items = [item for item in items if item.status is status]
        return items[:limit]


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


def test_external_disposition_mapper_builds_vendor_neutral_event() -> None:
    payload = _load_zeus_fixture()

    event = build_external_disposition_event(payload, _zeus_adapter_config())
    mapping = resolve_external_disposition_status(event, _mapping_config())

    assert event.schema_version == "soc.external_disposition.v1"
    assert event.external_system == "zeus"
    assert event.external_case_id == "ZEUS-CASE-20260707-0001"
    assert event.external_status == "误报关闭"
    assert event.external_reason == "经人工复核，该告警由授权安全测试触发，未发现入侵迹象。"
    assert event.external_tags == ["false_positive", "authorized_test"]
    assert event.soc_run_id == "RUN-ZEUS-MOCK-0001"
    assert event.soc_queue_id == "REV-ZEUS-MOCK-0001"
    assert event.raw_payload_hash
    assert mapping.canonical_status is SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE
    assert build_external_disposition_idempotency_key(event) == ("external_disposition:default:zeus:ZEUS-CASE-20260707-0001:ZEUS-EVT-20260707-0001")


def test_external_disposition_service_applies_high_trust_correction_and_closes_review() -> None:
    repository = InMemoryExternalDispositionRepository()
    alert_repository = InMemoryAlertRepository()
    review_repository = InMemoryReviewQueueRepository()
    audit_repository = InMemoryAuditRepository()
    event_sink = RecordingEventSink()
    memory_repository = InMemoryMemoryCandidateRepository()
    memory_service = SocMemoryService(
        candidate_repository=memory_repository,
        event_sink=event_sink,
    )
    run = AnalysisRun(
        run_id="RUN-ZEUS-MOCK-0001",
        alert_id="2026494",
        status=AnalysisRunStatus.NEEDS_REVIEW,
    )
    alert_repository.save_run(run)
    review_repository.save_review_item(
        ReviewQueueItem(
            queue_id="REV-ZEUS-MOCK-0001",
            run_id=run.run_id,
            alert_id=run.alert_id,
            reason="external disposition test",
            source_type=AlertSourceType.NDR,
        )
    )
    proposal_repository = InMemoryDispositionProposalRepository([_proposal_for_target(run.run_id, run.alert_id, "REV-ZEUS-MOCK-0001")])
    evaluation_repository = InMemoryDispositionEvaluationRepository()
    evaluation_service = SocDispositionEvaluationService(
        repository=evaluation_repository,
        proposal_repository=proposal_repository,
        review_queue_repository=review_repository,
        event_sink=event_sink,
    )
    service = SocExternalDispositionService(
        repository=repository,
        mapping_config=_mapping_config(),
        alert_repository=alert_repository,
        review_queue_repository=review_repository,
        audit_repository=audit_repository,
        event_sink=event_sink,
        memory_service=memory_service,
        disposition_proposal_repository=proposal_repository,
        disposition_evaluation_service=evaluation_service,
    )
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="zeus-webhook",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.API,
            roles=["external_disposition_adapter"],
            auth_source=ActorAuthSource.EXTERNAL_ADAPTER,
        ),
        request_id="REQ-external-disposition",
    )

    result = service.apply_event(build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config()), context=context)

    assert result.idempotent is False
    assert result.audit_written is True
    assert result.correction_applied is True
    assert result.memory_candidate_created is True
    assert result.disposition_outcome_recorded is True
    assert result.disposition_outcome_id is not None
    assert result.disposition_outcome_idempotent is False
    assert result.disposition_outcome_skip_reason is None
    assert result.record.apply_status is SocExternalDispositionApplyStatus.MAPPED
    assert result.record.canonical_status is SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE
    assert result.record.target_run_id == run.run_id
    assert result.record.target_alert_id == run.alert_id
    assert result.record.target_queue_id == "REV-ZEUS-MOCK-0001"
    assert result.record.matched_by == "soc_queue_id"
    assert result.record.correction_id is not None
    assert result.record.memory_candidate_id is not None
    candidate = memory_repository.get_memory_candidate(result.record.memory_candidate_id)
    assert candidate is not None
    assert candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert candidate.runtime_decision_allowed is False
    assert candidate.candidate_type is SocMemoryCandidateType.BENIGN_PATTERN
    assert candidate.content == "经人工复核，该告警由授权安全测试触发，未发现入侵迹象。"
    assert candidate.source.source_id == result.record.disposition_id
    assert candidate.source.run_id == run.run_id
    assert candidate.source.queue_id == "REV-ZEUS-MOCK-0001"
    assert candidate.source.correction_id == result.record.correction_id
    assert candidate.idempotency_key == f"memory_candidate:{result.record.idempotency_key}"
    assert candidate.facets["source_type"] == ["external_disposition"]
    assert candidate.facets["external_system"] == ["zeus"]
    assert candidate.facets["canonical_status"] == ["closed_false_positive"]
    assert candidate.facets["mapping_trust_level"] == ["high"]
    assert candidate.review_owner == "soc_analyst"
    assert candidate.labels == [
        "external-disposition",
        "candidate-only",
        "zeus",
        "closed_false_positive",
    ]
    corrected_run = alert_repository.get_run(run.run_id)
    assert corrected_run is not None
    assert corrected_run.decision is not None
    assert corrected_run.decision.verdict is Verdict.FALSE_POSITIVE
    assert corrected_run.decision.confidence == 1.0
    assert corrected_run.decision.confidence_source is DecisionConfidenceSource.EXTERNAL_DISPOSITION
    assert corrected_run.decision.confidence_is_calibrated is False
    assert corrected_run.decision.policy_version == "soc.correction_policy.v1"
    assert corrected_run.decision.confidence_explanation == ("Trusted external disposition confirmation strength; not a calibrated probability.")
    assert corrected_run.corrections[0].confidence_was_explicit is False
    assert review_repository.get_review_item("REV-ZEUS-MOCK-0001").status is ReviewQueueStatus.CLOSED
    outcome = evaluation_repository.get_disposition_outcome(result.disposition_outcome_id)
    assert outcome is not None
    assert outcome.source is SocDispositionOutcomeSource.EXTERNAL_DISPOSITION
    assert outcome.source_ref == result.record.disposition_id
    assert outcome.observed_disposition is SocOperationalDisposition.CLOSED_FALSE_POSITIVE
    assert outcome.outcome_status is SocDispositionOutcomeStatus.OVERRIDDEN
    assert outcome.reviewed_by.actor_id == "zeus-webhook"
    assert {record.action for record in audit_repository.records} == {
        AuditAction.CORRECTION,
        AuditAction.EXTERNAL_DISPOSITION,
    }
    external_audit = [record for record in audit_repository.records if record.action is AuditAction.EXTERNAL_DISPOSITION][0]
    correction_audit = [record for record in audit_repository.records if record.action is AuditAction.CORRECTION][0]
    assert correction_audit.confidence == 1.0
    assert correction_audit.payload["confidence_source"] == "external_disposition"
    assert correction_audit.payload["confidence_is_calibrated"] is False
    assert correction_audit.payload["confidence_was_explicit"] is False
    assert external_audit.payload["apply_status"] == "mapped"
    assert external_audit.payload["correction_id"] == result.record.correction_id
    assert external_audit.payload["memory_candidate_id"] == result.record.memory_candidate_id
    assert external_audit.payload["disposition_outcome_id"] == result.disposition_outcome_id
    assert [event.event_type.value for event in event_sink.events] == [
        "review.corrected",
        "memory.updated",
        "disposition.outcome_recorded",
        "external_disposition.received",
    ]

    duplicate = service.apply_event(build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config()), context=context)
    assert duplicate.idempotent is True
    assert duplicate.correction_applied is False
    assert duplicate.memory_candidate_created is False
    assert duplicate.disposition_outcome_recorded is True
    assert duplicate.disposition_outcome_id == result.disposition_outcome_id
    assert duplicate.disposition_outcome_idempotent is True
    assert duplicate.record.disposition_id == result.record.disposition_id
    assert len(audit_repository.records) == 2
    assert len(memory_repository.list_memory_candidates()) == 1
    assert len(evaluation_repository.list_disposition_outcomes()) == 1

    analyst_result = evaluation_service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=proposal_repository.list_disposition_proposals(limit=1)[0].proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_TRUE_POSITIVE,
            source=SocDispositionOutcomeSource.ANALYST,
            reason="Analyst reviewed the evidence and corrected the external label.",
            evidence_refs=["review:manual-confirmation"],
            supersedes_outcome_id=result.disposition_outcome_id,
            idempotency_key="outcome:analyst:zeus-mock:0001",
        ),
        context=ServiceRequestContext(
            actor=ActorContext(
                actor_id="analyst-7",
                actor_type=ActorType.USER,
                surface=EntrySurface.TUI,
                roles=["soc_analyst"],
            )
        ),
    )
    assert analyst_result.outcome.source is SocDispositionOutcomeSource.ANALYST

    later_payload = _load_zeus_fixture()
    later_payload["event"]["id"] = "ZEUS-EVT-20260707-0002"
    later_payload["event"]["version"] = "2"
    later_payload["event"]["updatedAt"] = "2026-07-07T10:30:00Z"
    later = service.apply_event(
        build_external_disposition_event(later_payload, _zeus_adapter_config()),
        context=context,
    )

    assert later.disposition_outcome_recorded is False
    assert later.disposition_outcome_id is None
    assert later.disposition_outcome_skip_reason == (f"latest primary outcome {analyst_result.outcome.outcome_id} is not external; an explicit analyst supersession is required")
    assert len(evaluation_repository.list_disposition_outcomes()) == 2


def test_external_disposition_service_does_not_apply_low_trust_correction() -> None:
    repository = InMemoryExternalDispositionRepository()
    alert_repository = InMemoryAlertRepository()
    review_repository = InMemoryReviewQueueRepository()
    memory_repository = InMemoryMemoryCandidateRepository()
    run = AnalysisRun(
        run_id="RUN-ZEUS-MOCK-0001",
        alert_id="2026494",
        status=AnalysisRunStatus.NEEDS_REVIEW,
    )
    alert_repository.save_run(run)
    review_repository.save_review_item(
        ReviewQueueItem(
            queue_id="REV-ZEUS-MOCK-0001",
            run_id=run.run_id,
            alert_id=run.alert_id,
            reason="external disposition test",
            source_type=AlertSourceType.NDR,
        )
    )
    proposal_repository = InMemoryDispositionProposalRepository([_proposal_for_target(run.run_id, run.alert_id, "REV-ZEUS-MOCK-0001")])
    evaluation_repository = InMemoryDispositionEvaluationRepository()
    service = SocExternalDispositionService(
        repository=repository,
        mapping_config=SocExternalDispositionMappingConfig(
            status_mappings=[
                SocExternalDispositionStatusMapping(
                    external_system="zeus",
                    external_status="误报关闭",
                    canonical_status=SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE,
                    trust_level="medium",
                )
            ]
        ),
        alert_repository=alert_repository,
        review_queue_repository=review_repository,
        memory_service=SocMemoryService(candidate_repository=memory_repository),
        disposition_proposal_repository=proposal_repository,
        disposition_evaluation_service=SocDispositionEvaluationService(
            repository=evaluation_repository,
            proposal_repository=proposal_repository,
            review_queue_repository=review_repository,
        ),
    )

    result = service.apply_event(
        build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config()),
        context=_external_adapter_context(),
    )

    assert result.record.apply_status is SocExternalDispositionApplyStatus.MAPPED
    assert result.correction_applied is False
    assert result.memory_candidate_created is True
    assert result.record.correction_id is None
    assert result.record.memory_candidate_id is not None
    assert result.disposition_outcome_recorded is False
    assert result.disposition_outcome_skip_reason == "external disposition mapping is not high trust"
    assert alert_repository.get_run(run.run_id).decision is None
    assert review_repository.get_review_item("REV-ZEUS-MOCK-0001").status is ReviewQueueStatus.OPEN
    candidate = memory_repository.get_memory_candidate(result.record.memory_candidate_id)
    assert candidate is not None
    assert candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert candidate.source.correction_id is None
    assert candidate.confidence == 0.5
    assert candidate.facets["mapping_trust_level"] == ["medium"]
    assert evaluation_repository.list_disposition_outcomes() == []


def test_external_disposition_service_keeps_unmapped_or_unmatched_event_review_safe() -> None:
    repository = InMemoryExternalDispositionRepository()
    service = SocExternalDispositionService(
        repository=repository,
        mapping_config=SocExternalDispositionMappingConfig(),
        memory_service=SocMemoryService(candidate_repository=InMemoryMemoryCandidateRepository()),
    )
    payload = {
        "event": {"id": "evt-unknown", "updatedAt": "2026-07-07T10:00:00Z"},
        "case": {
            "id": "CASE-unknown",
            "status": "custom unresolved status",
            "reason": "operator wrote a free-text reason",
        },
    }
    event = build_external_disposition_event(payload, _minimal_adapter_config())

    result = service.apply_event(event, context=_external_adapter_context())

    assert result.audit_written is False
    assert result.record.apply_status is SocExternalDispositionApplyStatus.UNMATCHED
    assert result.record.canonical_status is SocExternalDispositionCanonicalStatus.UNKNOWN
    assert result.record.target_run_id is None
    assert result.record.correction_id is None
    assert result.record.memory_candidate_id is None
    assert result.memory_candidate_created is False
    assert result.record.apply_reason == "external status is unmapped"


def test_external_disposition_service_requires_repository() -> None:
    service = SocExternalDispositionService()

    with pytest.raises(SocServiceNotImplementedError):
        service.apply_event(build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config()))


def test_external_disposition_service_rejects_unauthenticated_or_wrong_role() -> None:
    service = SocExternalDispositionService(
        repository=InMemoryExternalDispositionRepository(),
    )
    event = build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config())

    with pytest.raises(SocServiceAuthorizationError, match="authenticated actor"):
        service.apply_event(event)
    with pytest.raises(SocServiceAuthorizationError, match="external_disposition_adapter, soc_admin"):
        service.apply_event(
            event,
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="analyst-1",
                    actor_type=ActorType.USER,
                    surface=EntrySurface.API,
                    roles=["soc_analyst"],
                    auth_source=ActorAuthSource.SESSION,
                )
            ),
        )


def _proposal_for_target(
    run_id: str,
    alert_id: str,
    queue_id: str,
) -> SocDispositionProposalRecord:
    return SocDispositionProposalRecord(
        proposal_id="DPROP-ZEUS-MOCK-0001",
        proposal_key="a" * 64,
        run_id=run_id,
        alert_id=alert_id,
        queue_id=queue_id,
        source_enrichment_id="AAE-ZEUS-MOCK-0001",
        source_query_hash="b" * 64,
        source_matcher_policy_version="soc.authorization_match.v1",
        source_fact_refs=[
            AuthorizationFactRef(
                fact_id="GCF-ZEUS-MOCK-0001",
                fact_version_id="GCFV-ZEUS-MOCK-0001-1",
                version=1,
                status=GovernedContextFactStatus.ACTIVE,
                content_hash="c" * 64,
            )
        ],
        source_evidence_refs=["fact:GCFV-ZEUS-MOCK-0001-1"],
        detection_truth=SocDetectionTruthSnapshot(
            verdict=Verdict.TRUE_POSITIVE,
            confidence=0.95,
            source="decision",
            decision_policy_version="soc.decision_policy.v2",
        ),
        proposed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
        reason_code=SocDispositionProposalReasonCode.AUTHORIZED_ACTIVITY_EXACT_MATCH,
        rationale=["Exact governed authorization matched the true-positive behavior."],
        idempotency_key="proposal:zeus-mock:0001",
    )


def _load_zeus_fixture() -> dict:
    return json.loads((SAMPLES / "zeus_status_update.json").read_text(encoding="utf-8"))


def _zeus_adapter_config() -> SocExternalDispositionAdapterConfig:
    return SocExternalDispositionAdapterConfig(
        external_system="zeus",
        field_paths={
            "external_case_id": "case.id",
            "source_event_id": "event.id",
            "source_version": "event.version",
            "external_alert_ref": "case.alertRef",
            "soc_alert_id": "soc.alertId",
            "soc_run_id": "soc.runId",
            "soc_queue_id": "soc.queueId",
            "external_status": "case.status",
            "external_reason": "case.reason",
            "external_tags": "case.tags",
            "operator": "operator",
            "updated_at": "event.updatedAt",
        },
    )


def _minimal_adapter_config() -> SocExternalDispositionAdapterConfig:
    return SocExternalDispositionAdapterConfig(
        external_system="custom_soc",
        field_paths={
            "external_case_id": "case.id",
            "source_event_id": "event.id",
            "external_status": "case.status",
            "external_reason": "case.reason",
            "updated_at": "event.updatedAt",
        },
    )


def _mapping_config() -> SocExternalDispositionMappingConfig:
    return SocExternalDispositionMappingConfig(
        status_mappings=[
            SocExternalDispositionStatusMapping(
                external_system="zeus",
                external_status="误报关闭",
                canonical_status=SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE,
                trust_level="high",
            ),
            SocExternalDispositionStatusMapping(
                external_system="zeus",
                external_status="真实攻击关闭",
                canonical_status=SocExternalDispositionCanonicalStatus.CLOSED_TRUE_POSITIVE,
                trust_level="high",
            ),
        ]
    )
