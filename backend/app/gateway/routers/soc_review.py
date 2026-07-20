"""SOC review queue API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.routers.soc_dependencies import get_or_create_soc_repository, soc_service_context_from_request
from app.gateway.routers.soc_transport import create_soc_router
from soc_agent.contracts import (
    AnalysisRun,
    CorrectionCommand,
    InvestigationContext,
    ReviewQueueCloseCommand,
    ReviewQueueItem,
    ReviewQueueStatus,
    SocDispositionOutcomeApplyResult,
    SocDispositionOutcomeCommand,
    SocDispositionOutcomeReviewKind,
    SocDispositionOutcomeSource,
    SocDispositionSampleManifestListResponse,
    SocDispositionSampleReviewInbox,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import (
    DispositionEvaluationIdempotencyConflictError,
    DispositionEvaluationIneligibleError,
    SocDispositionEvaluationService,
    SocReviewService,
    SocServiceAuthorizationError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

router = create_soc_router(prefix="/api/soc/review", tags=["soc-review"])


class ReviewQueueListResponse(BaseModel):
    items: list[ReviewQueueItem]


class ReviewQueueCloseRequest(BaseModel):
    reason: str = Field(min_length=1)


class ReviewCorrectionRequest(BaseModel):
    verdict: Verdict
    reason: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class DispositionOutcomeRecordRequest(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=64)
    observed_disposition: SocOperationalDisposition
    review_kind: SocDispositionOutcomeReviewKind = SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION
    sample_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=300)
    observed_at: datetime | None = None
    supersedes_outcome_id: str | None = Field(default=None, min_length=1, max_length=64)


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
        authorization_enrichment_repository=repository,
        disposition_proposal_repository=repository,
        disposition_evaluation_repository=repository,
        external_disposition_repository=repository,
        memory_candidate_repository=repository,
        memory_record_repository=repository,
    )


ReviewServiceDep = Annotated[SocReviewService, Depends(get_soc_review_service)]


def get_soc_disposition_evaluation_service(request: Request) -> SocDispositionEvaluationService:
    injected = getattr(request.app.state, "soc_disposition_evaluation_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    return SocDispositionEvaluationService(
        repository=repository,
        proposal_repository=repository,
        authorization_enrichment_repository=repository,
        review_queue_repository=repository,
    )


DispositionEvaluationServiceDep = Annotated[
    SocDispositionEvaluationService,
    Depends(get_soc_disposition_evaluation_service),
]


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
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/disposition-outcomes", response_model=SocDispositionOutcomeApplyResult)
def record_disposition_outcome(
    body: DispositionOutcomeRecordRequest,
    request: Request,
    service: DispositionEvaluationServiceDep,
) -> SocDispositionOutcomeApplyResult:
    context = soc_service_context_from_request(request, include_soc_roles=True)
    if context.idempotency_key is None:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    try:
        return service.record_outcome(
            SocDispositionOutcomeCommand(
                proposal_id=body.proposal_id,
                observed_disposition=body.observed_disposition,
                review_kind=body.review_kind,
                source=SocDispositionOutcomeSource.ANALYST,
                sample_id=body.sample_id,
                reason=body.reason,
                evidence_refs=body.evidence_refs,
                observed_at=body.observed_at,
                supersedes_outcome_id=body.supersedes_outcome_id,
                idempotency_key=context.idempotency_key,
            ),
            context=context,
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        DispositionEvaluationIdempotencyConflictError,
        DispositionEvaluationIneligibleError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/disposition-samples", response_model=SocDispositionSampleManifestListResponse)
def list_disposition_sample_campaigns(
    service: DispositionEvaluationServiceDep,
    limit: int = Query(default=50, ge=1, le=500),
) -> SocDispositionSampleManifestListResponse:
    try:
        return service.list_sample_review_campaigns(limit=limit)
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/disposition-samples/{sample_id}/inbox",
    response_model=SocDispositionSampleReviewInbox,
)
def get_disposition_sample_review_inbox(
    sample_id: str,
    request: Request,
    service: DispositionEvaluationServiceDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> SocDispositionSampleReviewInbox:
    context = soc_service_context_from_request(request)
    try:
        return service.get_sample_review_inbox(
            sample_id,
            reviewer_actor_id=context.actor.actor_id,
            offset=offset,
            limit=limit,
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
