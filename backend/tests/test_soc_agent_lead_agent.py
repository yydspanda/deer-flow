from __future__ import annotations

import json

from soc_agent.cli import main
from soc_agent.contracts import (
    AlertClassification,
    AlertEntitySet,
    AlertInput,
    AlertSourceRef,
    AlertSourceType,
    ConflictReport,
    DetectionRuleRef,
    EmailEntityRef,
    EvidenceCoverageGap,
    EvidenceCoverageReport,
    ExtractedEntities,
    FactReconstructionResult,
    HostEntityRef,
    HttpEntityRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ProcessEntityRef,
)
from soc_agent.lead_agent import build_soc_lead_agent_profile
from soc_agent.skills import (
    SOC_ALERT_TRIAGE_SKILL,
    SOC_ASSET_DIRECTION_SKILL,
    SOC_ASSET_EXTRACTION_SKILL,
    SOC_EMAIL_PHISHING_TRIAGE_SKILL,
    SOC_ENDPOINT_TRIAGE_SKILL,
    SOC_LEAD_AGENT_NAME,
    SOC_NETWORK_APT_TRIAGE_SKILL,
    SOC_WEB_APPLICATION_TRIAGE_SKILL,
    SocSkillResolver,
    build_soc_skill_context,
)


def _skill_names(request: LLMAnalysisRequest) -> list[str]:
    return [item.skill_name for item in SocSkillResolver().resolve_for_analysis_request(request).selected_skills]


def test_soc_lead_agent_profile_uses_deerflow_custom_agent_shape() -> None:
    profile = build_soc_lead_agent_profile()

    assert profile.name == SOC_LEAD_AGENT_NAME
    assert SOC_ALERT_TRIAGE_SKILL in profile.skills
    assert SOC_ENDPOINT_TRIAGE_SKILL in profile.skills
    assert SOC_NETWORK_APT_TRIAGE_SKILL in profile.skills
    assert SOC_WEB_APPLICATION_TRIAGE_SKILL in profile.skills
    assert SOC_EMAIL_PHISHING_TRIAGE_SKILL in profile.skills
    assert SOC_ASSET_EXTRACTION_SKILL in profile.skills
    assert "DeerFlow custom agent" in profile.soul
    assert "Do not invent a second SOC agent runtime" in profile.soul
    assert "real rule/model hit" in profile.soul
    assert "best current risk, effect, and impact conclusion" in profile.soul
    assert "deterministic policy authorization" in profile.soul
    assert "asset.locate" in profile.soul
    assert profile.tool_groups is None


def test_skill_resolver_selects_endpoint_skill_for_edr_context() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-EDR",
        source=AlertSourceRef(source_type=AlertSourceType.EDR, product="EDR"),
        detection=DetectionRuleRef(rule_name="Suspicious endpoint process"),
        extracted_entities=ExtractedEntities(processes=["powershell.exe"], hosts=["HOST-1"], users=["UM001"]),
    )

    skill_names = _skill_names(request)

    assert skill_names[0] == SOC_ENDPOINT_TRIAGE_SKILL
    assert SOC_ALERT_TRIAGE_SKILL in skill_names


def test_skill_resolver_selects_network_apt_skill_for_apt_context() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-APT",
        source=AlertSourceRef(source_type=AlertSourceType.NIDS, product="Tianyan"),
        detection=DetectionRuleRef(rule_name="APT malicious outbound C2 callback"),
        extracted_entities=ExtractedEntities(ips=["203.0.113.10"], domains=["evil.example"]),
    )

    skill_names = _skill_names(request)

    assert skill_names[0] == SOC_NETWORK_APT_TRIAGE_SKILL
    assert SOC_ALERT_TRIAGE_SKILL in skill_names


def test_skill_resolver_does_not_cross_route_hids_from_ambiguous_keywords() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-HIDS-COMMAND",
        source=AlertSourceRef(source_type=AlertSourceType.HIDS, product="Host Agent"),
        detection=DetectionRuleRef(rule_name="[恶意命令执行] 可疑系统命令"),
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(
                process_name="sh",
                parent_process_name="sshd",
                command_line="id",
            )
        ),
    )

    skill_names = _skill_names(request)

    assert SOC_ENDPOINT_TRIAGE_SKILL in skill_names
    assert SOC_NETWORK_APT_TRIAGE_SKILL not in skill_names
    assert SOC_WEB_APPLICATION_TRIAGE_SKILL not in skill_names


