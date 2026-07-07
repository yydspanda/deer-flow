from __future__ import annotations

import json

from soc_agent.cli import main
from soc_agent.eval import (
    DEFAULT_PINGAN_CAPABILITY_EVAL_DIR,
    load_pingan_capability_eval_fixtures,
    run_pingan_capability_eval,
    run_pingan_domain_triage_eval,
)


def test_pingan_capability_eval_runs_default_fixtures() -> None:
    fixtures = load_pingan_capability_eval_fixtures(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR)

    report = run_pingan_capability_eval(fixtures)

    assert report.schema_version == "soc.pingan_capability_eval_report.v1"
    assert report.sample_count == 3
    assert report.action_count == 6
    assert report.evidence_count == 6
    assert report.failed_count == 0
    assert report.passed_count == 3
    assert report.source_type_counts == {"edr": 1, "hids": 1, "ndr": 1}

    by_sample = {result.sample_id: result for result in report.results}
    apt = by_sample["pingan-apt-action-evidence"]
    assert apt.conflict_count >= 1
    assert "source_candidate_conflict" in apt.conflict_types

    edr = by_sample["pingan-edr-action-evidence"]
    assert all(action.passed for action in edr.actions)

    hids = by_sample["pingan-hids-action-evidence"]
    assert hids.source_type.value == "hids"
    assert all(action.evidence_id for action in hids.actions)


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
    assert report.sample_count == 3
    assert report.finding_count == 3
    assert report.failed_count == 0
    assert report.passed_count == 3
    assert report.domain_counts == {"apt": 1, "edr": 1, "hids": 1}

    by_sample = {result.sample_id: result for result in report.results}
    apt = by_sample["pingan-apt-action-evidence"]
    assert apt.domain.value == "apt"
    assert apt.handler_id == "soc.domain.apt.v1"
    assert "PA-APT-001" in apt.findings[0].capability_card_refs
    assert any(ref.startswith("EVI-") for ref in apt.findings[0].evidence_refs)

    edr = by_sample["pingan-edr-action-evidence"]
    assert edr.domain.value == "edr"
    assert edr.findings[0].disposition == "suspicious"
    assert "PA-EDR-001" in edr.findings[0].capability_card_refs

    hids = by_sample["pingan-hids-action-evidence"]
    assert hids.domain.value == "hids"
    assert hids.findings[0].disposition == "benign_authorized_candidate"
    assert "PA-HIDS-001" in hids.findings[0].capability_card_refs


def test_cli_eval_pingan_domain_outputs_report(capsys) -> None:
    code = main(["eval", "pingan-domain", "--pretty"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 0
    assert data["schema_version"] == "soc.pingan_domain_triage_eval_report.v1"
    assert data["sample_count"] == 3
    assert data["failed_count"] == 0
