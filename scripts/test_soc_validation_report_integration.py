"""Real schema, synthetic data, independently copied stdlib reporting tool."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event

# Only the fixture imports the application schema. The independent CLI child
# clears PYTHONPATH and disables site initialization below.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from soc_agent.db.base import create_soc_tables
from soc_agent.db.models import (
    SocAnalysisRunRow,
    SocCorpusExperimentMemberRow,
    SocCorpusExperimentRow,
    SocCorpusRoundItemRow,
    SocCorpusRoundRow,
    SocMemoryCandidateRow,
    SocMemoryPatternObservationRow,
    SocMemoryRecordRow,
    SocMemoryUseRow,
    SocProcessingJobRow,
)

TOOLS = (
    "soc_pingan_validation_report.py",
    "soc_validation_report_data.py",
    "soc_validation_report_render.py",
)
BASE = datetime(2026, 9, 1, tzinfo=UTC)
SECRET = "SYNTHETIC_RAW_PAYLOAD_MUST_NOT_BE_EXPORTED"
OPTIONS = {
    "normalization_review_mode": "apply",
    "refresh_normalization": False,
    "tenant_policy_enabled": False,
    "tenant_policy_advisor_enabled": False,
    "tenant_policy_signal_providers_enabled": False,
}


def _insert(conn, model, **values):
    conn.execute(model.__table__.insert().values(**values))


def _seed_job(
    conn,
    round_id,
    alert,
    sequence,
    created,
    *,
    status="completed",
    tokens=None,
    memory=False,
):
    job_id = f"job-{round_id}-{alert}"
    run_id = f"run-{round_id}-{alert}"
    done = created + timedelta(hours=1)
    summary = {
        "analysis_status": status,
        "effective_verdict": "false_positive" if memory else "suspicious",
        "recommended_handling": "ignore" if memory else "transfer",
        "decision_usable": status == "completed",
        "processing_path": "memory" if memory else "model",
        "base_model_evaluated": not memory,
        "memory_uses": [],
        "measurements": {},
    }
    _insert(
        conn,
        SocCorpusRoundItemRow,
        round_id=round_id,
        alert_id=alert,
        group_id={"L1": "g1", "L2": "g2", "V1": "g1", "V2": "g2", "V3": "g3"}[alert],
        sequence_number=sequence,
        job_id=job_id,
    )
    _insert(
        conn,
        SocProcessingJobRow,
        job_id=job_id,
        tenant_id="pingan",
        workload_kind="soc_corpus",
        queue_name="soc_corpus",
        status=status,
        idempotency_key=job_id,
        submission_sha256="1" * 64,
        payload_sha256="2" * 64,
        alert_id=alert,
        priority=0,
        input_payload={"raw": SECRET},
        metadata_payload={},
        attempt_count=1,
        version=1,
        available_at=created,
        created_at=created,
        updated_at=done,
        started_at=created,
        completed_at=done,
        run_id=run_id,
        result_payload={"summary": summary, "snapshot_changed_during_run": False},
    )
    _insert(
        conn,
        SocAnalysisRunRow,
        run_id=run_id,
        alert_id=alert,
        status=status,
        input_hash=hashlib.sha256(alert.encode()).hexdigest(),
        pipeline_version="synthetic",
        model_name="no-model-called",
        prompt_version="synthetic",
        started_at=created,
        ended_at=done,
        created_at=created,
        updated_at=done,
        total_duration_ms=100 if status == "completed" else None,
        total_tokens=tokens,
        usage_measurement_status="reported" if tokens is not None else "unavailable",
        input_payload={"raw": SECRET},
        run_payload={
            "raw_evidence": SECRET,
            "normalization_assistance": {
                "status": "applied",
                "mode": "apply",
                "changes": [{"private_value": SECRET}],
                "observation_changes": [],
                "issues": [],
            },
        },
    )
    return run_id


@pytest.fixture
def real_schema_database(tmp_path):
    """Use all current ORM tables, with the deployed 0032 revision marker."""
    path = tmp_path / "database" / "soc_agent_dev.db"
    path.parent.mkdir()
    engine = create_engine(f"sqlite+pysqlite:///{path}")

    @event.listens_for(engine, "connect")
    def fixture_only_fast_schema(dbapi_connection, _record):
        # Only the disposable fixture writer skips fsync. The CLI opens its own
        # normal read-only sqlite3 connection after this engine is disposed.
        dbapi_connection.execute("PRAGMA synchronous=OFF")

    create_soc_tables(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE soc_alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO soc_alembic_version VALUES ('0032_corpus_revision_index')"
        )
        _insert(
            conn,
            SocCorpusExperimentRow,
            experiment_id="EXP-synthetic",
            plan_id="a" * 64,
            manifest_hash="b" * 64,
            created_at=BASE,
            record_payload={"private": SECRET},
        )
        for sequence, (alert, group, batch, tier) in enumerate(
            [
                ("L1", "g1", "learning", None),
                ("L2", "g2", "learning", None),
                ("V1", "g1", "validation", "main"),
                ("V2", "g2", "validation", "main"),
                ("V3", "g3", "validation", "supplementary"),
            ]
        ):
            _insert(
                conn,
                SocCorpusExperimentMemberRow,
                experiment_id="EXP-synthetic",
                alert_id=alert,
                group_id=group,
                batch=batch,
                validation_tier=tier,
                rule_code="synthetic-rule",
                sequence_number=sequence,
                record_payload={
                    "detection_key": f"detection-{alert}",
                    "payload_hash": hashlib.sha256(alert.encode()).hexdigest(),
                },
            )
        snapshot = [
            {
                "memory_id": "MEM-shared",
                "version": 1,
                "content_hash": "c" * 64,
                "facets_hash": "d" * 64,
                "record_hash": "e" * 64,
            }
        ]
        for round_id, batch, day, selected, superseded in [
            ("ROUND-learning", "learning", 1, [], None),
            ("ROUND-old", "validation", 3, [], "ROUND-full"),
            ("ROUND-full", "validation", 4, [], None),
            ("ROUND-single", "validation", 6, ["V1"], None),
        ]:
            payload = {
                "selection": {
                    "batch": batch,
                    "scope": "reuse" if batch == "learning" else "all",
                    "alert_ids": selected,
                    "group_ids": [],
                    "rule_codes": [],
                },
                "options": OPTIONS,
                "memory_mode": "read_only",
                "config_hash": "f" * 64,
                "memory_snapshot": snapshot if batch == "validation" else [],
            }
            if superseded:
                payload["superseded_by_round_id"] = superseded
            _insert(
                conn,
                SocCorpusRoundRow,
                round_id=round_id,
                experiment_id="EXP-synthetic",
                batch=batch,
                state="completed",
                version=1,
                created_at=BASE + timedelta(days=day),
                record_payload=payload,
            )
            alerts = (
                ["L1", "L2"] if batch == "learning" else selected or ["V1", "V2", "V3"]
            )
            for index, alert in enumerate(alerts):
                _seed_job(
                    conn,
                    round_id,
                    alert,
                    index,
                    BASE + timedelta(days=day),
                    status="failed" if alert == "V3" else "completed",
                    tokens=(12 if alert == "V1" else 23)
                    if round_id == "ROUND-full" and alert != "V3"
                    else 999,
                    memory=round_id == "ROUND-full" and alert == "V1",
                )
        for candidate_id, source_alert, status, update_day in [
            ("C-shared", "L1", "confirmed", 2),
            ("C-rejected", "L1", "rejected", 2),
            ("C-later-review", "L2", "confirmed", 7),
        ]:
            _insert(
                conn,
                SocMemoryCandidateRow,
                candidate_id=candidate_id,
                candidate_type="triage_pattern",
                target_artifact="memory",
                status=status,
                tenant_scope="tenant",
                tenant_id="pingan",
                source_type="runtime",
                source_run_id=f"run-ROUND-learning-{source_alert}",
                source_alert_id=source_alert,
                confidence=0.8,
                decision_impact="advisory",
                runtime_decision_allowed=False,
                review_required=True,
                summary=SECRET,
                content=SECRET,
                created_at=BASE + timedelta(days=2),
                updated_at=BASE + timedelta(days=update_day),
                reviewed_at=BASE + timedelta(days=update_day),
                candidate_payload={
                    "metadata": {
                        "experiment_id": "EXP-synthetic",
                        "observation_ids": ["OBS-L2"]
                        if candidate_id == "C-shared"
                        else [],
                    },
                    "review_reason": "审核人决定放弃沉淀该候选，未形成可复用 Memory。"
                    if status == "rejected"
                    else SECRET,
                },
            )
        _insert(
            conn,
            SocMemoryPatternObservationRow,
            observation_id="OBS-L2",
            idempotency_key="obs-l2",
            aggregation_key="g" * 64,
            lineage_key="h" * 64,
            content_hash="i" * 64,
            tenant_id="pingan",
            environment="dev",
            data_class="synthetic",
            source_type="runtime",
            source_id="L2",
            run_id="run-ROUND-learning-L2",
            alert_id="L2",
            pattern_dimension="behavior",
            pattern_value="synthetic",
            mocked=True,
            observed_at=BASE,
            window_start=BASE,
            window_end=BASE + timedelta(days=2),
            created_at=BASE + timedelta(days=2),
            observation_payload={"private": SECRET},
        )
        _insert(
            conn,
            SocMemoryRecordRow,
            memory_id="MEM-shared",
            version=1,
            memory_type="triage_pattern",
            target_artifact="memory",
            status="confirmed",
            tenant_scope="tenant",
            tenant_id="pingan",
            source_candidate_id="C-shared",
            source_type="runtime",
            content_hash="c" * 64,
            facets_hash="d" * 64,
            retrieval_enabled=True,
            confidence=0.8,
            created_by_actor_id="synthetic-reviewer",
            summary=SECRET,
            content=SECRET,
            created_at=BASE + timedelta(days=2),
            updated_at=BASE + timedelta(days=2),
            record_payload={"reviewed_verdict": "false_positive", "raw": SECRET},
        )
        for alert, directive in [("V1", True), ("V2", False)]:
            _insert(
                conn,
                SocMemoryUseRow,
                use_id=f"use-{alert}",
                idempotency_key=f"use-{alert}",
                memory_id="MEM-shared",
                memory_version=1,
                run_id=f"run-ROUND-full-{alert}",
                alert_id=alert,
                tenant_id="pingan",
                effect="direct_reused" if directive else "context_only",
                directive_applied=directive,
                created_at=BASE + timedelta(days=4),
                use_payload={"private": SECRET},
            )
    engine.dispose()
    return path


def _run_standalone(database, tmp_path, *, corpus_index=None):
    tool = tmp_path / "standalone-tool"
    tool.mkdir()
    for filename in TOOLS:
        shutil.copyfile(Path(__file__).with_name(filename), tool / filename)
    installed = tmp_path / "installed-checkout"
    (installed / "scripts").mkdir(parents=True)
    for filename in TOOLS:
        (installed / "scripts" / filename).write_text(
            "raise AssertionError('target application must not be imported')\n",
            encoding="utf-8",
        )
    if corpus_index is not None:
        index = (
            installed
            / "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.workbench-index.json"
        )
        index.parent.mkdir(parents=True)
        index.write_bytes(corpus_index)
    output = tmp_path / "export"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(tool / TOOLS[0]),
            "--root",
            str(installed),
            "--database",
            str(database),
            "--output-dir",
            str(output),
            "--groups-per-batch",
            "2",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result, output, tool


def test_real_0032_standalone_report_preserves_source_and_fixed_round(
    real_schema_database, tmp_path
):
    database = real_schema_database
    before = database.read_bytes()
    siblings_before = {p.name for p in database.parent.iterdir()}
    result, output, tool = _run_standalone(database, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert database.read_bytes() == before
    assert {p.name for p in database.parent.iterdir()} == siblings_before
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["metadata"]["round_id"] == "ROUND-full"
    assert report["metadata"]["source_schema_revision"] == [
        "0032_corpus_revision_index"
    ]
    main = report["metrics"]["main"]
    assert main["selected"] == 2
    assert report["metrics"]["supplementary"]["status_counts"] == {"failed": 1}
    assert main["memory_usage"] == {
        "directive": 1,
        "reference": 1,
        "none": 0,
        "unknown": 0,
    }
    assert main["direct_model_skip"]["numerator"] == 1
    assert main["tokens"]["total_tokens"]["known_sum"] == 35
    assert main["historical_handling_agreement"]["denominator"] == 0
    assert main["historical_handling_agreement"]["rate"] is None
    first_validation = next(row for row in report["alerts"] if row["alert_id"] == "V1")
    assert first_validation["semantic_review_status"] == "applied"
    assert first_validation["semantic_review_change_count"] == 1
    assert len(report["followups"]) == 1
    assert report["followups"][0]["round_id"] == "ROUND-single"
    candidates = {row["candidate_id"]: row for row in report["candidates"]}
    assert candidates["C-shared"]["group_ids"] == ["g1", "g2"]
    assert candidates["C-rejected"]["as_of_status"] == "rejected"
    assert candidates["C-rejected"]["reason_recorded"] is False
    assert candidates["C-later-review"]["as_of_status"] == "unknown"
    assert report["memories"][0]["source_group_ids"] == ["g1", "g2"]
    assert report["memories"][0]["validation_alert_count"] == 2
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["read_only"] is True and manifest["model_calls"] == 0
    assert manifest["round_id"] == "ROUND-full"
    assert set(manifest["tool_files_sha256"]) == set(TOOLS)
    for filename, digest in manifest["tool_files_sha256"].items():
        assert digest == hashlib.sha256((tool / filename).read_bytes()).hexdigest()
    for filename, item in manifest["files"].items():
        assert (
            item["sha256"]
            == hashlib.sha256((output / filename).read_bytes()).hexdigest()
        )
    assert all(SECRET not in p.read_text(encoding="utf-8") for p in output.iterdir())


def test_real_0032_unfinished_latest_full_round_never_falls_back(
    real_schema_database, tmp_path
):
    with sqlite3.connect(real_schema_database) as conn:
        conn.execute(
            "UPDATE soc_processing_jobs SET status='queued', completed_at=NULL WHERE job_id='job-ROUND-full-V1'"
        )
    before = real_schema_database.read_bytes()
    result, output, _tool = _run_standalone(real_schema_database, tmp_path)
    assert result.returncode == 2
    assert "未结束任务" in result.stderr
    assert not output.exists()
    assert real_schema_database.read_bytes() == before


def test_real_0032_default_frozen_json_index_supplies_historical_comparison(
    real_schema_database, tmp_path
):
    cases = [
        {
            "alert_id": alert,
            "group_id": group,
            "payload_hash": hashlib.sha256(alert.encode()).hexdigest(),
            "operational_label_available": True,
            "operational_label": "转交",
            "label_temporal_status": "valid",
            "operational_label_method": "synthetic_review",
        }
        for alert, group in [
            ("L1", "g1"),
            ("L2", "g2"),
            ("V1", "g1"),
            ("V2", "g2"),
            ("V3", "g3"),
        ]
    ]
    index_bytes = json.dumps({"cases": cases}, ensure_ascii=False).encode("utf-8")
    index_hash = hashlib.sha256(index_bytes).hexdigest()
    with sqlite3.connect(real_schema_database) as conn:
        conn.execute(
            "UPDATE soc_corpus_experiments SET record_payload=? WHERE experiment_id='EXP-synthetic'",
            (json.dumps({"source_identity": {"index": {"sha256": index_hash}}}),),
        )
    before = real_schema_database.read_bytes()
    result, output, _tool = _run_standalone(
        real_schema_database, tmp_path, corpus_index=index_bytes
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert real_schema_database.read_bytes() == before
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["metadata"]["label_source"]["verified"] is True
    assert report["metadata"]["label_source"]["sha256"] == index_hash
    assert report["metadata"]["causal_attribution_allowed"] is False
    metric = report["metrics"]["main"]
    assert metric["historical_handling_agreement"] == {
        "numerator": 1,
        "denominator": 2,
        "rate": 0.5,
    }
    assert metric["historical_transfer_now_ignore"] == {
        "numerator": 1,
        "denominator": 2,
        "rate": 0.5,
    }
    assert any(
        item["alert_id"] == "V1" and item["code"] == "historical_transfer_now_ignore"
        for item in report["differences"]
    )
    assert (
        report["metrics"]["supplementary"]["historical_handling_agreement"][
            "denominator"
        ]
        == 0
    )
