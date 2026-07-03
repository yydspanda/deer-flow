"""SOC approved action API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.routers.soc_dependencies import get_or_create_soc_repository, soc_service_context_from_request
from soc_agent.contracts import SocAgentActionResult, SocAgentApprovalGrant, SocAgentApprovalRequest, SocAgentApprovedActionCommand
from soc_agent.core import SocAgentApprovalService, SocServiceError, SocServiceNotFoundError, SocServiceNotImplementedError

router = APIRouter(prefix="/api/soc/approvals", tags=["soc-approvals"])


class ApprovalGrantRequest(BaseModel):
    approval_request: SocAgentApprovalRequest
    reason: str = Field(min_length=1)
    expires_in_seconds: int = Field(default=900, gt=0, le=86_400)


def get_soc_approval_service(request: Request) -> SocAgentApprovalService:
    injected = getattr(request.app.state, "soc_approval_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    return SocAgentApprovalService(grant_repository=repository)


ApprovalServiceDep = Annotated[SocAgentApprovalService, Depends(get_soc_approval_service)]


@router.post("/grants", response_model=SocAgentApprovalGrant)
def create_approval_grant(
    body: ApprovalGrantRequest,
    request: Request,
    service: ApprovalServiceDep,
) -> SocAgentApprovalGrant:
    try:
        return service.approve(
            body.approval_request,
            context=soc_service_context_from_request(request, include_soc_roles=True),
            reason=body.reason,
            expires_in_seconds=body.expires_in_seconds,
        )
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/actions/dry-run", response_model=SocAgentActionResult)
def dry_run_approved_action(
    command: SocAgentApprovedActionCommand,
    request: Request,
    service: ApprovalServiceDep,
) -> SocAgentActionResult:
    try:
        return service.dry_run_approved_action(command, context=soc_service_context_from_request(request, include_soc_roles=True))
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/actions/execute", response_model=SocAgentActionResult)
def execute_approved_action(
    command: SocAgentApprovedActionCommand,
    request: Request,
    service: ApprovalServiceDep,
) -> SocAgentActionResult:
    try:
        return service.execute_approved_action(command, context=soc_service_context_from_request(request, include_soc_roles=True))
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
