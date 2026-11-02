import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

import pandas as pd

import materials

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "historical_inventory", HERE.parent / "step06o_ingress_readiness/materials_check.py"
)
inventory = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = inventory
spec.loader.exec_module(inventory)


class Rules:
    def __init__(self):
        self.rule = {
            "company_code": "SYNTHETIC",
            "topic": materials.TARGET[0],
            "rule_code": materials.TARGET[1],
            "rule_name": "人工规则",
            "rule_config_version": "test-version",
            "source": {"kind": "csv_snapshot", "file_sha256": materials.CSV_SHA},
            "configuration": {"terms": [{"condition": "原条件，完整保留"}]},
        }

    def get_rule(self, code, *, topic):
        if (topic, code) != materials.TARGET:
            raise ValueError("wrong_test_scope")
        return self.rule


def payload(identity="alert-1", logs=None):
    return {
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": identity,
        "alert_data": {
            "alert": {
                "alertId": identity,
                "hitLog": [
                    {
                        "topic": materials.TARGET[0],
                        "ruleCode": materials.TARGET[1],
                        "zeusRawLogs": [{"path": "/original", "marker": "unchanged"}]
                        if logs is None
                        else logs,
                    }
                ],
            },
            "relatedAlertList": [],
        },
    }


class HistoricalMaterialsTests(unittest.TestCase):
    def build(self, rows, ids=None, rules=None):
        data = {
            "alert_full_data": rows,
            "agent_response": ["OLD-MODEL-ANSWER"] * len(rows),
        }
        if ids is not None:
            data["alert_id"] = ids
        return materials.build_context(pd.DataFrame(data), rules or Rules(), inventory)

    def test_original_identity_and_complete_logs_are_copied_without_old_answers(self):
        row = payload()
        before = copy.deepcopy(row)
        rules = Rules()
        out = self.build([row], rules=rules)
        self.assertEqual(row, before)
        self.assertEqual(out["samples"][0]["alert_id"], "alert-1")
        self.assertEqual(
            out["samples"][0]["raw_logs"],
            row["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"],
        )
        self.assertEqual(out["rule"]["configuration"], rules.rule["configuration"])
        self.assertNotIn("OLD-MODEL-ANSWER", materials.encode(out).decode())
        out["samples"][0]["raw_logs"][0]["path"] = "mutated"
        self.assertEqual(row, before)

    def test_historical_context_does_not_invent_live_time_window_or_burst(self):
        out = self.build([payload()])
        self.assertEqual(out["material_source"], "historical_export")
        self.assertEqual(out["trigger"], "manual_sample_review")
        self.assertFalse(out["observed_surge"])
        self.assertIsNone(out["rate_window"])
        for forbidden in (
            "current_count",
            "window_start",
            "window_end",
            "signal_id",
            "task_ids",
            "received_unique_alerts",
        ):
            self.assertNotIn(forbidden, materials.encode(out).decode())

    def test_identical_duplicates_count_once_but_conflicting_identity_is_excluded(self):
        conflict = payload("conflict", [{"path": "/a"}])
        other = payload("conflict", [{"path": "/b"}])
        good = payload("good")
        out = self.build([conflict, other, good, copy.deepcopy(good)])
        self.assertEqual([sample["alert_id"] for sample in out["samples"]], ["good"])
        self.assertEqual(out["coverage"]["conflicting_identity_count"], 1)
        self.assertEqual(out["coverage"]["duplicate_target_rows"], 2)

    def test_request_and_dataframe_identity_mismatch_are_not_repaired(self):
        bad = payload("inner")
        bad["alert_id"] = "outer"
        out = self.build([bad, payload("good")], ids=["inner", "wrong"])
        self.assertEqual(out["samples"], [])
        self.assertEqual(out["coverage"]["issue_counts"]["row_not_eligible"], 2)
        self.assertEqual(bad["alert_id"], "outer")

    def test_raw_alert_wrapper_and_numpy_integer_column_are_supported(self):
        out = self.build([payload("123")["alert_data"]], ids=[123])
        self.assertEqual(out["samples"][0]["alert_id"], "123")

    def test_duplicate_json_keys_and_non_json_numbers_are_rejected(self):
        duplicate = '{"alert_id":"one","alert_id":"two"}'
        row = payload()
        row["extra"] = float("nan")
        out = self.build([duplicate, json.dumps(row)])
        self.assertEqual(out["samples"], [])
        self.assertEqual(out["coverage"]["issue_counts"]["row_not_eligible"], 2)

    def test_first_200_rows_and_first_20_log_eligible_alerts_only(self):
        out = self.build([payload(str(i)) for i in range(201)])
        self.assertEqual(out["coverage"]["inspected_rows"], 200)
        self.assertEqual(out["coverage"]["matching_unique_alerts"], 200)
        self.assertEqual(
            [s["alert_id"] for s in out["samples"]], [str(i) for i in range(20)]
        )
        self.assertEqual(out["coverage"]["omitted_alerts"], 180)

    def test_oversized_logs_omitted_whole_and_three_logs_per_alert(self):
        large = {"path": "x" * 8192}
        logs = [large, *[{"path": f"/path-{i}"} for i in range(5)]]
        out = self.build([payload(logs=logs)])
        self.assertEqual(out["samples"][0]["raw_logs"], logs[1:4])
        self.assertEqual(out["coverage"]["invalid_or_oversized_logs"], 1)
        self.assertEqual(out["coverage"]["omitted_logs"], 3)
        self.assertEqual(large["path"], "x" * 8192)

    def test_total_context_budget_preserves_rule_and_each_included_log(self):
        rules = Rules()
        rules.rule["configuration"]["large_config"] = "x" * 30000
        raw_log = {"field": "y" * 7900}
        out = self.build(
            [payload(str(i), [raw_log] * 3) for i in range(20)], rules=rules
        )
        self.assertLessEqual(len(materials.encode(out)), materials.CONTEXT_LIMIT)
        self.assertGreater(out["coverage"]["omitted_logs"], 0)
        self.assertEqual(out["rule"]["configuration"], rules.rule["configuration"])
        self.assertTrue(
            all(log == raw_log for s in out["samples"] for log in s["raw_logs"])
        )

    def test_hit_and_raw_log_inspection_limits(self):
        row = payload(logs=[{"path": str(i)} for i in range(101)])
        out = self.build([row])
        self.assertEqual(out["coverage"]["logs_not_inspected_due_to_limit"], 1)
        excessive = payload()
        excessive["alert_data"]["alert"]["hitLog"] *= 1001
        out = self.build([excessive])
        self.assertEqual(out["samples"], [])

    def test_rule_configuration_is_never_trimmed_to_fit(self):
        rules = Rules()
        rules.rule["configuration"]["oversized"] = "x" * (33 * 1024)
        with self.assertRaisesRegex(ValueError, "rule_configuration_over_budget"):
            self.build([payload()], rules=rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
