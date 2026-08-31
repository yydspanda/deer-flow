"""Explicitly gated browser explorer for the complete local SOC corpus."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request

from app.gateway.routers.soc_transport import create_soc_router
from app.gateway.soc_dependencies import soc_service_context_from_request
from app.gateway.soc_dev_workbench import resolve_soc_dev_workbench_runtime
from soc_agent.application.analysis import build_soc_analysis_service
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.core import SocMemoryPatternService, SocServiceConflictError
from soc_agent.demo.corpus_workbench import (
    CORPUS_WORKBENCH_ENVIRONMENT,
    CorpusComparisonFilter,
    CorpusReadiness,
    SocCorpusWorkbenchActivity,
    SocCorpusWorkbenchAuditBundle,
    SocCorpusWorkbenchBusyError,
    SocCorpusWorkbenchCapacityError,
    SocCorpusWorkbenchError,
    SocCorpusWorkbenchExecution,
    SocCorpusWorkbenchProcessResult,
    SocCorpusWorkbenchService,
    SocCorpusWorkbenchState,
)

router = create_soc_router(
    prefix="/api/soc/dev/corpus-workbench",
    tags=["soc-dev-corpus-workbench"],
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CORPUS = _REPO_ROOT / "validation" / "compact_zeus" / "data" / "corpus" / "full_alert_dams_labeled_merged.pkl"
_SERVICE_INIT_LOCK = Lock()


def get_soc_corpus_workbench_service(
    request: Request,
) -> SocCorpusWorkbenchService:
    injected = getattr(request.app.state, "soc_corpus_workbench_service", None)
    if injected is not None:
        return injected
    with _SERVICE_INIT_LOCK:
        injected = getattr(
            request.app.state,
            "soc_corpus_workbench_service",
            None,
        )
        if injected is not None:
            return injected
        runtime = resolve_soc_dev_workbench_runtime(
            request,
            enabled_flag="SOC_DEV_CORPUS_WORKBENCH_ENABLED",
        )
        source_path = Path(
            os.environ.get(
                "SOC_DEV_CORPUS_WORKBENCH_PATH",
                str(_DEFAULT_CORPUS),
            )
        )
        try:
            service = SocCorpusWorkbenchService(
                repository=runtime.repository,
                analysis_service=build_soc_analysis_service(
                    runtime.repository,
                    settings=runtime.settings,
                    runtime_environment=CORPUS_WORKBENCH_ENVIRONMENT,
                    pattern_observation_enabled=False,
                ),
                pattern_service=SocMemoryPatternService(
                    repository=runtime.repository,
                    candidate_repository=runtime.repository,
                    profile_registry=build_soc_memory_profile_registry(),
                ),
                source_path=source_path,
                settings=runtime.settings,
                database_file=runtime.database_file.name,
                tenant_policy=runtime.tenant_policy,
                software_path_fast_policy=runtime.software_path_fast_policy,
            )
        except (OSError, ValueError, SocCorpusWorkbenchError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        request.app.state.soc_corpus_workbench_service = service
        return service


CorpusWorkbenchServiceDep = Annotated[
    SocCorpusWorkbenchService,
    Depends(get_soc_corpus_workbench_service),
]


@router.get(
    "",
    response_model=SocCorpusWorkbenchState,
    response_model_exclude_none=True,
)
def get_corpus_workbench_state(
    service: CorpusWorkbenchServiceDep,
    search: Annotated[str | None, Query(max_length=256)] = None,
    readiness: CorpusReadiness | None = None,
    source_type: Annotated[str | None, Query(max_length=64)] = None,
    group_id: Annotated[str | None, Query(max_length=512)] = None,
    comparison: CorpusComparisonFilter | None = None,
    unprocessed_only: bool = True,
    focus_alert_id: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SocCorpusWorkbenchState:
    return service.get_state(
        search=search,
        readiness=readiness,
        source_type=source_type,
        group_id=group_id,
        comparison=comparison,
        unprocessed_only=unprocessed_only,
        focus_alert_id=focus_alert_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/activity",
    response_model=SocCorpusWorkbenchActivity,
    response_model_exclude_none=True,
)
def get_corpus_workbench_activity(
    service: CorpusWorkbenchServiceDep,
) -> SocCorpusWorkbenchActivity:
    return service.get_activity()


@router.get(
    "/alerts/{alert_id}/execution",
    response_model=SocCorpusWorkbenchExecution,
    response_model_exclude_none=True,
)
def get_corpus_workbench_execution(
    alert_id: str,
    service: CorpusWorkbenchServiceDep,
) -> SocCorpusWorkbenchExecution:
    try:
        return service.get_execution(alert_id)
    except SocCorpusWorkbenchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/alerts/{alert_id}/audit",
    response_model=SocCorpusWorkbenchAuditBundle,
    response_model_exclude_none=True,
)
def get_corpus_workbench_audit(
    alert_id: str,
    request: Request,
    service: CorpusWorkbenchServiceDep,
) -> SocCorpusWorkbenchAuditBundle:
    context = soc_service_context_from_request(request, include_soc_roles=True)
    if "soc_admin" not in context.actor.roles:
        raise HTTPException(
            status_code=403,
            detail="SOC DEV corpus audit requires an administrator account",
        )
    try:
        return service.get_audit_bundle(alert_id, context=context)
    except SocCorpusWorkbenchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/alerts/{alert_id}/process",
    response_model=SocCorpusWorkbenchProcessResult,
    response_model_exclude_none=True,
)
def process_corpus_workbench_alert(
    alert_id: str,
    request: Request,
    service: CorpusWorkbenchServiceDep,
) -> SocCorpusWorkbenchProcessResult:
    context = soc_service_context_from_request(request, include_soc_roles=True)
    if "soc_admin" not in context.actor.roles:
        raise HTTPException(
            status_code=403,
            detail="SOC DEV corpus workbench requires an administrator account",
        )
    try:
        return service.process_alert(alert_id, context=context)
    except (SocCorpusWorkbenchBusyError, SocCorpusWorkbenchCapacityError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocServiceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocCorpusWorkbenchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = ["router"]
