import copy
import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


budget = load("context_budget", HERE / "context_budget.py")
review = load("historical_review", HERE / "review.py")
old_materials = load(
    "historical_materials", HERE.parent / "step06x_historical_review/materials.py"
)
materials = load("historical_batch_materials", HERE / "materials.py")
package = types.ModuleType("_historical_batch_materials_installed_6w")
package.__path__ = [
    str(HERE.parent / "step06w_unified_limits/payload/my_workflows/zeus/rule_refine")
]
sys.modules[package.__name__] = package
analysis = importlib.import_module(package.__name__ + ".analysis")
Limits = budget.ContextBudget
filter_evaluation = importlib.import_module(package.__name__ + ".filter_evaluation")
budget.install_compatibility(
    SimpleNamespace(analysis=analysis, filter_evaluation=filter_evaluation)
)
inventory = load(
    "_historical_batch_inventory",
    HERE.parent / "step06o_ingress_readiness/materials_check.py",
)


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


def entries(plan):
    return [
        (sample["alert_id"], log, ref)
        for context in plan["batches"]
        for sample in context["samples"]
        for log, ref in zip(sample["raw_logs"], sample["log_refs"], strict=True)
    ]


class HistoricalBatchMaterialsTests(unittest.TestCase):
    def build(self, rows, *, ids=None, rules=None, limits=None, index=None):
        data = {
            "alert_full_data": rows,
            "agent_response": ["OLD-MODEL-ANSWER-NOT-EVIDENCE"] * len(rows),
        }
        if ids is not None:
            data["alert_id"] = ids
        frame = pd.DataFrame(data, index=index)
        before = copy.deepcopy(frame)
        selected_rules = rules or Rules()
        before_rule = copy.deepcopy(selected_rules.rule)
        plan = materials.build_plan(
            frame,
            selected_rules,
            inventory,
            analysis=analysis,
            limits=limits or Limits(),
        )
        pd.testing.assert_frame_equal(frame, before)
        self.assertEqual(selected_rules.rule, before_rule)
        self.assertNotIn("OLD-MODEL-ANSWER-NOT-EVIDENCE", json.dumps(plan))
        return plan

    def assert_plan(self, plan, limits=None):
        limits = limits or Limits()
        observed = entries(plan)
        refs = {
            (
                identity,
                ref["source_row_position_zero_based"],
                ref["hit_index"],
                ref["raw_log_index"],
            )
            for identity, _, ref in observed
        }
        coverage = plan["coverage"]
        self.assertEqual(len(refs), len(observed))
        self.assertEqual(coverage["included_logs"], len(refs))
        self.assertEqual(coverage["planned_logs"], len(refs))
        self.assertEqual(coverage["eligible_logs"], len(refs))
        self.assertEqual(
            coverage["included_alerts"], len({identity for identity, _, _ in observed})
        )
        self.assertTrue(coverage["all_eligible_logs_planned"])
        self.assertEqual(coverage["inspected_rows"], coverage["file_rows"])
        self.assertEqual(coverage["logs_not_inspected_due_to_limit"], 0)
        for index, context in enumerate(plan["batches"], 1):
            request = review.build_request(context, analysis, limits=limits)
            self.assertEqual(context["batch_index"], index)
            self.assertLessEqual(
                request["estimated_input_tokens"], limits.input_token_budget
            )
            self.assertEqual(
                request["estimated_input_tokens"],
                plan["estimated_tokens_per_batch"][index - 1],
            )
            self.assertFalse(request["token_count_exact"])
            self.assertEqual(
                request["input_bytes"], plan["input_bytes_per_batch"][index - 1]
            )
            self.assertTrue(context["samples"])
            self.assertEqual(
                context["coverage"]["included_logs"],
                sum(len(sample["raw_logs"]) for sample in context["samples"]),
            )

    def test_scans_all225_rows_and_keeps_them_together_when_capacity_allows(self):
        plan = self.build([payload(f"alert-{i}") for i in range(225)])
        self.assert_plan(plan)
        self.assertEqual(plan["coverage"]["included_alerts"], 225)
        self.assertEqual(len(plan["batches"]), 1)
        self.assertTrue(plan["coverage"]["complete_source_coverage"])
        self.assertEqual(entries(plan)[-1][2]["source_row_position_zero_based"], 224)

    def test_50_alerts_51_whole_logs_covered_without_silent_budget_omission(self):
        rows = [payload(f"alert-{i}", [{"value": "测试" * 1600}]) for i in range(50)]
        rows[23]["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"].append(
            {"extra": "unaltered"}
        )
        plan = self.build(rows)
        self.assert_plan(plan)
        self.assertEqual(plan["coverage"]["included_alerts"], 50)
        self.assertEqual(plan["coverage"]["included_logs"], 51)
        self.assertEqual(len(plan["batches"]), 1)
        self.assertGreater(plan["input_bytes_per_batch"][0], 400000)
        self.assertEqual(plan["coverage"]["budget_omitted_logs"], 0)
        self.assertEqual(plan["coverage"]["omitted_logs"], 0)

    def test_same_alert_spans_batches_without_global_double_count(self):
        logs = [{"position": i, "value": "x" * 15000} for i in range(19)]
        limits = Limits(input_fraction=0.08)
        plan = self.build([payload("split-alert", logs)], limits=limits)
        self.assert_plan(plan, limits)
        self.assertGreater(len(plan["batches"]), 1)
        self.assertEqual(plan["coverage"]["included_alerts"], 1)
        self.assertEqual(plan["coverage"]["included_logs"], 19)
        self.assertEqual([entry[1] for entry in entries(plan)], logs)
        self.assertEqual(
            [entry[2]["raw_log_index"] for entry in entries(plan)], list(range(19))
        )

    def test_all50_alert_references_are_validated_in_one_model_answer(self):
        plan = self.build(
            [payload(f"alert-{i}", [{"path": "/health"}]) for i in range(50)]
        )
        self.assertEqual(len(plan["batches"]), 1)
        context = plan["batches"][0]
        reply = json.dumps(
            {
                "status": "proposal",
                "issue": "人工示例：健康探测需要运营确认。",
                "recommendation": "人工示例：确认业务后再考虑过滤。",
                "certainty": "仅为结构与链路测试。",
                "sample_alert_ids": [
                    sample["alert_id"] for sample in context["samples"]
                ],
                "filter": {
                    "topic": materials.TARGET[0],
                    "expression": "A",
                    "A": {"name": "path", "value": "/health", "operator": "equal"},
                },
            }
        )
        result = review.analyze(
            context,
            lambda messages: reply,
            analysis=analysis,
            evaluator=filter_evaluation.evaluate_filter,
            save=lambda name, data: None,
            limits=Limits(),
            response_source="synthetic_reply",
        )
        self.assertEqual(result["status"], "proposal", result)
        self.assertEqual(result["cited_sample_count"], 50)
        self.assertEqual(
            result["evaluation"]["counts"],
            {"matched": 50, "not_matched": 0, "unknown": 0},
        )

    def test_no_three_or_hundred_log_cutoff_or_thousand_hit_cutoff(self):
        row = payload(logs=[{"position": i} for i in range(105)])
        hit = row["alert_data"]["alert"]["hitLog"][0]
        row["alert_data"]["alert"]["hitLog"] += [
            {**copy.deepcopy(hit), "zeusRawLogs": [{"hit": i}]} for i in range(1001)
        ]
        plan = self.build([row])
        self.assert_plan(plan)
        self.assertEqual(plan["coverage"]["included_logs"], 1106)
        self.assertEqual(len(plan["batches"]), 1)
        self.assertEqual(entries(plan)[-1][2]["hit_index"], 1001)

    def test_over100_logs_one_request_executes_all_local_proposal_checks(self):
        evaluator = importlib.import_module(package.__name__ + ".filter_evaluation")
        plan = self.build(
            [payload("many-logs", [{"path": "/health", "n": i} for i in range(105)])]
        )
        self.assertEqual(
            [context["coverage"]["included_logs"] for context in plan["batches"]],
            [105],
        )
        matched_references = set()
        for context in plan["batches"]:
            reply = json.dumps(
                {
                    "status": "proposal",
                    "issue": "人工示例：健康探测需要运营确认。",
                    "recommendation": "人工示例：确认业务后再考虑过滤。",
                    "certainty": "仅为结构与链路测试。",
                    "sample_alert_ids": ["many-logs"],
                    "filter": {
                        "topic": materials.TARGET[0],
                        "expression": "A",
                        "A": {"name": "path", "value": "/health", "operator": "equal"},
                    },
                }
            )
            result = review.analyze(
                context,
                lambda messages: reply,
                analysis=analysis,
                evaluator=evaluator.evaluate_filter,
                save=lambda name, data: None,
                limits=Limits(),
                response_source="synthetic_reply",
            )
            self.assertEqual(result["status"], "proposal", result)
            self.assertTrue(result["local_evaluation_completed"])
            sample = context["samples"][0]
            for item in result["evaluation"]["results"]:
                self.assertEqual(item["status"], "matched")
                ref = sample["log_refs"][item["log_index"]]
                matched_references.add(
                    (
                        ref["source_row_position_zero_based"],
                        ref["hit_index"],
                        ref["raw_log_index"],
                    )
                )
        self.assertEqual(len(matched_references), 105)

    def test_duplicate_alert_payload_is_counted_once_and_first_lineage_kept(self):
        row = payload("same", [{"path": "/a"}, {"path": "/a"}])
        second = copy.deepcopy(row)
        second["session_id"] = "different-wrapper-only"
        plan = self.build([row, second, json.dumps(row)], index=[800, 900, 1000])
        self.assert_plan(plan)
        self.assertEqual(plan["coverage"]["duplicate_payload_rows"], 2)
        self.assertEqual(plan["coverage"]["duplicate_target_rows"], 2)
        self.assertEqual(plan["coverage"]["included_logs"], 2)
        self.assertEqual(
            [entry[2]["source_row_position_zero_based"] for entry in entries(plan)],
            [0, 0],
        )
        self.assertEqual([entry[2]["raw_log_index"] for entry in entries(plan)], [0, 1])

    def test_conflicting_identity_excluded_even_if_other_variant_nonmatching(self):
        changed = payload("conflict")
        changed["alert_data"]["alert"]["hitLog"][0]["ruleCode"] = "OTHER"
        rows = [payload("conflict"), payload("valid"), changed]
        plan = self.build(rows)
        self.assert_plan(plan)
        self.assertEqual([entry[0] for entry in entries(plan)], ["valid"])
        self.assertEqual(plan["coverage"]["conflicting_identity_count"], 1)
        self.assertEqual(plan["coverage"]["excluded_conflicting_target_alerts"], 1)
        self.assertEqual(plan["coverage"]["excluded_conflicting_target_logs"], 1)
        self.assertFalse(plan["coverage"]["complete_source_coverage"])

    def test_late_conflict_after_first200_excludes_earlier_variant(self):
        rows = [payload(f"alert-{i}") for i in range(205)]
        rows.append(payload("alert-0", [{"different": True}]))
        plan = self.build(rows)
        self.assert_plan(plan)
        self.assertEqual(plan["coverage"]["included_alerts"], 204)
        self.assertNotIn("alert-0", [entry[0] for entry in entries(plan)])
        self.assertEqual(plan["coverage"]["excluded_conflicting_target_logs"], 2)

    def test_invalid_json_rows_and_logs_have_explicit_reason_counts(self):
        bad_hits = payload("bad-hits")
        bad_hits["alert_data"]["alert"]["hitLog"] = "not-a-list"
        bad_list = payload("bad-list")
        bad_list["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"] = "not-a-list"
        row = payload("mixed", [None, {}, [], "text", {"original": "kept"}])
        plan = self.build(
            ["not-json", '{"a":1,"a":2}', bad_hits, bad_list, payload("empty", []), row]
        )
        self.assert_plan(plan)
        c = plan["coverage"]
        self.assertEqual(c["invalid_rows"], 3)
        self.assertEqual(sum(c["row_issue_counts"].values()), 3)
        self.assertEqual(c["invalid_logs"], 4)
        self.assertEqual(
            c["log_issue_counts"], {"log_not_object": 3, "empty_log_object": 1}
        )
        self.assertEqual(c["issue_counts"]["raw_logs_not_list"], 1)
        self.assertEqual(c["issue_counts"]["empty_raw_logs_list"], 1)
        self.assertEqual(c["included_logs"], 1)
        self.assertEqual(c["omitted_logs"], 4)
        self.assertFalse(c["complete_source_coverage"])

    def test_structural_2mib_row_guard_is_labelled_not_silently_sampled(self):
        plan = self.build(
            [payload("huge", [{"body": "x" * (2 * 1024 * 1024)}]), payload("small")]
        )
        self.assert_plan(plan)
        self.assertEqual(plan["coverage"]["row_issue_counts"]["row_over_budget"], 1)
        self.assertEqual(plan["coverage"]["invalid_rows"], 1)
        self.assertFalse(plan["coverage"]["complete_source_coverage"])

    def test_single_valid_log_over_request_budget_stops_entire_plan(self):
        frame = pd.DataFrame(
            {
                "alert_full_data": [
                    payload("fits"),
                    payload("large", [{"whole": "z" * 850000}]),
                ]
            }
        )
        before = copy.deepcopy(frame)
        with self.assertRaises(materials.PlanningError) as caught:
            materials.build_plan(
                frame,
                Rules(),
                inventory,
                analysis=analysis,
                limits=Limits(),
            )
        error = caught.exception
        self.assertEqual(error.code, "historical_single_log_over_budget")
        self.assertEqual(error.details["alert_id"], "large")
        self.assertEqual(error.details["log_ref"]["source_row_position_zero_based"], 1)
        self.assertGreater(error.details["required_estimated_tokens"], 800000)
        self.assertEqual(error.details["input_token_budget"], 800000)
        self.assertEqual(error.coverage["eligible_logs"], 2)
        self.assertEqual(error.coverage["planned_logs"], 0)
        self.assertEqual(error.details["model_calls"], 0)
        pd.testing.assert_frame_equal(frame, before)

    def test_fixed_rule_over_budget_stops_plan_with_required_size(self):
        rules = Rules()
        rules.rule["configuration"]["full_rule"] = "x" * 850000
        with self.assertRaises(materials.PlanningError) as caught:
            self.build([payload()], rules=rules, limits=Limits())
        self.assertEqual(caught.exception.code, "historical_fixed_content_over_budget")
        self.assertGreater(
            caught.exception.details["required_estimated_tokens"], 800000
        )

    def test_complete_messages_budget_handles_escaping_and_utf8(self):
        rows = [payload(f"a-{i}", [{"request": '中文\\"' * 2000}]) for i in range(5)]
        limits = Limits(input_fraction=0.08)
        plan = self.build(rows, limits=limits)
        self.assert_plan(plan, limits)
        self.assertEqual(len(entries(plan)), 5)
        self.assertGreater(len(plan["batches"]), 1)

    def test_plan_deterministic_and_raw_logs_independent_of_source(self):
        rows = [payload(str(i), [{"body": "a" * 18000}]) for i in range(20)]
        before = copy.deepcopy(rows)
        one, two = self.build(rows), self.build(rows)
        self.assertEqual(one, two)
        one["batches"][0]["samples"][0]["raw_logs"][0]["body"] = "modified-output"
        self.assertEqual(rows, before)

    def test_numeric_identity_and_empty_valid_scope(self):
        plan = self.build([payload(123)], ids=[123])
        self.assert_plan(plan)
        self.assertEqual(entries(plan)[0][0], "123")
        row = payload()
        row["alert_data"]["alert"]["hitLog"][0]["topic"] = "other"
        plan = self.build([row])
        self.assertEqual(plan["batches"], [])
        self.assertEqual(plan["coverage"]["included_logs"], 0)


if __name__ == "__main__":
    unittest.main()
