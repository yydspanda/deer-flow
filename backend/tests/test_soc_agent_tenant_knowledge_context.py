from __future__ import annotations

import pytest
from pydantic import ValidationError

from soc_agent.contracts import (
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    DetectionRuleRef,
    ExtractedEntities,
    HostEntityRef,
    HttpEntityRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ProcessEntityRef,
    TenantKnowledgeSelector,
    UserEntityRef,
)
from soc_agent.integrations.pingan.knowledge import (
    load_pingan_internal_systems_profile,
    load_pingan_network_direction_profile,
    load_pingan_platform_context_profile,
    load_pingan_tenant_knowledge_profiles,
)
from soc_agent.knowledge import TenantKnowledgeAnalysisRequestEnricher
from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs


def _request(
    *,
    integration_name: str = "pingan_legacy_alert_platform",
    source_type: AlertSourceType = AlertSourceType.NDR,
    canonical_entities: AlertEntitySet | None = None,
    extracted_entities: ExtractedEntities | None = None,
    rule_name: str = "发现反弹SHELL行为（Linux）",
) -> LLMAnalysisRequest:
    return LLMAnalysisRequest(
        alert_id="ALT-DIRECTION-CONTEXT-1",
        tenant_id="pingan",
        source=AlertSourceRef(
            source_type=source_type,
            source_system="zeus",
            integration_name=integration_name,
        ),
        detection=DetectionRuleRef(rule_name=rule_name),
        canonical_entities=canonical_entities or AlertEntitySet(network=NetworkEntityRef(source_ip="30.116.114.150", destination_ip="30.174.29.44")),
        extracted_entities=extracted_entities or ExtractedEntities(ips=["30.116.114.150", "30.174.29.44"]),
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
    enricher = TenantKnowledgeAnalysisRequestEnricher(load_pingan_tenant_knowledge_profiles())

    request = enricher(_request(integration_name="another_vendor"))

    assert request.context_catalog == []


@pytest.mark.parametrize("address", ["26.1.2.3", "29.4.5.6", "172.31.9.8"])
def test_confirmed_pingan_internal_ranges_are_projected(address: str) -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(source_ip=address)),
        extracted_entities=ExtractedEntities(ips=[address]),
        rule_name="generic network event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert context["pa.internal-address-space"].metadata["matched_values"] == {"cidrs": [address]}


def test_office_subnet_refines_but_does_not_replace_internal_ownership() -> None:
    address = "10.107.11.132"
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(source_ip=address)),
        extracted_entities=ExtractedEntities(ips=[address]),
        rule_name="generic endpoint event",
    )

    fact_ids = {item.metadata["fact_id"] for item in enricher(request).context_catalog}

    assert {"pa.internal-address-space", "pa.office-address-space"} <= fact_ids


def test_public_corporate_domain_projects_negative_direction_caveat() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_network_direction_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(http=HttpEntityRef(host="www.pingan.com.cn")),
        extracted_entities=ExtractedEntities(domains=["www.pingan.com.cn"]),
        rule_name="generic web event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert "pa.public-corporate-domain-caveat" in context
    assert "must not be used as proof" in context["pa.public-corporate-domain-caveat"].summary


def test_typed_internal_system_selectors_use_canonical_entities() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    request = _request(
        source_type=AlertSourceType.EDR,
        canonical_entities=AlertEntitySet(
            host=HostEntityRef(host_name="CTXGMPVS-PA178"),
            process=ProcessEntityRef(
                process_name=r"C:\Program Files\pingantechmail\B\PaMailH5App.exe",
                process_path=r"C:\Program Files\pingantechmail\B\PaMailH5App.exe",
            ),
            user=UserEntityRef(um_account="EX-ZHANGWU233"),
            http=HttpEntityRef(path="/pws/askbob-gpt/chat/completions"),
        ),
        extracted_entities=ExtractedEntities(),
        rule_name="generic endpoint event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert {
        "pa.ctx-cloud-desktop-host",
        "pa.pamail-client-process",
        "pa.pamail-install-path",
        "pa.askbob-llm-endpoint",
        "pa.domain-account-convention",
    } <= context.keys()
    assert context["pa.ctx-cloud-desktop-host"].metadata["matched_values"] == {"host_prefixes": ["ctxgmpvs-pa178"]}
    assert context["pa.pamail-client-process"].metadata["matched_values"] == {"process_names": ["pamailh5app.exe"]}
    assert context["pa.domain-account-convention"].metadata["decision_authority"] == "none"


def test_typed_selectors_do_not_match_terms_only_present_in_detection_text() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    request = _request(
        canonical_entities=AlertEntitySet(),
        extracted_entities=ExtractedEntities(),
        rule_name=("mentions CTXGMPVS-PA178 PaMailH5App.exe EX-ZHANGWU233 /pws/askbob-gpt but has no typed entities"),
    )

    assert enricher(request).context_catalog == []


def test_multi_signal_application_identity_requires_every_selector_group() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    partial = _request(
        source_type=AlertSourceType.HIDS,
        canonical_entities=AlertEntitySet(process=ProcessEntityRef(process_name="ubiops-agent")),
        extracted_entities=ExtractedEntities(),
        rule_name="generic process event",
    )
    complete = partial.model_copy(
        update={
            "canonical_entities": AlertEntitySet(
                process=ProcessEntityRef(
                    process_name="ubiops-agent",
                    process_path="/tmp/ubiops-agent/install.sh",
                )
            )
        }
    )

    assert "pa.ubiops-agent-installation" not in {item.metadata["fact_id"] for item in enricher(partial).context_catalog}
    assert "pa.ubiops-agent-installation" in {item.metadata["fact_id"] for item in enricher(complete).context_catalog}


def test_codepilot_uri_identity_does_not_require_raw_text_search() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_internal_systems_profile()])
    request = _request(
        source_type=AlertSourceType.NIDS,
        canonical_entities=AlertEntitySet(http=HttpEntityRef(url="https://wizard.internal/code_pilot/api/v1/chat/completions?stream=true")),
        extracted_entities=ExtractedEntities(),
        rule_name="generic web event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert context["pa.codepilot-endpoint"].metadata["matched_values"] == {"uri_prefixes": ["/code_pilot/api/v1/chat/completions"]}
    assert context["pa.codepilot-endpoint"].metadata["decision_authority"] == "none"


def test_hids_platform_context_rejects_topic_based_environment_inference() -> None:
    enricher = TenantKnowledgeAnalysisRequestEnricher([load_pingan_platform_context_profile()])
    request = _request(
        source_type=AlertSourceType.HIDS,
        canonical_entities=AlertEntitySet(),
        extracted_entities=ExtractedEntities(),
        rule_name="generic host event",
    )

    context = {item.metadata["fact_id"]: item for item in enricher(request).context_catalog}

    assert "pa.hids-qingteng-source-context" in context
    assert "pa.hids-topic-does-not-prove-environment" in context
    assert "Do not infer development, staging, or production" in context["pa.hids-topic-does-not-prove-environment"].summary


def test_account_selector_rejects_invalid_regex() -> None:
    with pytest.raises(ValidationError, match="invalid tenant knowledge account pattern"):
        TenantKnowledgeSelector(account_patterns=["["])
