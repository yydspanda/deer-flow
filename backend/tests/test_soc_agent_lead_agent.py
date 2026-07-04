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
    ExtractedEntities,
    FactReconstructionResult,
    HttpEntityRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ProcessEntityRef,
)
from soc_agent.lead_agent import build_soc_lead_agent_profile
from soc_agent.skills import (
    SOC_ALERT_TRIAGE_SKILL,
    SOC_ASSET_DIRECTION_SKILL,
    SOC_ENDPOINT_TRIAGE_SKILL,
    SOC_LEAD_AGENT_NAME,
    SOC_NETWORK_APT_TRIAGE_SKILL,
    SOC_WAF_F5_TRIAGE_SKILL,
    SocSkillResolver,
)


def _skill_names(request: LLMAnalysisRequest) -> list[str]:
    return [item.skill_name for item in SocSkillResolver().resolve_for_analysis_request(request).selected_skills]


def test_soc_lead_agent_profile_uses_deerflow_custom_agent_shape() -> None:
    profile = build_soc_lead_agent_profile()

    assert profile.name == SOC_LEAD_AGENT_NAME
    assert SOC_ALERT_TRIAGE_SKILL in profile.skills
    assert SOC_ENDPOINT_TRIAGE_SKILL in profile.skills
    assert SOC_NETWORK_APT_TRIAGE_SKILL in profile.skills
    assert "DeerFlow custom agent" in profile.soul
    assert "Do not invent a second SOC agent runtime" in profile.soul
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


def test_skill_resolver_selects_waf_and_asset_direction_for_http_conflict() -> None:
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

    assert SOC_WAF_F5_TRIAGE_SKILL in skill_names
    assert SOC_ASSET_DIRECTION_SKILL in skill_names


def test_skill_resolver_respects_available_skill_whitelist() -> None:
    request = LLMAnalysisRequest(
        alert_id="ALT-EDR",
        source=AlertSourceRef(source_type=AlertSourceType.EDR),
        extracted_entities=ExtractedEntities(processes=["cmd.exe"]),
    )

    resolution = SocSkillResolver(available_skill_names={SOC_ALERT_TRIAGE_SKILL}).resolve_for_analysis_request(request)

    assert [item.skill_name for item in resolution.selected_skills] == [SOC_ALERT_TRIAGE_SKILL]
    assert resolution.available_agent_skills == [SOC_ALERT_TRIAGE_SKILL]


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

    assert SOC_WAF_F5_TRIAGE_SKILL in [item.skill_name for item in resolution.selected_skills]


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
