from __future__ import annotations

from soc_agent.contracts import (
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    DetectionRuleRef,
    ExtractedEntities,
    LLMAnalysisRequest,
    NetworkEntityRef,
)
from soc_agent.integrations.pingan.knowledge import load_pingan_network_direction_profile
from soc_agent.knowledge import TenantKnowledgeAnalysisRequestEnricher
from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs


def _request(*, integration_name: str = "pingan_legacy_alert_platform") -> LLMAnalysisRequest:
    return LLMAnalysisRequest(
        alert_id="ALT-DIRECTION-CONTEXT-1",
        tenant_id="pingan",
        source=AlertSourceRef(
            source_type=AlertSourceType.NDR,
            source_system="zeus",
            integration_name=integration_name,
        ),
        detection=DetectionRuleRef(rule_name="发现反弹SHELL行为（Linux）"),
        canonical_entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="30.116.114.150",
                destination_ip="30.174.29.44",
            )
        ),
        extracted_entities=ExtractedEntities(
            ips=["30.116.114.150", "30.174.29.44"],
        ),
    )


def test_pingan_direction_knowledge_projects_only_relevant_context() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])

    request = finalize_analysis_reference_catalogs(enricher(_request()))

    context = {item.metadata.get("fact_id"): item for item in request.context_catalog}
    assert "pa.internal-address-space" in context
    assert "pa.network-direction-method" in context
    assert "pa.reverse-connection-role-inversion" in context
    assert "pa.proxy-cdn-client-chain" not in context
    assert context["pa.internal-address-space"].context_ref.startswith("C-")
    assert context["pa.internal-address-space"].metadata["matched_values"] == {"cidrs": ["30.116.114.150", "30.174.29.44"]}
    assert "10.0.0.0/8" not in context["pa.internal-address-space"].summary
    assert context["pa.internal-address-space"].metadata["decision_authority"] == "none"


def test_tenant_profile_does_not_leak_into_another_integration() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])

    request = enricher(_request(integration_name="another_vendor"))

    assert request.context_catalog == []
