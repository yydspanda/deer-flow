from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.soc_alpha_acceptance import (
    CORE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    AcceptanceFailure,
    _prepare_output_dir,
    finalize_acceptance_report,
    run_core_acceptance,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
ROOT_SCRIPT = REPOSITORY_ROOT / "scripts" / "soc-alpha-acceptance.sh"


def test_core_acceptance_proves_cli_gateway_feedback_audit_and_replay(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc-alpha-core.db'}"

    result = run_core_acceptance(output_dir=tmp_path, database_url=database_url)

    assert result["schema_version"] == CORE_SCHEMA_VERSION
    assert result["status"] == "passed"
    assert result["coverage"]["sample_ids"] == [
        "pingan-apt-action-evidence",
        "pingan-edr-action-evidence",
        "pingan-hids-action-evidence",
    ]
    assert result["coverage"]["source_types"] == ["edr", "hids", "ndr"]
    assert len(result["coverage"]["replay_run_ids"]) == 3
    assert all(result["checks"].values())
    assert result["database"]["locator"] == str(tmp_path / "soc-alpha-core.db")

    gateway = _read_json(tmp_path / "core" / "gateway-feedback-journey.json")
    assert gateway["api_version"] == "1"
    assert gateway["first_apply"]["correction_applied"] is True
    assert gateway["duplicate_apply"]["idempotent"] is True
    assert gateway["changed_retry_status"] == 409
    assert gateway["updated_verdict"] == "false_positive"

    persistence = _read_json(tmp_path / "core" / "persistence-audit.json")
    assert persistence["persisted_verdict"] == "false_positive"
    assert persistence["persisted_queue_status"] is None
    assert {"analysis", "correction", "external_disposition"} <= set(persistence["decision_audit_actions"])
    assert persistence["mutation_audits"]


def test_finalize_requires_all_three_kafka_sources_and_frontend_gates(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "core" / "core-result.json",
        {
            "schema_version": CORE_SCHEMA_VERSION,
            "status": "passed",
            "coverage": {
                "sample_ids": sorted(
                    {
                        "pingan-apt-action-evidence",
                        "pingan-edr-action-evidence",
                        "pingan-hids-action-evidence",
                    }
                ),
                "source_types": ["edr", "hids", "ndr"],
            },
        },
    )
    _write_json(
        tmp_path / "kafka" / "status.json",
        {"schema_version": "soc.alpha_kafka_status.v1", "status": "passed"},
    )
    for name, alert_id in {
        "apt": "2026494",
        "edr": "1965810",
        "hids": "HIDS-2026-0001",
    }.items():
        _write_json(
            tmp_path / "kafka" / f"{name}.json",
            {
                "schema_version": "soc.kafka_smoke_result.v1",
                "alert_id": alert_id,
                "consume_result": {"status": "processed", "committed": True},
                "post_commit_result": {"status": "idle"},
            },
        )
    _write_json(
        tmp_path / "frontend" / "status.json",
        {"schema_version": "soc.alpha_frontend_status.v1", "status": "passed"},
    )

    report = finalize_acceptance_report(tmp_path)

    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["status"] == "passed"
    assert report["component_status"] == {
        "core": True,
        "kafka": True,
        "frontend": True,
    }
    assert report["kafka"]["coverage_verified"] is True
    assert (tmp_path / "alpha-acceptance-report.json").exists()


def test_finalize_writes_failed_report_for_missing_or_malformed_components(tmp_path: Path) -> None:
    core_path = tmp_path / "core" / "core-result.json"
    core_path.parent.mkdir(parents=True)
    core_path.write_text("{bad-json", encoding="utf-8")

    report = finalize_acceptance_report(tmp_path)

    assert report["status"] == "failed"
    assert report["component_status"] == {
        "core": False,
        "kafka": False,
        "frontend": False,
    }
    assert report["core"]["status"] == "failed"
    assert report["failure_reasons"]
    persisted = _read_json(tmp_path / "alpha-acceptance-report.json")
    assert persisted["status"] == "failed"


def test_prepare_only_clears_explicit_soc_alpha_directory(tmp_path: Path) -> None:
    safe_output = tmp_path / "custom-soc-alpha-run"
    safe_output.mkdir()
    (safe_output / "stale.txt").write_text("stale", encoding="utf-8")

    _prepare_output_dir(safe_output)

    assert safe_output.is_dir()
    assert list(safe_output.iterdir()) == []
    with pytest.raises(AcceptanceFailure, match="basename does not contain 'soc-alpha'"):
        _prepare_output_dir(tmp_path)


def test_root_alpha_acceptance_script_has_valid_bash_and_all_component_commands() -> None:
    result = subprocess.run(
        ["bash", "-n", str(ROOT_SCRIPT)],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    source = ROOT_SCRIPT.read_text(encoding="utf-8")
    assert "run_core || status=1" in source
    assert "run_kafka || status=1" in source
    assert "run_frontend || status=1" in source
    assert "finalize || status=1" in source
    assert "pingan_legacy_apt.json" in source
    assert "pingan_legacy_edr.json" in source
    assert "pingan_legacy_hids.json" in source
    frontend_runner = source.split("run_frontend() {", 1)[1].split("\n}\n\nfinalize()", 1)[0]
    assert frontend_runner.index("pnpm check") < frontend_runner.rindex("stop_frontend_server")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
