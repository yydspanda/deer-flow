"""Shared safety boundary for explicitly gated SOC DEV browser workbenches."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    tenant_policy: Literal["disabled", "deterministic", "deterministic_and_llm"]
    software_path_fast_policy: bool


@dataclass(frozen=True)
class SocDevPolicySafety:
    tenant_policy: Literal["disabled", "deterministic", "deterministic_and_llm"]
    software_path_fast_policy: bool


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
    policy_safety = resolve_soc_dev_policy_safety()
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
        tenant_policy=policy_safety.tenant_policy,
        software_path_fast_policy=policy_safety.software_path_fast_policy,
    )


def resolve_soc_dev_policy_safety() -> SocDevPolicySafety:
    """Resolve an explicit DEV-only policy mode without relaxing action safety."""

    enabled = strict_env_bool("SOC_TENANT_POLICY_ENABLED", False)
    advisor_mode = os.environ.get("SOC_TENANT_POLICY_ADVISOR_MODE", "off").strip().casefold()
    if advisor_mode not in {"off", "llm"}:
        raise HTTPException(
            status_code=503,
            detail="SOC_TENANT_POLICY_ADVISOR_MODE must be 'off' or 'llm'",
        )
    fast_policy = strict_env_bool(
        "SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED",
        False,
    )
    if not enabled:
        if advisor_mode == "llm" or fast_policy:
            raise HTTPException(
                status_code=503,
                detail="SOC DEV policy sub-features require SOC_TENANT_POLICY_ENABLED=true",
            )
        return SocDevPolicySafety(
            tenant_policy="disabled",
            software_path_fast_policy=False,
        )
    if not strict_env_bool("SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY", False):
        raise HTTPException(
            status_code=503,
            detail=("SOC DEV workbench tenant policy requires the explicit DEV policy allow switch"),
        )
    require_dev_environment("SOC_TENANT_POLICY_ENVIRONMENT", required=True)
    return SocDevPolicySafety(
        tenant_policy=("deterministic_and_llm" if advisor_mode == "llm" else "deterministic"),
        software_path_fast_policy=fast_policy,
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
    "SocDevPolicySafety",
    "SocDevWorkbenchRuntime",
    "require_dev_environment",
    "resolve_soc_dev_policy_safety",
    "resolve_soc_dev_workbench_runtime",
    "strict_env_bool",
]
