from __future__ import annotations

import json

import pytest

from soc_agent.cli import main
from soc_agent.core import SocAnalysisService, SocMainOrchestratorService
from soc_agent.eval import (
    DEFAULT_PINGAN_CAPABILITY_EVAL_DIR,
    load_pingan_capability_eval_fixtures,
    run_pingan_capability_eval,
    run_pingan_domain_triage_eval,
    run_pingan_main_orchestrator_eval,
)


def test_pingan_capability_eval_runs_default_fixtures() -> None:
    fixtures = load_pingan_capability_eval_fixtures(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR)

    report = run_pingan_capability_eval(fixtures)

    assert report.schema_version == "soc.pingan_capability_eval_report.v1"
    assert report.sample_count == 3
    assert report.action_count == 4
    assert report.evidence_count == 4
    assert report.failed_count == 0
    assert report.passed_count == 3
    assert report.source_type_counts == {"edr": 1, "hids": 1, "ndr": 1}

    by_sample = {result.sample_id: result for result in report.results}
    apt = by_sample["pingan-apt-action-evidence"]
    assert apt.conflict_count == 0
    assert apt.conflict_types == []

    edr = by_sample["pingan-edr-action-evidence"]
    assert all(action.passed for action in edr.actions)

    hids = by_sample["pingan-hids-action-evidence"]
    assert hids.source_type.value == "hids"
    assert all(action.evidence_id for action in hids.actions)
    unavailable_routes = {"endpoint.process_tree.lookup", "host.event_context.lookup"}
    assert unavailable_routes.isdisjoint(action.route for result in report.results for action in result.actions)


def test_cli_eval_pingan_outputs_report(capsys) -> None:
    code = main(["eval", "pingan", "--pretty"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 0
    assert data["schema_version"] == "soc.pingan_capability_eval_report.v1"
    assert data["sample_count"] == 3
    assert data["failed_count"] == 0


def test_pingan_domain_triage_eval_runs_default_fixtures() -> None:
    fixtures = load_pingan_capability_eval_fixtures(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR)

    report = run_pingan_domain_triage_eval(fixtures)

    assert report.schema_version == "soc.pingan_domain_triage_eval_report.v1"
    assert report.scenario_taxonomy_version == "soc.scenario_taxonomy.v1"
    assert report.sample_count == 3
    assert report.finding_count >= 6
    assert report.scenario_finding_count >= 3
    assert "execution.reverse_shell" in report.scenario_taxonomy_keys
    assert {"execution.suspicious_command", "network.malicious_outbound", "lateral_movement"}.issubset(set(report.covered_scenario_keys))
    assert report.unmapped_vendor_scenario_count == 0
    assert "execution.reverse_shell" in report.missing_scenario_taxonomy_keys
    assert "web.webshell" in report.missing_scenario_taxonomy_keys
    assert report.failed_count == 0
    assert report.passed_count == 3
    assert report.domain_counts == {"apt": 1, "edr": 1, "hids": 1}

    by_sample = {result.sample_id: result for result in report.results}
    apt = by_sample["pingan-apt-action-evidence"]
    assert apt.domain.value == "apt"
    assert apt.handler_id == "soc.domain.apt.v1"
    assert "PA-APT-001" in apt.findings[0].capability_card_refs
    assert any(ref.startswith("EVI-") for ref in apt.findings[0].evidence_refs)
    assert {item.scenario_key for item in apt.findings if item.scenario_key}.issuperset({"execution.suspicious_command", "network.malicious_outbound"})

    edr = by_sample["pingan-edr-action-evidence"]
    assert edr.domain.value == "edr"
    assert edr.findings[0].disposition == "needs_more_evidence"
    assert edr.findings[0].confidence == 0.5
    assert "PA-EDR-001" in edr.findings[0].capability_card_refs
    assert "lateral_movement" in {item.scenario_key for item in edr.findings}

    hids = by_sample["pingan-hids-action-evidence"]
    assert hids.domain.value == "hids"
    assert hids.findings[0].disposition == "needs_more_evidence"
    assert hids.findings[0].confidence == 0.5
    assert "PA-HIDS-001" in hids.findings[0].capability_card_refs
    assert "execution.suspicious_command" in {item.scenario_key for item in hids.findings}
    assert all(item.conclusion_summary for result in by_sample.values() for item in result.findings)


def test_cli_eval_pingan_domain_outputs_report(capsys) -> None:
    code = main(["eval", "pingan-domain", "--pretty"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 0
    assert data["schema_version"] == "soc.pingan_domain_triage_eval_report.v1"
    assert data["sample_count"] == 3
    assert data["failed_count"] == 0


def test_pingan_main_orchestrator_eval_runs_default_fixtures() -> None:
    fixtures = load_pingan_capability_eval_fixtures(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR)

    report = run_pingan_main_orchestrator_eval(fixtures)

    assert report.schema_version == "soc.pingan_main_orchestrator_eval_report.v1"
    assert report.sample_count == 3
    assert report.route_step_count == 4
    assert report.evidence_count == 4
    assert report.correlation_match_count == 3
    assert report.reusable_evidence_count == 4
    assert report.domain_finding_count >= 6
    assert report.failed_count == 0
    assert report.passed_count == 3

    by_sample = {result.sample_id: result for result in report.results}
    apt = by_sample["pingan-apt-action-evidence"]
    assert apt.report.skill_context.selected_skills
    assert [step.route for step in apt.report.route_steps] == [
        "threat_intel.ip_reputation.lookup",
        "security_tag.lookup",
    ]
    assert all(step.evidence_id for step in apt.report.route_steps)
    assert apt.report.correlation_result is not None
    assert len(apt.report.correlation_result.matches) == 1
    assert apt.report.correlation_result.reusable_evidence_count == 2
    assert all(evidence.run_id == apt.report.correlation_result.matches[0].summary.run_id for evidence in apt.report.correlation_result.matches[0].reusable_evidence)
    apt_finding = apt.report.domain_triage_results[0].findings[0]
    assert apt_finding.capability_card_refs
    assert apt_finding.evidence_profile.sources["correlation"] == "available"
    assert f"correlation_run:{apt.report.correlation_result.matches[0].summary.run_id}" in apt_finding.evidence_refs
    assert {evidence.evidence_id for evidence in apt.report.correlation_result.matches[0].reusable_evidence}.issubset(apt_finding.evidence_refs)
    assert apt.report.review_context.run_id == apt.report.run.run_id
    assert apt.report.review_context.correlation_match_count == 1
    assert apt.report.review_context.reusable_evidence_count == 2
    assert apt.report.metadata["writes_db"] is False
    assert apt.report.metadata["executes_high_risk_actions"] is False

    hids = by_sample["pingan-hids-action-evidence"]
    assert hids.report.review_context.action_evidence_count == 1
    assert hids.report.review_context.domain_finding_count >= 1
    assert "execution.suspicious_command" in {finding.scenario_key for result in hids.report.domain_triage_results for finding in result.findings}


def test_main_orchestrator_rejects_partial_custom_correlation_wiring() -> None:
    with pytest.raises(ValueError, match="requires both analysis_service and correlation_service"):
        SocMainOrchestratorService(analysis_service=SocAnalysisService())


def test_cli_eval_pingan_main_outputs_report(capsys) -> None:
    code = main(["eval", "pingan-main", "--pretty"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 0
    assert data["schema_version"] == "soc.pingan_main_orchestrator_eval_report.v1"
    assert data["sample_count"] == 3
    assert data["failed_count"] == 0
