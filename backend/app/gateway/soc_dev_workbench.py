"""Shared safety boundary for explicitly gated SOC DEV browser workbenches."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request
from sqlalchemy.engine import make_url

from app.gateway.soc_dependencies import get_or_create_soc_repository
from soc_agent.db import SqlAlchemyAlertRepository, resolve_database_url, to_sync_database_url
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings, resolve_soc_model_name


@dataclass(frozen=True)
class SocDevWorkbenchRuntime:
    repository: SqlAlchemyAlertRepository
    settings: SocLLMSettings
    database_file: Path


def resolve_soc_dev_workbench_runtime(
    request: Request,
    *,
    enabled_flag: str,
) -> SocDevWorkbenchRuntime:
    if not strict_env_bool(enabled_flag, False):
        raise HTTPException(status_code=404, detail="SOC DEV workbench is disabled")
    if strict_env_bool("SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS", False):
        raise HTTPException(
            status_code=503,
            detail=("SOC DEV workbench requires external action execution to remain disabled"),
        )
    if strict_env_bool("SOC_TENANT_POLICY_ENABLED", False):
        raise HTTPException(
            status_code=503,
            detail="SOC DEV workbench requires tenant policy evaluation to remain disabled",
        )
    require_dev_environment("SOC_MEMORY_ENVIRONMENT", required=False)
    require_dev_environment("SOC_AUTOMATION_ENVIRONMENT", required=True)

    database_url = resolve_database_url()
    parsed_url = make_url(to_sync_database_url(database_url))
    if parsed_url.get_backend_name() != "sqlite":
        raise HTTPException(
            status_code=503,
            detail="SOC DEV workbench requires an isolated SQLite database",
        )
    database_file = Path(parsed_url.database or "").expanduser().resolve()
    if database_file.name == "deerflow.db":
        raise HTTPException(
            status_code=503,
            detail="SOC DEV workbench cannot use DeerFlow's primary database",
        )

    settings = SocLLMSettings.from_env()
    if settings.mode is not SocAnalyzerMode.LLM:
        raise HTTPException(
            status_code=503,
            detail="SOC DEV workbench requires SOC_ANALYZER_MODE=llm",
        )
    try:
        settings = settings.with_overrides(
            model_name=resolve_soc_model_name(settings.model_name),
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SocDevWorkbenchRuntime(
        repository=get_or_create_soc_repository(request),
        settings=settings,
        database_file=database_file,
    )


def strict_env_bool(name: str, default: bool) -> bool:
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


def require_dev_environment(name: str, *, required: bool) -> None:
    value = os.environ.get(name, "").strip().casefold()
    if not value and not required:
        return
    if value != "dev":
        raise HTTPException(
            status_code=503,
            detail=f"{name} must be explicitly set to dev",
        )


__all__ = [
    "SocDevWorkbenchRuntime",
    "require_dev_environment",
    "resolve_soc_dev_workbench_runtime",
    "strict_env_bool",
]
