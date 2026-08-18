"""Explicitly gated browser workflow for the local SOC Memory DEV cohort."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import make_url

from app.gateway.routers.soc_transport import create_soc_router
from app.gateway.soc_dependencies import (
    get_or_create_soc_repository,
    soc_service_context_from_request,
)
from soc_agent.application.analysis import build_soc_analysis_service
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.core import SocMemoryPatternService, SocServiceConflictError
from soc_agent.db import resolve_database_url, to_sync_database_url
from soc_agent.demo.memory_workbench import (
    MEMORY_WORKBENCH_ENVIRONMENT,
    SocMemoryWorkbenchConflictError,
    SocMemoryWorkbenchError,
    SocMemoryWorkbenchProcessResult,
    SocMemoryWorkbenchService,
    SocMemoryWorkbenchState,
)
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings, resolve_soc_model_name

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
    if not _strict_env_bool("SOC_DEV_MEMORY_WORKBENCH_ENABLED", False):
        raise HTTPException(
            status_code=404,
            detail="SOC DEV memory workbench is disabled",
        )
    if _strict_env_bool("SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS", False):
        raise HTTPException(
            status_code=503,
            detail=("SOC DEV memory workbench requires external action execution to remain disabled"),
        )
    if _strict_env_bool("SOC_TENANT_POLICY_ENABLED", False):
        raise HTTPException(
            status_code=503,
            detail=("SOC DEV memory workbench requires tenant policy evaluation to remain disabled"),
        )
    _require_dev_environment("SOC_MEMORY_ENVIRONMENT", required=False)
    _require_dev_environment("SOC_AUTOMATION_ENVIRONMENT", required=True)

    database_url = resolve_database_url()
    parsed_url = make_url(to_sync_database_url(database_url))
    if parsed_url.get_backend_name() != "sqlite":
        raise HTTPException(
            status_code=503,
            detail="SOC DEV memory workbench requires an isolated SQLite database",
        )
    database_file = Path(parsed_url.database or "").expanduser().resolve()
    if database_file.name == "deerflow.db":
        raise HTTPException(
            status_code=503,
            detail="SOC DEV memory workbench cannot use DeerFlow's primary database",
        )

    settings = SocLLMSettings.from_env()
    if settings.mode is not SocAnalyzerMode.LLM:
        raise HTTPException(
            status_code=503,
            detail="SOC DEV memory workbench requires SOC_ANALYZER_MODE=llm",
        )
    try:
        settings = settings.with_overrides(
            model_name=resolve_soc_model_name(settings.model_name),
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    source_path = Path(os.environ.get("SOC_DEV_MEMORY_CORPUS_PATH", str(_DEFAULT_CORPUS)))
    repository = get_or_create_soc_repository(request)
    try:
        service = SocMemoryWorkbenchService(
            repository=repository,
            analysis_service=build_soc_analysis_service(
                repository,
                settings=settings,
                memory_environment=MEMORY_WORKBENCH_ENVIRONMENT,
            ),
            pattern_service=SocMemoryPatternService(
                repository=repository,
                candidate_repository=repository,
                profile_registry=build_soc_memory_profile_registry(),
            ),
            source_path=source_path,
            settings=settings,
            database_file=database_file.name,
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


def _strict_env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(
        status_code=503,
        detail=f"{name} must be a strict boolean",
    )


def _require_dev_environment(name: str, *, required: bool) -> None:
    value = os.environ.get(name, "").strip().casefold()
    if not value and not required:
        return
    if value != MEMORY_WORKBENCH_ENVIRONMENT:
        raise HTTPException(
            status_code=503,
            detail=f"{name} must be explicitly set to dev",
        )


__all__ = ["router"]
