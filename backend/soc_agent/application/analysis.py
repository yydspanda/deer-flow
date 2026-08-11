"""Shared composition root for the persisted SOC analysis service."""

from __future__ import annotations

import os

from soc_agent.automation import load_soc_automation_policy
from soc_agent.core import (
    DeterministicAnalysisRuntime,
    SocAnalysisService,
    SocAuthorizedActivityService,
    SocAutomationService,
    SocMemoryService,
    SocNormalizationMaintenanceService,
    SocTenantPolicyEvaluationService,
)
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.llm import SocLLMSettings, build_configured_analyzer
from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher
from soc_agent.protocols import PostAnalysisObserver, SocActionAdapterRegistryPort
from soc_agent.tenant_policy import StaticTenantPolicyResolver, load_tenant_disposition_policies


def build_soc_analysis_service(
    repository: SqlAlchemyAlertRepository | None = None,
    *,
    settings: SocLLMSettings | None = None,
    action_adapter_registry: SocActionAdapterRegistryPort | None = None,
) -> SocAnalysisService:
    """Build the one analysis service shared by CLI and offline batch entry points."""

    resolved_settings = settings or SocLLMSettings.from_env()
    maintenance = (
        SocNormalizationMaintenanceService(
            baseline_repository=repository,
            issue_repository=repository,
        )
        if repository is not None
        else None
    )
    post_analysis_observers = _build_post_analysis_observers(
        repository,
        action_adapter_registry=action_adapter_registry,
    )
    analysis_request_enricher = (
        ConfirmedMemoryAnalysisRequestEnricher(
            SocMemoryService(record_repository=repository),
        )
        if repository is not None
        else None
    )
    return SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=build_configured_analyzer(settings=resolved_settings),
            analysis_request_enricher=analysis_request_enricher,
            sensitive_evidence_mode=resolved_settings.sensitive_evidence_mode,
        ),
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
        normalization_maintenance_monitor=maintenance,
        post_analysis_observers=post_analysis_observers,
    )


def _build_post_analysis_observers(
    repository: SqlAlchemyAlertRepository | None,
    *,
    action_adapter_registry: SocActionAdapterRegistryPort | None = None,
) -> tuple[PostAnalysisObserver, ...]:
    observers: list[PostAnalysisObserver] = []
    policy_path = os.environ.get("SOC_TENANT_DISPOSITION_POLICY_PATH", "").strip()
    if policy_path:
        if repository is None:
            raise ValueError("SOC tenant policy evaluation requires persisted analysis repository")
        environment = os.environ.get("SOC_TENANT_POLICY_ENVIRONMENT", "").strip()
        if not environment:
            raise ValueError("SOC_TENANT_POLICY_ENVIRONMENT is required when SOC_TENANT_DISPOSITION_POLICY_PATH is configured")
        policies = load_tenant_disposition_policies(policy_path)
        observers.append(
            SocTenantPolicyEvaluationService(
                policy_resolver=StaticTenantPolicyResolver(policies),
                repository=repository,
                environment=environment,
                authorized_activity_service=SocAuthorizedActivityService(repository=repository),
                event_timezone=os.environ.get("SOC_TENANT_POLICY_EVENT_TIMEZONE") or None,
            )
        )

    automation_path = os.environ.get("SOC_AUTOMATION_POLICY_PATH", "").strip()
    if automation_path:
        if repository is None:
            raise ValueError("SOC automation evaluation requires persisted analysis repository")
        environment = os.environ.get("SOC_AUTOMATION_ENVIRONMENT", "").strip()
        if not environment:
            raise ValueError("SOC_AUTOMATION_ENVIRONMENT is required when SOC_AUTOMATION_POLICY_PATH is configured")
        execute_actions = _strict_env_bool(
            "SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS",
            default=False,
        )
        observers.append(
            SocAutomationService(
                repository=repository,
                policy=load_soc_automation_policy(automation_path),
                environment=environment,
                memory_repository=repository,
                action_adapter_registry=action_adapter_registry,
                execute_authorized_actions=execute_actions,
            )
        )
    return tuple(observers)


def _strict_env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a strict boolean")


__all__ = ["build_soc_analysis_service"]
