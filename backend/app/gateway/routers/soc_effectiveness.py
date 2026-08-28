"""Read-only SOC quality, automation and rule-effectiveness API."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, Request

from app.gateway.routers.soc_transport import create_soc_router
from soc_agent.contracts import (
    SocEffectivenessSnapshot,
    SocRuleEffectivenessDetail,
)
from soc_agent.core import (
    SocEffectivenessService,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)
from soc_agent.operations import build_soc_effectiveness_service

router = create_soc_router(prefix="/api/soc/effectiveness", tags=["soc-effectiveness"])


def get_soc_effectiveness_service(request: Request) -> SocEffectivenessService:
    injected = getattr(request.app.state, "soc_effectiveness_service", None)
    if injected is not None:
        return injected
    service = build_soc_effectiveness_service()
    request.app.state.soc_effectiveness_service = service
    return service


EffectivenessServiceDep = Annotated[
    SocEffectivenessService,
    Depends(get_soc_effectiveness_service),
]


@router.get("/snapshot", response_model=SocEffectivenessSnapshot)
def get_effectiveness_snapshot(
    service: EffectivenessServiceDep,
    window_days: int = Query(default=30, ge=1, le=366),
    tenant_id: str | None = Query(default=None, min_length=1, max_length=128),
    source_type: str | None = Query(default=None, min_length=1, max_length=32),
) -> SocEffectivenessSnapshot:
    """Return denominator-visible quality metrics and advisory rule guidance."""

    return service.get_snapshot(
        window_days=window_days,
        tenant_id=tenant_id,
        source_type=source_type,
    )


@router.get(
    "/rules/{group_key}",
    response_model=SocRuleEffectivenessDetail,
)
def get_rule_effectiveness_detail(
    group_key: Annotated[str, Path(pattern=r"^[0-9a-f]{16}$")],
    service: EffectivenessServiceDep,
    window_days: int = Query(default=30, ge=1, le=366),
    tenant_id: str | None = Query(default=None, min_length=1, max_length=128),
    source_type: str | None = Query(default=None, min_length=1, max_length=32),
) -> SocRuleEffectivenessDetail:
    """Return one Rule Code with its same-behavior and Memory evidence."""

    try:
        return service.get_rule_detail(
            group_key,
            window_days=window_days,
            tenant_id=tenant_id,
            source_type=source_type,
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]
