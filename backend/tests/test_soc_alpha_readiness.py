from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.soc_alpha_readiness import (
    ACCEPTANCE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    ReadinessFailure,
    finalize_readiness_report,
    parse_capability_ids,
    parse_completeness_counts,
    parse_pytest_summary,
    parse_work_package_ids,
    prepare_output_dir,
    record_pytest_gate,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
ROOT_SCRIPT = REPOSITORY_ROOT / "scripts" / "soc-alpha-readiness.sh"
COMPLETENESS_MATRIX = REPOSITORY_ROOT / ".notes" / "ai_soc" / "audits" / "alpha-completeness-matrix.md"
DELIVERY_ROADMAP = REPOSITORY_ROOT / ".notes" / "ai_soc" / "delivery-roadmap.md"


def test_parse_pytest_summary_extracts_counts_and_duration() -> None:
    summary = parse_pytest_summary("...\n551 passed, 2 skipped in 108.60s (0:01:48)\n")

    assert summary == {
        "passed": 551,
        "failed": 0,
        "errors": 0,
        "skipped": 2,
        "duration_seconds": 108.6,
    }


def test_record_pytest_gate_requires_successful_nonempty_run(tmp_path: Path) -> None:
    log = tmp_path / "tests" / "backend-soc.log"
    log.parent.mkdir(parents=True)
    log.write_text("551 passed in 108.60s\n", encoding="utf-8")

    result = record_pytest_gate(
        output_dir=tmp_path,
        gate_name="backend-soc",
        exit_code=0,
        command="pytest",
        log_file=log,
    )

    assert result["status"] == "passed"
    assert result["summary"]["passed"] == 551
    assert (tmp_path / "tests" / "backend-soc.status.json").exists()

    log.write_text("1 failed, 550 passed in 10.00s\n", encoding="utf-8")
    failed = record_pytest_gate(
        output_dir=tmp_path,
        gate_name="backend-soc",
        exit_code=1,
        command="pytest",
        log_file=log,
    )
    assert failed["status"] == "failed"


def test_matrix_and_roadmap_parsers_keep_authoritative_sources() -> None:
    matrix = """
| Complete | 34 |
| Gap | 0 |
| Mock | 1 |
| Data-gated | 6 |
| Deferred | 9 |
| **Total** | **50** |
## 4. Data-Gated Register / 外部条件台账
| `AC-09` | x |
| `AC-19` | y |
## 5. Deferred Register / 明确后置
| `AC-05` | x |
"""
    roadmap = """
## 6. Stage 4 - Real Data & Production Integration
| `PI-01` | providers |
| `PI-02` | infrastructure |
## 7. Parking Lot
"""

    assert parse_completeness_counts(matrix)["Gap"] == 0
    assert parse_capability_ids(matrix, "## 4. Data-Gated Register") == ["AC-09", "AC-19"]
    assert parse_capability_ids(matrix, "## 5. Deferred Register") == ["AC-05"]
    assert parse_work_package_ids(roadmap, "## 6. Stage 4 - Real Data & Production Integration") == [
        "PI-01",
        "PI-02",
    ]


def test_authoritative_matrix_and_roadmap_remain_machine_readable() -> None:
    matrix = COMPLETENESS_MATRIX.read_text(encoding="utf-8")
    roadmap = DELIVERY_ROADMAP.read_text(encoding="utf-8")

    assert parse_completeness_counts(matrix) == {
        "Complete": 34,
        "Gap": 0,
        "Mock": 1,
        "Data-gated": 6,
        "Deferred": 9,
        "Total": 50,
    }
    assert parse_capability_ids(matrix, "## 4. Data-Gated Register") == [
        "AC-09",
        "AC-19",
        "AC-33",
        "AC-36",
        "AC-44",
        "AC-48",
    ]
    assert len(parse_capability_ids(matrix, "## 5. Deferred Register")) == 9
    assert parse_work_package_ids(roadmap, "## 6. Stage 4 - Real Data & Production Integration") == [
        "PI-01",
        "PI-02",
        "PI-03",
        "PI-04",
        "PI-05",
    ]


def test_finalize_seals_acceptance_tests_matrix_and_handoff(tmp_path: Path) -> None:
    acceptance = tmp_path / "soc-alpha-acceptance" / "alpha-acceptance-report.json"
    _write_json(
        acceptance,
        {
            "schema_version": ACCEPTANCE_SCHEMA_VERSION,
            "status": "passed",
            "generated_at": "2026-07-20T00:00:00+00:00",
            "component_status": {"core": True, "kafka": True, "frontend": True},
            "mock_and_data_boundaries": [{"capability": "analyzer", "mode": "stub"}],
        },
    )
    for gate_name, passed in {"backend-soc": 551, "architecture-migrations": 16}.items():
        log = tmp_path / "tests" / f"{gate_name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"{passed} passed in 1.00s\n", encoding="utf-8")
        record_pytest_gate(
            output_dir=tmp_path,
            gate_name=gate_name,
            exit_code=0,
            command="pytest",
            log_file=log,
        )

    matrix = tmp_path / "alpha-completeness-matrix.md"
    matrix.write_text(
        """| Complete | 34 |
| Gap | 0 |
| Mock | 1 |
| Data-gated | 6 |
| Deferred | 9 |
| **Total** | **50** |
## 4. Data-Gated Register
| `AC-09` | source |
## 5. Deferred Register
| `AC-05` | source |
""",
        encoding="utf-8",
    )
    roadmap = tmp_path / "delivery-roadmap.md"
    roadmap.write_text(
        """## 6. Stage 4 - Real Data & Production Integration
| `PI-01` | provider |
## 7. Parking Lot
""",
        encoding="utf-8",
    )

    report = finalize_readiness_report(
        tmp_path,
        completeness_matrix=matrix,
        delivery_roadmap=roadmap,
    )

    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["status"] == "passed"
    assert report["alpha_candidate_ready"] is True
    assert report["release_decision"] == "pending_owner_review"
    assert report["stage_transition_allowed"] is False
    assert report["production_ready"] is False
    assert report["completeness"]["counts"]["Gap"] == 0
    assert report["stage4_handoff"]["work_packages"] == ["PI-01"]
    assert (tmp_path / "alpha-readiness-report.json").exists()


def test_finalize_fails_closed_for_missing_evidence(tmp_path: Path) -> None:
    matrix = tmp_path / "alpha-completeness-matrix.md"
    matrix.write_text(
        """| Complete | 33 |
| Gap | 1 |
| Mock | 1 |
| Data-gated | 6 |
| Deferred | 9 |
| **Total** | **50** |
## 4. Data-Gated Register
| `AC-09` | source |
## 5. Deferred Register
| `AC-05` | source |
""",
        encoding="utf-8",
    )
    roadmap = tmp_path / "delivery-roadmap.md"
    roadmap.write_text(
        """## 6. Stage 4 - Real Data & Production Integration
| `PI-01` | provider |
""",
        encoding="utf-8",
    )

    report = finalize_readiness_report(
        tmp_path,
        completeness_matrix=matrix,
        delivery_roadmap=roadmap,
    )

    assert report["status"] == "failed"
    assert report["alpha_candidate_ready"] is False
    assert report["failure_reasons"]


def test_prepare_and_root_script_are_safe_and_valid(tmp_path: Path) -> None:
    safe = tmp_path / "custom-soc-alpha-readiness-run"
    safe.mkdir()
    (safe / "stale").write_text("stale", encoding="utf-8")

    prepare_output_dir(safe)

    assert list(safe.iterdir()) == []
    with pytest.raises(ReadinessFailure, match="soc-alpha-readiness"):
        prepare_output_dir(tmp_path)

    result = subprocess.run(
        ["bash", "-n", str(ROOT_SCRIPT)],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    source = ROOT_SCRIPT.read_text(encoding="utf-8")
    assert "run_acceptance || status=1" in source
    assert "run_backend || status=1" in source
    assert "run_architecture || status=1" in source
    assert "finalize || status=1" in source


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
