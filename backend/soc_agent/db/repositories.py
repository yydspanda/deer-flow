"""SQLAlchemy repository implementations for SOC Agent contracts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from soc_agent.contracts import AlertSummary, AnalysisRun, DecisionAuditRecord, ReviewQueueItem, ReviewQueueStatus
from soc_agent.db.models import SocAlertSummaryRow, SocAnalysisRunRow, SocDecisionAuditLogRow, SocReviewQueueRow


class SqlAlchemyAlertRepository:
    """SQLAlchemy-backed implementation of ``AlertRepository``.

    The repository accepts a sync ``Session`` factory so Phase 1 headless CLI and
    service tests can use the same persistence boundary. Async Gateway adapters
    should call it off the event loop or get a dedicated async adapter later.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def save_run(self, run: AnalysisRun) -> None:
        payload = run.model_dump(mode="json")
        now = datetime.now(UTC)

        with self._session_factory() as session:
            row = session.get(SocAnalysisRunRow, run.run_id)
            if row is None:
                row = SocAnalysisRunRow(
                    run_id=run.run_id,
                    created_at=now,
                    **_row_values(run, payload, updated_at=now),
                )
                session.add(row)
            else:
                for key, value in _row_values(run, payload, updated_at=now).items():
                    setattr(row, key, value)
            session.commit()

    def get_run(self, run_id: str) -> AnalysisRun | None:
        with self._session_factory() as session:
            row = session.get(SocAnalysisRunRow, run_id)
            if row is None:
                return None
            return AnalysisRun.model_validate(row.run_payload)

    def save_audit_record(self, record: DecisionAuditRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocDecisionAuditLogRow, record.audit_id)
            if row is None:
                session.add(SocDecisionAuditLogRow(audit_id=record.audit_id, **_audit_row_values(record, payload)))
            else:
                for key, value in _audit_row_values(record, payload).items():
                    setattr(row, key, value)
            session.commit()

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]:
        with self._session_factory() as session:
            result = session.execute(select(SocDecisionAuditLogRow).where(SocDecisionAuditLogRow.run_id == run_id).order_by(SocDecisionAuditLogRow.occurred_at.asc()))
            return [DecisionAuditRecord.model_validate(row.record_payload) for row in result.scalars()]

    def save_alert_summary(self, summary: AlertSummary) -> None:
        payload = summary.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocAlertSummaryRow, summary.run_id)
            if row is None:
                session.add(SocAlertSummaryRow(run_id=summary.run_id, **_summary_row_values(summary, payload)))
            else:
                for key, value in _summary_row_values(summary, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_alert_summary(self, run_id: str) -> AlertSummary | None:
        with self._session_factory() as session:
            row = session.get(SocAlertSummaryRow, run_id)
            if row is None:
                return None
            return AlertSummary.model_validate(row.summary_payload)

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]:
        with self._session_factory() as session:
            result = session.execute(select(SocAlertSummaryRow).order_by(SocAlertSummaryRow.updated_at.desc()).limit(limit))
            return [AlertSummary.model_validate(row.summary_payload) for row in result.scalars()]

    def save_review_item(self, item: ReviewQueueItem) -> None:
        payload = item.model_dump(mode="json")
        with self._session_factory() as session:
            row = session.get(SocReviewQueueRow, item.queue_id)
            if row is None:
                session.add(SocReviewQueueRow(queue_id=item.queue_id, **_review_queue_row_values(item, payload)))
            else:
                for key, value in _review_queue_row_values(item, payload).items():
                    setattr(row, key, value)
            session.commit()

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        with self._session_factory() as session:
            row = session.get(SocReviewQueueRow, queue_id)
            if row is None:
                return None
            return ReviewQueueItem.model_validate(row.item_payload)

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None:
        with self._session_factory() as session:
            result = session.execute(select(SocReviewQueueRow).where(SocReviewQueueRow.run_id == run_id, SocReviewQueueRow.status == ReviewQueueStatus.OPEN.value).order_by(SocReviewQueueRow.updated_at.desc()).limit(1))
            row = result.scalar_one_or_none()
            return ReviewQueueItem.model_validate(row.item_payload) if row is not None else None

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = None,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        with self._session_factory() as session:
            query = select(SocReviewQueueRow)
            if status is not None:
                query = query.where(SocReviewQueueRow.status == status.value)
            result = session.execute(query.order_by(SocReviewQueueRow.updated_at.desc()).limit(limit))
            return [ReviewQueueItem.model_validate(row.item_payload) for row in result.scalars()]


def _row_values(run: AnalysisRun, payload: dict, *, updated_at: datetime) -> dict:
    return {
        "alert_id": run.alert_id,
        "status": run.status.value,
        "input_hash": run.input_hash,
        "replay_of_run_id": run.replay_of_run_id,
        "pipeline_version": run.pipeline_version,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "input_payload": run.input_payload,
        "run_payload": payload,
        "updated_at": updated_at,
    }


def _audit_row_values(record: DecisionAuditRecord, payload: dict) -> dict:
    return {
        "action": record.action.value,
        "run_id": record.run_id,
        "alert_id": record.alert_id,
        "actor_id": record.actor.actor_id,
        "actor_type": record.actor.actor_type.value,
        "actor_surface": record.actor.surface.value,
        "occurred_at": record.occurred_at,
        "input_hash": record.input_hash,
        "previous_verdict": record.previous_verdict.value if record.previous_verdict is not None else None,
        "final_verdict": record.final_verdict.value if record.final_verdict is not None else None,
        "confidence": record.confidence,
        "replay_of_run_id": record.replay_of_run_id,
        "correction_id": record.correction_id,
        "record_payload": payload,
    }


def _summary_row_values(summary: AlertSummary, payload: dict) -> dict:
    return {
        "alert_id": summary.alert_id,
        "tenant_id": summary.tenant_id,
        "source_type": summary.source_type.value,
        "source_system": summary.source_system,
        "detection_key": summary.detection_key,
        "rule_code": summary.rule_code,
        "rule_name": summary.rule_name,
        "severity": summary.severity,
        "category": summary.category,
        "entity_keys": summary.entity_keys,
        "status": summary.status.value,
        "verdict": summary.verdict.value if summary.verdict is not None else None,
        "confidence": summary.confidence,
        "needs_review": summary.needs_review,
        "summary": summary.summary,
        "recommended_action": summary.recommended_action,
        "input_hash": summary.input_hash,
        "replay_of_run_id": summary.replay_of_run_id,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
        "summary_payload": payload,
    }


def _review_queue_row_values(item: ReviewQueueItem, payload: dict) -> dict:
    return {
        "run_id": item.run_id,
        "alert_id": item.alert_id,
        "tenant_id": item.tenant_id,
        "status": item.status.value,
        "priority": item.priority.value,
        "reason": item.reason,
        "source_type": item.source_type.value,
        "source_system": item.source_system,
        "rule_code": item.rule_code,
        "rule_name": item.rule_name,
        "severity": item.severity,
        "category": item.category,
        "verdict": item.verdict.value if item.verdict is not None else None,
        "confidence": item.confidence,
        "entity_keys": item.entity_keys,
        "summary": item.summary,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "closed_at": item.closed_at,
        "closed_by_payload": item.closed_by.model_dump(mode="json") if item.closed_by is not None else None,
        "close_reason": item.close_reason,
        "item_payload": payload,
    }
