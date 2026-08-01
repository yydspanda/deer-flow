"""Read-only SOC operations snapshot API."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.gateway.routers.soc_transport import create_soc_router
from soc_agent.contracts import SocOperationsSnapshot
from soc_agent.core import SocOperationsService
from soc_agent.operations import build_soc_operations_service

router = create_soc_router(prefix="/api/soc/operations", tags=["soc-operations"])


def get_soc_operations_service(request: Request) -> SocOperationsService:
    injected = getattr(request.app.state, "soc_operations_service", None)
    if injected is not None:
        return injected
    service = build_soc_operations_service()
    request.app.state.soc_operations_service = service
    return service


OperationsServiceDep = Annotated[SocOperationsService, Depends(get_soc_operations_service)]


@router.get("/snapshot", response_model=SocOperationsSnapshot)
def get_operations_snapshot(service: OperationsServiceDep) -> SocOperationsSnapshot:
    """Return passive persisted/Kafka observations without active broker IO."""

    return service.get_snapshot(check_broker=False)


__all__ = ["router"]
