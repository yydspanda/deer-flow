from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertSourceType,
    AnalysisRun,
    AnalysisRunStatus,
    AuditAction,
    DecisionAuditRecord,
    EntrySurface,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocEvent,
    SocExternalDispositionAdapterConfig,
    SocExternalDispositionApplyStatus,
    SocExternalDispositionCanonicalStatus,
    SocExternalDispositionMappingConfig,
    SocExternalDispositionStatusMapping,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    Verdict,
)
from soc_agent.core import SocExternalDispositionService, SocMemoryService, SocServiceNotImplementedError
from soc_agent.external_disposition import (
    InMemoryExternalDispositionRepository,
    build_external_disposition_event,
    build_external_disposition_idempotency_key,
    resolve_external_disposition_status,
)
from soc_agent.memory import InMemoryMemoryCandidateRepository

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "external_disposition"


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
    service = SocExternalDispositionService(
        repository=repository,
        mapping_config=_mapping_config(),
        alert_repository=alert_repository,
        review_queue_repository=review_repository,
        audit_repository=audit_repository,
        event_sink=event_sink,
        memory_service=memory_service,
    )
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="zeus-webhook",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.API,
            roles=["external_disposition_adapter"],
        ),
        request_id="REQ-external-disposition",
    )

    result = service.apply_event(build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config()), context=context)

    assert result.idempotent is False
    assert result.audit_written is True
    assert result.correction_applied is True
    assert result.memory_candidate_created is True
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
    assert alert_repository.get_run(run.run_id).decision.verdict is Verdict.FALSE_POSITIVE
    assert review_repository.get_review_item("REV-ZEUS-MOCK-0001").status is ReviewQueueStatus.CLOSED
    assert {record.action for record in audit_repository.records} == {
        AuditAction.CORRECTION,
        AuditAction.EXTERNAL_DISPOSITION,
    }
    external_audit = [record for record in audit_repository.records if record.action is AuditAction.EXTERNAL_DISPOSITION][0]
    assert external_audit.payload["apply_status"] == "mapped"
    assert external_audit.payload["correction_id"] == result.record.correction_id
    assert external_audit.payload["memory_candidate_id"] == result.record.memory_candidate_id
    assert [event.event_type.value for event in event_sink.events] == [
        "review.corrected",
        "memory.updated",
        "external_disposition.received",
    ]

    duplicate = service.apply_event(build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config()), context=context)
    assert duplicate.idempotent is True
    assert duplicate.correction_applied is False
    assert duplicate.memory_candidate_created is False
    assert duplicate.record.disposition_id == result.record.disposition_id
    assert len(audit_repository.records) == 2
    assert len(memory_repository.list_memory_candidates()) == 1


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
    )

    result = service.apply_event(build_external_disposition_event(_load_zeus_fixture(), _zeus_adapter_config()))

    assert result.record.apply_status is SocExternalDispositionApplyStatus.MAPPED
    assert result.correction_applied is False
    assert result.memory_candidate_created is True
    assert result.record.correction_id is None
    assert result.record.memory_candidate_id is not None
    assert alert_repository.get_run(run.run_id).decision is None
    assert review_repository.get_review_item("REV-ZEUS-MOCK-0001").status is ReviewQueueStatus.OPEN
    candidate = memory_repository.get_memory_candidate(result.record.memory_candidate_id)
    assert candidate is not None
    assert candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert candidate.source.correction_id is None
    assert candidate.confidence == 0.5
    assert candidate.facets["mapping_trust_level"] == ["medium"]


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

    result = service.apply_event(event)

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
