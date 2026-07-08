from __future__ import annotations

import json

from soc_agent.cli import main


def test_cli_demo_run_seeds_reviewable_investigation(tmp_path, capsys) -> None:
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
    assert result["queue_id"].startswith("REV-")
    assert result["queue_status"] == "open"
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

    context_code = main(["review", "context", result["queue_id"], "--database-url", database_url, "--pretty"])
    context_output = capsys.readouterr()
    context = json.loads(context_output.out)

    assert context_code == 0
    assert context["queue_item"]["queue_id"] == result["queue_id"]
    assert context["investigation_view"]["counts"]["action_evidence"] >= 2
    assert context["investigation_view"]["counts"]["domain_findings"] >= 1
    assert context["investigation_view"]["counts"]["relevant_memories"] >= 1


def test_cli_demo_run_reuses_existing_sample_chain(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'soc_demo_reuse.db'}"

    first_code = main(["demo", "run", "hids", "--database-url", database_url, "--init-db", "--pretty"])
    first_report = json.loads(capsys.readouterr().out)
    second_code = main(["demo", "run", "hids", "--database-url", database_url, "--pretty"])
    second_report = json.loads(capsys.readouterr().out)

    assert first_code == 0
    assert second_code == 0
    assert second_report["run_ids"] == first_report["run_ids"]
    assert second_report["queue_ids"] == first_report["queue_ids"]
    assert all(action["skipped_existing"] for action in second_report["results"][0]["actions"])
