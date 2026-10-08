"""Synthetic, read-only evidence export; no operational stores or providers."""

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import pytest

from scripts import soc_memory_snapshot_audit as audit


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "soc.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE soc_corpus_rounds(round_id TEXT,experiment_id TEXT,batch TEXT,created_at TEXT,record_payload TEXT);
            CREATE TABLE soc_corpus_experiments(experiment_id TEXT,record_payload TEXT);
            CREATE TABLE soc_memory_candidates(candidate_id TEXT,source_run_id TEXT,created_at TEXT,updated_at TEXT,candidate_payload TEXT);
            CREATE TABLE soc_memory_records(memory_id TEXT,source_candidate_id TEXT,created_at TEXT,updated_at TEXT,record_payload TEXT);
            CREATE TABLE soc_corpus_round_items(round_id TEXT,job_id TEXT,alert_id TEXT);
            CREATE TABLE soc_processing_jobs(job_id TEXT,run_id TEXT);
            CREATE TABLE soc_mutation_audit_log(audit_id TEXT,target_id TEXT,result_ref TEXT,occurred_at TEXT,record_payload TEXT);
            CREATE TABLE soc_memory_revision_proposals(proposal_id TEXT,memory_id TEXT,created_at TEXT,proposal_payload TEXT);
        """)
        conn.execute(
            "INSERT INTO soc_corpus_rounds VALUES (?,?,?,?,?)",
            (
                "ROUND",
                "EXP",
                "validation",
                "2026-09-22T12:00:00Z",
                json.dumps(
                    {
                        "memory_mode": "snapshot",
                        "memory_snapshot": [],
                        "config_snapshot": {"api_key": "SECRET"},
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO soc_corpus_experiments VALUES (?,?)",
            (
                "EXP",
                json.dumps(
                    {
                        "tenant_id": "tenant",
                        "environment": "dev",
                        "source_identity": {
                            "memory_profile": {
                                "profile_id": "profile",
                                "profile_version": "7",
                            }
                        },
                    }
                ),
            ),
        )
        candidate = {
            "candidate_id": "C",
            "status": "confirmed",
            "reviewed_at": "2026-09-20T12:00:00Z",
            "metadata": {"experiment_id": "EXP", "secret": "SECRET"},
            "source": {"run_id": "RUN", "metadata": {}},
            "content": "RAW_LOG_SECRET",
        }
        conn.execute(
            "INSERT INTO soc_memory_candidates VALUES (?,?,?,?,?)",
            (
                "C",
                "RUN",
                "2026-09-18T12:00:00Z",
                "2026-09-20T12:00:00Z",
                json.dumps(candidate),
            ),
        )
        record = {
            "memory_id": "M",
            "version": 6,
            "source_candidate_id": "C",
            "tenant_id": "tenant",
            "status": "confirmed",
            "retrieval_enabled": False,
            "validity": {
                "valid_from": "2026-09-18T12:00:00Z",
                "notes": "RAW_LOG_SECRET",
            },
            "content": "RAW_LOG_SECRET",
            "metadata": {"api_key": "SECRET"},
            "facets": {"environment": ["dev"], "entity": ["PRIVATE_ENTITY"]},
            "applicability": {
                "profile_id": "profile",
                "profile_version": "7",
                "feature_schema_version": "v5",
            },
        }
        conn.execute(
            "INSERT INTO soc_memory_records VALUES (?,?,?,?,?)",
            (
                "M",
                "C",
                "2026-09-20T12:00:00Z",
                "2026-09-20T12:00:00Z",
                json.dumps(record),
            ),
        )
    return path


def collect(database):
    return audit.collect(database, round_id="ROUND", memory_id="M", candidate_id="C")


def test_record_timestamps_are_evidence_but_not_proof_of_snapshot_state(database):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = collect(database)
    assert result["snapshot"]["target_entries"] == []
    assert (
        result["memory"]["as_of"]["status"]
        == "current_record_timestamps_precede_round_time"
    )
    assert result["memory"]["as_of"]["actual_snapshot_state"] == "unknown"
    assert result["memory"]["current"]["retrieval_enabled"] is False
    assert (
        result["candidate"]["as_of"]["status"]
        == "current_record_timestamps_precede_round_time"
    )
    assert result["learning_scope_current"]["candidate_metadata_matches"] is True
    assert "SECRET" not in json.dumps(result)
    assert "PRIVATE_ENTITY" not in json.dumps(result)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_later_disabled_state_does_not_prove_disabled_at_freeze(database):
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE soc_memory_records SET updated_at='2026-09-24T12:00:00Z'")
        payload = {
            "operation": "memory.retrieval_activation",
            "target_id": "M",
            "result_status": "succeeded",
            "payload": {
                "action": "disable",
                "previous_retrieval_enabled": True,
                "retrieval_enabled": False,
                "result_record_version": 6,
                "api_key": "SECRET",
            },
            "reason": "RAW_LOG_SECRET",
        }
        conn.execute(
            "INSERT INTO soc_mutation_audit_log VALUES (?,?,?,?,?)",
            ("A", "M", "M:v6", "2026-09-24T12:00:00Z", json.dumps(payload)),
        )
    result = collect(database)
    assert result["memory"]["as_of"]["status"] == "unknown_current_record_changed"
    assert result["audits"][0]["relative_to_round_created_at"] == "after"
    assert result["audits"][0]["payload"]["previous_retrieval_enabled"] is True
    assert "SECRET" not in json.dumps(result)


def test_revision_review_audit_is_collected_by_proposal_identity(database):
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO soc_memory_revision_proposals VALUES (?,?,?,?)",
            (
                "REV",
                "M",
                "2026-09-21T12:00:00Z",
                json.dumps(
                    {
                        "status": "approved",
                        "memory_version": 5,
                        "reviewed_at": "2026-09-24T12:00:00Z",
                        "proposed_content": "SECRET",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO soc_mutation_audit_log VALUES (?,?,?,?,?)",
            (
                "A",
                "REV",
                "REV:approved",
                "2026-09-24T12:00:00Z",
                json.dumps(
                    {
                        "operation": "memory.revision_review",
                        "result_status": "succeeded",
                        "payload": {
                            "decision": "approve",
                            "result_record_version": 6,
                            "content": "SECRET",
                        },
                    }
                ),
            ),
        )
    result = collect(database)
    proposal = result["revision_proposals"][0]
    assert proposal["reviewed_at"] == "2026-09-24T12:00:00Z"
    assert proposal["temporal_scope"] == "current_record"
    assert result["audits"][0]["target_id"] == "REV"
    assert result["audits"][0]["relative_to_round_created_at"] == "after"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"memory_mode": "snapshot"},
        {"memory_mode": "snapshot", "memory_snapshot": None},
        {"memory_mode": "snapshot", "memory_snapshot": "[]"},
        {"memory_mode": "snapshot", "memory_snapshot": 1},
    ],
)
def test_missing_or_null_snapshot_is_not_a_complete_empty_snapshot(database, payload):
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE soc_corpus_rounds SET record_payload=?", (json.dumps(payload),)
        )
    with pytest.raises(ValueError, match="快照"):
        collect(database)


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"version": 1},
        {"memory_id": "M"},
        {"memory_id": "M", "version": True},
        {"memory_id": "M", "version": 0},
    ],
)
def test_incomplete_snapshot_entry_is_not_treated_as_complete_membership(
    database, entry
):
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE soc_corpus_rounds SET record_payload=?",
            (json.dumps({"memory_mode": "snapshot", "memory_snapshot": [entry]}),),
        )
    with pytest.raises(ValueError, match="快照"):
        collect(database)


def test_reader_row_limit_rejects_instead_of_silently_truncating():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="2000"):
            audit.Reader(conn, 30).read(
                "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<2001) SELECT x FROM n"
            )


def test_reader_timeout_is_bounded_and_clears_progress_handler(monkeypatch):
    calls = iter([0.0, 1.0])
    monkeypatch.setattr(audit.time, "monotonic", lambda: next(calls, 1.0))
    sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<10000) SELECT sum(x) FROM n"
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            audit.Reader(conn, 0.5).read(sql)
        assert conn.execute(sql).fetchone()[0] == 50005000


def test_missing_selector_definition_is_reported_as_partial(tmp_path):
    path = tmp_path / "backend/soc_agent/demo/corpus_experiments.py"
    path.parent.mkdir(parents=True)
    path.write_text("def create_round():\n    return []\n", encoding="utf-8")
    result = audit.selector_sources(tmp_path)
    assert result[0]["status"] == "partial"
    assert result[0]["missing_definitions"] == ["_eligible"]
    assert result[1]["status"] == "unavailable"


def test_snapshot_checks_all_versions_and_exports_link_branches(database):
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE soc_corpus_rounds SET record_payload=?",
            (
                json.dumps(
                    {
                        "memory_mode": "snapshot",
                        "memory_snapshot": [{"memory_id": "M", "version": 2}],
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO soc_corpus_rounds VALUES (?,?,?,?,?)",
            ("LEARN", "EXP", "learning", "2026-09-19T12:00:00Z", "{}"),
        )
        conn.execute(
            "INSERT INTO soc_corpus_round_items VALUES ('LEARN','JOB','ALERT')"
        )
        conn.execute("INSERT INTO soc_processing_jobs VALUES ('JOB','RUN')")
    result = collect(database)
    assert result["snapshot"]["target_entries"][0]["version"] == 2
    assert result["memory"]["current"]["version"] == 6
    assert (
        result["learning_scope_current"]["source_run_links"][0]["round_id"] == "LEARN"
    )


def test_missing_round_fails_without_creating_database_or_output(tmp_path, database):
    missing = tmp_path / "missing.db"
    assert (
        audit.main(
            [
                "--database",
                str(missing),
                "--round",
                "ROUND",
                "--memory",
                "M",
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )
        == 2
    )
    assert not missing.exists()
    with pytest.raises(ValueError, match="轮次"):
        audit.collect(database, round_id="OTHER", memory_id="M")
    assert not (tmp_path / "output").exists()


def test_current_source_link_mismatch_is_not_historical_root_cause(database):
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE soc_memory_candidates SET candidate_payload=?",
            (
                json.dumps(
                    {
                        "candidate_id": "C",
                        "status": "confirmed",
                        "source": {"metadata": {"experiment_id": "OTHER"}},
                    }
                ),
            ),
        )
    result = collect(database)
    scope = result["learning_scope_current"]
    assert scope["candidate_metadata_matches"] is False
    assert scope["source_metadata_matches"] is False
    assert scope["source_run_links"] == []
    assert result["historical_exclusion_reason"] == "not_inferred"


def test_source_metadata_branch_is_distinct_from_candidate_metadata(database):
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE soc_memory_candidates SET candidate_payload=?",
            (
                json.dumps(
                    {
                        "candidate_id": "C",
                        "source": {"metadata": {"experiment_id": "EXP"}},
                    }
                ),
            ),
        )
    scope = collect(database)["learning_scope_current"]
    assert scope["source_metadata_matches"] is True
    assert scope["candidate_metadata_matches"] is False


def test_reader_enforces_query_only_without_a_long_transaction(database, monkeypatch):
    original = audit.Reader.read

    def read(self, sql, params=()):
        assert self.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert not self.conn.in_transaction
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            self.conn.execute("DELETE FROM soc_memory_records")
        return original(self, sql, params)

    monkeypatch.setattr(audit.Reader, "read", read)
    collect(database)


def test_external_commit_during_collection_rejects_result(database, monkeypatch):
    original = audit.Reader.read
    changed = False

    def read(self, sql, params=()):
        nonlocal changed
        result = original(self, sql, params)
        if "soc_memory_records" in sql and not changed:
            changed = True
            with sqlite3.connect(database) as conn:
                conn.execute(
                    "UPDATE soc_memory_records SET updated_at='2026-09-25T12:00:00Z'"
                )
        return result

    monkeypatch.setattr(audit.Reader, "read", read)
    with pytest.raises(ValueError, match="变化"):
        collect(database)


def test_standalone_private_export_and_no_overwrite(database, tmp_path):
    standalone = tmp_path / "standalone.py"
    shutil.copyfile(audit.__file__, standalone)
    output = tmp_path / "evidence"
    args = [
        sys.executable,
        "-I",
        str(standalone),
        "--database",
        str(database),
        "--round",
        "ROUND",
        "--memory",
        "M",
        "--candidate",
        "C",
        "--output-dir",
        str(output),
    ]
    result = subprocess.run(args, cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["read_only"] is True and manifest["model_calls"] == 0
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    assert (
        manifest["evidence_sha256"]
        == hashlib.sha256((output / "evidence.json").read_bytes()).hexdigest()
    )
    assert subprocess.run(args, cwd=tmp_path, capture_output=True).returncode == 2


def test_payload_identity_conflict_rejected(database):
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE soc_memory_records SET source_candidate_id='OTHER'")
    with pytest.raises(ValueError, match="候选"):
        collect(database)
