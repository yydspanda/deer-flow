import copy
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

import pandas as pd

import review

sys.modules["historical_review"] = review
materials = importlib.import_module("materials")

HERE = Path(__file__).resolve().parent
package = types.ModuleType("_historical_materials_installed_6w")
package.__path__ = [
    str(HERE.parent / "step06w_unified_limits/payload/my_workflows/zeus/rule_refine")
]
sys.modules[package.__name__] = package
analysis = importlib.import_module(package.__name__ + ".analysis")
Limits = importlib.import_module(package.__name__ + ".limits").Limits
spec = importlib.util.spec_from_file_location(
    "_historical_inventory_x",
    HERE.parent / "step06o_ingress_readiness/materials_check.py",
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
                        "zeusRawLogs": [{"path": "/original"}]
                        if logs is None
                        else logs,
                    }
                ],
            },
            "relatedAlertList": [],
        },
    }


class HistoricalMaterialsTests(unittest.TestCase):
    def build(self, rows, *, ids=None, rules=None, limits=None):
        data = {
            "alert_full_data": rows,
            "agent_response": ["OLD-MODEL-ANSWER"] * len(rows),
        }
        if ids is not None:
            data["alert_id"] = ids
        return materials.build_context(
            pd.DataFrame(data),
            rules or Rules(),
            inventory,
            analysis=analysis,
            limits=limits or Limits(),
        )

    def assert_coverage(self, context):
        c = context["coverage"]
        self.assertEqual(
            c["available_logs_in_nonconflicting_alerts"],
            c["included_logs"]
            + c["invalid_logs"]
            + c["budget_omitted_logs"]
            + c["selection_omitted_logs"],
        )
        self.assertEqual(
            c["omitted_alerts"], c["eligible_unique_alerts"] - c["included_alerts"]
        )
        self.assertEqual(
            c["omitted_logs"],
            c["available_logs_in_nonconflicting_alerts"] - c["included_logs"],
        )
        request = review.build_request(
            context, analysis, limits=Limits(), require_samples=False
        )
        self.assertLessEqual(request["input_bytes"], 128 * 1024)

    def test_complete_logs_and_identity_unchanged_without_old_model_answers(self):
        row = payload()
        before = copy.deepcopy(row)
        out = self.build([row])
        self.assertEqual(row, before)
        self.assertEqual(out["samples"][0]["alert_id"], "alert-1")
        self.assertEqual(
            out["samples"][0]["raw_logs"],
            row["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"],
        )
        self.assertNotIn("OLD-MODEL-ANSWER", json.dumps(out))
        self.assertFalse(out["historical_model_answers_included"])
        out["samples"][0]["raw_logs"][0]["path"] = "mutated"
        self.assertEqual(row, before)

    def test_historical_source_does_not_manufacture_live_signal(self):
        out = self.build([payload()])
        self.assertFalse(out["observed_surge"])
        self.assertIsNone(out["rate_window"])
        self.assertEqual(out["trigger"], "manual_sample_review")
        for name in (
            "window_start",
            "current_count",
            "signal_id",
            "task_ids",
            "received_unique_alerts",
        ):
            self.assertNotIn(name, json.dumps(out))

    def test_identical_duplicate_once_but_conflicting_identity_is_excluded(self):
        rows = [
            payload("same"),
            payload("same"),
            payload("conflict"),
            payload("conflict", [{"path": "/other"}]),
        ]
        out = self.build(rows)
        self.assertEqual([s["alert_id"] for s in out["samples"]], ["same"])
        self.assertEqual(out["coverage"]["conflicting_identity_count"], 1)
        self.assertEqual(out["coverage"]["duplicate_target_rows"], 2)

    def test_conflicting_identity_in_nonmatching_rule_also_excluded(self):
        other = payload("same")
        other["alert_data"]["alert"]["hitLog"][0]["ruleCode"] = "OTHER"
        out = self.build([payload("same"), other])
        self.assertEqual(out["samples"], [])
        self.assertEqual(out["coverage"]["excluded_conflicting_target_alerts"], 1)

    def test_wrapper_or_column_identity_mismatch_is_not_repaired(self):
        row = payload()
        row["alert_id"] = "different"
        self.assertEqual(self.build([row])["samples"], [])
        self.assertEqual(self.build([payload()], ids=["different"])["samples"], [])

    def test_raw_wrapper_and_integer_column_supported(self):
        row = payload("12")
        out = self.build([row["alert_data"]], ids=[12])
        self.assertEqual(out["samples"][0]["alert_id"], "12")

    def test_duplicate_keys_non_json_number_and_bad_shape_rejected(self):
        good = json.dumps(payload())
        duplicate = good.replace(
            '"alert_id": "alert-1"', '"alert_id": "alert-1", "alert_id": "alert-1"'
        )
        invalid_number = json.dumps(payload("bad", [{"n": float("nan")}]))
        out = self.build([duplicate, invalid_number, None])
        self.assertEqual(out["coverage"]["issue_counts"]["row_not_eligible"], 3)
        self.assertEqual(out["samples"], [])

    def test_first_200_rows_and_20_sample_selection(self):
        rows = [payload(str(i)) for i in range(214)]
        out = self.build(rows)
        self.assertEqual(out["coverage"]["inspected_rows"], 200)
        self.assertEqual(out["coverage"]["eligible_unique_alerts"], 200)
        self.assertEqual(
            [s["alert_id"] for s in out["samples"]], [str(i) for i in range(20)]
        )
        self.assertEqual(out["coverage"]["selection_omitted_alerts"], 180)
        self.assert_coverage(out)

    def test_large_log_qualifies_and_default_budget_keeps_one_hundred_kib(self):
        log = {"body": "x" * (100 * 1024)}
        out = self.build([payload(logs=[log])])
        self.assertEqual(out["samples"][0]["raw_logs"], [log])
        self.assertEqual(out["coverage"]["invalid_logs"], 0)
        self.assert_coverage(out)

    def test_large_rule_no_longer_rejected_at_32_kib(self):
        rules = Rules()
        rules.rule["configuration"]["full_rule"] = "x" * (60 * 1024)
        out = self.build([payload()], rules=rules)
        self.assertEqual(out["rule"]["configuration"], rules.rule["configuration"])
        self.assertEqual(len(out["samples"]), 1)
        self.assert_coverage(out)

    def test_fixed_rule_larger_than_full_budget_is_not_trimmed(self):
        rules = Rules()
        rules.rule["configuration"]["full_rule"] = "x" * (130 * 1024)
        before = copy.deepcopy(rules.rule)
        with self.assertRaisesRegex(ValueError, "historical_fixed_content_over_budget"):
            self.build([payload()], rules=rules)
        self.assertEqual(rules.rule, before)

    def test_dense_fields_use_byte_budget_not_inventory_node_count(self):
        log = {"f" + str(i): "" for i in range(11000)}
        row = payload(logs=[log])
        with self.assertRaises(ValueError):
            inventory.bounded_json_shape(row)
        out = self.build([row], limits=Limits(input_kib=256))
        self.assertEqual(out["samples"][0]["raw_logs"], [log])

    def test_whole_request_counts_escaping_and_separates_budget_omissions(self):
        logs = [{"body": '"\\\n' * 6000} for _ in range(3)]
        out = self.build([payload(logs=logs)])
        c = out["coverage"]
        self.assertGreater(c["budget_omitted_logs"], 0)
        self.assertEqual(c["invalid_logs"], 0)
        for item in out["samples"][0]["raw_logs"]:
            self.assertIn(item, logs)
        self.assert_coverage(out)

    def test_valid_logs_survive_invalid_siblings_and_limits_are_separate(self):
        logs = [None, {}, "not-an-object"] + [{"n": i} for i in range(110)]
        out = self.build([payload(logs=logs)])
        c = out["coverage"]
        self.assertEqual(c["invalid_logs"], 3)
        self.assertEqual(c["logs_not_inspected_due_to_limit"], 13)
        self.assertEqual(c["included_logs"], 3)
        self.assertEqual(c["budget_omitted_logs"], 0)
        self.assert_coverage(out)

    def test_row_limit_and_hits_limit_are_reported_before_model(self):
        row = payload(logs=[{"body": "x" * (2 * 1024 * 1024)}])
        out = self.build([row])
        self.assertEqual(out["coverage"]["issue_counts"], {"row_not_eligible": 1})
        row = payload()
        row["alert_data"]["alert"]["hitLog"] *= 1001
        out = self.build([row])
        self.assertEqual(
            out["coverage"]["issue_counts"], {"invalid_or_excessive_hits": 1}
        )

    def test_rule_identity_checked_before_selection_and_long_name_supported(self):
        rules = Rules()
        rules.rule["rule_name"] = "规则" * 300
        self.assertEqual(len(self.build([payload()], rules=rules)["samples"]), 1)
        rules.rule["company_code"] = " SYNTHETIC"
        with self.assertRaises(ValueError):
            self.build([payload()], rules=rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
