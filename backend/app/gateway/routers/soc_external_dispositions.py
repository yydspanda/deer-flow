"""Authenticated SOC external-disposition ingestion API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.gateway.routers.soc_dependencies import (
    get_or_create_soc_repository,
    soc_service_context_from_request,
)
from soc_agent.contracts import (
    SocExternalDispositionApplyResult,
    SocExternalDispositionIngressCommand,
    SocExternalDispositionMappingConfig,
)
from soc_agent.core import (
    SocDispositionEvaluationService,
    SocExternalDispositionService,
    SocMemoryService,
    SocServiceAuthorizationError,
    SocServiceConflictError,
    SocServiceError,
    SocServiceNotImplementedError,
)

router = APIRouter(
    prefix="/api/soc/external-dispositions",
    tags=["soc-external-dispositions"],
)


def get_soc_external_disposition_service(request: Request) -> SocExternalDispositionService:
    injected = getattr(request.app.state, "soc_external_disposition_service", None)
    if injected is not None:
        return injected

    repository = get_or_create_soc_repository(request)
    mapping_config = SocExternalDispositionMappingConfig.model_validate(getattr(request.app.state, "soc_external_disposition_mapping_config", None) or {})
    memory_service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
    )
    evaluation_service = SocDispositionEvaluationService(
        repository=repository,
        proposal_repository=repository,
        authorization_enrichment_repository=repository,
        review_queue_repository=repository,
    )
    return SocExternalDispositionService(
        repository=repository,
        mapping_config=mapping_config,
        alert_repository=repository,
        summary_repository=repository,
        review_queue_repository=repository,
        audit_repository=repository,
        memory_service=memory_service,
        disposition_proposal_repository=repository,
        disposition_evaluation_service=evaluation_service,
    )


ExternalDispositionServiceDep = Annotated[
    SocExternalDispositionService,
    Depends(get_soc_external_disposition_service),
]


@router.post("", response_model=SocExternalDispositionApplyResult)
def apply_external_disposition(
    command: SocExternalDispositionIngressCommand,
    request: Request,
    service: ExternalDispositionServiceDep,
) -> SocExternalDispositionApplyResult:
    """Apply one canonical, source-event-idempotent feedback event."""

    try:
        return service.apply_event(
            command.event,
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
