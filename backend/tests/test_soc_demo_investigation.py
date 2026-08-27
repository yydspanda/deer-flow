from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.cli import main
from soc_agent.core import SocReviewService
from soc_agent.db import SqlAlchemyAlertRepository


def test_cli_demo_run_seeds_run_scoped_investigation(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'soc_demo.db'}"

    code = main(["demo", "run", "apt", "--database-url", database_url, "--init-db", "--pretty"])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert code == 0
    assert report["schema_version"] == "soc.demo_investigation_report.v1"
    assert report["scenario"] == "apt"
    assert report["sample_count"] == 1
    assert report["failed_count"] == 0

    result = report["results"][0]
    assert result.get("queue_id") is None
    assert result.get("queue_status") is None
    assert result["source_type"] == "ndr"
    assert result["action_count"] == 2
    assert result["evidence_count"] >= 2
    assert result["domain_finding_count"] >= 1
    assert result["relevant_memory_count"] >= 1
    assert result["memory_candidate_id"].startswith("MC-")
    assert result["memory_record_id"].startswith("MEM-")
    assert "read_only_evidence" in result["timeline_kinds"]
    assert "domain_finding" in result["timeline_kinds"]
    assert "relevant_memory" in result["timeline_kinds"]

    repository = SqlAlchemyAlertRepository(
        sessionmaker(
            bind=create_engine(database_url),
            expire_on_commit=False,
        )
    )
    context = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        memory_candidate_repository=repository,
        memory_record_repository=repository,
    ).get_alert_investigation_context(result["run_id"])

    assert context.result.queue_item is None
    assert context.investigation_view is not None
    assert context.investigation_view.counts["action_evidence"] >= 2
    assert context.investigation_view.counts["domain_findings"] >= 1
    assert context.investigation_view.counts["relevant_memories"] >= 1
    assert context.relevant_memories is not None
    assert context.relevant_memories.matches[0].record.summary


def test_cli_demo_run_reuses_existing_sample_chain(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'soc_demo_reuse.db'}"

    first_code = main(["demo", "run", "hids", "--database-url", database_url, "--init-db", "--pretty"])
    first_report = json.loads(capsys.readouterr().out)
    second_code = main(["demo", "run", "hids", "--database-url", database_url, "--pretty"])
    second_report = json.loads(capsys.readouterr().out)

    assert first_code == 0
    assert second_code == 0
    assert second_report["run_ids"] == first_report["run_ids"]
    assert second_report["queue_ids"] == first_report["queue_ids"] == []
    assert all(action["skipped_existing"] for action in second_report["results"][0]["actions"])


def test_cli_boss_demo_builds_isolated_launch_manifest(tmp_path, capsys) -> None:
    database_path = tmp_path / "soc_boss_demo.db"
    database_url = f"sqlite:///{database_path}"

    code = main(
        [
            "demo",
            "boss",
            "--database-url",
            database_url,
            "--reset",
            "--web-base-url",
            "http://localhost:2026/",
            "--pretty",
        ]
    )
    manifest = json.loads(capsys.readouterr().out)

    assert code == 0
    assert database_path.exists()
    assert manifest["schema_version"] == "soc.boss_demo_manifest.v1"
    assert manifest["stage_task_id"] == "BD-01"
    assert manifest["demo_version"] == "v0.1"
    assert manifest["status"] == "ready"
    assert manifest["scenario"] == "apt"
    assert manifest["database_backend"] == "sqlite"
    assert manifest["database_locator"] == str(database_path)
    assert manifest["analyzer"]["mode"] == "stub"
    assert manifest["analyzer"]["silent_fallback_allowed"] is False
    assert manifest["web_url"].startswith("http://localhost:2026/workspace/soc/alerts?run_id=RUN-")

    primary = manifest["primary_investigation"]
    assert primary["run_id"].startswith("RUN-")
    assert primary.get("queue_id") is None
    assert primary["domain_finding_count"] >= 1
    assert primary["action_evidence_count"] >= 2
    assert primary["relevant_memory_count"] >= 1

    boundaries = {item["capability"]: item for item in manifest["capability_boundaries"]}
    assert boundaries["alert_input"]["mode"] == "fixture"
    assert boundaries["read_only_investigation_actions"]["mode"] == "mock"
    assert boundaries["high_risk_response"]["mode"] == "disabled"
    assert boundaries["governed_disposition"]["mode"] == "shadow_only"
    assert primary["run_id"] in manifest["review_context_api_url"]
    assert primary["run_id"] in manifest["launch_commands"]["review_context"]


def test_cli_boss_demo_reset_replaces_only_sqlite_database(tmp_path, capsys) -> None:
    database_path = tmp_path / "soc_boss_demo_reset.db"
    database_url = f"sqlite:///{database_path}"

    first_code = main(["demo", "boss", "--database-url", database_url])
    first_manifest = json.loads(capsys.readouterr().out)
    second_code = main(["demo", "boss", "--database-url", database_url, "--reset"])
    second_manifest = json.loads(capsys.readouterr().out)

    assert first_code == 0
    assert second_code == 0
    assert first_manifest["reset_applied"] is False
    assert second_manifest["reset_applied"] is True
    assert second_manifest["status"] == "ready"
    assert first_manifest["primary_investigation"]["run_id"].startswith("RUN-")
    assert second_manifest["primary_investigation"]["run_id"].startswith("RUN-")
    assert second_manifest["primary_investigation"].get("queue_id") is None
