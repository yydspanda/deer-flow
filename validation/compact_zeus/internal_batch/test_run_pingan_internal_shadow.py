from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

from validation.compact_zeus.internal_batch.run_pingan_internal_shadow import (
    build_paths,
    build_plan,
    parse_args,
    run_orchestration,
)


def test_plan_is_serializable_and_reuses_the_fixed_internal_real_profile(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    args = parse_args(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "evidence"),
        ]
    )

    plan = build_plan(args, python_executable=Path(sys.executable))

    json.dumps(plan.payload)
    assert plan.payload["acceptance_mode"] == "internal_real"
    assert plan.payload["ramp_stage"] == "5"
    assert plan.payload["configuration"]["required_result_mode"] == "real"
    assert plan.payload["safety"]["live_execution_default"] is False
    assert plan.static_steps[0].argv[0] == str(Path(sys.executable).absolute())
    assert [step.step_id for step in plan.static_steps] == [
        "runtime_static_plan",
        "investigation_static_plan",
    ]
    assert [step.step_id for step in plan.live_steps] == [
        "pingan_dev_environment_preflight",
        "live_mcp_inventory_preflight",
        "sqlite_migration",
        "runtime_compatibility_batch",
        "persisted_investigation_batch",
        "paired_internal_real_gate",
    ]
    runtime = plan.live_steps[3].argv
    investigation = plan.live_steps[4].argv
    assert "--persist" not in runtime
    assert "--enrichment-composition" not in runtime
    assert "--persist" in investigation
    assert "pingan-internal-shadow.yaml" in " ".join(investigation)
    assert "extensions.internal.json" in " ".join(investigation)
    assert runtime[runtime.index("--limit") + 1] == "5"
    assert investigation[investigation.index("--limit") + 1] == "5"


def test_default_mode_runs_only_static_plans(
    tmp_path: Path,
    capsys,
) -> None:
    source = _source(tmp_path)
    args = parse_args(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "evidence"),
        ]
    )
    calls: list[str] = []

    exit_code = run_orchestration(
        args,
        executor=lambda step: calls.append(step.step_id) or 0,
    )

    assert exit_code == 0
    assert calls == ["runtime_static_plan", "investigation_static_plan"]
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "planned"
    assert output["execute_requested"] is False
    assert not (tmp_path / "evidence").exists()


def test_live_execution_requires_both_independent_confirmations(
    tmp_path: Path,
    capsys,
) -> None:
    source = _source(tmp_path)
    args = parse_args(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "evidence"),
            "--execute",
            "--confirm-live",
        ]
    )
    calls: list[str] = []

    exit_code = run_orchestration(
        args,
        executor=lambda step: calls.append(step.step_id) or 0,
    )

    assert exit_code == 2
    assert calls == []
    assert "requires both --confirm-live" in capsys.readouterr().err


def test_fresh_live_execution_rejects_nonempty_output_root(
    tmp_path: Path,
    capsys,
) -> None:
    source = _source(tmp_path)
    output_root = tmp_path / "evidence"
    output_root.mkdir()
    marker = output_root / "unrelated.txt"
    marker.write_text("do not overwrite", encoding="utf-8")
    calls: list[str] = []

    exit_code = run_orchestration(
        _live_args(source, output_root),
        executor=lambda step: calls.append(step.step_id) or 0,
    )

    assert exit_code == 2
    assert calls == []
    assert marker.read_text(encoding="utf-8") == "do not overwrite"
    assert "new or empty --output-root" in capsys.readouterr().err


def test_live_resume_requires_matching_orchestration_report(
    tmp_path: Path,
    capsys,
) -> None:
    source = _source(tmp_path)
    output_root = tmp_path / "evidence"
    output_root.mkdir()
    calls: list[str] = []

    exit_code = run_orchestration(
        _live_args(source, output_root, resume=True),
        executor=lambda step: calls.append(step.step_id) or 0,
    )

    assert exit_code == 2
    assert calls == []
    assert "matching orchestration report" in capsys.readouterr().err


