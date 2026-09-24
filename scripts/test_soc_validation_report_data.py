"""Synthetic SQLite coverage; never connect to the application database."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from soc_validation_report_data import ReportDataError, _Reader, collect

EARLY = "2026-09-01 10:00:00"
LEARNING = "2026-09-02 10:00:00"
BASELINE = "2026-09-03 10:00:00"
AFTER = "2026-09-04 10:00:00"


class Fixture:
    def __init__(self, directory, validation_count=2):
        self.path = Path(directory) / "isolated.db"
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE soc_alembic_version(version_num TEXT);
                INSERT INTO soc_alembic_version VALUES('0032_corpus_revision_index');
                CREATE TABLE soc_corpus_experiments(experiment_id TEXT PRIMARY KEY,plan_id TEXT,manifest_hash TEXT,record_payload TEXT);
                CREATE TABLE soc_corpus_experiment_members(experiment_id TEXT,alert_id TEXT,group_id TEXT,batch TEXT,validation_tier TEXT,rule_code TEXT,sequence_number INTEGER,record_payload TEXT,PRIMARY KEY(experiment_id,alert_id));
                CREATE TABLE soc_corpus_rounds(round_id TEXT PRIMARY KEY,experiment_id TEXT,batch TEXT,state TEXT,created_at TEXT,record_payload TEXT);
                CREATE TABLE soc_corpus_round_items(round_id TEXT,alert_id TEXT,group_id TEXT,sequence_number INTEGER,job_id TEXT,PRIMARY KEY(round_id,alert_id));
                CREATE TABLE soc_processing_jobs(job_id TEXT PRIMARY KEY,status TEXT,run_id TEXT,attempt_count INTEGER,error_code TEXT,started_at TEXT,completed_at TEXT,updated_at TEXT,result_payload TEXT);
                CREATE TABLE soc_analysis_runs(run_id TEXT PRIMARY KEY,total_duration_ms INTEGER,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,provider_call_count INTEGER,usage_measurement_status TEXT,output_quality_status TEXT,repair_applied INTEGER,deterministic_fallback_used INTEGER,degraded_section_count INTEGER,run_payload TEXT,input_hash TEXT,alert_id TEXT);
                CREATE TABLE soc_memory_uses(run_id TEXT,memory_id TEXT,memory_version INTEGER,effect TEXT,directive_applied INTEGER,use_payload TEXT);
                CREATE TABLE soc_memory_candidates(candidate_id TEXT PRIMARY KEY,status TEXT,source_run_id TEXT,source_alert_id TEXT,created_at TEXT,updated_at TEXT,reviewed_at TEXT,candidate_payload TEXT);
                CREATE TABLE soc_memory_pattern_observations(observation_id TEXT PRIMARY KEY,run_id TEXT,alert_id TEXT);
                CREATE TABLE soc_memory_records(memory_id TEXT PRIMARY KEY,version INTEGER,source_candidate_id TEXT,status TEXT,retrieval_enabled INTEGER,created_at TEXT,updated_at TEXT,content_hash TEXT,facets_hash TEXT,record_payload TEXT);
            """)
        self.add(
            "soc_corpus_experiments",
            experiment_id="EXP",
            plan_id="plan",
            manifest_hash="hash",
        )
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                "INSERT INTO soc_corpus_experiment_members VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        "EXP",
                        str(i),
                        f"G{i % 2}",
                        "learning" if i == 0 else "validation",
                        None if i == 0 else "main" if i % 2 else "supplementary",
                        "RULE",
                        i,
                        json.dumps({"event_time": EARLY}),
                    )
                    for i in range(validation_count + 1)
                ],
            )
        self.round("LEARN", LEARNING, ["0"], batch="learning")
        self.round("MAIN", BASELINE, [str(i) for i in range(1, validation_count + 1)])

    def add(self, table, **values):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                [
                    json.dumps(value) if isinstance(value, (dict, list)) else value
                    for value in values.values()
                ],
            )

    def execute(self, query, args=()):
        with sqlite3.connect(self.path) as conn:
            conn.execute(query, args)

    def round(
        self,
        identity,
        created,
        alerts,
        batch="validation",
        status="completed",
        selection=None,
        superseded=None,
        snapshot=None,
    ):
        payload = {
            "selection": selection
            or {"batch": batch, "scope": "all" if batch == "validation" else "reuse"},
            "options": {"normalization_review_mode": "apply"},
            "memory_mode": "snapshot",
            "memory_snapshot": snapshot or [],
            "superseded_by_round_id": superseded,
        }
        self.add(
            "soc_corpus_rounds",
            round_id=identity,
            experiment_id="EXP",
            batch=batch,
            state="completed" if status == "completed" else "running",
            created_at=created,
            record_payload=payload,
        )
        # Bulk insert fixtures without thousands of commits.
        with sqlite3.connect(self.path) as conn:
            for i, alert in enumerate(alerts):
                run_id = "RUN-" + identity + "-" + alert
                job_id = "JOB-" + identity + "-" + alert
                summary = {
                    "base_verdict": "false_positive",
                    "effective_verdict": "false_positive",
                    "recommended_handling": "ignore",
                    "memory_uses": [],
                    "measurements": {
                        "input_tokens": 10,
                        "output_tokens": 4,
                        "total_tokens": 14,
                    },
                    "phase_timings": [],
                    "base_model_evaluated": True,
                }
                conn.execute(
                    "INSERT INTO soc_corpus_round_items VALUES(?,?,?,?,?)",
                    (identity, alert, "G" + str(int(alert) % 2), i, job_id),
                )
                conn.execute(
                    "INSERT INTO soc_processing_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        status,
                        run_id,
                        1,
                        None,
                        created,
                        created,
                        created,
                        json.dumps({"summary": summary}),
                    ),
                )
                conn.execute(
                    "INSERT INTO soc_analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        1000,
                        10,
                        4,
                        14,
                        1,
                        "reported",
                        "valid",
                        0,
                        0,
                        0,
                        json.dumps(
                            {
                                "normalization_assistance": {
                                    "status": "applied",
                                    "mode": "apply",
                                    "changes": [{"secret": "must not export"}],
                                    "observation_changes": [],
                                    "issues": [],
                                },
                                "private": "MODEL_PROMPT_SECRET",
                            }
                        ),
                        "hash-" + alert,
                        alert,
                    ),
                )


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fx = Fixture(self.directory.name)

    def test_readonly_fixed_round_and_followup(self):
        self.fx.round(
            "ONE", AFTER, ["1"], selection={"scope": "all", "alert_ids": ["1"]}
        )
        before = hashlib.sha256(self.fx.path.read_bytes()).hexdigest()
        result = collect(self.fx.path)
        self.assertEqual(result["metadata"]["round_id"], "MAIN")
        self.assertEqual(len(result["validation_rows"]), 2)
        self.assertEqual(result["followup_rows"][0]["run_id"], "RUN-ONE-1")
        self.assertEqual(result["validation_rows"][0]["run_id"], "RUN-MAIN-1")
        self.assertEqual(
            result["validation_rows"][0]["semantic_review"]["change_count"], 1
        )
        self.assertNotIn("MODEL_PROMPT_SECRET", json.dumps(result))
        self.assertNotIn("must not export", json.dumps(result))
        self.assertEqual(before, hashlib.sha256(self.fx.path.read_bytes()).hexdigest())
        self.assertFalse(Path(str(self.fx.path) + "-journal").exists())

    def test_newer_full_round_queued_does_not_fall_back(self):
        self.fx.round("NEW", AFTER, ["1", "2"], status="queued")
        with self.assertRaisesRegex(ReportDataError, "未结束"):
            collect(self.fx.path)

    def test_newer_incomplete_all_round_does_not_fall_back(self):
        self.fx.round("NEW", AFTER, ["1"])
        with self.assertRaisesRegex(ReportDataError, "实际覆盖"):
            collect(self.fx.path)

    def test_explicit_round_requires_whole_validation(self):
        self.fx.round(
            "ONE", AFTER, ["1"], selection={"scope": "all", "alert_ids": ["1"]}
        )
        with self.assertRaisesRegex(ReportDataError, "全量"):
            collect(self.fx.path, round_id="ONE")

    def test_failed_terminal_is_reportable(self):
        self.fx.execute(
            "UPDATE soc_processing_jobs SET status='failed',run_id=NULL,error_code='payload_error' WHERE job_id='JOB-MAIN-2'"
        )
        data = collect(self.fx.path)
        self.assertEqual(data["validation_rows"][1]["status"], "failed")

    def test_multiple_experiments_need_explicit_choice(self):
        self.fx.add(
            "soc_corpus_experiments",
            experiment_id="OTHER",
            plan_id="p2",
            manifest_hash="h2",
        )
        self.fx.add(
            "soc_corpus_rounds",
            round_id="OTHER-R",
            experiment_id="OTHER",
            batch="validation",
            state="completed",
            created_at=BASELINE,
            record_payload={},
        )
        with self.assertRaisesRegex(ReportDataError, "--experiment.*EXP.*OTHER"):
            collect(self.fx.path)
        self.assertEqual(
            collect(self.fx.path, experiment_id="EXP")["metadata"]["round_id"], "MAIN"
        )

    def test_changed_source_rejects_export(self):
        called = False

        def progress(_message):
            nonlocal called
            if not called:
                self.fx.execute("UPDATE soc_corpus_experiments SET plan_id='changed'")
                called = True

        with self.assertRaisesRegex(ReportDataError, "混合快照"):
            collect(self.fx.path, progress=progress)

    def test_latest_learning_written_after_cutoff_is_unknown(self):
        self.fx.execute(
            "UPDATE soc_processing_jobs SET completed_at=?,updated_at=? WHERE job_id='JOB-LEARN-0'",
            (AFTER, AFTER),
        )
        row = collect(self.fx.path)["learning_rows"][0]
        self.assertEqual(row["as_of_status"], "unknown")
        self.assertNotIn("effective_verdict", row["summary"])

    def test_candidate_unchanged_asof_and_changed_unknown(self):
        for identity, updated in (("OLD", LEARNING), ("CHANGED", AFTER)):
            self.fx.add(
                "soc_memory_candidates",
                candidate_id=identity,
                status="rejected",
                source_run_id="RUN-LEARN-0",
                source_alert_id="0",
                created_at=LEARNING,
                updated_at=updated,
                reviewed_at=LEARNING,
                candidate_payload={
                    "review_reason": "审核人决定放弃沉淀该候选，未形成可复用 Memory。"
                },
            )
        rows = {row["candidate_id"]: row for row in collect(self.fx.path)["candidates"]}
        self.assertEqual(rows["OLD"]["as_of_status"], "rejected")
        self.assertEqual(rows["CHANGED"]["as_of_status"], "unknown")
        self.assertEqual(rows["CHANGED"]["current_status"], "rejected")
        self.assertIsNone(rows["OLD"]["reason"])

    def test_observation_provenance_adds_all_learning_groups(self):
        self.fx.add(
            "soc_corpus_experiment_members",
            experiment_id="EXP",
            alert_id="3",
            group_id="G1",
            batch="learning",
            validation_tier=None,
            rule_code="OTHER",
            sequence_number=3,
            record_payload={},
        )
        self.fx.round("LEARN2", LEARNING, ["3"], batch="learning")
        self.fx.add(
            "soc_memory_pattern_observations",
            observation_id="OBS",
            run_id="RUN-LEARN2-3",
            alert_id="3",
        )
        self.fx.add(
            "soc_memory_candidates",
            candidate_id="C",
            status="confirmed",
            source_run_id="RUN-LEARN-0",
            source_alert_id="0",
            created_at=LEARNING,
            updated_at=LEARNING,
            reviewed_at=LEARNING,
            candidate_payload={"metadata": {"observation_ids": ["OBS"]}},
        )
        candidate = collect(self.fx.path)["candidates"][0]
        self.assertEqual(candidate["group_ids"], ["G0", "G1"])
        self.assertEqual(candidate["source_alert_ids"], ["0", "3"])

    def test_historical_memory_verdict_not_taken_from_new_version(self):
        self.fx.add(
            "soc_memory_candidates",
            candidate_id="C",
            status="confirmed",
            source_run_id="RUN-LEARN-0",
            source_alert_id="0",
            created_at=LEARNING,
            updated_at=AFTER,
            reviewed_at=LEARNING,
            candidate_payload={},
        )
        self.fx.add(
            "soc_memory_records",
            memory_id="M",
            version=2,
            source_candidate_id="C",
            status="confirmed",
            retrieval_enabled=1,
            created_at=LEARNING,
            updated_at=AFTER,
            content_hash="c",
            facets_hash="f",
            record_payload={"reviewed_verdict": "true_positive"},
        )
        self.fx.execute(
            "UPDATE soc_corpus_rounds SET record_payload=json_set(record_payload,'$.memory_snapshot',json(?)) WHERE round_id='MAIN'",
            (
                json.dumps(
                    [
                        {
                            "memory_id": "M",
                            "version": 1,
                            "content_hash": "c",
                            "facets_hash": "f",
                            "record_hash": "r",
                        }
                    ]
                ),
            ),
        )
        data = collect(self.fx.path)
        self.assertEqual(data["candidates"][0]["as_of_status"], "unknown")
        self.assertEqual(data["candidates"][0]["snapshot_memory_ids"], ["M"])
        self.assertTrue(data["memories"][0]["snapshot_included"])
        self.assertIsNone(data["memories"][0]["reviewed_verdict"])

    def test_unknown_uses_stay_unknown_but_empty_is_known_zero(self):
        self.fx.execute(
            "UPDATE soc_processing_jobs SET result_payload=json_remove(result_payload,'$.summary.memory_uses') WHERE job_id='JOB-MAIN-1'"
        )
        data = collect(self.fx.path)
        self.assertIsNone(data["validation_rows"][0]["summary"]["memory_uses"])
        self.assertEqual(data["validation_rows"][1]["summary"]["memory_uses"], [])

    def test_json_booleans_and_applicability_scalars(self):
        self.fx.execute(
            "UPDATE soc_processing_jobs SET result_payload=json_set(result_payload,'$.summary.base_model_evaluated',json('false')) WHERE job_id='JOB-MAIN-1'"
        )
        self.fx.add(
            "soc_memory_uses",
            run_id="RUN-MAIN-1",
            memory_id="M",
            memory_version=1,
            effect="direct_reused",
            directive_applied=1,
            use_payload={
                "applicability_report": {
                    "status": "matched",
                    "reason_codes": ["all_required_facets_matched"],
                    "matched_required_facets": {"secret": ["PRIVATE_IP"]},
                }
            },
        )
        data = collect(self.fx.path)
        row = data["validation_rows"][0]
        self.assertIs(row["summary"]["base_model_evaluated"], False)
        self.assertIs(row["summary"]["memory_uses"][0]["directive_applied"], True)
        self.assertEqual(
            row["summary"]["memory_uses"][0]["applicability_status"], "matched"
        )
        self.assertNotIn("PRIVATE_IP", json.dumps(data))

    def test_rejection_reasons_are_bounded_and_only_for_rejected_candidates(self):
        for identity, status in (("R", "rejected"), ("C", "confirmed")):
            self.fx.add(
                "soc_memory_candidates",
                candidate_id=identity,
                status=status,
                source_run_id="RUN-LEARN-0",
                source_alert_id="0",
                created_at=LEARNING,
                updated_at=LEARNING,
                reviewed_at=LEARNING,
                candidate_payload={"review_reason": "缺少核心行为证据" * 200},
            )
        rows = {row["candidate_id"]: row for row in collect(self.fx.path)["candidates"]}
        self.assertEqual(len(rows["R"]["reason"]), 1000)
        self.assertTrue(rows["R"]["reason_truncated"])
        self.assertIsNone(rows["C"]["reason"])

    def test_superseded_latest_round_is_not_selected(self):
        self.fx.round("REPLACED", AFTER, ["1", "2"], superseded="MAIN")
        self.assertEqual(collect(self.fx.path)["metadata"]["round_id"], "MAIN")

    def test_ambiguous_timestamp_needs_explicit_round(self):
        self.fx.round("OTHER", BASELINE, ["1", "2"])
        with self.assertRaisesRegex(ReportDataError, "--round"):
            collect(self.fx.path)

    def test_query_timeout_has_actionable_message(self):
        with sqlite3.connect(self.fx.path) as conn:
            conn.row_factory = sqlite3.Row
            reader = _Reader(conn, 0.000001)
            with self.assertRaisesRegex(ReportDataError, "SQLITE_INTERRUPT"):
                reader.read(
                    "WITH RECURSIVE x(a) AS (SELECT 1 UNION ALL SELECT a+1 FROM x WHERE a<1000000) SELECT sum(a) FROM x"
                )

    def test_schema_failure_does_not_suggest_lock_recovery(self):
        self.fx.execute("DROP TABLE soc_memory_uses")
        with self.assertRaisesRegex(
            ReportDataError, "数据库字段不兼容（soc_memory_uses）.*不自动迁移"
        ):
            collect(self.fx.path)

    def test_malformed_json_is_reported_without_source_content(self):
        self.fx.execute(
            "UPDATE soc_corpus_rounds SET record_payload='RAW_PRIVATE_NOT_JSON' WHERE round_id='MAIN'"
        )
        with self.assertRaises(ReportDataError) as caught:
            collect(self.fx.path)
        self.assertIn("JSON 字段无法解析", str(caught.exception))
        self.assertNotIn("RAW_PRIVATE_NOT_JSON", str(caught.exception))

    def test_historical_observation_run_requires_matching_input_hash(self):
        self.fx.add(
            "soc_corpus_experiment_members",
            experiment_id="EXP",
            alert_id="3",
            group_id="G3",
            batch="learning",
            validation_tier=None,
            rule_code="R3",
            sequence_number=3,
            record_payload={"payload_hash": "hash3"},
        )
        for suffix, input_hash in (("OK", "hash3"), ("BAD", "different")):
            self.fx.add(
                "soc_analysis_runs",
                run_id="HIST-" + suffix,
                alert_id="3",
                input_hash=input_hash,
            )
            self.fx.add(
                "soc_memory_pattern_observations",
                observation_id=suffix,
                run_id="HIST-" + suffix,
                alert_id="3",
            )
        self.fx.add(
            "soc_memory_candidates",
            candidate_id="C",
            status="confirmed",
            source_run_id="RUN-LEARN-0",
            source_alert_id="0",
            created_at=LEARNING,
            updated_at=LEARNING,
            reviewed_at=LEARNING,
            candidate_payload={"metadata": {"observation_ids": ["OK", "BAD"]}},
        )
        data = collect(self.fx.path)
        candidate = data["candidates"][0]
        self.assertEqual(candidate["group_ids"], ["G0", "G3"])
        self.assertEqual(candidate["unresolved_observation_count"], 1)
        self.assertFalse(candidate["source_groups_complete"])

    def test_large_members_use_bounded_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(directory, validation_count=12284)
            original = _Reader.read
            largest = 0

            def checked(reader, sql, params=()):
                nonlocal largest
                largest = max(largest, len(params))
                result = original(reader, sql, params)
                if (
                    sql.lstrip().upper().startswith("SELECT")
                    and "soc_alembic_version" not in sql
                ):
                    self.assertLessEqual(len(result), 200)
                return result

            with patch.object(_Reader, "read", checked):
                data = collect(fixture.path)
            self.assertEqual(len(data["validation_rows"]), 12284)
            self.assertLessEqual(largest, 202)


if __name__ == "__main__":
    unittest.main()
