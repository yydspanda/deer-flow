from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.routers import soc_review
from soc_agent.contracts import (
    AlertSummary,
    AnalysisRun,
    DecisionAuditRecord,
    EntrySurface,
    InvestigationEvidence,
    ReviewQueueItem,
    ReviewQueueStatus,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocExternalDispositionApplyStatus,
    SocExternalDispositionCanonicalStatus,
    SocExternalDispositionEvent,
    SocExternalDispositionRecord,
    SocMemoryCandidate,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.core import SocAnalysisService, SocMemoryService, SocReviewService

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class InMemorySocRepository:
    def __init__(self) -> None:
        self.runs: dict[str, AnalysisRun] = {}
        self.summaries: dict[str, AlertSummary] = {}
        self.review_items: dict[str, ReviewQueueItem] = {}
        self.audit_records: list[DecisionAuditRecord] = []
        self.evidence: list[InvestigationEvidence] = []
        self.external_dispositions: list[SocExternalDispositionRecord] = []
        self.memory_candidates: list[SocMemoryCandidate] = []

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

    def save_evidence(self, evidence: InvestigationEvidence) -> None:
        self.evidence.append(evidence)

    def list_evidence(
        self,
        *,
        queue_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[InvestigationEvidence]:
        filters = {
            "queue_id": queue_id,
            "run_id": run_id,
            "alert_id": alert_id,
            "thread_id": thread_id,
        }
        active_filters = {key: value for key, value in filters.items() if value}
        evidence = self.evidence
        if active_filters:
            evidence = [item for item in evidence if any(getattr(item, key) == value for key, value in active_filters.items())]
        return sorted(evidence, key=lambda item: item.created_at, reverse=True)[:limit]

    def save_external_disposition(self, record: SocExternalDispositionRecord) -> None:
        self.external_dispositions.append(record)

    def find_external_disposition_by_idempotency_key(self, idempotency_key: str) -> SocExternalDispositionRecord | None:
        for record in self.external_dispositions:
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def list_external_dispositions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        external_system: str | None = None,
        external_case_id: str | None = None,
        limit: int = 50,
    ) -> list[SocExternalDispositionRecord]:
        target_filters = {
            "target_run_id": run_id,
            "target_alert_id": alert_id,
            "target_queue_id": queue_id,
        }
        active_target_filters = {key: value for key, value in target_filters.items() if value}
        records = self.external_dispositions
        if active_target_filters:
            records = [item for item in records if any(getattr(item, key) == value for key, value in active_target_filters.items())]
        if external_system:
            records = [item for item in records if item.event.external_system == external_system]
        if external_case_id:
            records = [item for item in records if item.event.external_case_id == external_case_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)[:limit]

    def save_memory_candidate(self, candidate: SocMemoryCandidate) -> None:
        self.memory_candidates.append(candidate)

    def get_memory_candidate(self, candidate_id: str) -> SocMemoryCandidate | None:
        for candidate in self.memory_candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        return None

    def find_memory_candidate_by_idempotency_key(self, idempotency_key: str) -> SocMemoryCandidate | None:
        for candidate in self.memory_candidates:
            if candidate.idempotency_key == idempotency_key:
                return candidate
        return None

    def list_memory_candidates(
        self,
        *,
        status: SocMemoryCandidateStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryCandidate]:
        candidates = self.memory_candidates
        if status is not None:
            candidates = [item for item in candidates if item.status == status]
        if tenant_scope is not None:
            candidates = [item for item in candidates if item.tenant_scope == tenant_scope]
        if tenant_id is not None:
            candidates = [item for item in candidates if item.tenant_id == tenant_id]
        source_filters = {
            "run_id": run_id,
            "alert_id": alert_id,
            "queue_id": queue_id,
        }
        active_source_filters = {key: value for key, value in source_filters.items() if value}
        if active_source_filters:
            candidates = [item for item in candidates if any(getattr(item.source, key) == value for key, value in active_source_filters.items())]
        return sorted(candidates, key=lambda item: item.created_at, reverse=True)[:limit]


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


class FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None, user_id: str | None = None) -> None:
        self.headers = headers or {}
        if user_id is not None:
            self.state = SimpleNamespace(user=SimpleNamespace(id=user_id))


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
        evidence_repository=repository,
        external_disposition_repository=repository,
        memory_candidate_repository=repository,
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
    service, repository, item = review_api
    repository.save_evidence(
        InvestigationEvidence(
            route="asset.locate",
            action="asset.locate",
            status="success",
            message="Asset location completed.",
            result_payload={"mcp_result": {"company_code": "PA011", "mocked": True}},
            queue_id=item.queue_id,
            run_id=item.run_id,
            alert_id=item.alert_id,
        )
    )
    repository.save_external_disposition(
        SocExternalDispositionRecord(
            event=SocExternalDispositionEvent(
                external_system="zeus",
                external_case_id="ZEUS-CASE-ROUTER-1",
                soc_alert_id=item.alert_id,
                soc_run_id=item.run_id,
                soc_queue_id=item.queue_id,
                external_status="误报关闭",
                external_reason="老工单确认授权测试。",
                updated_at=item.updated_at,
                raw_payload_hash="hash-router-zeus",
            ),
            canonical_status=SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE,
            apply_status=SocExternalDispositionApplyStatus.MAPPED,
            idempotency_key="external_disposition:zeus:router-1",
            target_run_id=item.run_id,
            target_alert_id=item.alert_id,
            target_queue_id=item.queue_id,
            matched_by="soc_queue_id",
            apply_reason="external status mapped to a unique local target",
        )
    )
    memory_candidate = SocMemoryService(candidate_repository=repository).propose_candidate(
        _memory_candidate_command(
            run_id=item.run_id,
            alert_id=item.alert_id,
            queue_id=item.queue_id,
        )
    )

    context = soc_review.get_review_context(item.queue_id, service=service)

    assert context.queue_item.queue_id == item.queue_id
    assert context.run.run_id == item.run_id
    assert context.summary is not None
    assert context.summary.run_id == item.run_id
    assert len(context.audit_records) == 1
    assert len(context.action_evidence) == 1
    assert context.action_evidence[0].action == "asset.locate"
    assert len(context.external_dispositions) == 1
    assert context.external_dispositions[0].event.external_system == "zeus"
    assert context.memory_candidates == [memory_candidate]


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


