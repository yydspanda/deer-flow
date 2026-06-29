from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import AuditAction, CorrectionCommand, ReviewQueueStatus, Verdict
from soc_agent.core import SocAnalysisService
from soc_agent.core.service import SocReviewService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlAlchemyAlertRepository(session_factory)


def test_sqlalchemy_alert_repository_saves_and_gets_run() -> None:
    repository = _repository()
    run = SocAnalysisService(repository=repository, summary_repository=repository).analyze(_sample("approved_scanner.json"))

    saved = repository.get_run(run.run_id)
    summary = repository.get_alert_summary(run.run_id)

    assert saved == run
    assert saved is not None
    assert saved.input_payload == run.input_payload
    assert saved.input_hash == run.input_hash
    assert summary is not None
    assert summary.alert_id == run.alert_id
    assert summary.verdict == Verdict.FALSE_POSITIVE
    assert summary.rule_code == "EDR-SCAN-001"
    assert "host:scanner-01" in summary.entity_keys


def test_sqlalchemy_alert_repository_updates_existing_run() -> None:
    repository = _repository()
    run = SocAnalysisService(repository=repository, summary_repository=repository).analyze(_sample("approved_scanner.json"))
    run.model_name = "updated-model"

    repository.save_run(run)

    saved = repository.get_run(run.run_id)
    assert saved is not None
    assert saved.model_name == "updated-model"


def test_sqlalchemy_alert_repository_supports_service_replay() -> None:
    repository = _repository()
    service = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
    )
    original = service.analyze(_sample("approved_scanner.json"))

    replayed = service.replay(original.run_id)

    assert replayed.run_id != original.run_id
    assert replayed.replay_of_run_id == original.run_id
    assert repository.get_run(original.run_id) == original
    assert repository.get_run(replayed.run_id) == replayed

    original_records = repository.list_audit_records(original.run_id)
    replay_records = repository.list_audit_records(replayed.run_id)
    assert original_records[0].action == AuditAction.ANALYSIS
    assert replay_records[0].action == AuditAction.REPLAY
    assert replay_records[0].replay_of_run_id == original.run_id

    replay_summary = repository.get_alert_summary(replayed.run_id)
    assert replay_summary is not None
    assert replay_summary.replay_of_run_id == original.run_id
    assert replayed.run_id in {summary.run_id for summary in repository.list_alert_summaries(limit=2)}


def test_sqlalchemy_alert_repository_persists_corrections() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
    ).analyze(_sample("approved_scanner.json"))

    corrected = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.TRUE_POSITIVE,
            reason="Confirmed malicious behavior after host review.",
        )
    )

    saved = repository.get_run(run.run_id)
    assert saved == corrected
    assert saved is not None
    assert saved.decision is not None
    assert saved.decision.verdict == Verdict.TRUE_POSITIVE
    assert saved.corrections[0].previous_verdict == Verdict.FALSE_POSITIVE

    records = repository.list_audit_records(run.run_id)
    assert [record.action for record in records] == [AuditAction.ANALYSIS, AuditAction.CORRECTION]
    assert records[1].previous_verdict == Verdict.FALSE_POSITIVE
    assert records[1].final_verdict == Verdict.TRUE_POSITIVE

    summary = repository.get_alert_summary(run.run_id)
    assert summary is not None
    assert summary.verdict == Verdict.TRUE_POSITIVE
    assert summary.confidence == 1.0
    assert summary.needs_review is False


def test_sqlalchemy_alert_repository_persists_review_queue_items() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_apt.json"))

    items = repository.list_review_items(status=ReviewQueueStatus.OPEN)

    assert len(items) == 1
    item = items[0]
    assert item.run_id == run.run_id
    assert item.alert_id == "2026494"
    assert item.status == ReviewQueueStatus.OPEN
    assert item.reason == "summary.needs_review"
    assert item.priority.value == "high"
    assert item.rule_code == "RPAADM_002635"
    assert "ip:30.180.248.178" in item.entity_keys
    assert repository.get_open_review_item_by_run(run.run_id) == item
    assert repository.get_review_item(item.queue_id) == item


def test_sqlalchemy_alert_repository_closes_review_queue_after_correction() -> None:
    repository = _repository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_edr.json"))
    open_item = repository.get_open_review_item_by_run(run.run_id)
    assert open_item is not None

    SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.FALSE_POSITIVE,
            reason="Analyst confirmed authorized lateral movement test.",
        )
    )

    assert repository.get_open_review_item_by_run(run.run_id) is None
    closed = repository.get_review_item(open_item.queue_id)
    assert closed is not None
    assert closed.status == ReviewQueueStatus.CLOSED
    assert closed.close_reason == "manual correction: Analyst confirmed authorized lateral movement test."
    assert closed.closed_by is not None
    assert repository.list_review_items(status=ReviewQueueStatus.OPEN) == []
    assert repository.list_review_items(status=ReviewQueueStatus.CLOSED) == [closed]
