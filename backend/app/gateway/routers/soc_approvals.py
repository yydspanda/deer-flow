"""SOC approved action API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.routers.soc_dependencies import get_or_create_soc_repository, soc_service_context_from_request
from soc_agent.contracts import (
    SocAgentActionResult,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentApprovalRequestStatus,
    SocAgentApprovedActionCommand,
)
from soc_agent.core import (
    SocAgentApprovalService,
    SocServiceAuthorizationError,
    SocServiceConflictError,
    SocServiceError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

router = APIRouter(prefix="/api/soc/approvals", tags=["soc-approvals"])


class ApprovalGrantRequest(BaseModel):
    approval_request_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1)
    expires_in_seconds: int = Field(default=900, gt=0, le=86_400)


class ApprovalResolutionRequest(BaseModel):
    reason: str = Field(min_length=1)


class ApprovalRequestListResponse(BaseModel):
    items: list[SocAgentApprovalRequest]


def get_soc_approval_service(request: Request) -> SocAgentApprovalService:
    injected = getattr(request.app.state, "soc_approval_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    action_adapter_registry = getattr(request.app.state, "soc_action_adapter_registry", None)
    return SocAgentApprovalService(
        grant_repository=repository,
        request_repository=repository,
        action_adapter_registry=action_adapter_registry,
    )


ApprovalServiceDep = Annotated[SocAgentApprovalService, Depends(get_soc_approval_service)]


@router.post("/requests", response_model=SocAgentApprovalRequest)
def create_approval_request(
    approval_request: SocAgentApprovalRequest,
    request: Request,
    service: ApprovalServiceDep,
) -> SocAgentApprovalRequest:
    try:
        return service.submit_request(
            approval_request,
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/requests", response_model=ApprovalRequestListResponse)
def list_approval_requests(
    service: ApprovalServiceDep,
    status: SocAgentApprovalRequestStatus | None = Query(default=SocAgentApprovalRequestStatus.PENDING),
    limit: int = Query(default=50, ge=1, le=200),
) -> ApprovalRequestListResponse:
    try:
        return ApprovalRequestListResponse(items=service.list_requests(status=status, limit=limit))
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/requests/{approval_request_id}", response_model=SocAgentApprovalRequest)
def get_approval_request(
    approval_request_id: str,
    service: ApprovalServiceDep,
) -> SocAgentApprovalRequest:
    try:
        return service.get_request(approval_request_id)
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/grants", response_model=SocAgentApprovalGrant)
def create_approval_grant(
    body: ApprovalGrantRequest,
    request: Request,
    service: ApprovalServiceDep,
) -> SocAgentApprovalGrant:
    try:
        return service.approve(
            body.approval_request_id,
            context=soc_service_context_from_request(request, include_soc_roles=True),
            reason=body.reason,
            expires_in_seconds=body.expires_in_seconds,
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/requests/{approval_request_id}/reject", response_model=SocAgentApprovalRequest)
def reject_approval_request(
    approval_request_id: str,
    body: ApprovalResolutionRequest,
    request: Request,
    service: ApprovalServiceDep,
) -> SocAgentApprovalRequest:
    try:
        return service.reject(
            approval_request_id,
            context=soc_service_context_from_request(request, include_soc_roles=True),
            reason=body.reason,
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/requests/{approval_request_id}/expire", response_model=SocAgentApprovalRequest)
def expire_approval_request(
    approval_request_id: str,
    body: ApprovalResolutionRequest,
    request: Request,
    service: ApprovalServiceDep,
) -> SocAgentApprovalRequest:
    try:
        return service.expire(
            approval_request_id,
            context=soc_service_context_from_request(request, include_soc_roles=True),
            reason=body.reason,
        )
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
