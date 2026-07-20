"""SOC memory candidate API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.routers.soc_dependencies import get_or_create_soc_repository, soc_service_context_from_request
from app.gateway.routers.soc_transport import create_soc_router
from soc_agent.contracts import (
    SocMemoryCandidate,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateReviewResult,
    SocMemoryCandidateStatus,
    SocMemoryQuery,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMemoryRetrievalResult,
)
from soc_agent.core import (
    SocMemoryService,
    SocServiceAuthorizationError,
    SocServiceError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

router = create_soc_router(prefix="/api/soc/memory", tags=["soc-memory"])


class MemoryCandidateListResponse(BaseModel):
    items: list[SocMemoryCandidate]


class MemoryRecordListResponse(BaseModel):
    items: list[SocMemoryRecord]


class MemoryCandidateReviewRequest(BaseModel):
    decision: SocMemoryCandidateReviewDecision
    reason: str = Field(min_length=1)
    record_summary: str | None = None
    record_content: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


def get_soc_memory_service(request: Request) -> SocMemoryService:
    injected = getattr(request.app.state, "soc_memory_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    return SocMemoryService(candidate_repository=repository, record_repository=repository)


MemoryServiceDep = Annotated[SocMemoryService, Depends(get_soc_memory_service)]


@router.get("/candidates", response_model=MemoryCandidateListResponse)
def list_memory_candidates(
    service: MemoryServiceDep,
    status: SocMemoryCandidateStatus | None = Query(default=SocMemoryCandidateStatus.PENDING_REVIEW),
    tenant_scope: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    alert_id: str | None = Query(default=None),
    queue_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryCandidateListResponse:
    try:
        return MemoryCandidateListResponse(
            items=service.list_candidates(
                status=status,
                tenant_scope=tenant_scope,
                tenant_id=tenant_id,
                run_id=run_id,
                alert_id=alert_id,
                queue_id=queue_id,
                limit=limit,
            )
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/candidates/{candidate_id}", response_model=SocMemoryCandidate)
def get_memory_candidate(candidate_id: str, service: MemoryServiceDep) -> SocMemoryCandidate:
    try:
        return service.get_candidate(candidate_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/review", response_model=SocMemoryCandidateReviewResult)
def review_memory_candidate(
    candidate_id: str,
    payload: MemoryCandidateReviewRequest,
    request: Request,
    service: MemoryServiceDep,
) -> SocMemoryCandidateReviewResult:
    try:
        return service.review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=candidate_id,
                decision=payload.decision,
                reason=payload.reason,
                record_summary=payload.record_summary,
                record_content=payload.record_content,
                metadata=payload.metadata,
            ),
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/records", response_model=MemoryRecordListResponse)
def list_memory_records(
    service: MemoryServiceDep,
    status: SocMemoryRecordStatus | None = Query(default=SocMemoryRecordStatus.CONFIRMED),
    tenant_scope: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    source_candidate_id: str | None = Query(default=None),
    retrieval_enabled: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryRecordListResponse:
    try:
        return MemoryRecordListResponse(
            items=service.list_records(
                status=status,
                tenant_scope=tenant_scope,
                tenant_id=tenant_id,
                source_candidate_id=source_candidate_id,
                retrieval_enabled=retrieval_enabled,
                limit=limit,
            )
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/records/{memory_id}", response_model=SocMemoryRecord)
def get_memory_record(memory_id: str, service: MemoryServiceDep) -> SocMemoryRecord:
    try:
        return service.get_record(memory_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/search", response_model=SocMemoryRetrievalResult)
def search_memory_records(payload: SocMemoryQuery, service: MemoryServiceDep) -> SocMemoryRetrievalResult:
    try:
        return service.find_relevant_records(payload)
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
