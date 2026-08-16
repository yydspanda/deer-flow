"""Application composition helpers for SOC entry surfaces."""

from soc_agent.application.analysis import (
    build_configured_tenant_policy_signal_providers,
    build_soc_analysis_service,
)
from soc_agent.application.enrichment import (
    SocEnrichmentCompositionError,
    build_soc_investigation_reporting_service,
    build_soc_investigation_workflow_service,
    build_soc_main_orchestrator_service,
    load_soc_enrichment_composition_config,
    validate_soc_enrichment_registry,
)
from soc_agent.application.memory import build_soc_memory_profile_registry

__all__ = [
    "SocEnrichmentCompositionError",
    "build_configured_tenant_policy_signal_providers",
    "build_soc_analysis_service",
    "build_soc_investigation_reporting_service",
    "build_soc_investigation_workflow_service",
    "build_soc_main_orchestrator_service",
    "build_soc_memory_profile_registry",
    "load_soc_enrichment_composition_config",
    "validate_soc_enrichment_registry",
]
