"""Shared composition root for the persisted SOC analysis service."""

from __future__ import annotations

import os

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.automation import load_soc_automation_policy
from soc_agent.core import (
    DeterministicAnalysisRuntime,
    SocAnalysisService,
    SocAuthorizedActivityService,
    SocAutomationService,
    SocMemoryEvolutionService,
    SocMemoryService,
    SocNormalizationMaintenanceService,
    SocTenantPolicyEvaluationService,
)
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.integrations.pingan.knowledge import load_pingan_tenant_knowledge_profiles
from soc_agent.knowledge import (
    CompositeAnalysisRequestEnricher,
    TenantKnowledgeAnalysisRequestEnricher,
)
from soc_agent.llm import (
    SocLLMSettings,
    build_configured_analysis_nodes,
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
    memory_environment: str | None = None,
    runtime_environment: str | None = None,
) -> SocAnalysisService:
    """Build the one analysis service shared by CLI and offline batch entry points."""

    resolved_settings = settings or SocLLMSettings.from_env()
    resolved_environment = _resolve_runtime_environment(
        memory_environment=memory_environment,
        runtime_environment=runtime_environment,
    )
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
        runtime_environment=resolved_environment,
    )
    analysis_request_enricher = _build_analysis_request_enricher(
        repository,
        memory_environment=resolved_environment,
    )
    analyzer, role_verifier = build_configured_analysis_nodes(
        settings=resolved_settings,
    )
    return SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=analyzer,
            role_verifier=role_verifier,
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


def _build_analysis_request_enricher(
    repository: SqlAlchemyAlertRepository | None,
    *,
    memory_environment: str | None,
) -> CompositeAnalysisRequestEnricher:
    enrichers = [
        TenantKnowledgeAnalysisRequestEnricher(
            load_pingan_tenant_knowledge_profiles(),
        )
    ]
    if repository is not None:
        profile_registry = build_soc_memory_profile_registry()
        enrichers.append(
            ConfirmedMemoryAnalysisRequestEnricher(
                SocMemoryService(
                    record_repository=repository,
                    profile_registry=profile_registry,
                ),
                profile_registry=profile_registry,
                environment=memory_environment,
            )
        )
    return CompositeAnalysisRequestEnricher(enrichers)


def _resolve_memory_environment(explicit: str | None) -> str | None:
    candidates = {
        value.strip().casefold()
        for value in (
            explicit,
            os.environ.get("SOC_MEMORY_ENVIRONMENT"),
            os.environ.get("SOC_TENANT_POLICY_ENVIRONMENT"),
            os.environ.get("SOC_AUTOMATION_ENVIRONMENT"),
        )
        if value is not None and value.strip()
    }
    if len(candidates) > 1:
        raise ValueError("SOC memory, tenant-policy and automation environments must match")
    return next(iter(candidates), None)


def _resolve_runtime_environment(
    *,
    memory_environment: str | None,
    runtime_environment: str | None,
) -> str | None:
    """Resolve one environment for every scoped component in a service instance."""

    if runtime_environment is None:
        return _resolve_memory_environment(memory_environment)
    resolved = runtime_environment.strip().casefold()
    if not resolved:
        raise ValueError("runtime_environment must not be blank")
    if memory_environment is not None and memory_environment.strip().casefold() != resolved:
        raise ValueError("memory_environment and runtime_environment must match")
    return resolved


def _build_post_analysis_observers(
    repository: SqlAlchemyAlertRepository | None,
    *,
    settings: SocLLMSettings,
    action_adapter_registry: SocActionAdapterRegistryPort | None = None,
    runtime_environment: str | None = None,
) -> tuple[PostAnalysisObserver, ...]:
    observers: list[PostAnalysisObserver] = []
    tenant_policy_enabled = _strict_env_bool(
        "SOC_TENANT_POLICY_ENABLED",
        default=False,
    )
    policy_path = os.environ.get("SOC_TENANT_DISPOSITION_POLICY_PATH", "").strip()
    tenant_environment = (
        runtime_environment
        or os.environ.get(
            "SOC_TENANT_POLICY_ENVIRONMENT",
            "",
        ).strip()
    )
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
    if repository is not None:
        automation_environment = (
            runtime_environment
            or os.environ.get(
                "SOC_AUTOMATION_ENVIRONMENT",
                "",
            ).strip()
        )
        if automation_path and not automation_environment:
            raise ValueError("SOC_AUTOMATION_ENVIRONMENT is required when SOC_AUTOMATION_POLICY_PATH is configured")
        if automation_environment and tenant_environment and automation_environment.casefold() != tenant_environment.casefold():
            raise ValueError("SOC automation and tenant policy environments must match")
        environment = automation_environment or tenant_environment or "default"
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
        observers.append(
            SocMemoryEvolutionService(
                repository=repository,
                memory_record_repository=repository,
                automation_repository=repository,
                mutation_audit_repository=repository,
                mutation_uow=repository,
            )
        )
    elif automation_path or tenant_policy_enabled:
        raise ValueError("SOC automation and Memory evolution require persisted analysis repository")
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
