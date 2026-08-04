"""Shared composition root for the persisted SOC analysis service."""

from __future__ import annotations

from soc_agent.core import (
    DeterministicAnalysisRuntime,
    SocAnalysisService,
    SocNormalizationMaintenanceService,
)
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.llm import SocLLMSettings, build_configured_analyzer


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
    )


__all__ = ["build_soc_analysis_service"]
