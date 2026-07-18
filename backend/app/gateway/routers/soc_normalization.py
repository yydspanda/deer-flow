"""SOC normalization baseline and parser-maintenance API endpoints."""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.routers.soc_dependencies import (
    get_or_create_soc_repository,
    soc_service_context_from_request,
)
from soc_agent.contracts import (
    NormalizationBaselineAcceptCommand,
    NormalizationBaselineStatus,
    NormalizationMaintenanceIssue,
    NormalizationMaintenanceIssueStatus,
    NormalizationMaintenanceIssueUpdateCommand,
    NormalizationSchemaBaseline,
)
from soc_agent.core import (
    SocNormalizationMaintenanceService,
    SocServiceAuthorizationError,
    SocServiceError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)

router = APIRouter(prefix="/api/soc/normalization", tags=["soc-normalization"])


class NormalizationBaselineListResponse(BaseModel):
    items: list[NormalizationSchemaBaseline]


class NormalizationIssueListResponse(BaseModel):
    items: list[NormalizationMaintenanceIssue]


class NormalizationIssueUpdateRequest(BaseModel):
    status: str = Field(pattern="^(acknowledged|resolved|ignored)$")
    reason: str = Field(min_length=1)


class NormalizationOperationsMetrics(BaseModel):
    schema_version: str = "soc.normalization_operations_metrics.v1"
    open_issue_count: int = 0
    issue_type_counts: dict[str, int] = Field(default_factory=dict)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    source_system_counts: dict[str, int] = Field(default_factory=dict)
    active_baseline_count: int = 0


def get_soc_normalization_service(request: Request) -> SocNormalizationMaintenanceService:
    injected = getattr(request.app.state, "soc_normalization_maintenance_service", None)
    if injected is not None:
        return injected
    repository = get_or_create_soc_repository(request)
    return SocNormalizationMaintenanceService(
        baseline_repository=repository,
        issue_repository=repository,
    )


NormalizationServiceDep = Annotated[
    SocNormalizationMaintenanceService,
    Depends(get_soc_normalization_service),
]


@router.post("/baselines", response_model=NormalizationSchemaBaseline)
def accept_normalization_baseline(
    body: NormalizationBaselineAcceptCommand,
    request: Request,
    service: NormalizationServiceDep,
) -> NormalizationSchemaBaseline:
    try:
        return service.accept_baseline(
            body,
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/baselines", response_model=NormalizationBaselineListResponse)
def list_normalization_baselines(
    service: NormalizationServiceDep,
    status: NormalizationBaselineStatus | None = Query(default=NormalizationBaselineStatus.ACTIVE),
    tenant_id: str | None = Query(default=None),
    source_system: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> NormalizationBaselineListResponse:
    try:
        return NormalizationBaselineListResponse(
            items=service.list_baselines(
                status=status,
                tenant_id=tenant_id,
                source_system=source_system,
                limit=limit,
            )
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/issues", response_model=NormalizationIssueListResponse)
def list_normalization_issues(
    service: NormalizationServiceDep,
    status: NormalizationMaintenanceIssueStatus | None = Query(default=NormalizationMaintenanceIssueStatus.OPEN),
    tenant_id: str | None = Query(default=None),
    source_system: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> NormalizationIssueListResponse:
    try:
        return NormalizationIssueListResponse(
            items=service.list_issues(
                status=status,
                tenant_id=tenant_id,
                source_system=source_system,
                limit=limit,
            )
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.patch("/issues/{issue_id}", response_model=NormalizationMaintenanceIssue)
def update_normalization_issue(
    issue_id: str,
    body: NormalizationIssueUpdateRequest,
    request: Request,
    service: NormalizationServiceDep,
) -> NormalizationMaintenanceIssue:
    try:
        return service.update_issue(
            NormalizationMaintenanceIssueUpdateCommand(
                issue_id=issue_id,
                status=body.status,
                reason=body.reason,
            ),
            context=soc_service_context_from_request(request, include_soc_roles=True),
        )
    except SocServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SocServiceAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SocServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/metrics", response_model=NormalizationOperationsMetrics)
def get_normalization_operations_metrics(
    service: NormalizationServiceDep,
) -> NormalizationOperationsMetrics:
    """Return bounded cardinality metrics suitable for an operator UI/exporter."""

    try:
        issues = service.list_issues(
            status=NormalizationMaintenanceIssueStatus.OPEN,
            limit=200,
        )
        baselines = service.list_baselines(
            status=NormalizationBaselineStatus.ACTIVE,
            limit=200,
        )
    except SocServiceNotImplementedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return NormalizationOperationsMetrics(
        open_issue_count=len(issues),
        issue_type_counts=dict(Counter(item.issue_type.value for item in issues)),
        severity_counts=dict(Counter(item.severity.value for item in issues)),
        source_system_counts=dict(Counter(item.source_system or "unknown" for item in issues)),
        active_baseline_count=len(baselines),
    )


__all__ = ["router"]
