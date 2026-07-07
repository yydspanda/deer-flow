"""SOC review queue API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.routers.soc_dependencies import get_or_create_soc_repository, soc_service_context_from_request
from soc_agent.contracts import (
    AnalysisRun,
    CorrectionCommand,
    InvestigationContext,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueueStatus,
    Verdict,
)
from soc_agent.core import SocReviewService, SocServiceNotFoundError, SocServiceNotImplementedError

router = APIRouter(prefix="/api/soc/review", tags=["soc-review"])


class ReviewQueueListResponse(BaseModel):
    items: list[ReviewQueueItem]


class ReviewQueueCloseRequest(BaseModel):
    reason: str = Field(min_length=1)


class ReviewCorrectionRequest(BaseModel):
    verdict: Verdict
    reason: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def get_soc_review_service(request: Request) -> SocReviewService:
    injected = getattr(request.app.state, "soc_review_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    return SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        external_disposition_repository=repository,
        memory_candidate_repository=repository,
    )


ReviewServiceDep = Annotated[SocReviewService, Depends(get_soc_review_service)]


@router.get("/items", response_model=ReviewQueueListResponse)
def list_review_items(
    service: ReviewServiceDep,
    status: ReviewQueueStatus | None = Query(default=ReviewQueueStatus.OPEN),
    limit: int = Query(default=50, ge=1, le=200),
) -> ReviewQueueListResponse:
    try:
        return ReviewQueueListResponse(items=service.list_queue(status=status, limit=limit))
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/items/{queue_id}/context", response_model=InvestigationContext)
def get_review_context(queue_id: str, service: ReviewServiceDep) -> InvestigationContext:
    try:
        return service.get_investigation_context(queue_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/items/{queue_id}/close", response_model=ReviewQueueItem)
def close_review_item(
    queue_id: str,
    body: ReviewQueueCloseRequest,
    request: Request,
    service: ReviewServiceDep,
) -> ReviewQueueItem:
    try:
        return service.close_queue_item(
            ReviewQueueCloseCommand(queue_id=queue_id, reason=body.reason),
            context=soc_service_context_from_request(request),
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/runs/{run_id}/correct", response_model=AnalysisRun)
def correct_review_run(
    run_id: str,
    body: ReviewCorrectionRequest,
    request: Request,
    service: ReviewServiceDep,
) -> AnalysisRun:
    try:
        return service.correct(
            CorrectionCommand(
                run_id=run_id,
                corrected_verdict=body.verdict,
                corrected_confidence=body.confidence,
                reason=body.reason,
            ),
            context=soc_service_context_from_request(request),
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
