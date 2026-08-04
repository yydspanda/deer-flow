from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from soc_agent.actions.adapters import (
    InMemoryAssetLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    InMemoryThreatIntelIpReputationLookupActionAdapter,
    SocActionAdapterRegistry,
    asset_lookup_adapter_descriptor,
)
from soc_agent.actions.mcp import (
    build_mcp_action_adapter_registry,
    load_mcp_action_adapter_configs,
)
from soc_agent.application import (
    SocEnrichmentCompositionError,
    build_soc_main_orchestrator_service,
    load_soc_enrichment_composition_config,
    validate_soc_enrichment_registry,
)
from soc_agent.contracts import (
    AlertEntitySet,
    AlertInput,
    NetworkEntityRef,
    SocAgentActionAdapterDescriptor,
    SocEnrichmentCompositionConfig,
    SocEnrichmentPlanStatus,
    SocMainOrchestratorRequest,
)
from soc_agent.core import InMemoryInvestigationEvidenceRepository

SAMPLES_DIR = Path(__file__).parents[1] / "samples" / "enrichment"
MCP_SAMPLES_DIR = Path(__file__).parents[1] / "samples" / "mcp"


def test_composition_is_default_off_and_rejects_ambiguous_enabled_config() -> None:
    default = SocEnrichmentCompositionConfig()

    assert default.enabled is False
    assert default.policy.enabled_routes == []
    assert default.bindings == []

    with pytest.raises(ValidationError, match="requires an explicit tenant_id"):
        SocEnrichmentCompositionConfig.model_validate(_composition_document(tenant_id=None))
    with pytest.raises(ValidationError, match="exactly match enabled routes"):
        SocEnrichmentCompositionConfig.model_validate(_composition_document(bindings=[]))
    with pytest.raises(ValidationError, match="cannot bind the same route more than once"):
        document = _composition_document()
        document["bindings"].append(dict(document["bindings"][0]))
        SocEnrichmentCompositionConfig.model_validate(document)


def test_composition_rejects_unscoped_threat_intel_and_two_asset_routes() -> None:
    with pytest.raises(ValidationError, match="requires explicit tenant internal_networks"):
        SocEnrichmentCompositionConfig.model_validate(
            _composition_document(
                routes=["threat_intel.ip_reputation.lookup"],
                bindings=[
                    {
                        "route": "threat_intel.ip_reputation.lookup",
                        "action": "threat_intel.ip_reputation.lookup",
                        "adapter_id": "threat-intel-ip-reputation-in-memory",
                        "adapter_kind": "service",
                    }
                ],
                internal_networks=[],
                asset_route=None,
            )
        )

    with pytest.raises(ValidationError, match="cannot enable both"):
        SocEnrichmentCompositionConfig.model_validate(
            _composition_document(
                routes=["asset.lookup", "asset.locate"],
                bindings=[
                    {
                        "route": "asset.lookup",
                        "action": "asset.lookup",
                        "adapter_id": "asset-lookup-in-memory",
                        "adapter_kind": "service",
                    },
                    {
                        "route": "asset.locate",
                        "action": "asset.locate",
                        "adapter_id": "asset-locate-pingan-mcp",
                        "adapter_kind": "mcp",
                    },
                ],
            )
        )


def test_load_sample_compositions() -> None:
    disabled = load_soc_enrichment_composition_config(SAMPLES_DIR / "disabled.yaml")
    enabled = load_soc_enrichment_composition_config(SAMPLES_DIR / "enabled.mock.yaml")

    assert disabled.enabled is False
    assert enabled.enabled is True
    assert enabled.required_result_mode.value == "mock"
    assert enabled.policy.asset_route == "asset.lookup"
    assert {binding.route for binding in enabled.bindings} == set(enabled.policy.enabled_routes)


def test_registry_validation_accepts_exact_mock_bindings() -> None:
    config = load_soc_enrichment_composition_config(SAMPLES_DIR / "enabled.mock.yaml")
    registry = _mock_registry()

    selected = validate_soc_enrichment_registry(config, registry)

    assert [descriptor.adapter_id for descriptor in selected] == [
        "asset-lookup-in-memory",
        "threat-intel-ip-reputation-in-memory",
        "security-tag-in-memory",
    ]


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("missing", "has no registered adapter"),
        ("identity", "identity mismatch"),
        ("kind", "kind mismatch"),
        ("write", "risk_level=read_only"),
        ("side_effect", "external_side_effect=read"),
        ("execute", "must support execute"),
        ("payload", "planner cannot guarantee"),
        ("context", "orchestrator cannot inject"),
        ("provenance", "must declare valid metadata.result_provenance_contract"),
    ],
)
def test_registry_validation_fails_closed_on_adapter_contract_mismatch(
    mutation: str,
    error: str,
) -> None:
    config = SocEnrichmentCompositionConfig.model_validate(_composition_document())
    descriptor = asset_lookup_adapter_descriptor()
    if mutation == "missing":
        registry = SocActionAdapterRegistry()
    else:
        changes: dict = {}
        if mutation == "identity":
            config = SocEnrichmentCompositionConfig.model_validate(_composition_document(adapter_id="another-adapter"))
        elif mutation == "kind":
            config = SocEnrichmentCompositionConfig.model_validate(_composition_document(adapter_kind="mcp"))
        elif mutation == "write":
            changes = {"risk_level": "high", "external_side_effect": "write"}
        elif mutation == "side_effect":
            changes = {"external_side_effect": "none"}
        elif mutation == "execute":
            changes = {"execute_supported": False}
        elif mutation == "payload":
            changes = {"required_payload_fields": ["asset_key", "um"]}
        elif mutation == "context":
            changes = {"required_context_refs": ["tenant_secret"]}
        elif mutation == "provenance":
            changes = {"metadata": {}}
        descriptor = descriptor.model_copy(update=changes)
        registry = SocActionAdapterRegistry([_DescriptorAdapter(descriptor)])

    with pytest.raises(SocEnrichmentCompositionError, match=error):
        validate_soc_enrichment_registry(config, registry)


