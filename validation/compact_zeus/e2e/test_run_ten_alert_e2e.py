from __future__ import annotations

import json
from pathlib import Path

from validation.compact_zeus.e2e.run_ten_alert_e2e import (
    DEFAULT_CASES,
    _grounding_quality_findings,
    build_batch_command,
    build_paths,
    load_case_manifest,
    main,
)


def test_case_manifest_selects_ten_complete_alerts_and_excludes_known_gaps() -> None:
    manifest, cases = load_case_manifest(DEFAULT_CASES)

    alert_ids = [case.alert_id for case in cases]
    assert len(alert_ids) == 10
    assert "1965452" not in alert_ids
    assert "1965795" not in alert_ids
    assert {"2025642", "1980502"}.issubset(alert_ids)
    assert {item["alert_id"] for item in manifest["excluded"]} == {
        "1965452",
        "1965795",
    }


def test_batch_command_uses_exact_ids_and_production_service_boundaries(
    tmp_path: Path,
) -> None:
    _manifest, cases = load_case_manifest(DEFAULT_CASES)
    paths = build_paths(tmp_path / "output")
    command = build_batch_command(
        python_executable=Path("/tmp/python"),
        source=tmp_path / "source.pkl",
        paths=paths,
        cases=cases,
        model_name="deepseek-v4-flash",
        execute=True,
        resume=False,
    )

    selected_ids = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--alert-id"
    ]
    assert selected_ids == [case.alert_id for case in cases]
    assert "--start-index" not in command
    assert "--limit" not in command
    assert "--persist" in command
    assert "--confirm-live" in command
    assert "--confirm-investigation" in command
    assert "--enrichment-composition" in command
    assert "--enrichment-extensions-config" in command


def test_plan_only_is_read_only_and_exposes_fixed_cohort(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "source.pkl"
    source.write_bytes(b"plan-only-does-not-read-source")

    exit_code = main(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "output"),
            "--python-executable",
            str(Path(__file__).resolve().parents[3] / "backend/.venv/bin/python"),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["live_model_call_count"] == 10
    assert output["provider_mode"] == "simulated_read_only"
    assert output["alert_ids"][-2:] == ["2025642", "1980502"]
    assert "--plan-only" in output["batch_command"]
    assert output["batch_command"][0].endswith("backend/.venv/bin/python")
    assert not (tmp_path / "output").exists()


def test_grounding_quality_findings_separate_safety_pass_from_model_quality() -> None:
    assert _grounding_quality_findings(
        {
            "total_count": 18,
            "grounded_count": 0,
            "ungrounded_count": 18,
            "description_leakage_count": 0,
            "reasoning_ungrounded_count": 2,
        }
    ) == [
        "no_analyzer_evidence_grounded",
        "18_analyzer_evidence_items_rejected",
        "2_analysis_reasoning_items_rejected",
    ]
