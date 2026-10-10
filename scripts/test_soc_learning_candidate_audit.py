"""Only synthetic SQLite databases; never loads operational alerts or Runtime."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys

import pytest

from scripts import soc_learning_candidate_audit as audit


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "soc_agent_dev.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE soc_corpus_experiments(experiment_id TEXT PRIMARY KEY,plan_id TEXT,created_at TEXT,record_payload TEXT);
            CREATE TABLE soc_corpus_experiment_members(experiment_id TEXT,alert_id TEXT,group_id TEXT,rule_code TEXT,sequence_number INTEGER,batch TEXT);
            CREATE TABLE soc_corpus_rounds(round_id TEXT,experiment_id TEXT,batch TEXT,state TEXT,created_at TEXT,record_payload TEXT);
            CREATE TABLE soc_corpus_round_items(round_id TEXT,alert_id TEXT,job_id TEXT,sequence_number INTEGER);
            CREATE TABLE soc_processing_jobs(job_id TEXT,status TEXT,run_id TEXT,error_code TEXT,completed_at TEXT,updated_at TEXT,result_payload TEXT);
            CREATE TABLE soc_analysis_runs(run_id TEXT,run_payload TEXT,model_name TEXT,prompt_version TEXT);
            CREATE TABLE soc_alert_summaries(run_id TEXT,source_type TEXT,source_system TEXT,detection_key TEXT);
            CREATE TABLE soc_memory_pattern_observations(observation_id TEXT,aggregation_key TEXT,lineage_key TEXT,run_id TEXT,alert_id TEXT,profile_id TEXT,profile_version TEXT,feature_schema_version TEXT,created_at TEXT,observation_payload TEXT);
            CREATE TABLE soc_memory_candidates(candidate_id TEXT,status TEXT,source_type TEXT,source_run_id TEXT,source_alert_id TEXT,created_at TEXT,updated_at TEXT,candidate_payload TEXT);
            CREATE TABLE soc_mutation_audit_log(audit_id TEXT,target_id TEXT,occurred_at TEXT,operation TEXT,record_payload TEXT);
        """)
        conn.execute(
            "INSERT INTO soc_corpus_experiments VALUES (?,?,?,?)",
            (
                "EXP",
                "PLAN",
                "2026-10-09T12:00:00Z",
                json.dumps(
                    {
                        "environment": "dev-corpus-eval",
                        "tenant_id": "pingan",
                        "secret": "NEVER_EXPORT",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO soc_corpus_rounds VALUES (?,?,?,?,?,?)",
            (
                "R1",
                "EXP",
                "learning",
                "completed",
                "2026-10-09T12:01:00Z",
                json.dumps(
                    {
                        "options": {
                            "normalization_review_mode": "apply",
                            "tenant_policy_enabled": False,
                        },
                        "config_hash": "hash",
                        "config_snapshot": {"secret": "NEVER_EXPORT"},
                    }
                ),
            ),
        )
        for index in range(1, 4):
            conn.execute(
                "INSERT INTO soc_corpus_experiment_members VALUES (?,?,?,?,?,?)",
                (
                    "EXP",
                    str(index),
                    "G1" if index < 3 else "G2",
                    "RULE1" if index < 3 else "RULE2",
                    index,
                    "learning",
                ),
            )
            conn.execute(
                "INSERT INTO soc_corpus_round_items VALUES (?,?,?,?)",
                ("R1", str(index), "J" + str(index), index),
            )
            summary = {
                "model_name": "model-v1",
                "prompt_version": "prompt-v47",
                "analysis_status": "success",
                "reasoning": "NEVER_EXPORT",
                "operational_label": "NEVER_EXPORT",
            }
            if index < 3:
                summary.update(
                    pattern_support_count=index,
                    candidate_created=index == 2,
                    pattern_quality_gate=index == 2,
                    pattern_reason_codes=["recurrence_threshold_not_met"]
                    if index == 1
                    else [],
                    actual_pattern={
                        "facets": {
                            "behavior_strength": ["strong"],
                            "behavior_component_core": ["PRIVATE_COMMAND"],
                            "entity": ["PRIVATE_ENTITY"],
                        }
                    },
                )
            payload = {
                "observation_id": "O" + str(index) if index < 3 else None,
                "candidate_id": "C1" if index == 2 else None,
                "pattern_reason": [
                    "observation retained; repeated-pattern thresholds not reached",
                    "one frozen pending repeated-pattern candidate created for human review",
                    "unknown issue NEVER_EXPORT",
                ][index - 1],
                "summary": summary,
            }
            conn.execute(
                "INSERT INTO soc_processing_jobs VALUES (?,?,?,?,?,?,?)",
                (
                    "J" + str(index),
                    "completed",
                    "RUN" + str(index),
                    None,
                    "2026-10-09T12:10:00Z",
                    "2026-10-09T12:10:00Z",
                    json.dumps(payload),
                ),
            )
            conn.execute(
                "INSERT INTO soc_analysis_runs VALUES (?,?,?,?)",
                (
                    "RUN" + str(index),
                    json.dumps(
                        {
                            "normalization_assistance": {
                                "status": "partial" if index == 1 else "applied",
                                "mode": "apply",
                                "changes": ["NEVER_EXPORT"],
                            },
                            "raw": "NEVER_EXPORT",
                        }
                    ),
                    "model-v1",
                    "prompt-v47",
                ),
            )
            conn.execute(
                "INSERT INTO soc_alert_summaries VALUES (?,?,?,?)",
                (
                    "RUN" + str(index),
                    "hids" if index < 3 else "nids",
                    "system",
                    "detection",
                ),
            )
            if index < 3:
                observation = {
                    "accumulation_scope": {"experiment_id": "EXP"},
                    "signature": summary["actual_pattern"],
                    "aggregation_policy": {
                        "minimum_support": 5,
                        "minimum_distinct_sources": 5,
                        "minimum_conclusive_support": 5,
                    },
                    "lesson": {"summary": "NEVER_EXPORT"},
                }
                conn.execute(
                    "INSERT INTO soc_memory_pattern_observations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "O" + str(index),
                        "AGG",
                        "LIN",
                        "RUN" + str(index),
                        str(index),
                        "profile",
                        "2",
                        "features.v3",
                        "2026-10-09T12:10:00Z",
                        json.dumps(observation),
                    ),
                )
                ingest = {
                    "aggregation_key": "AGG",
                    "support_count": index,
                    "distinct_source_count": index,
                    "threshold_met": index == 2,
                    "quality_gate_passed": index == 2,
                    "quality_reason_codes": ["recurrence_threshold_not_met"]
                    if index == 1
                    else [],
                    "candidate_created": index == 2,
                    "candidate_coverage": "current_cohort" if index == 2 else "none",
                    "candidate_id": "C1" if index == 2 else None,
                    "private": "NEVER_EXPORT",
                }
                conn.execute(
                    "INSERT INTO soc_mutation_audit_log VALUES (?,?,?,?,?)",
                    (
                        "AUD" + str(index),
                        "O" + str(index),
                        f"2026-10-09T12:10:0{index}Z",
                        "memory_pattern_observation.ingest",
                        json.dumps({"payload": ingest, "reason": "NEVER_EXPORT"}),
                    ),
                )
        candidate = {
            "metadata": {
                "experiment_id": "EXP",
                "aggregation_key": "AGG",
                "support_count_at_creation": 5,
            },
            "applicability": {"profile_id": "profile", "profile_version": "2"},
            "content": "NEVER_EXPORT",
        }
        conn.execute(
            "INSERT INTO soc_memory_candidates VALUES (?,?,?,?,?,?,?,?)",
            (
                "C1",
                "pending_review",
                "repeated_pattern",
                "RUN1",
                "1",
                "2026-10-09T12:10:02Z",
                "2026-10-09T12:10:02Z",
                json.dumps(candidate),
            ),
        )
        conn.execute(
            "INSERT INTO soc_memory_candidates VALUES (?,?,?,?,?,?,?,?)",
            (
                "UNRELATED",
                "confirmed",
                "manual",
                "UNRELATED_RUN",
                "999",
                "2026-10-01T00:00:00Z",
                "2026-10-01T00:00:00Z",
                "{}",
            ),
        )
    return path


def collect(database):
    return audit.collect(database)


def update(database, sql, values=()):
    with sqlite3.connect(database) as conn:
        conn.execute(sql, values)


def test_summary_counts_fixed_members_candidates_and_saved_reasons(database):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = collect(database)
    summary = result["summary"]
    assert summary["scope"] == {
        "learning_members": 3,
        "learning_groups": 2,
        "round_count": 1,
        "attempt_count": 3,
        "last_attempt_per_member_count": 3,
    }
    assert summary["last_attempt_per_member"]["job_status"] == {"completed": 3}
    assert summary["last_attempt_per_member"]["semantic_status"] == {
        "applied": 2,
        "partial": 1,
    }
    assert summary["last_attempt_per_member"]["source_type"] == {"hids": 2, "nids": 1}
    assert summary["last_attempt_per_member"]["model_name"] == {"model-v1": 3}
    assert summary["observations"]["count"] == 2
    assert summary["observations"]["aggregation_keys"] == 1
    assert summary["observations"]["saved_minimum_support"] == {"5": 2}
    assert summary["candidates_current"]["count"] == 1
    assert summary["candidates_current"]["status"] == {"pending_review": 1}
    assert summary["candidates_current"]["source_alert_type"] == {"hids": 1}
    assert summary["last_saved_ingest_per_aggregation"]["quality_reason_codes"] == {}
    assert summary["last_saved_ingest_per_aggregation"]["quality_gate_passed"] == {
        "1": 1
    }
    assert summary["evidence_gaps"]["last_attempt_without_observation_ref"] == 1
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    text = json.dumps(result)
    for private in (
        "NEVER_EXPORT",
        "PRIVATE_ENTITY",
        "PRIVATE_COMMAND",
        '"operational_label"',
        '"reasoning"',
    ):
        assert private not in text


def test_no_label_comparison_or_inferred_reason_for_missing_observation(database):
    update(
        database,
        "UPDATE soc_processing_jobs SET result_payload=json_remove(result_payload,'$.pattern_reason') WHERE job_id='J3'",
    )
    result = collect(database)
    row = next(row for row in result["alerts"] if row["alert_id"] == "3")
    assert row["pattern_reason_code"] == "unknown_not_recorded"
    assert row["quality_reason_codes"] is None
    assert row["core_component_count"] is None
    assert (
        result["summary"]["last_attempt_per_member"][
            "saved_quality_reason_codes_missing"
        ]
        == 1
    )


def test_zero_core_and_missing_core_are_distinct(database):
    update(
        database,
        "UPDATE soc_memory_pattern_observations SET observation_payload=json_set(observation_payload,'$.signature.facets.behavior_component_core',json('[]')) WHERE observation_id='O2'",
    )
    result = collect(database)
    assert result["summary"]["observations"]["core_component_count"] == {"0": 1, "1": 1}
    assert result["summary"]["last_attempt_per_member"]["core_component_count"] == {
        "1": 2,
        "unknown": 1,
    }


@pytest.mark.parametrize("status", ["running", "pending", "retry_wait", None])
def test_refuses_unfinished_or_missing_jobs(database, status):
    if status is None:
        update(database, "DELETE FROM soc_processing_jobs WHERE job_id='J1'")
    else:
        update(
            database,
            "UPDATE soc_processing_jobs SET status=? WHERE job_id='J1'",
            (status,),
        )
    with pytest.raises(audit.AuditError, match="未结束或缺失"):
        collect(database)


def test_rejects_missing_member_execution(database):
    update(database, "DELETE FROM soc_corpus_round_items WHERE alert_id='3'")
    with pytest.raises(audit.AuditError, match="固定成员"):
        collect(database)


def test_ambiguous_experiment_requires_exact_selector(database):
    update(
        database,
        "INSERT INTO soc_corpus_experiments VALUES ('OTHER','PLAN','2026-10-01','{}')",
    )
    with pytest.raises(audit.AuditError, match="--experiment"):
        collect(database)
    assert (
        audit.collect(database, experiment_id="EXP")["summary"]["experiment"][
            "experiment_id"
        ]
        == "EXP"
    )
    with pytest.raises(audit.AuditError, match="不存在"):
        audit.collect(database, experiment_id="MISSING")


def test_refuses_other_environment(database):
    update(
        database,
        "UPDATE soc_corpus_experiments SET record_payload=json_set(record_payload,'$.environment','stg')",
    )
    with pytest.raises(audit.AuditError, match="仅支持 PingAn DEV"):
        collect(database)


def add_rerun(database, created="2026-10-09T13:00:00Z"):
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO soc_corpus_rounds VALUES ('R2','EXP','learning','completed',?,'{}')",
            (created,),
        )
        conn.execute("INSERT INTO soc_corpus_round_items VALUES ('R2','1','J4',1)")
        conn.execute(
            "INSERT INTO soc_processing_jobs VALUES ('J4','completed','RUN4',NULL,?,?,?)",
            (
                created,
                created,
                json.dumps(
                    {"observation_id": "O1", "summary": {"model_name": "newmodel"}}
                ),
            ),
        )
        conn.execute(
            "INSERT INTO soc_analysis_runs VALUES ('RUN4','{}','newmodel','prompt-v47')"
        )
        conn.execute(
            "INSERT INTO soc_alert_summaries VALUES ('RUN4','hids','system','detection')"
        )


def test_reruns_preserved_without_double_counting_members_or_observations(database):
    add_rerun(database)
    result = collect(database)
    assert result["summary"]["scope"]["attempt_count"] == 4
    assert result["summary"]["scope"]["last_attempt_per_member_count"] == 3
    assert result["summary"]["observations"]["count"] == 2
    assert result["summary"]["candidates_current"]["count"] == 1
    assert (
        next(row for row in result["alerts"] if row["alert_id"] == "1")["run_id"]
        == "RUN4"
    )


def test_tied_round_creation_time_refused(database):
    add_rerun(database, created="2026-10-09T12:01:00Z")
    with pytest.raises(audit.AuditError, match="创建时间相同"):
        collect(database)


def test_tied_audits_do_not_invent_last_quality(database):
    update(
        database, "UPDATE soc_mutation_audit_log SET occurred_at='2026-10-09T12:10:02Z'"
    )
    summary = collect(database)["summary"]["last_saved_ingest_per_aggregation"]
    assert summary["tied_latest_audit_count"] == 1
    assert summary["quality_gate_passed"] == {"unknown": 1}


def test_missing_refs_recorded_as_evidence_gaps(database):
    update(database, "DELETE FROM soc_memory_candidates WHERE candidate_id='C1'")
    update(
        database,
        "DELETE FROM soc_memory_pattern_observations WHERE observation_id='O2'",
    )
    update(database, "DELETE FROM soc_analysis_runs WHERE run_id='RUN3'")
    gaps = collect(database)["summary"]["evidence_gaps"]
    assert gaps["candidate_refs_without_record"] == 1
    assert gaps["observation_refs_without_record"] == 1
    assert gaps["run_refs_without_record"] == 1


def test_paging_retains_all_rows_and_closes_read_cursors(database, monkeypatch):
    monkeypatch.setattr(audit, "PAGE_SIZE", 1)
    result = collect(database)
    assert result["summary"]["scope"]["attempt_count"] == 3
    assert result["summary"]["observations"]["count"] == 2


def test_concurrent_commit_rejects_entire_collection(database, monkeypatch):
    original = audit.Reader.read
    changed = False

    def read(reader, sql, params=()):
        nonlocal changed
        rows = original(reader, sql, params)
        if not changed and "soc_corpus_rounds" in sql:
            changed = True
            update(
                database,
                "UPDATE soc_memory_candidates SET status='confirmed' WHERE candidate_id='C1'",
            )
        return rows

    monkeypatch.setattr(audit.Reader, "read", read)
    with pytest.raises(audit.AuditError, match="发生变化"):
        collect(database)


def test_missing_database_never_created(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(audit.AuditError, match="不存在"):
        collect(path)
    assert not path.exists()


def test_private_output_new_directory_hash_manifest_and_csv_escape(database, tmp_path):
    result = collect(database)
    result["candidates"][0]["candidate_id"] = "=DANGEROUS()"
    output = tmp_path / "audit"
    summary = audit.write_report(result, output)
    assert summary.name == "summary.json"
    assert output.stat().st_mode & 0o777 == 0o700
    manifest = json.loads((output / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected
        assert (output / name).stat().st_mode & 0o777 == 0o600
    assert "'=DANGEROUS()" in (output / "candidates.csv").read_text()
    with pytest.raises(FileExistsError):
        audit.write_report(result, output)


def test_standalone_cli_has_no_checkout_imports_or_mutations(database, tmp_path):
    target = tmp_path / "fake checkout"
    destination = target / "backend/.deer-flow/data/soc_agent_dev.db"
    destination.parent.mkdir(parents=True)
    shutil.copyfile(database, destination)
    standalone = tmp_path / "tool.py"
    shutil.copyfile(audit.__file__, standalone)
    before = hashlib.sha256(destination.read_bytes()).hexdigest()
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(standalone),
            "--root",
            str(target),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["learning_members"] == 3
    assert receipt["candidates_current"]["status"] == {"pending_review": 1}
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == before


def test_saved_projection_audit_is_optional_and_only_codes_exported(database):
    saved = {
        "llm_analysis_request": {
            "memory_profile": {"profile_version": "3"},
            "memory_projection_audit": {
                "projection_gaps": ["command_missing"],
                "usages": [
                    {
                        "status": "used",
                        "canonical_path": "private",
                        "components": ["NEVER_EXPORT"],
                    }
                ],
            },
        }
    }
    update(
        database,
        "UPDATE soc_analysis_runs SET run_payload=? WHERE run_id='RUN1'",
        (json.dumps(saved),),
    )
    result = collect(database)
    final = result["summary"]["last_attempt_per_member"]
    assert final["projection_audit_present"] == 1
    assert final["saved_projection_gaps"] == {"command_missing": 1}
    assert final["projection_profile_versions"] == {"3": 1, "unknown": 2}
    assert "NEVER_EXPORT" not in json.dumps(result)


def test_queries_work_against_actual_orm_schema(database, tmp_path, monkeypatch):
    """Create only a disposable ORM schema; no engine service or Runtime instance."""
    from pathlib import Path

    from sqlalchemy import Boolean, DateTime, Float, Integer, JSON
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateTable

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from soc_agent.db.models import SocBase

    target = tmp_path / "actual-schema.db"
    with sqlite3.connect(database) as fixture, sqlite3.connect(target) as conn:
        fixture.row_factory = sqlite3.Row
        names = [
            row[0]
            for row in fixture.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        for name in names:
            table = SocBase.metadata.tables[name]
            conn.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
            for number, source in enumerate(fixture.execute(f'SELECT * FROM "{name}"')):
                values = dict(source)
                for column in table.columns:
                    if (
                        column.name in values
                        or column.nullable
                        or column.server_default is not None
                    ):
                        continue
                    if isinstance(column.type, (Integer, Boolean)):
                        value = number + 1
                    elif isinstance(column.type, Float):
                        value = 0.5
                    elif isinstance(column.type, DateTime):
                        value = "2026-10-09T12:00:00Z"
                    elif isinstance(column.type, JSON):
                        value = "{}"
                    else:
                        value = f"synthetic-{number}-{column.name}"
                    values[column.name] = value
                columns = ",".join(f'"{name}"' for name in values)
                marks = ",".join("?" for _ in values)
                conn.execute(
                    f'INSERT INTO "{table.name}" ({columns}) VALUES ({marks})',
                    tuple(values.values()),
                )
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    result = collect(target)
    assert result["summary"]["candidates_current"]["count"] == 1
    assert result["summary"]["last_attempt_per_member"]["job_status"] == {
        "completed": 3
    }
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("timeout", [0, -1, 61])
def test_invalid_timeout_rejected(database, timeout):
    with pytest.raises(audit.AuditError, match="query-timeout"):
        audit.collect(database, query_timeout=timeout)