def test_live_failure_stops_before_llm_and_persists_failed_step(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    output_root = tmp_path / "evidence"
    args = _live_args(source, output_root)
    calls: list[str] = []

    def execute(step) -> int:
        calls.append(step.step_id)
        return 7 if step.step_id == "live_mcp_inventory_preflight" else 0

    exit_code = run_orchestration(args, executor=execute)

    assert exit_code == 7
    assert calls == [
        "pingan_dev_environment_preflight",
        "live_mcp_inventory_preflight",
    ]
    report_path = build_paths(output_root, ramp_stage="5").orchestration_report
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failed_step"] == "live_mcp_inventory_preflight"
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(output_root.stat().st_mode) == 0o700


def test_successful_live_run_requires_a_passed_gate_report(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    output_root = tmp_path / "evidence"
    args = _live_args(source, output_root)
    paths = build_paths(output_root, ramp_stage="5")
    calls: list[str] = []

    def execute(step) -> int:
        calls.append(step.step_id)
        if step.step_id == "paired_internal_real_gate":
            paths.gate_report.write_text(
                json.dumps(
                    {
                        "report_id": "PI01E-REAL-001",
                        "gate_status": "passed",
                        "blocking_failure_ids": [],
                        "claims": {
                            "next_stage": "internal_real_50",
                            "next_stage_requires_human_review": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
        return 0

    exit_code = run_orchestration(args, executor=execute)

    assert exit_code == 0
    assert len(calls) == 6
    report = json.loads(paths.orchestration_report.read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["gate"] == {
        "report_id": "PI01E-REAL-001",
        "gate_status": "passed",
        "blocking_failure_ids": [],
        "next_stage": "internal_real_50",
        "next_stage_requires_human_review": True,
    }


def test_failed_gate_cannot_complete_the_orchestration(tmp_path: Path) -> None:
    source = _source(tmp_path)
    output_root = tmp_path / "evidence"
    args = _live_args(source, output_root)
    paths = build_paths(output_root, ramp_stage="5")

    def execute(step) -> int:
        if step.step_id == "paired_internal_real_gate":
            paths.gate_report.write_text(
                json.dumps(
                    {
                        "report_id": "PI01E-REAL-FAILED",
                        "gate_status": "failed",
                        "blocking_failure_ids": ["real_provider_observed"],
                        "claims": {
                            "next_stage": "internal_real_50",
                            "next_stage_requires_human_review": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            return 1
        return 0

    exit_code = run_orchestration(args, executor=execute)

    assert exit_code == 1
    report = json.loads(paths.orchestration_report.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failed_step"] == "paired_internal_real_gate"
    assert report["gate"]["blocking_failure_ids"] == ["real_provider_observed"]


def test_resume_is_forwarded_only_to_the_two_batch_executions(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    args = _live_args(source, tmp_path / "evidence", resume=True)

    plan = build_plan(args, python_executable=Path(sys.executable))

    by_id = {step.step_id: step for step in plan.live_steps}
    assert "--resume" in by_id["runtime_compatibility_batch"].argv
    assert "--resume" in by_id["persisted_investigation_batch"].argv
    assert "--resume" not in by_id["live_mcp_inventory_preflight"].argv


def test_all_stage_omits_limit_from_both_batches(tmp_path: Path) -> None:
    source = _source(tmp_path)
    args = parse_args(
        [
            "--source",
            str(source),
            "--output-root",
            str(tmp_path / "evidence"),
            "--ramp-stage",
            "all",
        ]
    )

    plan = build_plan(args, python_executable=Path(sys.executable))

    assert all("--limit" not in step.argv for step in plan.static_steps)
    assert "--limit" not in plan.live_steps[3].argv
    assert "--limit" not in plan.live_steps[4].argv


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "approved-alerts.pkl"
    source.write_bytes(b"approved-internal-fixture")
    return source


def _live_args(
    source: Path,
    output_root: Path,
    *,
    resume: bool = False,
):
    argv = [
        "--source",
        str(source),
        "--output-root",
        str(output_root),
        "--execute",
        "--confirm-live",
        "--confirm-investigation",
    ]
    if resume:
        argv.append("--resume")
    return parse_args(argv)