def test_skill_resolver_keeps_typed_cross_domain_evidence_for_hids() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-HIDS-HTTP",
        source=AlertSourceRef(source_type=AlertSourceType.HIDS, product="Host Agent"),
        detection=DetectionRuleRef(rule_name="Web command execution"),
        canonical_entities=AlertEntitySet(
            process=ProcessEntityRef(process_name="sh", parent_process_name="java"),
            network=NetworkEntityRef(source_ip="10.0.0.5", destination_ip="198.51.100.2"),
            http=HttpEntityRef(method="POST", host="app.example", path="/execute"),
        ),
    )

    skill_names = _skill_names(request)

    assert SOC_ENDPOINT_TRIAGE_SKILL in skill_names
    assert SOC_NETWORK_APT_TRIAGE_SKILL in skill_names
    assert SOC_WEB_APPLICATION_TRIAGE_SKILL in skill_names


def test_skill_resolver_uses_network_source_to_scope_web_behavior_keyword() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-NIDS-WEB-COMMAND",
        source=AlertSourceRef(source_type=AlertSourceType.NIDS, product="Network IDS"),
        detection=DetectionRuleRef(rule_name="Web命令执行"),
    )

    skill_names = _skill_names(request)

    assert SOC_NETWORK_APT_TRIAGE_SKILL in skill_names
    assert SOC_WEB_APPLICATION_TRIAGE_SKILL in skill_names
    assert SOC_ENDPOINT_TRIAGE_SKILL not in skill_names


def test_skill_resolver_does_not_guess_domain_from_ambiguous_unknown_text() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-UNKNOWN-COMMAND",
        source=AlertSourceRef(source_type=AlertSourceType.UNKNOWN),
        detection=DetectionRuleRef(rule_name="恶意命令执行"),
    )

    assert _skill_names(request) == [SOC_ALERT_TRIAGE_SKILL]


def test_skill_resolver_selects_web_and_asset_direction_for_http_conflict() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-F5",
        source=AlertSourceRef(source_type=AlertSourceType.F5, product="F5"),
        detection=DetectionRuleRef(rule_name="F5 web SQL injection"),
        canonical_entities=AlertEntitySet(
            network=NetworkEntityRef(source_ip="198.51.100.2", destination_ip="10.0.0.5"),
            http=HttpEntityRef(host="app.example", url="https://app.example/login", x_forwarded_for="198.51.100.2"),
        ),
        fact_reconstruction=FactReconstructionResult(
            conflict_reports=[
                ConflictReport(
                    conflict_type="role_conflict",
                    description="source/destination role conflict",
                    evidence_paths=["entities.network.source_ip", "entities.network.destination_ip"],
                )
            ]
        ),
        conflict_count=1,
    )

    skill_names = _skill_names(request)

    assert SOC_WEB_APPLICATION_TRIAGE_SKILL in skill_names
    assert SOC_ASSET_DIRECTION_SKILL in skill_names
    assert SOC_ASSET_EXTRACTION_SKILL not in skill_names


def test_skill_resolver_does_not_reload_extraction_for_already_typed_assets() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-ASSET",
        source=AlertSourceRef(source_type=AlertSourceType.EDR, product="EDR"),
        detection=DetectionRuleRef(rule_name="Endpoint alert involving UM account and hostname"),
        extracted_entities=ExtractedEntities(hosts=["HOST-1"], users=["UM001"]),
    )

    skill_names = _skill_names(request)

    assert SOC_ASSET_EXTRACTION_SKILL not in skill_names
    assert SOC_ENDPOINT_TRIAGE_SKILL in skill_names


def test_skill_resolver_selects_asset_extraction_for_mapping_gap() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-ASSET-GAP",
        evidence_coverage=EvidenceCoverageReport(
            high_value_gaps=[
                EvidenceCoverageGap(
                    field_path="primary.message#parsed.device_name",
                    expected_target="canonical host or asset entity",
                    reason="new field has no reviewed mapping",
                )
            ]
        ),
    )

    assert SOC_ASSET_EXTRACTION_SKILL in _skill_names(request)