def test_registry_validation_separates_mock_real_and_runtime_declared_modes() -> None:
    mock_descriptor = asset_lookup_adapter_descriptor()
    real_config = SocEnrichmentCompositionConfig.model_validate(_composition_document(result_mode="real"))
    with pytest.raises(SocEnrichmentCompositionError, match="cannot select mock-only"):
        validate_soc_enrichment_registry(
            real_config,
            SocActionAdapterRegistry([_DescriptorAdapter(mock_descriptor)]),
        )

    runtime_descriptor = mock_descriptor.model_copy(
        update={
            "metadata": {
                "result_provenance_contract": "runtime_declared",
                "result_mode_field": "mocked",
            }
        }
    )
    selected = validate_soc_enrichment_registry(
        real_config,
        SocActionAdapterRegistry([_DescriptorAdapter(runtime_descriptor)]),
    )
    assert selected == [runtime_descriptor]

    missing_runtime_field = runtime_descriptor.model_copy(update={"metadata": {"result_provenance_contract": "runtime_declared"}})
    with pytest.raises(SocEnrichmentCompositionError, match="result_mode_field='mocked'"):
        validate_soc_enrichment_registry(
            real_config,
            SocActionAdapterRegistry([_DescriptorAdapter(missing_runtime_field)]),
        )

    real_only_descriptor = mock_descriptor.model_copy(update={"metadata": {"result_provenance_contract": "real_only"}})
    mock_config = SocEnrichmentCompositionConfig.model_validate(_composition_document(result_mode="mock"))
    with pytest.raises(SocEnrichmentCompositionError, match="cannot select real-only"):
        validate_soc_enrichment_registry(
            mock_config,
            SocActionAdapterRegistry([_DescriptorAdapter(real_only_descriptor)]),
        )


def test_pingan_runtime_declared_mcp_bindings_form_one_real_profile() -> None:
    configs = []
    for directory in (
        "pingan_asset",
        "pingan_threat_intel",
        "pingan_security_tag",
    ):
        configs.extend(load_mcp_action_adapter_configs(MCP_SAMPLES_DIR / directory / "action_adapters.json"))
    registry = build_mcp_action_adapter_registry(configs, _NeverMcpProvider())
    composition = SocEnrichmentCompositionConfig.model_validate(
        {
            "enabled": True,
            "required_result_mode": "real",
            "policy": {
                "policy_version": "pingan-enrichment-example-v1",
                "tenant_id": "pingan",
                "enabled_routes": [
                    "asset.locate",
                    "threat_intel.ip_reputation.lookup",
                    "security_tag.lookup",
                ],
                "asset_route": "asset.locate",
                "internal_networks": ["30.0.0.0/8"],
            },
            "bindings": [
                {
                    "route": "asset.locate",
                    "action": "asset.locate",
                    "adapter_id": "asset-locate-pingan-mcp",
                    "adapter_kind": "mcp",
                },
                {
                    "route": "threat_intel.ip_reputation.lookup",
                    "action": "threat_intel.ip_reputation.lookup",
                    "adapter_id": "threat-intel-pingan-mcp",
                    "adapter_kind": "mcp",
                },
                {
                    "route": "security_tag.lookup",
                    "action": "security_tag.lookup",
                    "adapter_id": "security-tag-pingan-mcp",
                    "adapter_kind": "mcp",
                },
            ],
        }
    )

    selected = validate_soc_enrichment_registry(composition, registry)

    assert [descriptor.adapter_id for descriptor in selected] == [
        "asset-locate-pingan-mcp",
        "threat-intel-pingan-mcp",
        "security-tag-pingan-mcp",
    ]
    assert all(descriptor.metadata["result_provenance_contract"] == "runtime_declared" and descriptor.metadata["result_mode_field"] == "mocked" and "mocked" in descriptor.metadata["mcp"]["output_fields"] for descriptor in selected)


