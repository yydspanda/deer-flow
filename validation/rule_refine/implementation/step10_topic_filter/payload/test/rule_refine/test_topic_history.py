"""Synthetic source tests: original business time/types, no live database."""

import copy
import importlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

package = types.ModuleType("_topic_history_test")
package.__path__ = [
    str(Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine")
]
sys.modules[package.__name__] = package
module = importlib.import_module(package.__name__ + ".topic_history")


class Rules:
    def __init__(self):
        self.rule = {
            "company_code": "company",
            "topic": "topic",
            "rule_code": "rule",
            "rule_name": "Synthetic",
            "rule_config_version": "synthetic-v1",
            "configuration": {"terms": []},
        }

    def get_rule(self, code, *, topic, company_code):
        if (company_code, topic, code) != ("company", "topic", "rule"):
            raise ValueError("wrong_scope")
        return self.rule

    def list_rules(self):
        return [self.rule]


def row(
    identity, when="2026-11-05T10:00:00", execute_type=3, *, topic="topic", rule="rule"
):
    return identity, {
        "alert": {
            "alertId": identity,
            "createAt": when,
            "executeType": execute_type,
            "hitLog": [
                {
                    "topic": topic,
                    "ruleCode": rule,
                    "zeusRawLogs": [{"command": "synthetic --inventory"}],
                }
            ],
        }
    }


class TopicHistoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row("current")]
        self.source = module.TopicTaskSource(
            Rules(), row_loader=lambda: iter(self.rows)
        )
        self.signal = {
            "source_app": "zeus",
            "company_code": "company",
            "topic": "topic",
            "rule_code": "rule",
            "sample_alert_ids": ["current"],
            "current_alert_ids": ["current"],
        }

    def test_nearest_twenty_uses_business_time_and_keeps_originals(self):
        now = datetime(2026, 11, 5, 10)
        self.rows.extend(
            row(f"h{i:02}", (now - timedelta(days=i + 1)).isoformat(), 0)
            for i in range(24)
        )
        self.rows.append(copy.deepcopy(self.rows[1]))
        case = self.source.load_case(self.signal)
        self.assertEqual(
            [s["alert_id"] for s in case["history_samples"]],
            [f"h{i:02}" for i in range(20)],
        )
        self.assertEqual(case["execute_type"]["values"], [3])
        self.assertEqual(case["execute_type"]["status"], "resolved")
        self.assertEqual(case["coverage"]["duplicate_identity_rows"], 1)
        self.assertFalse(case["source"]["historical_model_answers_included"])
        self.assertFalse(case["source"]["task_created_date_used"])

    def test_all_current_ids_control_type_not_signal_sample_or_history(self):
        self.rows.extend([row("other", execute_type=0), row("older", "2026-01-01", 1)])
        self.signal["current_alert_ids"].append("other")
        case = self.source.load_case(self.signal)
        self.assertEqual(case["execute_type"]["values"], [0, 3])
        self.assertEqual(case["execute_type"]["status"], "needs_confirmation")
        self.assertEqual(case["execute_type"]["current_alert_count"], 2)

    def test_missing_invalid_all_and_boolean_type_never_default(self):
        for value in (None, -1, True, False, 3.0, " 3", [], "all"):
            with self.subTest(value=value):
                self.rows[:] = [row("current", execute_type=value)]
                outcome = self.source.load_case(self.signal)["execute_type"]
                self.assertEqual(outcome["status"], "needs_confirmation")
                self.assertEqual(outcome["values"], [])
                self.assertEqual(outcome["missing_or_invalid_count"], 1)

    def test_string_business_type_is_explicit_not_environment_derived(self):
        self.rows[:] = [row("current", execute_type="3")]
        self.assertEqual(
            self.source.load_case(self.signal)["execute_type"]["values"], [3]
        )

    def test_conflicts_missing_anchors_and_invalid_logs_are_not_partial_success(self):
        cases = [[], [row("current"), row("current", execute_type=0)]]
        invalid = row("current")
        invalid[1]["alert"]["hitLog"][0]["zeusRawLogs"] = ["not-json"]
        cases.append([invalid])
        for rows in cases:
            with self.subTest(rows=len(rows)):
                self.rows[:] = rows
                with self.assertRaises(
                    module.SurgeTaskSource.load_case.__globals__[
                        "MaterialsPendingError"
                    ]
                ):
                    self.source.load_case(self.signal)

    def test_other_rule_logs_are_excluded_and_scope_is_checked(self):
        self.rows.append(row("other-rule", rule="different"))
        case = self.source.load_case(self.signal)
        self.assertEqual(len(case["current_samples"]), 1)
        self.assertEqual(case["coverage"]["matching_unique_alerts"], 1)
        with self.assertRaises(ValueError):
            self.source.load_case({**self.signal, "source_app": "different"})

    def test_database_loader_selects_only_original_columns_and_rolls_back(self):
        try:
            from sqlalchemy import Column, JSON, MetaData, String, Table
        except ImportError:
            self.skipTest("SQLAlchemy is a dependency of the target application")
        table = Table(
            "synthetic_workflow",
            MetaData(),
            Column("alert_id", String),
            Column("alert_data", JSON),
            Column("app_code", String),
            Column("flow_id", String),
            Column("agent_response", JSON),
        )
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.dialect.name = "postgresql"
        connection.execution_options.return_value = connection
        result = MagicMock()
        result.yield_per.return_value = iter([row("current")])
        connection.execute.return_value = result
        engine = MagicMock(connect=MagicMock(return_value=connection))
        self.assertEqual(len(list(module.iter_workflow_rows(engine, table))), 1)
        sql = str(connection.execute.call_args.args[0])
        self.assertNotIn("agent_response", sql)
        self.assertIn("alert_id", sql)
        self.assertIn("alert_data", sql)
        self.assertEqual(
            set(connection.execute.call_args.args[0].compile().params.values()),
            {"zeus", "alert_agent"},
        )
        self.assertIn("READ ONLY", str(connection.execute.call_args_list[0].args[0]))
        connection.begin.return_value.rollback.assert_called_once()
        connection.commit.assert_not_called()
        result.close.assert_called_once()

    def test_row_iterator_is_closed_on_scan_failure(self):
        closed = []

        def rows():
            try:
                yield row("current")
                raise OSError("synthetic read failure")
            finally:
                closed.append(True)

        self.source.row_loader = rows
        with self.assertRaises(OSError):
            self.source.load_case(self.signal)
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
