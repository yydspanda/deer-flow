"""Offline real-export recall contracts; no network, models, or operational writes."""

import copy
import csv
import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


module("surge_materials", HERE.parent / "step06z_surge_recommendation/materials.py")
module(
    "restricted_dataframe_pickle",
    ROOT / "validation/compact_zeus/shared/restricted_dataframe_pickle.py",
)
materials = module("real_csv_materials", HERE / "materials.py")
history = module(
    "real_csv_installed_history",
    HERE.parent
    / "step07_surge_runtime/payload/my_workflows/zeus/rule_refine/surge_history.py",
)


class Rules:
    file_sha256 = "test-rule-file"

    def __init__(self):
        group = {
            "conditions": [
                {
                    "leftFieldName": "event_content",
                    "operator": "NotContains",
                    "rightFieldValue": "net1 accounts",
                },
                {
                    "leftFieldName": "event_content",
                    "operator": "NotContains",
                    "rightFieldValue": "different-existing-exclusion",
                },
            ]
        }
        self.rule = {
            "company_code": "company",
            "topic": "security_qthids",
            "rule_code": "RPAADM_000598",
            "rule_name": "Example",
            "rule_config_version": "test-real-rule-version",
            "configuration": {
                "terms": [
                    {
                        "alias": "B",
                        "config": copy.deepcopy(group),
                        "standard_field_config": copy.deepcopy(group),
                    }
                ]
            },
            "source": {"kind": "csv_snapshot"},
        }

    def get_rule(self, rule_code, *, topic, company_code=None):
        if (rule_code, topic) != (self.rule["rule_code"], self.rule["topic"]):
            raise ValueError("wrong_rule")
        return copy.deepcopy(self.rule)

    def list_rules(self):
        return [copy.deepcopy(self.rule)]


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = self.root / "current.csv"
        self.db = self.root / "nas/data/sec_know_model.sqlite"
        self.db.parent.mkdir(parents=True)
        with sqlite3.connect(self.db) as db:
            db.execute(
                "CREATE TABLE uas_app_workflow_task_status "
                "(app_code TEXT, flow_id TEXT, alert_id TEXT, alert_data TEXT, "
                "agent_response TEXT, created_date TEXT)"
            )
        self.rules = Rules()
        self.cutoff = datetime(2026, 9, 17, 10)
        self.write_current([self.payload("current", self.cutoff)])

    def payload(self, identity, at, *, rule="RPAADM_000598", topic="security_qthids"):
        return {
            "alert": {
                "alertId": identity,
                "createAt": at.isoformat(sep=" "),
                "hitLog": [
                    {
                        "topic": topic,
                        "ruleCode": rule,
                        "zeusRawLogs": [{"event_content": "ORIGINAL-" + identity}],
                    }
                ],
            },
            "relatedAlertList": [],
        }

    def write_current(self, payloads):
        with self.current.open("w", encoding="utf-8", newline="") as out:
            writer = csv.DictWriter(
                out, fieldnames=["app_code", "flow_id", "alert_id", "alert_data"]
            )
            writer.writeheader()
            for data in payloads:
                writer.writerow(
                    {
                        "app_code": "zeus",
                        "flow_id": "alert_agent",
                        "alert_id": data["alert"]["alertId"],
                        "alert_data": json.dumps(data),
                    }
                )

    def add_db(self, data, *, app="zeus", flow="alert_agent"):
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO uas_app_workflow_task_status VALUES (?, ?, ?, ?, ?, ?)",
                (
                    app,
                    flow,
                    data["alert"]["alertId"],
                    json.dumps(data),
                    "OLD_MODEL_ANSWER_MUST_NOT_ENTER_CONTEXT",
                    "2099-01-01",
                ),
            )

    def write_pickle(self, payloads):
        path = self.root / "history.pkl"
        rows = []
        for data in payloads:
            identity = data["alert"]["alertId"]
            rows.append(
                {
                    "alert_id": identity,
                    "alert_full_data": {
                        "app_code": "zeus",
                        "flow_id": "alert_agent",
                        "alert_id": identity,
                        "alert_data": json.dumps(data),
                    },
                    "agent_response": "OLD_PICKLE_ANSWER_MUST_NOT_ENTER_CONTEXT",
                }
            )
        frame = pd.DataFrame(rows, dtype=object)
        frame.columns = pd.Index(list(frame.columns), dtype=object)
        frame.to_pickle(path)
        return path

    def prepare(self, pkl=None):
        return materials.prepare_case(
            self.root,
            self.current,
            pkl,
            lambda name: history,
            self.rules,
        )

    def test_nearest_twenty_real_history_business_time_not_import_time(self):
        for index in range(25):
            self.add_db(
                self.payload(
                    f"prior-{index:02}", self.cutoff - timedelta(days=index + 1)
                )
            )
        self.add_db(self.payload("same-time", self.cutoff))
        self.add_db(self.payload("later", self.cutoff + timedelta(hours=1)))
        before = self.db.read_bytes()
        case, signal, audit = self.prepare()
        self.assertEqual(
            [s["alert_id"] for s in case["history_samples"]],
            [f"prior-{i:02}" for i in range(20)],
        )
        self.assertEqual(audit["history_available"], 25)
        self.assertEqual(audit["history_gap_seconds"], 86400)
        self.assertEqual(signal["current_alert_ids"], ["current"])
        self.assertEqual(before, self.db.read_bytes())
        self.assertTrue(audit["temporary_database_removed"])
        self.assertNotIn("OLD_MODEL_ANSWER", json.dumps(case))

    def test_small_pickle_preserves_real_history_and_deduplicates_with_db(self):
        prior = self.payload("prior", self.cutoff - timedelta(days=150))
        self.add_db(prior)
        pkl = self.write_pickle([prior])
        case, _, audit = self.prepare(pkl)
        self.assertEqual(len(case["history_samples"]), 1)
        self.assertEqual(audit["history_available"], 1)
        self.assertFalse(audit["synthetic_history_used"])
        self.assertNotIn("OLD_PICKLE_ANSWER", json.dumps(case))
        self.assertEqual(
            audit["sources"][2]["file_sha256"],
            hashlib.sha256(pkl.read_bytes()).hexdigest(),
        )

    def test_missing_history_remains_zero_not_synthetic(self):
        self.db.unlink()
        case, _, audit = self.prepare()
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(audit["history_selected"], 0)
        self.assertIsNone(audit["history_gap_seconds"])
        self.assertEqual(audit["sources"][1]["status"], "not_present")
        self.assertFalse(self.db.exists())

    def test_non_target_workflow_rule_and_explicit_synthetic_not_used(self):
        at = self.cutoff - timedelta(hours=1)
        self.add_db(self.payload("other-rule", at, rule="other"))
        self.add_db(self.payload("other-topic", at, topic="other"))
        self.add_db(self.payload("other-app", at), app="other")
        synthetic = self.payload("marked-synthetic", at)
        synthetic["material_source"] = "synthetic_dev_fixture"
        self.add_db(synthetic)
        self.add_db(self.payload("real", at))
        case, _, audit = self.prepare()
        self.assertEqual([s["alert_id"] for s in case["history_samples"]], ["real"])
        self.assertEqual(audit["sources"][1]["synthetic_rows_excluded"], 1)

    def test_conflicting_historical_id_is_quarantined_not_cherry_picked(self):
        prior = self.payload("prior", self.cutoff - timedelta(days=1))
        self.add_db(prior)
        changed = copy.deepcopy(prior)
        changed["alert"]["hitLog"][0]["zeusRawLogs"][0]["event_content"] = "changed"
        case, _, audit = self.prepare(self.write_pickle([changed]))
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(case["coverage"]["conflicting_identity_count"], 1)
        self.assertEqual(audit["history_available"], 0)

    def test_conflicting_current_db_version_blocks_material_acceptance(self):
        changed = self.payload("current", self.cutoff)
        changed["alert"]["hitLog"][0]["zeusRawLogs"][0]["event_content"] = "changed"
        self.add_db(changed)
        with self.assertRaises(history.MaterialsPendingError):
            self.prepare()

    def test_counterfactual_only_removes_named_later_exclusion_in_memory(self):
        before = copy.deepcopy(self.rules.rule)
        case, _, audit = self.prepare()
        self.assertEqual(self.rules.rule, before)
        self.assertEqual(case["original_rule"], before)
        self.assertEqual(len(audit["removed_conditions"]), 2)
        self.assertNotIn("net1 accounts", json.dumps(case["rule"]))
        self.assertNotIn("removed_conditions", case["source"])
        self.assertTrue(audit["source_alert_content_unchanged"])

    def test_corrupt_or_explicitly_missing_pickle_fails(self):
        path = self.root / "history.pkl"
        with self.assertRaisesRegex(ValueError, "historical_material_file_missing"):
            self.prepare(path)
        path.write_bytes(b"cbuiltins\neval\n.")
        with self.assertRaises(Exception):
            self.prepare(path)

    def test_full_current_log_arrays_preserved(self):
        data = self.payload("current", self.cutoff)
        data["alert"]["hitLog"][0]["zeusRawLogs"].append({"message": "x" * 20000})
        self.write_current([data])
        case, _, audit = self.prepare()
        self.assertEqual(
            case["current_samples"][0]["raw_logs"],
            data["alert"]["hitLog"][0]["zeusRawLogs"],
        )
        self.assertEqual(audit["current_target_logs"], 2)


if __name__ == "__main__":
    unittest.main()
