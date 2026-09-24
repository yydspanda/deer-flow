"""CLI safety boundaries; database/report math have their own synthetic suites."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import soc_pingan_validation_report as cli


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "soc.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE preserved(value TEXT)")
        conn.execute("INSERT INTO preserved VALUES ('untouched')")
    return path


def test_missing_database_never_created(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path)]) == 2
    assert not (tmp_path / "backend").exists()
    assert "未找到数据库" in capsys.readouterr().err


def test_existing_output_refused_before_read(database, tmp_path, monkeypatch):
    output = tmp_path / "existing"
    output.mkdir()
    monkeypatch.setattr(
        cli,
        "collect",
        lambda *args, **kwargs: pytest.fail("must refuse before reading"),
    )
    assert cli.main(["--database", str(database), "--output-dir", str(output)]) == 2
    assert list(output.iterdir()) == []


def test_failed_consistency_read_publishes_nothing(
    database, tmp_path, monkeypatch, capsys
):
    output = tmp_path / "report"

    def changed(*args, **kwargs):
        raise ValueError("读取期间数据发生变化")

    monkeypatch.setattr(cli, "collect", changed)
    assert cli.main(["--database", str(database), "--output-dir", str(output)]) == 2
    assert not output.exists()
    assert "数据发生变化" in capsys.readouterr().err


def test_private_output_hashes_and_database_preserved(database, tmp_path, monkeypatch):
    output = tmp_path / "report"
    before = database.read_bytes()
    metadata = {"experiment_id": "EXP-SYNTHETIC", "round_id": "ROUND-SYNTHETIC"}
    monkeypatch.setattr(cli, "collect", lambda *args, **kwargs: {"metadata": metadata})
    monkeypatch.setattr(cli, "build_report", lambda data, **kwargs: data)

    def write(report, directory):
        (directory / "REPORT.md").write_text("synthetic\n", encoding="utf-8")
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")

    monkeypatch.setattr(cli, "write_report", write)
    assert cli.main(["--database", str(database), "--output-dir", str(output)]) == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["read_only"] is True
    assert manifest["model_calls"] == 0
    assert (
        manifest["files"]["REPORT.md"]["sha256"]
        == hashlib.sha256(b"synthetic\n").hexdigest()
    )
    assert len(manifest["tool_files_sha256"]) == 3
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    assert database.read_bytes() == before


@pytest.mark.parametrize("exclude_jobs", [False, True])
def test_semantic_exclusion_uses_union_and_keeps_partial(
    database, tmp_path, monkeypatch, exclude_jobs
):
    records = [
        ("good", "completed", "applied", "main"),
        ("partial", "completed", "partial", "main"),
        ("semantic", "completed", "failed", "main"),
        ("both", "failed", "failed", "main"),
        ("job", "failed", "skipped", "supplementary"),
    ]
    source = {
        "metadata": {"experiment_id": "exp", "round_id": "fixed"},
        "validation_rows": [
            {
                "alert_id": alert,
                "batch": "validation",
                "group_id": "g",
                "status": status,
                "validation_tier": tier,
                "semantic_review": {"status": semantic},
                "summary": {"memory_uses": []},
            }
            for alert, status, semantic, tier in records
        ],
    }
    monkeypatch.setattr(cli, "collect", lambda *args, **kwargs: source)
    output = tmp_path / "effect-union"
    args = [
        "--database",
        str(database),
        "--output-dir",
        str(output),
        "--exclude-semantic-failed",
    ]
    if exclude_jobs:
        args.append("--exclude-failed")
    assert cli.main(args) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    scope = report["metadata"]["evaluation_scope"]
    assert scope["excluded_validation_count"] == (3 if exclude_jobs else 2)
    assert scope["included_validation_count"] == (2 if exclude_jobs else 3)
    assert scope["excluded_by_reason"] == {
        "job_failed": 2 if exclude_jobs else 0,
        "semantic_review_failed": 2,
    }
    assert scope["excluded_overlap_count"] == (1 if exclude_jobs else 0)
    assert scope["exclude_semantic_review_failed"] is True
    excluded = {item["alert_id"] for item in scope["excluded_alerts"]}
    assert excluded == (
        {"semantic", "both", "job"} if exclude_jobs else {"semantic", "both"}
    )
    assert "partial" in {item["alert_id"] for item in report["alerts"]}
    assert (
        report["metrics"]["all"]["semantic_review_status_counts"].get("failed", 0) == 0
    )
    assert report["metrics"]["all"]["status_counts"].get("failed", 0) == (
        0 if exclude_jobs else 1
    )
    assert len(source["validation_rows"]) == 5


def test_standalone_help_does_not_import_checkout_or_require_dependencies(tmp_path):
    source = Path(cli.__file__).parent
    for name in (
        "soc_pingan_validation_report.py",
        "soc_validation_report_data.py",
        "soc_validation_report_render.py",
    ):
        shutil.copyfile(source / name, tmp_path / name)
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(tmp_path / "soc_pingan_validation_report.py"),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--groups-per-batch" in result.stdout


@pytest.mark.parametrize("exclude_failed", [False, True])
def test_effect_scope_filters_only_failed_validation_jobs(
    database, tmp_path, monkeypatch, exclude_failed
):
    def alert(alert_id, batch, status, tier="main", semantic="applied"):
        return {
            "alert_id": alert_id,
            "batch": batch,
            "status": status,
            "validation_tier": tier,
            "group_id": "g",
            "round_id": "fixed",
            "job_id": f"job-{alert_id}",
            "run_id": f"run-{alert_id}",
            "summary": {
                "memory_uses": [],
                "measurements": {"total_tokens": 10},
            },
            "semantic_review": {"status": semantic},
        }

    original = {
        "metadata": {"experiment_id": "exp", "round_id": "fixed"},
        "validation_rows": [
            alert("success", "validation", "completed"),
            alert("semantic-failed", "validation", "completed", semantic="failed"),
            alert("failed-main", "validation", "failed"),
            alert("failed-other", "validation", "failed", tier="supplementary"),
        ],
        "learning_rows": [alert("learning-failed", "learning", "failed")],
        "followup_rows": [alert("failed-main", "validation", "completed")],
    }
    monkeypatch.setattr(cli, "collect", lambda *args, **kwargs: original)
    output = tmp_path / "effect"
    args = ["--database", str(database), "--output-dir", str(output)]
    if exclude_failed:
        args.append("--exclude-failed")
    before = database.read_bytes()
    assert cli.main(args) == 0
    result = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert result["metrics"]["all"]["selected"] == (2 if exclude_failed else 4)
    assert result["metrics"]["all"]["tokens"]["total_tokens"]["known_sum"] == (
        20 if exclude_failed else 40
    )
    assert result["metrics"]["all"]["semantic_review_status_counts"]["failed"] == 1
    assert any(row["alert_id"] == "learning-failed" for row in result["alerts"])
    assert len(result["followups"]) == 1
    assert len(original["validation_rows"]) == 4
    if exclude_failed:
        scope = result["metadata"]["evaluation_scope"]
        assert scope["original_validation_count"] == 4
        assert scope["included_validation_count"] == 2
        assert scope["excluded_validation_count"] == 2
        assert scope["excluded_by_tier"] == {
            "main": 1,
            "supplementary": 1,
            "unknown": 0,
        }
        assert {row["alert_id"] for row in scope["excluded_alerts"]} == {
            "failed-main",
            "failed-other",
        }
        assert not any(
            row["code"] == "execution_failed" for row in result["differences"]
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert "excluded_alerts.csv" in manifest["files"]
    else:
        assert "evaluation_scope" not in result["metadata"]
        assert not (output / "excluded_alerts.csv").exists()
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "option,value",
    [("--query-timeout", "0"), ("--query-timeout", "61"), ("--groups-per-batch", "0")],
)
def test_invalid_limits_do_not_read_database(option, value, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "collect", lambda *args, **kwargs: pytest.fail("invalid bounds")
    )
    assert cli.main([option, value, "--root", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def label_fixture(tmp_path, **changes):
    case = {
        "alert_id": "a",
        "payload_hash": "p",
        "group_id": "g",
        "operational_label_available": True,
        "operational_label": "转交",
        "label_temporal_status": "valid",
        **changes,
    }
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8"
    )
    data = {
        "metadata": {
            "source_identity": {
                "index": {"sha256": hashlib.sha256(index.read_bytes()).hexdigest()}
            }
        },
        "members": [{"alert_id": "a", "payload_hash": "p", "group_id": "g"}],
        "validation_rows": [
            {
                "alert_id": "a",
                "summary": {"decision_usable": True, "recommended_handling": "ignore"},
            }
        ],
        "warnings": [],
    }
    return data, index


def test_labels_require_frozen_hash_and_member_identity(tmp_path):
    data, index = label_fixture(tmp_path)
    cli.enrich_labels(data, index)
    assert data["validation_rows"][0]["label"]["expected_handling"] == "transfer"
    assert data["validation_rows"][0]["label"]["scorable"] is True
    assert data["metadata"]["label_source"]["verified"] is True


@pytest.mark.parametrize(
    "changes", [{"payload_hash": "another"}, {"group_id": "another"}]
)
def test_label_member_drift_rejected(tmp_path, changes):
    data, index = label_fixture(tmp_path, **changes)
    cli.enrich_labels(data, index)
    assert "label" not in data["validation_rows"][0]
    assert data["metadata"]["label_source"]["verified"] is False
    assert data["warnings"]


def test_index_wrong_hash_cannot_supply_labels(tmp_path):
    data, index = label_fixture(tmp_path)
    index.write_text('{"cases": []}', encoding="utf-8")
    cli.enrich_labels(data, index)
    assert "label" not in data["validation_rows"][0]
    assert data["metadata"]["label_source"]["verified"] is False


def test_invalid_temporal_label_not_scorable(tmp_path):
    data, index = label_fixture(tmp_path, label_temporal_status="label_precedes_alert")
    cli.enrich_labels(data, index)
    assert data["validation_rows"][0]["label"]["scorable"] is False
    assert data["validation_rows"][0]["label"]["expected_handling"] is None