def test_soc_review_api_uses_authenticated_web_actor_over_header(review_api) -> None:
    service, _, item = review_api

    closed = soc_review.close_review_item(
        item.queue_id,
        soc_review.ReviewQueueCloseRequest(reason="复核完成"),
        FakeRequest(
            {
                "x-soc-actor-id": "spoofed-user",
                "x-soc-surface": "web",
                "x-trace-id": "trace-web-1",
                "idempotency-key": "idem-web-1",
            },
            user_id="auth-user-1",
        ),
        service=service,
    )

    assert closed.closed_by is not None
    assert closed.closed_by.actor_id == "auth-user-1"
    assert closed.closed_by.surface == EntrySurface.WEB


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


def _memory_candidate_command(
    *,
    run_id: str,
    alert_id: str,
    queue_id: str,
) -> SocMemoryCandidateCreateCommand:
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="Review context memory candidate",
        content="External feedback should remain pending until an analyst reviews it.",
        tenant_scope="pingan",
        tenant_id="pingan",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
            source_id="review-router-memory-test",
            run_id=run_id,
            alert_id=alert_id,
            queue_id=queue_id,
        ),
        evidence_refs=["manual:review-router-test"],
        validity=SocMemoryCandidateValidity(notes="Review router test candidate."),
        idempotency_key=f"memory:review-router:{queue_id}",
        confidence=0.6,
        facets={"tenant": ["pingan"]},
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc_analyst",
        labels=["candidate-only"],
    )
