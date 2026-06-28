from __future__ import annotations

import json
from pathlib import Path

from soc_agent.cli import main
from soc_agent.contracts import AnalysisRunStatus, Verdict
from soc_agent.core.runtime import analyze_alert

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def test_approved_scanner_returns_false_positive_candidate() -> None:
    run = analyze_alert(_sample("approved_scanner.json"))

    assert run.status == AnalysisRunStatus.SUCCESS
    assert run.analysis is not None
    assert run.decision is not None
    assert run.analysis.verdict == Verdict.FALSE_POSITIVE
    assert run.decision.automation_allowed is False
    assert [step.step_name for step in run.steps] == [
        "normalize",
        "entity_extract",
        "analyze_stub",
        "schema_validate",
        "decide",
    ]
    assert all(step.status.value == "success" for step in run.steps)


def test_malicious_ioc_returns_true_positive_candidate() -> None:
    run = analyze_alert(_sample("malicious_ioc.json"))

    assert run.status == AnalysisRunStatus.SUCCESS
    assert run.analysis is not None
    assert run.analysis.verdict == Verdict.TRUE_POSITIVE
    assert run.analysis.confidence >= 0.9


def test_low_context_alert_needs_review() -> None:
    run = analyze_alert(_sample("unknown_low_context.json"))

    assert run.status == AnalysisRunStatus.NEEDS_REVIEW
    assert run.decision is not None
    assert run.decision.needs_review is True


def test_missing_fields_do_not_break_entity_extraction() -> None:
    run = analyze_alert(_sample("missing_fields.json"))

    assert run.status == AnalysisRunStatus.NEEDS_REVIEW
    assert run.entities is not None
    assert "missing optional field: rule_name" in run.entities.warnings


def test_cli_analyze_file_outputs_json(capsys) -> None:
    exit_code = main(["analyze", str(SAMPLES / "approved_scanner.json")])

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["alert_id"] == "ALT-SAMPLE-FP-001"
    assert payload["analysis"]["verdict"] == "false_positive"
