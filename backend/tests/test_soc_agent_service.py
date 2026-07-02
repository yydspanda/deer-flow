from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.contracts import (
    ActorContext,
    AlertSummary,
    AnalysisRun,
    AuditAction,
    CorrectionCommand,
    DecisionAuditRecord,
    EntrySurface,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocAgentChatRequest,
    SocEvent,
    SocEventType,
    Verdict,
)
from soc_agent.core import (
    SocAgentChatService,
    SocAnalysisService,
    SocDaemonService,
    SocMemoryService,
    SocNormalizationService,
    SocReviewService,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

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


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.records: list[DecisionAuditRecord] = []

    def save_audit_record(self, record: DecisionAuditRecord) -> None:
        self.records.append(record)

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]:
        return [record for record in self.records if record.run_id == run_id]


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


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


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


def test_review_service_context_requires_existing_queue_item() -> None:
    with pytest.raises(SocServiceNotFoundError):
        SocReviewService(
            repository=InMemoryAlertRepository(),
            review_queue_repository=InMemoryReviewQueueRepository(),
        ).get_investigation_context("REV-UNKNOWN")


def test_agent_chat_service_streams_deerflow_like_events() -> None:
    service = SocAgentChatService()

    events = list(service.stream(SocAgentChatRequest(message="triage this alert", thread_id="soc-thread-1")))

    assert [event.type for event in events] == ["values", "messages-tuple", "end"]
    assert events[0].data["title"] == "triage this alert"
    assert events[0].data["thread_id"] == "soc-thread-1"
    assert events[0].data["artifacts"] == []
    assert events[1].data["type"] == "ai"
    assert "deterministic review context loading" in events[1].data["content"]
    assert events[-1].data["thread_id"] == "soc-thread-1"


def test_agent_chat_service_materializes_response_from_same_stream() -> None:
    response = SocAgentChatService().send_message("hello soc")

    assert response.thread_id.startswith("SOC-TH-")
    assert [event.type for event in response.events] == ["values", "messages-tuple", "end"]
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

    assert [event.type for event in events] == ["values", "custom", "messages-tuple", "end"]
    assert events[0].data["title"] == f"SOC Review {item.queue_id}"
    assert events[1].data == {
        "kind": "soc.review_context",
        "queue_id": item.queue_id,
        "run_id": run.run_id,
        "alert_id": run.alert_id,
        "actor_surface": "tui",
    }
    assert f"Loaded review context {item.queue_id}" in events[2].data["content"]


def test_agent_chat_service_requires_review_service_for_queue_context() -> None:
    with pytest.raises(SocServiceNotImplementedError):
        list(SocAgentChatService().stream(SocAgentChatRequest(message="open", queue_id="REV-1")))


def test_review_service_correct_requires_repository() -> None:
    with pytest.raises(SocServiceNotImplementedError):
        SocReviewService().correct(
            CorrectionCommand(
                run_id="RUN-UNKNOWN",
                corrected_verdict=Verdict.FALSE_POSITIVE,
                reason="manual correction",
            )
        )


def test_planned_services_fail_fast_until_implemented() -> None:
    with pytest.raises(SocServiceNotImplementedError):
        SocMemoryService().list_facts()
    with pytest.raises(SocServiceNotImplementedError):
        SocDaemonService().start()
