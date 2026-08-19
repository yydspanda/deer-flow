"""Explicitly gated browser workflow for the local SOC Memory DEV cohort."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.gateway.routers.soc_transport import create_soc_router
from app.gateway.soc_dependencies import (
    soc_service_context_from_request,
)
from app.gateway.soc_dev_workbench import resolve_soc_dev_workbench_runtime
from soc_agent.application.analysis import build_soc_analysis_service
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.core import SocMemoryPatternService, SocServiceConflictError
from soc_agent.demo.memory_workbench import (
    MEMORY_WORKBENCH_ENVIRONMENT,
    SocMemoryWorkbenchConflictError,
    SocMemoryWorkbenchError,
    SocMemoryWorkbenchProcessResult,
    SocMemoryWorkbenchService,
    SocMemoryWorkbenchState,
)

router = create_soc_router(
    prefix="/api/soc/dev/memory-workbench",
    tags=["soc-dev-memory-workbench"],
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CORPUS = _REPO_ROOT / "validation" / "compact_zeus" / "data" / "corpus" / "full_alert_validation_corpus.pkl"


def get_soc_memory_workbench_service(
    request: Request,
) -> SocMemoryWorkbenchService:
    injected = getattr(
        request.app.state,
        "soc_memory_workbench_service",
        None,
    )
    if injected is not None:
        return injected
    runtime = resolve_soc_dev_workbench_runtime(
        request,
        enabled_flag="SOC_DEV_MEMORY_WORKBENCH_ENABLED",
    )

    source_path = Path(os.environ.get("SOC_DEV_MEMORY_CORPUS_PATH", str(_DEFAULT_CORPUS)))
    try:
        service = SocMemoryWorkbenchService(
            repository=runtime.repository,
            analysis_service=build_soc_analysis_service(
                runtime.repository,
                settings=runtime.settings,
                memory_environment=MEMORY_WORKBENCH_ENVIRONMENT,
            ),
            pattern_service=SocMemoryPatternService(
                repository=runtime.repository,
                candidate_repository=runtime.repository,
                profile_registry=build_soc_memory_profile_registry(),
            ),
            source_path=source_path,
            settings=runtime.settings,
            database_file=runtime.database_file.name,
        )
    except (OSError, ValueError, SocMemoryWorkbenchError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    request.app.state.soc_memory_workbench_service = service
    return service


MemoryWorkbenchServiceDep = Annotated[
    SocMemoryWorkbenchService,
    Depends(get_soc_memory_workbench_service),
]


@router.get("", response_model=SocMemoryWorkbenchState)
def get_memory_workbench_state(
    service: MemoryWorkbenchServiceDep,
) -> SocMemoryWorkbenchState:
    return service.get_state()


@router.post(
    "/alerts/{alert_id}/process",
    response_model=SocMemoryWorkbenchProcessResult,
)
def process_memory_workbench_alert(
    alert_id: str,
    request: Request,
    service: MemoryWorkbenchServiceDep,
) -> SocMemoryWorkbenchProcessResult:
    context = soc_service_context_from_request(
        request,
        include_soc_roles=True,
    )
    if "soc_admin" not in context.actor.roles:
        raise HTTPException(
            status_code=403,
            detail="SOC DEV memory workbench requires an administrator account",
        )
    try:
        return service.process_alert(alert_id, context=context)
    except SocMemoryWorkbenchConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocMemoryWorkbenchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]
