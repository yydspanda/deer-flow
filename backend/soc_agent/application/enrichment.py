"""Composition root for governed read-only SOC investigation enrichment."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from soc_agent.contracts import (
    SocAgentActionAdapterDescriptor,
    SocAgentRiskLevel,
    SocEnrichmentAdapterProvenanceContract,
    SocEnrichmentCompositionConfig,
    SocEnrichmentResultMode,
)
from soc_agent.core import (
    SocAnalysisService,
    SocCorrelationService,
    SocDomainTriageService,
    SocEnrichmentPlanner,
    SocInvestigationWorkflowService,
    SocMainOrchestratorService,
)
from soc_agent.protocols import (
    AlertRepository,
    InvestigationEvidenceRepository,
    SocActionAdapterRegistryPort,
    SocEnrichmentExecutionRepository,
)

_PLANNER_GUARANTEED_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "asset.lookup": frozenset({"asset_key"}),
    "asset.locate": frozenset({"asset_key", "asset_type", "role"}),
    "threat_intel.ip_reputation.lookup": frozenset({"ip"}),
    "security_tag.lookup": frozenset({"entity_key", "entity_type"}),
}
_ORCHESTRATOR_CONTEXT_REFS = frozenset(
    {
        "alert_id",
        "run_id",
        "thread_id",
        "proposal_id",
        "enrichment_action_id",
    }
)
_RESULT_PROVENANCE_METADATA_KEY = "result_provenance_contract"
_RUNTIME_RESULT_MODE_FIELD_KEY = "result_mode_field"


class SocEnrichmentCompositionError(ValueError):
    """Raised when enrichment configuration and registered adapters disagree."""


def load_soc_enrichment_composition_config(
    config_path: str | Path,
) -> SocEnrichmentCompositionConfig:
    """Load one strict JSON/YAML composition document without interpolation."""

    path = Path(config_path)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SocEnrichmentCompositionError(f"unable to read enrichment composition {path}: {exc}") from exc
    try:
        document = json.loads(source) if path.suffix.lower() == ".json" else yaml.safe_load(source)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SocEnrichmentCompositionError(f"invalid enrichment composition document {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise SocEnrichmentCompositionError(f"enrichment composition {path} must contain one object")
    return _coerce_composition(document, source=str(path))


def validate_soc_enrichment_registry(
    config: SocEnrichmentCompositionConfig | Mapping[str, Any],
    registry: SocActionAdapterRegistryPort,
) -> list[SocAgentActionAdapterDescriptor]:
    """Fail closed unless every configured route has one exact safe adapter."""

    composition = _coerce_composition(config)
    if not composition.enabled:
        return []

    descriptors = registry.list_descriptors()
    descriptor_index: dict[tuple[str, str], SocAgentActionAdapterDescriptor] = {}
    for descriptor in descriptors:
        key = (descriptor.route, descriptor.action)
        if key in descriptor_index:
            raise SocEnrichmentCompositionError(f"registry exposes duplicate descriptors for route={descriptor.route!r} action={descriptor.action!r}")
        descriptor_index[key] = descriptor

    selected: list[SocAgentActionAdapterDescriptor] = []
    for binding in composition.bindings:
        descriptor = descriptor_index.get((binding.route, binding.action))
        if descriptor is None:
            raise SocEnrichmentCompositionError(f"configured enrichment route={binding.route!r} action={binding.action!r} has no registered adapter")
        if descriptor.adapter_id != binding.adapter_id:
            raise SocEnrichmentCompositionError(f"enrichment adapter identity mismatch for {binding.route!r}: configured={binding.adapter_id!r}, registered={descriptor.adapter_id!r}")
        if descriptor.adapter_kind != binding.adapter_kind:
            raise SocEnrichmentCompositionError(f"enrichment adapter kind mismatch for {binding.route!r}: configured={binding.adapter_kind!r}, registered={descriptor.adapter_kind!r}")
        _validate_read_only_descriptor(descriptor)
        _validate_planner_inputs(descriptor)
        _validate_result_provenance(descriptor, required_mode=composition.required_result_mode)
        selected.append(descriptor)
    return selected


def build_soc_main_orchestrator_service(
    *,
    composition: SocEnrichmentCompositionConfig | Mapping[str, Any] | None = None,
    action_adapter_registry: SocActionAdapterRegistryPort | None = None,
    evidence_repository: InvestigationEvidenceRepository | None = None,
    analysis_service: SocAnalysisService | None = None,
    correlation_service: SocCorrelationService | None = None,
    domain_triage_service: SocDomainTriageService | None = None,
) -> SocMainOrchestratorService:
    """Build the shared orchestrator with optional, default-off enrichment."""

    resolved = _coerce_composition(composition or {})
    planner: SocEnrichmentPlanner | None = None
    if resolved.enabled:
        if action_adapter_registry is None:
            raise SocEnrichmentCompositionError("enabled enrichment composition requires an action adapter registry")
        if evidence_repository is None:
            raise SocEnrichmentCompositionError("enabled enrichment composition requires an explicit evidence repository")
        validate_soc_enrichment_registry(resolved, action_adapter_registry)
        planner = SocEnrichmentPlanner(resolved.policy)

    return SocMainOrchestratorService(
        analysis_service=analysis_service,
        action_adapter_registry=action_adapter_registry,
        correlation_service=correlation_service,
        domain_triage_service=domain_triage_service,
        evidence_repository=evidence_repository,
        enrichment_planner=planner,
    )


def build_soc_investigation_workflow_service(
    *,
    composition: SocEnrichmentCompositionConfig | Mapping[str, Any],
    action_adapter_registry: SocActionAdapterRegistryPort,
    run_repository: AlertRepository,
    execution_repository: SocEnrichmentExecutionRepository,
    evidence_repository: InvestigationEvidenceRepository,
) -> SocInvestigationWorkflowService:
    """Build the persistent D3 workflow from explicit durable dependencies."""

    resolved = _coerce_composition(composition)
    if not resolved.enabled:
        raise SocEnrichmentCompositionError("persistent investigation workflow requires enabled enrichment composition")
    selected = validate_soc_enrichment_registry(
        resolved,
        action_adapter_registry,
    )
    return SocInvestigationWorkflowService(
        run_repository=run_repository,
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
        action_adapter_registry=action_adapter_registry,
        composition=resolved,
        selected_descriptors=selected,
    )


def _coerce_composition(
    value: SocEnrichmentCompositionConfig | Mapping[str, Any],
    *,
    source: str = "enrichment composition",
) -> SocEnrichmentCompositionConfig:
    try:
        return SocEnrichmentCompositionConfig.model_validate(value)
    except ValidationError as exc:
        raise SocEnrichmentCompositionError(f"invalid {source}: {exc}") from exc


def _validate_read_only_descriptor(descriptor: SocAgentActionAdapterDescriptor) -> None:
    if descriptor.risk_level is not SocAgentRiskLevel.READ_ONLY:
        raise SocEnrichmentCompositionError(f"enrichment adapter {descriptor.adapter_id!r} must be risk_level=read_only")
    if descriptor.external_side_effect != "read":
        raise SocEnrichmentCompositionError(f"enrichment adapter {descriptor.adapter_id!r} must declare external_side_effect=read")
    if not descriptor.execute_supported:
        raise SocEnrichmentCompositionError(f"enrichment adapter {descriptor.adapter_id!r} must support execute")


def _validate_planner_inputs(descriptor: SocAgentActionAdapterDescriptor) -> None:
    supported_payload = _PLANNER_GUARANTEED_PAYLOAD_FIELDS[descriptor.route]
    unsupported_payload = sorted(set(descriptor.required_payload_fields).difference(supported_payload))
    if unsupported_payload:
        raise SocEnrichmentCompositionError(f"enrichment adapter {descriptor.adapter_id!r} requires payload fields the planner cannot guarantee: {unsupported_payload}")
    unsupported_context = sorted(set(descriptor.required_context_refs).difference(_ORCHESTRATOR_CONTEXT_REFS))
    if unsupported_context:
        raise SocEnrichmentCompositionError(f"enrichment adapter {descriptor.adapter_id!r} requires context refs the orchestrator cannot inject: {unsupported_context}")


def _validate_result_provenance(
    descriptor: SocAgentActionAdapterDescriptor,
    *,
    required_mode: SocEnrichmentResultMode | None,
) -> None:
    raw_contract = descriptor.metadata.get(_RESULT_PROVENANCE_METADATA_KEY)
    try:
        contract = SocEnrichmentAdapterProvenanceContract(raw_contract)
    except (TypeError, ValueError) as exc:
        raise SocEnrichmentCompositionError(f"enrichment adapter {descriptor.adapter_id!r} must declare valid metadata.{_RESULT_PROVENANCE_METADATA_KEY}") from exc

    if contract is SocEnrichmentAdapterProvenanceContract.RUNTIME_DECLARED:
        mode_field = descriptor.metadata.get(_RUNTIME_RESULT_MODE_FIELD_KEY)
        if mode_field != "mocked":
            raise SocEnrichmentCompositionError(f"runtime-declared enrichment adapter {descriptor.adapter_id!r} must declare metadata.result_mode_field='mocked'")
        if descriptor.adapter_kind == "mcp":
            mcp_metadata = descriptor.metadata.get("mcp")
            output_fields = mcp_metadata.get("output_fields") if isinstance(mcp_metadata, Mapping) else None
            if not isinstance(output_fields, list) or mode_field not in output_fields:
                raise SocEnrichmentCompositionError(f"runtime-declared MCP enrichment adapter {descriptor.adapter_id!r} must project result mode field {mode_field!r}")
    if required_mode is SocEnrichmentResultMode.REAL and contract is SocEnrichmentAdapterProvenanceContract.MOCK_ONLY:
        raise SocEnrichmentCompositionError(f"real enrichment composition cannot select mock-only adapter {descriptor.adapter_id!r}")
    if required_mode is SocEnrichmentResultMode.MOCK and contract is SocEnrichmentAdapterProvenanceContract.REAL_ONLY:
        raise SocEnrichmentCompositionError(f"mock enrichment composition cannot select real-only adapter {descriptor.adapter_id!r}")


__all__ = [
    "SocEnrichmentCompositionError",
    "build_soc_investigation_workflow_service",
    "build_soc_main_orchestrator_service",
    "load_soc_enrichment_composition_config",
    "validate_soc_enrichment_registry",
]