def test_runtime_declared_mcp_must_project_mocked_result_field() -> None:
    descriptor = asset_lookup_adapter_descriptor().model_copy(
        update={
            "adapter_kind": "mcp",
            "metadata": {
                "result_provenance_contract": "runtime_declared",
                "result_mode_field": "mocked",
                "mcp": {"output_fields": ["asset_found"]},
            },
        }
    )
    config = SocEnrichmentCompositionConfig.model_validate(_composition_document(adapter_kind="mcp", result_mode="real"))

    with pytest.raises(SocEnrichmentCompositionError, match="must project result mode field"):
        validate_soc_enrichment_registry(
            config,
            SocActionAdapterRegistry([_DescriptorAdapter(descriptor)]),
        )


def test_composition_builder_keeps_disabled_default_and_runs_enabled_plan() -> None:
    disabled_service = build_soc_main_orchestrator_service()
    disabled_report = disabled_service.run(SocMainOrchestratorRequest(payload=_alert_payload(), thread_id="THR-disabled"))
    assert disabled_report.enrichment_plan is None
    assert disabled_report.route_steps == []

    evidence_repository = InMemoryInvestigationEvidenceRepository()
    enabled_service = build_soc_main_orchestrator_service(
        composition=load_soc_enrichment_composition_config(SAMPLES_DIR / "enabled.mock.yaml"),
        action_adapter_registry=_mock_registry(),
        evidence_repository=evidence_repository,
    )
    enabled_report = enabled_service.run(SocMainOrchestratorRequest(payload=_alert_payload(), thread_id="THR-enabled"))

    assert enabled_report.enrichment_plan is not None
    assert enabled_report.enrichment_plan.status is SocEnrichmentPlanStatus.PLANNED
    assert enabled_report.route_steps
    assert all(step.origin == "planned" for step in enabled_report.route_steps)
    assert len(evidence_repository.list_evidence(thread_id="THR-enabled")) == len(enabled_report.route_steps)


def test_enabled_composition_builder_requires_registry_and_explicit_evidence_store() -> None:
    config = SocEnrichmentCompositionConfig.model_validate(_composition_document())

    with pytest.raises(SocEnrichmentCompositionError, match="requires an action adapter registry"):
        build_soc_main_orchestrator_service(composition=config)
    with pytest.raises(SocEnrichmentCompositionError, match="requires an explicit evidence repository"):
        build_soc_main_orchestrator_service(
            composition=config,
            action_adapter_registry=SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter()]),
        )


class _DescriptorAdapter:
    def __init__(self, descriptor: SocAgentActionAdapterDescriptor) -> None:
        self.descriptor = descriptor

    def dry_run(self, command, *, context):  # pragma: no cover - validation never invokes it
        raise AssertionError("adapter must not be invoked during composition validation")

    def execute(self, command, *, context):  # pragma: no cover - validation never invokes it
        raise AssertionError("adapter must not be invoked during composition validation")


class _NeverMcpProvider:
    def list_tools(self):  # pragma: no cover - composition does not discover tools
        raise AssertionError("composition validation must not discover MCP tools")

    def invoke(self, tool_name, payload, *, timeout_seconds, server_name=None):  # pragma: no cover
        raise AssertionError("composition validation must not invoke MCP tools")


def _mock_registry() -> SocActionAdapterRegistry:
    return SocActionAdapterRegistry(
        [
            InMemoryAssetLookupActionAdapter(),
            InMemoryThreatIntelIpReputationLookupActionAdapter(),
            InMemorySecurityTagLookupActionAdapter(),
        ]
    )


def _composition_document(
    *,
    tenant_id: str | None = "tenant-a",
    routes: list[str] | None = None,
    bindings: list[dict] | None = None,
    internal_networks: list[str] | None = None,
    asset_route: str | None = "asset.lookup",
    adapter_id: str = "asset-lookup-in-memory",
    adapter_kind: str = "service",
    result_mode: str = "mock",
) -> dict:
    selected_routes = routes or ["asset.lookup"]
    selected_bindings = bindings
    if selected_bindings is None:
        selected_bindings = [
            {
                "route": "asset.lookup",
                "action": "asset.lookup",
                "adapter_id": adapter_id,
                "adapter_kind": adapter_kind,
            }
        ]
    return {
        "schema_version": "soc.enrichment_composition.v1",
        "enabled": True,
        "required_result_mode": result_mode,
        "policy": {
            "schema_version": "soc.enrichment_policy.v1",
            "policy_version": "tenant-a-enrichment-v1",
            "tenant_id": tenant_id,
            "enabled_routes": selected_routes,
            "asset_route": asset_route,
            "internal_networks": internal_networks if internal_networks is not None else ["30.0.0.0/8"],
        },
        "bindings": selected_bindings,
    }


def _alert_payload() -> dict:
    return AlertInput(
        tenant_id="example-tenant",
        alert_id="ALT-ENRICHMENT-COMPOSITION-001",
        entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="8.8.8.8",
                destination_ip="10.1.2.3",
                domain="example.test",
            )
        ),
    ).model_dump(mode="json")
