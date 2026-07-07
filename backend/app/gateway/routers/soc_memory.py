"""SOC memory candidate API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.gateway.routers.soc_dependencies import get_or_create_soc_repository
from soc_agent.contracts import SocMemoryCandidate, SocMemoryCandidateStatus
from soc_agent.core import SocMemoryService, SocServiceNotFoundError, SocServiceNotImplementedError

router = APIRouter(prefix="/api/soc/memory", tags=["soc-memory"])


class MemoryCandidateListResponse(BaseModel):
    items: list[SocMemoryCandidate]


def get_soc_memory_service(request: Request) -> SocMemoryService:
    injected = getattr(request.app.state, "soc_memory_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    return SocMemoryService(candidate_repository=repository)


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
