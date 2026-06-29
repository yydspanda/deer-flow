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
    ServiceRequestContext,
    SocEvent,
    SocEventType,
    Verdict,
)
from soc_agent.core import (
    SocAgentChatService,
    SocAnalysisService,
    SocDaemonService,
    SocMemoryService,
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
    with pytest.raises(SocServiceNotImplementedError):
        SocAgentChatService().send_message()
