"""Pure offline eligibility audit; synthetic frames and actual staged helpers."""

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

import pandas as pd

import log_audit

HERE = Path(__file__).resolve().parent


def load(name, source):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


materials = load(
    "log_audit_historical_materials", "step06r_historical_review/materials.py"
)
inventory = load("log_audit_inventory", "step06o_ingress_readiness/materials_check.py")


def hit(logs, target=None):
    topic, rule = target or materials.TARGET
    return {"topic": topic, "ruleCode": rule, "zeusRawLogs": logs}


def payload(identity="SENSITIVE-ALERT-ID", logs=None, hits=None):
    return {
        "alert": {
            "alertId": identity,
            "hitLog": hits
            if hits is not None
            else [hit(logs if logs is not None else [{"path": "/private"}])],
        },
        "relatedAlertList": [],
    }


class LogAuditTests(unittest.TestCase):
    def audit(self, rows):
        frame = pd.DataFrame(
            {
                "alert_full_data": rows,
                "agent_response": ["OLD-ANSWER-SECRET"] * len(rows),
            }
        )
        return log_audit.audit_logs(frame, materials, inventory)

    def test_whole_log_categories_and_exact_utf8_sizes(self):
        exact = {"value": "x" * (8192 - len(materials.encode({"value": ""})))}
        oversized = {"value": exact["value"] + "x"}
        chinese = {"中文": "值"}
        out = self.audit(
            [payload(logs=[exact, oversized, chinese, {}, None, "RAW-SECRET", []])]
        )
        self.assertEqual(
            out["log_categories"],
            {
                "eligible": 2,
                "non_object": 3,
                "empty_object": 1,
                "invalid_encoding": 0,
                "oversized": 1,
            },
        )
        self.assertEqual(out["invalid_or_oversized_logs"], 5)
        sizes = out["serialized_sizes"]
        self.assertEqual(sizes["count"], 3)
        self.assertEqual(sizes["minimum_bytes"], len(materials.encode(chinese)))
        self.assertEqual(sizes["maximum_bytes"], 8193)
        self.assertEqual(sizes["bins"]["over_4_up_to_8_kib"], 1)
        self.assertEqual(sizes["bins"]["over_8_up_to_16_kib"], 1)

    def test_other_rules_and_topics_are_not_audited(self):
        out = self.audit(
            [
                payload(
                    hits=[
                        hit([{"ok": "1"}]),
                        hit([{}, {}], (materials.TARGET[0], "OTHER")),
                        hit([None], ("OTHER", materials.TARGET[1])),
                    ]
                ),
                payload("only-other", hits=[hit([{}], ("other", "other"))]),
            ]
        )
        self.assertEqual(out["matching_unique_alerts"], 1)
        self.assertEqual(out["logs_inspected"], 1)
        self.assertEqual(out["invalid_or_oversized_logs"], 0)

    def test_duplicates_count_once_and_conflicts_are_excluded(self):
        one = payload("conflict", [{"key": "one"}])
        two = payload("conflict", [{"key": "two"}])
        good = payload("good")
        out = self.audit([one, two, good, copy.deepcopy(good)])
        self.assertEqual(out["matching_unique_alerts"], 2)
        self.assertEqual(out["conflicting_identity_count"], 1)
        self.assertEqual(out["excluded_conflicting_target_alerts"], 1)
        self.assertEqual(out["duplicate_target_rows"], 2)
        self.assertEqual(out["nonconflicting_target_alerts"], 1)
        self.assertEqual(out["logs_inspected"], 1)

    def test_conflict_with_other_rule_still_excludes_target_identity(self):
        out = self.audit(
            [payload("same"), payload("same", hits=[hit([{}], ("other", "other"))])]
        )
        self.assertEqual(out["excluded_conflicting_target_alerts"], 1)
        self.assertEqual(out["logs_inspected"], 0)

    def test_multiple_target_hits_share_the_first_100_log_limit(self):
        out = self.audit(
            [
                payload(
                    hits=[
                        hit([{}] * 60),
                        hit([None] * 40 + [{"later": "not inspected"}] * 9),
                    ]
                )
            ]
        )
        self.assertEqual(out["available_logs_in_nonconflicting_alerts"], 109)
        self.assertEqual(out["logs_inspected"], 100)
        self.assertEqual(out["logs_not_inspected_due_to_limit"], 9)
        self.assertEqual(out["log_categories"]["empty_object"], 60)
        self.assertEqual(out["log_categories"]["non_object"], 40)
        self.assertEqual(out["alerts_with_eligible_logs"], 0)
        self.assertEqual(out["alerts_without_eligible_logs"], 1)
        self.assertIsNone(out["serialized_sizes"]["minimum_bytes"])

    def test_alert_eligibility_includes_all_candidates_before_three_log_selection(self):
        out = self.audit(
            [
                payload("good", [{}] + [{"path": "/x"}] * 4),
                payload("bad", [{}]),
                payload("empty", []),
            ]
        )
        self.assertEqual(out["nonconflicting_target_alerts"], 3)
        self.assertEqual(out["alerts_with_eligible_logs"], 1)
        self.assertEqual(out["alerts_without_eligible_logs"], 2)
        self.assertEqual(out["log_categories"]["eligible"], 4)
        self.assertFalse(out["limits"]["model_selection_recomputed"])

    def test_row_and_hit_limits_match_original_inspection(self):
        rows = [payload(str(i)) for i in range(200)] + [payload("uninspected", [{}])]
        out = self.audit(rows)
        self.assertEqual(out["file_rows"], 201)
        self.assertEqual(out["inspected_rows"], 200)
        self.assertEqual(out["logs_inspected"], 200)
        excessive = self.audit([payload(hits=[hit([{}])] * 1001)])
        self.assertEqual(excessive["logs_inspected"], 0)
        self.assertEqual(excessive["issue_counts"], {"invalid_or_excessive_hits": 1})

    def test_bad_row_and_raw_log_container_count_fixed_reasons_only(self):
        out = self.audit(["{not-json-SECRET", payload(hits=[hit("NOT-A-LIST-SECRET")])])
        self.assertEqual(
            out["issue_counts"], {"row_not_eligible": 1, "raw_logs_not_list": 1}
        )
        self.assertEqual(out["alerts_without_eligible_logs"], 1)
        self.assertNotIn("SECRET", json.dumps(out))

    def test_encoding_failures_are_distinct_and_invalid_row_is_not_partly_audited(self):
        for log in ({"value": float("nan")}, {"value": object()}):
            with self.subTest(value_type=type(log["value"]).__name__):
                self.assertEqual(
                    log_audit._classify(log, materials), ("invalid_encoding", None)
                )
                out = self.audit([payload(logs=[{"good": "log"}, log])])
                self.assertEqual(out["logs_inspected"], 0)
                self.assertEqual(out["issue_counts"], {"row_not_eligible": 1})

    def test_output_is_aggregate_and_does_not_mutate_or_expose_values(self):
        row = payload(
            "NEVER-EXPORT-THIS-ID", [{"private-key-marker": "NEVER-EXPORT-THIS-VALUE"}]
        )
        before = copy.deepcopy(row)
        out = self.audit([row])
        self.assertEqual(row, before)
        text = json.dumps(out)
        for secret in (
            "NEVER-EXPORT",
            "private-key-marker",
            "OLD-ANSWER-SECRET",
            "raw_logs",
            'alert_id"',
        ):
            self.assertNotIn(secret, text)
        self.assertFalse(out["alert_identities_exported"])
        self.assertFalse(out["raw_values_exported"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
