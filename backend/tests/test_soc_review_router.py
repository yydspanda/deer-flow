from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.gateway.routers import soc_review
from soc_agent.contracts import (
    AlertSummary,
    AnalysisRun,
    DecisionAuditRecord,
    EntrySurface,
    ReviewQueueItem,
    ReviewQueueStatus,
    SimilarAlertMatch,
    SimilarAlertQuery,
    Verdict,
)
from soc_agent.core import SocAnalysisService, SocReviewService

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class InMemorySocRepository:
    def __init__(self) -> None:
        self.runs: dict[str, AnalysisRun] = {}
        self.summaries: dict[str, AlertSummary] = {}
        self.review_items: dict[str, ReviewQueueItem] = {}
        self.audit_records: list[DecisionAuditRecord] = []

    def save_run(self, run: AnalysisRun) -> None:
        self.runs[run.run_id] = run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]:
        return list(self.runs.values())[-limit:]

    def save_alert_summary(self, summary: AlertSummary) -> None:
        self.summaries[summary.run_id] = summary

    def get_alert_summary(self, run_id: str) -> AlertSummary | None:
        return self.summaries.get(run_id)

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]:
        return list(self.summaries.values())[:limit]

    def find_similar_alert_summaries(self, query: SimilarAlertQuery) -> list[SimilarAlertMatch]:
        return []

    def save_review_item(self, item: ReviewQueueItem) -> None:
        self.review_items[item.queue_id] = item

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        return self.review_items.get(queue_id)

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None:
        for item in self.review_items.values():
            if item.run_id == run_id and item.status == ReviewQueueStatus.OPEN:
                return item
        return None

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = None,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        items = list(self.review_items.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        return items[:limit]

    def save_audit_record(self, record: DecisionAuditRecord) -> None:
        self.audit_records.append(record)

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]:
        return [record for record in self.audit_records if record.run_id == run_id]


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


class FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


@pytest.fixture
def review_api() -> tuple[SocReviewService, InMemorySocRepository, ReviewQueueItem]:
    repository = InMemorySocRepository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_apt.json"))
    item = repository.get_open_review_item_by_run(run.run_id)
    assert item is not None

    service = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    )
    return service, repository, item


def test_soc_review_api_lists_open_items(review_api) -> None:
    service, _, item = review_api

    response = soc_review.list_review_items(
        service=service,
        status=ReviewQueueStatus.OPEN,
        limit=50,
    )

    assert [value.queue_id for value in response.items] == [item.queue_id]
    assert response.items[0].status == ReviewQueueStatus.OPEN


def test_soc_review_api_returns_investigation_context(review_api) -> None:
    service, _, item = review_api

    context = soc_review.get_review_context(item.queue_id, service=service)

    assert context.queue_item.queue_id == item.queue_id
    assert context.run.run_id == item.run_id
    assert context.summary is not None
    assert context.summary.run_id == item.run_id
    assert len(context.audit_records) == 1


def test_soc_review_api_closes_item_with_api_actor(review_api) -> None:
    service, repository, item = review_api

    closed = soc_review.close_review_item(
        item.queue_id,
        soc_review.ReviewQueueCloseRequest(reason="复核完成"),
        FakeRequest({"x-soc-actor-id": "analyst-api"}),
        service=service,
    )

    assert closed.status == ReviewQueueStatus.CLOSED
    assert closed.close_reason == "复核完成"
    assert closed.closed_by is not None
    assert closed.closed_by.actor_id == "analyst-api"
    assert closed.closed_by.surface == EntrySurface.API
    assert repository.get_review_item(item.queue_id).status == ReviewQueueStatus.CLOSED


def test_soc_review_api_corrects_run_and_closes_open_item(review_api) -> None:
    service, repository, item = review_api

    run = soc_review.correct_review_run(
        item.run_id,
        soc_review.ReviewCorrectionRequest(
            verdict=Verdict.FALSE_POSITIVE,
            confidence=0.93,
            reason="分析师确认是误报",
        ),
        FakeRequest({"x-soc-actor-id": "analyst-api"}),
        service=service,
    )

    assert run.decision is not None
    assert run.decision.verdict == Verdict.FALSE_POSITIVE
    assert run.decision.confidence == 0.93
    assert run.corrections[0].actor.surface == EntrySurface.API
    assert repository.get_review_item(item.queue_id).status == ReviewQueueStatus.CLOSED


def test_soc_review_api_missing_item_returns_404(review_api) -> None:
    service, _, _ = review_api

    with pytest.raises(HTTPException) as exc_info:
        soc_review.get_review_context("REV-MISSING", service=service)

    assert exc_info.value.status_code == 404


def test_soc_review_router_exposes_mvp_paths() -> None:
    paths = {route.path for route in soc_review.router.routes}

    assert "/api/soc/review/items" in paths
    assert "/api/soc/review/items/{queue_id}/context" in paths
    assert "/api/soc/review/items/{queue_id}/close" in paths
    assert "/api/soc/review/runs/{run_id}/correct" in paths
