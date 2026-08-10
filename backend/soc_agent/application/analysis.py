"""Shared composition root for the persisted SOC analysis service."""

from __future__ import annotations

import os

from soc_agent.core import (
    DeterministicAnalysisRuntime,
    SocAnalysisService,
    SocAuthorizedActivityService,
    SocNormalizationMaintenanceService,
    SocTenantPolicyEvaluationService,
)
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.llm import SocLLMSettings, build_configured_analyzer
from soc_agent.tenant_policy import StaticTenantPolicyResolver, load_tenant_disposition_policies


def build_soc_analysis_service(
    repository: SqlAlchemyAlertRepository | None = None,
    *,
    settings: SocLLMSettings | None = None,
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
    post_analysis_observers = _build_post_analysis_observers(repository)
    return SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=build_configured_analyzer(settings=resolved_settings),
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
) -> tuple[SocTenantPolicyEvaluationService, ...]:
    policy_path = os.environ.get("SOC_TENANT_DISPOSITION_POLICY_PATH", "").strip()
    if not policy_path:
        return ()
    if repository is None:
        raise ValueError("SOC tenant policy evaluation requires persisted analysis repository")
    environment = os.environ.get("SOC_TENANT_POLICY_ENVIRONMENT", "").strip()
    if not environment:
        raise ValueError("SOC_TENANT_POLICY_ENVIRONMENT is required when SOC_TENANT_DISPOSITION_POLICY_PATH is configured")
    policies = load_tenant_disposition_policies(policy_path)
    return (
        SocTenantPolicyEvaluationService(
            policy_resolver=StaticTenantPolicyResolver(policies),
            repository=repository,
            environment=environment,
            authorized_activity_service=SocAuthorizedActivityService(repository=repository),
            event_timezone=os.environ.get("SOC_TENANT_POLICY_EVENT_TIMEZONE") or None,
        ),
    )


__all__ = ["build_soc_analysis_service"]