def test_skill_resolver_keeps_asset_group_out_of_endpoint_routing() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-ASSET-GROUP",
        canonical_entities=AlertEntitySet(host=HostEntityRef(asset_group="Example Business Unit")),
        extracted_entities=ExtractedEntities(assets=["Example Business Unit"]),
    )

    skill_names = _skill_names(request)

    assert SOC_ALERT_TRIAGE_SKILL in skill_names
    assert SOC_ENDPOINT_TRIAGE_SKILL not in skill_names


def test_skill_resolver_treats_canonical_host_ip_as_endpoint_identity() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-HOST-IP",
        canonical_entities=AlertEntitySet(host=HostEntityRef(ip_addresses=["10.0.0.10"])),
    )

    assert SOC_ENDPOINT_TRIAGE_SKILL in _skill_names(request)


def test_skill_resolver_selects_email_skill_from_typed_email_entity() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-MAIL",
        canonical_entities=AlertEntitySet(
            email=EmailEntityRef(
                sender_addresses=["sender@example.test"],
                recipient_addresses=["analyst@example.test"],
                subject="Suspicious invoice",
            )
        ),
    )

    assert SOC_EMAIL_PHISHING_TRIAGE_SKILL in _skill_names(request)


def test_skill_resolver_respects_available_skill_whitelist() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-EDR",
        source=AlertSourceRef(source_type=AlertSourceType.EDR),
        extracted_entities=ExtractedEntities(processes=["cmd.exe"]),
    )

    resolution = SocSkillResolver(available_skill_names={SOC_ALERT_TRIAGE_SKILL}).resolve_for_analysis_request(request)

    assert [item.skill_name for item in resolution.selected_skills] == [SOC_ALERT_TRIAGE_SKILL]
    assert resolution.available_agent_skills == [SOC_ALERT_TRIAGE_SKILL]


def test_skill_context_compacts_selected_skill_metadata() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-EDR",
        source=AlertSourceRef(source_type=AlertSourceType.EDR),
        extracted_entities=ExtractedEntities(processes=["cmd.exe"]),
    )
    resolution = SocSkillResolver().resolve_for_analysis_request(request)

    context = build_soc_skill_context(resolution)

    endpoint_item = next(item for item in context.selected_skills if item.skill_name == SOC_ENDPOINT_TRIAGE_SKILL)
    assert len(endpoint_item.package_hash) == 64
    assert len(endpoint_item.guidance_hash) == 64
    assert endpoint_item.guidance_source == "references/runtime-guidance.md"
    assert endpoint_item.estimated_token_count <= endpoint_item.token_budget
    assert endpoint_item.token_budget == 240
    assert "Trust that the configured endpoint detector hit occurred" in endpoint_item.guidance
    assert context.total_token_budget == 240 * len(context.selected_skills)
    assert context.total_estimated_token_count == sum(item.estimated_token_count for item in context.selected_skills)


def test_skill_resolver_accepts_canonical_alert_input() -> None:
    alert = AlertInput(
        alert_id="ALT-CANONICAL",
        source=AlertSourceRef(source_type=AlertSourceType.WAF),
        detection=DetectionRuleRef(rule_name="WAF XSS attempt"),
        classification=AlertClassification(category="web attack"),
        entities=AlertEntitySet(
            process=ProcessEntityRef(process_name="nginx"),
            http=HttpEntityRef(host="portal.example", x_forwarded_for="198.51.100.10"),
        ),
    )

    resolution = SocSkillResolver().resolve_for_alert(alert)

    assert SOC_WEB_APPLICATION_TRIAGE_SKILL in [item.skill_name for item in resolution.selected_skills]


def test_soc_agent_profile_cli_outputs_deerflow_agent_payload(capsys) -> None:
    assert main(["agent", "profile"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["name"] == SOC_LEAD_AGENT_NAME
    assert payload["skills"][0] == SOC_ALERT_TRIAGE_SKILL
    assert "soul" in payload


def test_soc_agent_resolve_skills_cli_outputs_resolution(capsys) -> None:
    payload = {
        "source": {"source_type": "edr", "product": "EDR"},
        "detection": {"rule_name": "Suspicious endpoint process"},
        "entities": {"process": {"process_name": "powershell.exe"}},
    }

    assert main(["agent", "resolve-skills", "--json", json.dumps(payload)]) == 0

    result = json.loads(capsys.readouterr().out)
    skill_names = [item["skill_name"] for item in result["selected_skills"]]

    assert SOC_ENDPOINT_TRIAGE_SKILL in skill_names
    assert result["agent_name"] == SOC_LEAD_AGENT_NAME
