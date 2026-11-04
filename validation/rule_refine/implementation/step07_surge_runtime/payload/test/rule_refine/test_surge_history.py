"""Isolated SQLite acceptance tests; no application imports or live services."""

import copy
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "my_workflows/zeus/rule_refine/surge_history.py"
)
SPEC = importlib.util.spec_from_file_location("isolated_surge_history", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SurgeTaskSource = MODULE.SurgeTaskSource
MaterialsPendingError = MODULE.MaterialsPendingError


class Rules:
    def __init__(self):
        self.rule = {
            "company_code": "company",
            "topic": "topic",
            "rule_code": "rule",
            "rule_name": "Example",
            "rule_config_version": "test-v1",
            "configuration": {
                "id": "metadata",
                "terms": [
                    {
                        "id": "term",
                        "config": {"conditions": [{"field": "id", "value": "keep"}]},
                    }
                ],
            },
            "raw_record": {"previous_answer": "must not be in model rule"},
            "source": {"kind": "csv_snapshot"},
        }
        self.catalog = [
            {key: self.rule[key] for key in ("company_code", "topic", "rule_code")}
        ]

    def get_rule(self, code, *, topic, company_code):
        if (code, topic, company_code) != ("rule", "topic", "company"):
            raise ValueError("rule_not_found")
        return self.rule

    def list_rules(self):
        return self.catalog


class SurgeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite"
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status (app_code TEXT, flow_id TEXT, alert_id TEXT, alert_data TEXT, agent_response TEXT, created_date TEXT, status TEXT)"
            )
        self.rules = Rules()
        self.source = SurgeTaskSource(self.path, self.rules)
        self.signal = {
            "source_app": "zeus",
            "company_code": "company",
            "topic": "topic",
            "rule_code": "rule",
            "sample_alert_ids": ["current"],
            "window_start": "2026-11-04T00:00:00Z",
            "window_end": "2026-11-04T01:00:00Z",
        }

    def payload(
        self,
        identity,
        occurred="2026-09-17 10:00:00",
        *,
        logs=None,
        topic="topic",
        rule="rule",
    ):
        return {
            "alert": {
                "alertId": identity,
                "createAt": occurred,
                "hitLog": [
                    {
                        "topic": topic,
                        "ruleCode": rule,
                        "zeusRawLogs": logs
                        if logs is not None
                        else [{"event_content": identity}],
                    }
                ],
            }
        }

    def add(
        self,
        identity,
        occurred="2026-09-17 10:00:00",
        *,
        payload=None,
        app="zeus",
        flow="alert_agent",
        created="1900-01-01",
        answer="SECRET_PREVIOUS_MODEL_ANSWER",
        status="successed",
        **kwargs,
    ):
        payload = (
            self.payload(identity, occurred, **kwargs) if payload is None else payload
        )
        text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        )
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO uas_app_workflow_task_status VALUES (?, ?, ?, ?, ?, ?, ?)",
                (app, flow, identity, text, answer, created, status),
            )

    def ids(self, samples):
        return [sample["alert_id"] for sample in samples]

    def test_business_time_cutoff_strict_history_and_closed_current_range(self):
        self.add("current", "2026-09-17 10:00:00")
        self.add("end", "2026-09-17 10:10:00")
        self.add("middle", "2026-09-17 10:05:00")
        self.add("same-cutoff", "2026-09-17 10:00:00")
        self.add("future", "2026-09-17 10:10:01")
        self.add("prior", "2026-09-17 09:59:59", created="2099-01-01")
        self.signal["sample_alert_ids"].append("end")
        case = self.source.load_case(self.signal)
        self.assertEqual(
            self.ids(case["current_samples"]),
            ["current", "same-cutoff", "middle", "end"],
        )
        self.assertEqual(self.ids(case["history_samples"]), ["prior"])
        self.assertEqual(case["coverage"]["cutoff"], "2026-09-17 10:00:00")
        self.assertEqual(
            case["coverage"]["cutoff_basis"], "oldest_signal_sample_alert_time"
        )
        self.assertFalse(case["coverage"]["cutoff_is_verified_surge_start"])
        self.assertFalse(case["source"]["task_created_date_used"])
        self.assertFalse(
            case["source"]["receipt_window"]["used_for_business_time_selection"]
        )
        self.assertEqual(case["coverage"]["after_current_anchor_range_alerts"], 1)

    def test_nearest_twenty_distinct_history_with_deterministic_ties(self):
        self.add("current")
        start = datetime(2026, 9, 17, 10)
        for index in reversed(range(24)):
            self.add(
                f"h{index:02}",
                (start - timedelta(days=1, minutes=index // 2)).isoformat(),
            )
        self.add("h00", (start - timedelta(days=1)).isoformat())
        case = self.source.load_case(self.signal)
        self.assertEqual(
            self.ids(case["history_samples"]), [f"h{index:02}" for index in range(20)]
        )
        self.assertEqual(case["coverage"]["historical_available_unique_alerts"], 24)
        self.assertEqual(case["coverage"]["history_not_selected_count"], 4)
        self.assertIsNone(case["coverage"]["history_lookback_duration_limit"])
        self.assertEqual(case["coverage"]["duplicate_identity_rows"], 1)

    def test_expanded_current_ids_override_small_signal_sample(self):
        self.add("current")
        self.add("early", "2026-09-17 08:00:00")
        self.add("middle", "2026-09-17 09:00:00")
        self.signal["current_alert_ids"] = ["early", "current"]
        case = self.source.load_case(self.signal)
        self.assertEqual(self.ids(case["current_samples"]), ["early", "current"])
        self.assertEqual(
            case["coverage"]["cutoff_basis"], "oldest_signal_current_alert_time"
        )
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(
            case["coverage"]["current_selection_basis"],
            "exact_signal_current_alert_ids",
        )
        self.assertEqual(case["coverage"]["excluded_in_range_not_receipt_ids"], 1)

    def test_expanded_current_ids_exclude_other_receipts_at_identical_business_time(
        self,
    ):
        self.add("current")
        self.add("same-time-other-receipt")
        self.add("history", "2026-09-17 09:59:59")
        self.signal["current_alert_ids"] = ["current"]
        case = self.source.load_case(self.signal)
        self.assertEqual(self.ids(case["current_samples"]), ["current"])
        self.assertEqual(self.ids(case["history_samples"]), ["history"])
        self.assertEqual(case["coverage"]["excluded_in_range_not_receipt_ids"], 1)

    def test_scans_all_matching_workflow_rows_not_latest_twenty(self):
        self.add("old-history", "2020-01-01 01:00:00")
        self.add("current")
        for index in range(30):
            self.add(f"irrelevant{index}", topic="other")
        case = self.source.load_case(self.signal)
        self.assertEqual(self.ids(case["history_samples"]), ["old-history"])
        self.assertEqual(case["coverage"]["scanned_task_rows"], 32)
        self.assertTrue(case["coverage"]["database_scan_complete"])

    def test_scope_ignores_other_apps_flows_topics_rules_and_related_alerts(self):
        self.add("current")
        self.add("other-app", app="other")
        self.add("other-flow", flow="other")
        self.add("other-topic", topic="other")
        self.add("other-rule", rule="other")
        unrelated = self.payload("unrelated", topic="other")
        unrelated["relatedAlerts"] = [
            self.payload("hidden", "2020-01-01 00:00:00")["alert"]
        ]
        self.add("unrelated", payload=unrelated)
        case = self.source.load_case(self.signal)
        self.assertEqual(self.ids(case["current_samples"]), ["current"])
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(case["coverage"]["database_total_task_rows"], 6)
        self.assertEqual(case["coverage"]["scanned_task_rows"], 4)

    def test_conflicting_identity_excluded_even_if_other_version_changes_rule(self):
        self.add("current")
        self.add("conflict", "2026-09-17 09:00:00")
        self.add("conflict", "2026-09-17 09:00:00", rule="other")
        self.add("stable", "2026-09-17 08:00:00")
        case = self.source.load_case(self.signal)
        self.assertEqual(self.ids(case["history_samples"]), ["stable"])
        self.assertEqual(case["coverage"]["conflicting_identity_count"], 1)
        self.assertEqual(case["coverage"]["excluded_conflicting_target_alerts"], 1)

    def test_duplicate_main_ignores_changed_previous_answers_and_related_alerts(self):
        self.add("current", answer="first")
        repeated = self.payload("current")
        repeated["relatedAlerts"] = [{"different": True}]
        self.add("current", payload=repeated, answer="second")
        case = self.source.load_case(self.signal)
        self.assertEqual(len(case["current_samples"]), 1)
        self.assertEqual(case["coverage"]["conflicting_identity_count"], 0)
        self.assertEqual(case["coverage"]["duplicate_identity_rows"], 1)

    def test_conflicting_anchor_is_pending(self):
        self.add("current")
        self.add("current", logs=[{"different": "payload"}])
        with self.assertRaises(MaterialsPendingError) as caught:
            self.source.load_case(self.signal)
        self.assertEqual(caught.exception.code, "current_alert_materials_pending")
        self.assertEqual(caught.exception.details["conflicting_current_anchors"], 1)

    def test_missing_anchor_prevents_partial_case(self):
        self.add("current")
        self.signal["sample_alert_ids"].append("missing")
        with self.assertRaises(MaterialsPendingError) as caught:
            self.source.load_case(self.signal)
        self.assertEqual(caught.exception.details["available_current_anchors"], 1)
        self.assertEqual(caught.exception.details["unavailable_current_anchors"], 1)

    def test_invalid_known_identity_taints_valid_sibling_and_counts_errors(self):
        self.add("current")
        self.add("history", "2026-09-17 09:00:00")
        self.add("history", payload="{broken")
        self.add("bad-log", "2026-09-17 08:00:00", logs=["not-json"])
        case = self.source.load_case(self.signal)
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(case["coverage"]["invalid_rows"], 2)
        self.assertEqual(case["coverage"]["invalid_logs"], 1)
        self.assertEqual(case["coverage"]["excluded_invalid_target_alerts"], 1)

    def test_identity_mismatch_cannot_reintroduce_other_valid_identity(self):
        self.add("current")
        self.add("history", "2026-09-17 09:00:00")
        self.add("wrong-row", payload=self.payload("history", "2026-09-17 09:00:00"))
        case = self.source.load_case(self.signal)
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(case["coverage"]["issue_counts"]["alert_identity_mismatch"], 1)

    def test_full_large_logs_all_hits_and_current_alerts_retained(self):
        large = {"event_content": "界" * 100000, "nested": {"complete": [1, 2, 3]}}
        logs = [large, json.dumps({"encoded": "kept"})]
        main = self.payload("current", logs=logs)
        main["alert"]["hitLog"].append(
            {"topic": "topic", "ruleCode": "rule", "zeusRawLogs": [{"other_hit": True}]}
        )
        self.add("current", payload=main, status="created")
        for index in range(25):
            self.add(f"same-time-{index:02}")
        case = self.source.load_case(self.signal)
        self.assertEqual(len(case["current_samples"]), 26)
        actual = next(
            sample
            for sample in case["current_samples"]
            if sample["alert_id"] == "current"
        )
        self.assertEqual(
            actual["raw_logs"], [large, {"encoded": "kept"}, {"other_hit": True}]
        )
        self.assertEqual(case["coverage"]["current_log_count"], 28)

    def test_history_answers_never_selected_and_source_rule_signal_unchanged(self):
        self.add("current")
        self.add("prior", "2026-09-17 09:00:00")
        signal_before, rule_before = (
            copy.deepcopy(self.signal),
            copy.deepcopy(self.rules.rule),
        )
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        case = self.source.load_case(self.signal)
        self.assertNotIn("SECRET_PREVIOUS_MODEL_ANSWER", json.dumps(case))
        self.assertFalse(case["coverage"]["historical_model_answers_included"])
        self.assertFalse(case["coverage"]["history_normal_labels_assumed"])
        self.assertNotIn("raw_record", case["rule"])
        self.assertNotIn("id", case["rule"]["configuration"])
        self.assertEqual(
            case["rule"]["configuration"]["terms"][0]["config"]["conditions"][0][
                "field"
            ],
            "id",
        )
        case["original_rule"]["configuration"]["terms"].clear()
        self.assertEqual(self.rules.rule, rule_before)
        self.assertEqual(self.signal, signal_before)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_missing_database_is_not_created(self):
        missing = self.path.parent / "missing.sqlite"
        with self.assertRaisesRegex(ValueError, "local_task_database_missing"):
            SurgeTaskSource(missing, self.rules).load_case(self.signal)
        self.assertFalse(missing.exists())

    def test_readonly_database_and_no_agent_response_or_created_date_column_needed(
        self,
    ):
        minimal = self.path.parent / "minimal.sqlite"
        with sqlite3.connect(minimal) as db:
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status (app_code TEXT, flow_id TEXT, alert_id TEXT, alert_data TEXT)"
            )
            db.execute(
                "INSERT INTO uas_app_workflow_task_status VALUES (?, ?, ?, ?)",
                ("zeus", "alert_agent", "current", json.dumps(self.payload("current"))),
            )
        minimal.chmod(0o444)
        before = minimal.read_bytes()
        case = SurgeTaskSource(minimal, self.rules).load_case(self.signal)
        self.assertEqual(self.ids(case["current_samples"]), ["current"])
        self.assertEqual(minimal.read_bytes(), before)

    def test_ambiguous_company_is_rejected(self):
        self.rules.catalog.append(
            {"company_code": "other", "topic": "topic", "rule_code": "rule"}
        )
        with self.assertRaisesRegex(ValueError, "ambiguous_company_scope"):
            self.source.load_case(self.signal)

    def test_rule_provider_wrong_scope_is_rejected(self):
        self.rules.rule["company_code"] = "wrong"
        with self.assertRaisesRegex(ValueError, "rule_provider_scope_mismatch"):
            self.source.load_case(self.signal)

    def test_timezone_mixture_is_rejected_without_silent_assumption(self):
        self.add("current")
        self.add("prior", "2026-09-17T09:00:00+08:00")
        with self.assertRaisesRegex(ValueError, "mixed_aware_and_naive_alert_times"):
            self.source.load_case(self.signal)

    def test_offset_aware_times_order_by_instant(self):
        self.add("current", "2026-09-17T10:00:00+08:00")
        self.add("prior", "2026-09-17T01:59:59Z")
        self.add("same-instant", "2026-09-17T02:00:00Z")
        case = self.source.load_case(self.signal)
        self.assertEqual(self.ids(case["history_samples"]), ["prior"])
        self.assertEqual(self.ids(case["current_samples"]), ["current", "same-instant"])
        self.assertEqual(case["coverage"]["timezone"], "offset_in_source")

    def test_duplicate_json_keys_and_nonfinite_logs_are_rejected(self):
        self.add("current")
        self.add(
            "bad-duplicate",
            "2026-09-17 09:00:00",
            logs=['{"command":"a","command":"b"}'],
        )
        self.add("bad-number", "2026-09-17 09:00:00", logs=['{"number":1e999}'])
        case = self.source.load_case(self.signal)
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(case["coverage"]["invalid_logs"], 2)

    def test_no_history_is_explicit_and_not_a_failure(self):
        self.add("current")
        case = self.source.load_case(self.signal)
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(case["coverage"]["history_selected_unique_alerts"], 0)
        self.assertIsNone(case["coverage"]["history_first_alert_at"])


if __name__ == "__main__":
    unittest.main()
