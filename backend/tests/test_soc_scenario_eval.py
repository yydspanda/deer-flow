from __future__ import annotations

import json
from pathlib import Path

from soc_agent.cli import main
from soc_agent.eval import load_scenario_eval_report, run_scenario_eval

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def test_scenario_eval_reports_vendor_neutral_taxonomy_coverage() -> None:
    report = run_scenario_eval(
        [
            (str(SAMPLES / "pingan_legacy_apt.json"), _sample("pingan_legacy_apt.json")),
            (str(SAMPLES / "pingan_legacy_edr.json"), _sample("pingan_legacy_edr.json")),
            (str(SAMPLES / "pingan_legacy_hids.json"), _sample("pingan_legacy_hids.json")),
        ]
    )

    assert report.schema_version == "soc.scenario_eval_report.v1"
    assert report.scenario_taxonomy_version == "soc.scenario_taxonomy.v1"
    assert report.sample_count == 3
    assert report.failed_count == 0
    assert report.finding_count >= 6
    assert report.scenario_finding_count >= 3
    assert {"apt", "edr", "hids"}.issubset(report.domain_counts)
    assert {"execution.suspicious_command", "network.malicious_outbound", "lateral_movement"}.issubset(set(report.covered_scenario_keys))
    assert "execution.reverse_shell" in report.missing_scenario_taxonomy_keys
    assert report.unmapped_vendor_scenario_count == 0
    assert all(item.passed for item in report.results)


def test_scenario_eval_baseline_diff_reports_coverage_changes(tmp_path: Path) -> None:
    report = run_scenario_eval([(str(SAMPLES / "pingan_legacy_hids.json"), _sample("pingan_legacy_hids.json"))])
    baseline = report.model_copy(
        update={
            "sample_count": 0,
            "finding_count": 0,
            "scenario_finding_count": 0,
            "covered_scenario_keys": ["execution.reverse_shell"],
            "missing_scenario_taxonomy_keys": [key for key in report.scenario_taxonomy_keys if key != "execution.reverse_shell"],
        }
    )
    baseline_path = tmp_path / "scenario-baseline.json"
    baseline_path.write_text(baseline.model_dump_json(), encoding="utf-8")

    diffed = run_scenario_eval(
        [(str(SAMPLES / "pingan_legacy_hids.json"), _sample("pingan_legacy_hids.json"))],
        baseline=load_scenario_eval_report(baseline_path),
    )

    assert diffed.diff is not None
    assert diffed.diff.changed is True
    assert diffed.diff.sample_count_delta == 1
    assert "execution.reverse_shell" in diffed.diff.removed_covered_scenario_keys
    assert "execution.reverse_shell" in diffed.diff.newly_missing_scenario_keys
    assert "execution.suspicious_command" in diffed.diff.added_covered_scenario_keys


def test_cli_eval_scenarios_outputs_report(capsys) -> None:
    code = main(["eval", "scenarios", str(SAMPLES), "--glob", "pingan_legacy_hids.json", "--pretty"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 0
    assert data["schema_version"] == "soc.scenario_eval_report.v1"
    assert data["sample_count"] == 1
    assert data["failed_count"] == 0
    assert data["scenario_taxonomy_version"] == "soc.scenario_taxonomy.v1"
    assert "covered_scenario_keys" in data
