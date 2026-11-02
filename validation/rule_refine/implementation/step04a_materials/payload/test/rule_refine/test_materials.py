"""规则分析材料测试与人工演示；不读取真实任务，不连接外部服务。"""

import copy
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_materials_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
build = importlib.import_module(package.__name__ + ".materials").build_rule_materials
common = importlib.import_module(package.__name__ + ".rule_config_source")


def signal(ids=None):
    return {
        "schema_version": "sec_know.rule_burst_signal.v1",
        "mode": "demo",
        "signal_id": "demo-signal",
        "namespace": "demo-namespace",
        "source_app": "zeus",
        "company_code": "demo-company",
        "topic": "demo-topic",
        "rule_code": "DEMO_RULE",
        "current_count": 1000,
        "window_start": "2026-01-01T00:00:00+00:00",
        "window_end": "2026-01-01T01:00:00+00:00",
        "time_basis": "provided_received_at",
        "sample_alert_ids": ["101"] if ids is None else ids,
    }


def row(alert_id="101", task_id="task-a"):
    return {
        "id": task_id,
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": alert_id,
        "created_date": "2026-01-02T10:00:00+00:00",
        "alert_data": {
            "alert": {
                "alertId": alert_id,
                "hitLog": [
                    {"topic": "demo-topic", "ruleCode": "DEMO_RULE", "zeusRawLogs": [{"host": "synthetic.invalid"}]}
                ],
            }
        },
        "agent_response": {"handling": "ignore"},
    }


