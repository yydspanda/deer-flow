"""SOC alert-result APIs independent of optional human-review tasks."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel

from app.gateway.routers.soc_transport import create_soc_router
from app.gateway.soc_dependencies import get_soc_review_service
from soc_agent.contracts import (
    SocAlertAttentionLevel,
    SocAlertInvestigationContext,
    SocAlertResult,
)
from soc_agent.core import (
    SocReviewService,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

router = create_soc_router(prefix="/api/soc/alerts", tags=["soc-alerts"])


class SocAlertResultListResponse(BaseModel):
    items: list[SocAlertResult]


ReviewServiceDep = Annotated[SocReviewService, Depends(get_soc_review_service)]


@router.get("", response_model=SocAlertResultListResponse)
def list_alert_results(
    service: ReviewServiceDep,
    attention_level: SocAlertAttentionLevel | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> SocAlertResultListResponse:
    try:
        return SocAlertResultListResponse(
            items=service.list_alert_results(
                attention_level=attention_level,
                limit=limit,
            )
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{run_id}/context", response_model=SocAlertInvestigationContext)
def get_alert_investigation_context(
    run_id: str,
    service: ReviewServiceDep,
) -> SocAlertInvestigationContext:
    try:
        return service.get_alert_investigation_context(run_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]
