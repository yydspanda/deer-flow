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
from soc_agent.llm import (
    SocLLMSettings,
    build_configured_analyzer,
    build_configured_chat_client,
)
from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher
from soc_agent.protocols import (
    PostAnalysisObserver,
    SocActionAdapterRegistryPort,
    TenantPolicySignalProvider,
)
from soc_agent.tenant_policy import (
    LLMTenantPolicyAdvisor,
    StaticTenantPolicyResolver,
    load_tenant_disposition_policies,
)


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
        settings=resolved_settings,
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
    settings: SocLLMSettings,
    action_adapter_registry: SocActionAdapterRegistryPort | None = None,
) -> tuple[PostAnalysisObserver, ...]:
    observers: list[PostAnalysisObserver] = []
    tenant_policy_enabled = _strict_env_bool(
        "SOC_TENANT_POLICY_ENABLED",
        default=False,
    )
    policy_path = os.environ.get("SOC_TENANT_DISPOSITION_POLICY_PATH", "").strip()
    tenant_environment = os.environ.get("SOC_TENANT_POLICY_ENVIRONMENT", "").strip()
    advisor_mode = (
        os.environ.get(
            "SOC_TENANT_POLICY_ADVISOR_MODE",
            "off",
        )
        .strip()
        .casefold()
    )
    if advisor_mode not in {"off", "llm"}:
        raise ValueError("SOC_TENANT_POLICY_ADVISOR_MODE must be 'off' or 'llm'")
    advisor_skill_path = os.environ.get(
        "SOC_TENANT_POLICY_SKILL_PATH",
        "",
    ).strip()
    if advisor_mode == "off" and advisor_skill_path:
        raise ValueError("SOC_TENANT_POLICY_SKILL_PATH requires SOC_TENANT_POLICY_ADVISOR_MODE=llm")
    if advisor_mode == "llm" and not tenant_policy_enabled:
        raise ValueError("SOC_TENANT_POLICY_ADVISOR_MODE=llm requires SOC_TENANT_POLICY_ENABLED=true")
    if policy_path and not tenant_policy_enabled:
        raise ValueError("SOC_TENANT_DISPOSITION_POLICY_PATH is configured but SOC_TENANT_POLICY_ENABLED is false")
    if tenant_policy_enabled:
        if repository is None:
            raise ValueError("SOC tenant policy evaluation requires persisted analysis repository")
        if not policy_path:
            raise ValueError("SOC_TENANT_DISPOSITION_POLICY_PATH is required when SOC_TENANT_POLICY_ENABLED=true")
        if not tenant_environment:
            raise ValueError("SOC_TENANT_POLICY_ENVIRONMENT is required when SOC_TENANT_DISPOSITION_POLICY_PATH is configured")
        advisor = None
        if advisor_mode == "llm":
            if not advisor_skill_path:
                raise ValueError("SOC_TENANT_POLICY_SKILL_PATH is required when SOC_TENANT_POLICY_ADVISOR_MODE=llm")
            advisor_settings = settings.with_overrides(
                mode="llm",
                model_name=(os.environ.get("SOC_TENANT_POLICY_MODEL", "").strip() or settings.model_name),
            )
            advisor_client, advisor_model_name = build_configured_chat_client(
                settings=advisor_settings,
            )
            advisor = LLMTenantPolicyAdvisor(
                client=advisor_client,
                model_name=advisor_model_name,
                skill_path=advisor_skill_path,
            )
        policies = load_tenant_disposition_policies(policy_path)
        signal_providers = build_configured_tenant_policy_signal_providers()
        observers.append(
            SocTenantPolicyEvaluationService(
                policy_resolver=StaticTenantPolicyResolver(policies),
                repository=repository,
                environment=tenant_environment,
                authorized_activity_service=SocAuthorizedActivityService(repository=repository),
                event_timezone=os.environ.get("SOC_TENANT_POLICY_EVENT_TIMEZONE") or None,
                advisor=advisor,
                signal_providers=signal_providers,
            )
        )
    elif _strict_env_bool(
        "SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED",
        default=False,
    ):
        raise ValueError("SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED requires SOC_TENANT_POLICY_ENABLED=true")

    automation_path = os.environ.get("SOC_AUTOMATION_POLICY_PATH", "").strip()
    if automation_path or tenant_policy_enabled:
        if repository is None:
            raise ValueError("SOC automation evaluation requires persisted analysis repository")
        automation_environment = os.environ.get("SOC_AUTOMATION_ENVIRONMENT", "").strip()
        if automation_path and not automation_environment:
            raise ValueError("SOC_AUTOMATION_ENVIRONMENT is required when SOC_AUTOMATION_POLICY_PATH is configured")
        if automation_environment and tenant_environment and automation_environment.casefold() != tenant_environment.casefold():
            raise ValueError("SOC automation and tenant policy environments must match")
        environment = automation_environment or tenant_environment
        execute_actions = _strict_env_bool(
            "SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS",
            default=False,
        )
        if execute_actions and not automation_path:
            raise ValueError("SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS requires SOC_AUTOMATION_POLICY_PATH")
        observers.append(
            SocAutomationService(
                repository=repository,
                policy=(load_soc_automation_policy(automation_path) if automation_path else None),
                environment=environment,
                memory_repository=repository,
                tenant_policy_repository=repository,
                tenant_policy_application_enabled=tenant_policy_enabled,
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


def build_configured_tenant_policy_signal_providers() -> tuple[TenantPolicySignalProvider, ...]:
    """Build optional tenant integration providers without changing Runtime."""

    if not _strict_env_bool(
        "SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED",
        default=False,
    ):
        return ()
    from soc_agent.integrations.pingan.software_path_policy import (
        PingAnSoftwarePathPolicySignalProvider,
    )

    return (PingAnSoftwarePathPolicySignalProvider.from_env(),)


__all__ = [
    "build_configured_tenant_policy_signal_providers",
    "build_soc_analysis_service",
]