def rules():
    return Mock(
        get_rule=Mock(
            return_value={
                "company_code": "demo-company",
                "topic": "demo-topic",
                "rule_code": "DEMO_RULE",
                "rule_name": "人工演示规则",
                "rule_config_version": "demo-config-v1",
                "source": {"kind": "synthetic_fixture"},
                "terms": [{"expression": "A"}],
            }
        )
    )


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.rules = rules()

    def call(self, rows=None, sig=None, **options):
        return build(
            signal() if sig is None else sig,
            self.rules,
            [row()] if rows is None else rows,
            source_kind=options.get("source_kind", "synthetic_fixture"),
        )

    def codes(self, result):
        return {item["code"] for item in result["issues"]}

    def test_ready_materials_keep_scope_rule_and_reference_provenance(self):
        result = self.call()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["signal_scope"]["company_code"], "demo-company")
        self.assertEqual(result["rule"]["rule_config_version"], "demo-config-v1")
        self.assertEqual(result["sample_basis"], "signal_sample_alert_ids_only")
        self.assertEqual(result["stored_response_role"], "unverified_reference_not_human_label")
        self.assertEqual(result["samples"][0]["stored_response_status"], "present")
        self.rules.get_rule.assert_called_once_with("DEMO_RULE", topic="demo-topic")

    def test_rule_missing_and_ambiguous_return_insufficient_evidence(self):
        for error, code in [
            (common.RuleNotFoundError, "rule_not_found"),
            (common.AmbiguousRuleError, "rule_ambiguous"),
        ]:
            self.rules.get_rule.side_effect = error("private detail")
            result = self.call()
            self.assertEqual((result["status"], result["samples"]), ("insufficient_evidence", []))
            self.assertEqual(self.codes(result), {code})

    def test_wrong_company_and_malformed_provider_results_are_rejected(self):
        original = self.rules.get_rule.return_value
        for value in [
            None,
            {},
            {**original, "company_code": "other"},
            {**original, "topic": "other"},
            {**original, "source": {}},
        ]:
            with self.subTest(value=value), self.assertRaises(common.RuleConfigError):
                self.rules.get_rule.return_value = value
                self.call()

    def test_duplicate_tasks_keep_one_alert_and_all_task_references(self):
        result = self.call([row(task_id="b"), row(task_id="a")])
        self.assertEqual((len(result["samples"]), result["duplicate_task_rows"]), (1, 1))
        self.assertEqual(result["samples"][0]["task_ids"], ["a", "b"])

    def test_hit_rule_identity_trims_whitespace_like_ingress(self):
        data = row()
        data["alert_data"]["alert"]["hitLog"][0].update(topic=" demo-topic ", ruleCode=" DEMO_RULE\t")
        self.assertEqual(self.call([data])["status"], "ready")

    def test_conflicting_alert_versions_are_excluded_not_arbitrarily_selected(self):
        changed = row(task_id="b")
        changed["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0]["host"] = "different.invalid"
        result = self.call([row(), changed])
        self.assertEqual(result["samples"], [])
        self.assertIn("sample_conflicting_or_invalid", self.codes(result))

    def test_response_conflict_keeps_raw_evidence_without_using_either_verdict(self):
        changed = row(task_id="b")
        changed["agent_response"] = {"handling": "transfer"}
        result = self.call([row(), changed])
        self.assertEqual(result["status"], "partial")
        sample = result["samples"][0]
        self.assertEqual(sample["raw_logs"], row()["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"])
        self.assertEqual((sample["stored_response"], sample["stored_response_status"]), (None, "conflict"))

    def test_bad_logs_or_mismatched_ids_cannot_be_rescued_by_a_valid_copy(self):
        for logs in [None, [], [{}], "raw", [{"large": "x" * 65536}]]:
            changed = row(task_id="bad")
            changed["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"] = logs
            self.assertEqual(self.call([row(), changed])["samples"], [])
        changed = row()
        changed["alert_data"]["alert"]["alertId"] = "different"
        self.assertEqual(self.call([changed])["samples"], [])

    def test_other_apps_flows_rules_and_unrequested_alerts_are_isolated(self):
        unrelated = [row("999"), {**row(), "app_code": "other"}, {**row(), "flow_id": "other"}]
        result = self.call([row(), *unrelated])
        self.assertEqual((result["status"], len(result["samples"]), result["duplicate_task_rows"]), ("ready", 1, 0))
        changed = row()
        changed["alert_data"]["alert"]["hitLog"][0]["ruleCode"] = "OTHER_RULE"
        self.assertEqual(self.call([changed])["samples"], [])
        valid = row()
        valid["alert_data"]["relatedAlertList"] = [changed["alert_data"]["alert"]]
        self.assertEqual(self.call([valid])["samples"][0]["raw_logs"], [{"host": "synthetic.invalid"}])

    def test_unbounded_alert_objects_are_excluded_without_recursion_errors(self):
        deep = []
        for _ in range(40):
            deep = [deep]
        cycle = []
        cycle.append(cycle)
        for label, value in [
            ("depth", deep),
            ("cycle", cycle),
            ("string", "x" * (1024 * 1024 + 1)),
            ("nodes", [None] * 10001),
            ("integer", 1 << 4097),
        ]:
            with self.subTest(case=label):
                data = row()
                data["alert_data"]["extra"] = value
                result = self.call([data])
                self.assertEqual((result["status"], result["samples"]), ("insufficient_evidence", []))
                self.assertIn("invalid_or_oversized_alert_material", self.codes(result))

    def test_oversized_response_is_omitted_but_valid_raw_logs_remain(self):
        data = row()
        data["agent_response"] = {"explanation": "x" * (16 * 1024 + 1)}
        result = self.call([data])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.codes(result), {"stored_response_omitted"})
        sample = result["samples"][0]
        self.assertEqual(sample["raw_logs"], [{"host": "synthetic.invalid"}])
        self.assertEqual((sample["stored_response"], sample["stored_response_status"]), (None, "missing"))

    def test_empty_samples_and_bad_modes_windows_or_bounds(self):
        self.assertEqual(self.call([], signal([]))["status"], "insufficient_evidence")
        self.assertIn("sample_unavailable", self.codes(self.call([])))
        for update in [
            {"mode": "live"},
            {"current_count": True},
            {"current_count": 0},
            {"sample_alert_ids": [str(n) for n in range(21)]},
            {"window_start": "2026-01-01T00:00:00"},
        ]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.call(sig={**signal(), **update})
        with self.assertRaises(ValueError):
            self.call([row()] * 101)
        live = self.call(
            sig={**signal(), "mode": "live", "time_basis": "redis_observed_at"}, source_kind="workflow_task_store"
        )
        self.assertEqual(live["status"], "ready")
        self.assertEqual(live["window"]["time_basis"], "redis_observed_at")

    def test_input_is_unchanged_and_returned_nested_data_is_independent(self):
        sig, rows = signal(), [row()]
        before = copy.deepcopy((sig, rows, self.rules.get_rule.return_value))
        result = self.call(rows, sig)
        self.assertEqual((sig, rows, self.rules.get_rule.return_value), before)
        result["rule"]["terms"][0]["expression"] = "changed"
        result["samples"][0]["raw_logs"][0]["host"] = "changed"
        result["samples"][0]["stored_response"]["handling"] = "changed"
        self.assertEqual((sig, rows, self.rules.get_rule.return_value), before)

    def test_task_creation_time_outside_window_does_not_redefine_receive_time(self):
        self.assertEqual(self.call()["status"], "ready")
        result = self.call([{**row(), "created_date": "1999-01-01T00:00:00Z"}])
        self.assertEqual(result["window"]["count"], 1000)
        self.assertEqual(result["window"]["time_basis"], "provided_received_at")
        self.assertEqual(len(result["samples"]), 1)

    def test_provider_transport_failures_propagate(self):
        failure = OSError("injected transport failure")
        self.rules.get_rule.side_effect = failure
        with self.assertRaises(OSError) as caught:
            self.call()
        self.assertIs(caught.exception, failure)


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        rows = [row(str(n), f"task-{n}") for n in range(101, 104)] + [row("101", "task-retry")]
        result = build(signal(["101", "102", "103"]), rules(), rows, source_kind="synthetic_fixture")
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": result["status"],
                    "input_task_rows": len(rows),
                    "requested_samples": result["requested_sample_count"],
                    "material_samples": len(result["samples"]),
                    "duplicate_task_rows": result["duplicate_task_rows"],
                    "issue_count": len(result["issues"]),
                    "model_calls": 0,
                    "database_calls": 0,
                    "redis_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
