"""Offline synthetic tests for export provenance, history selection, and rule simulation."""

import copy
import csv
import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "surge_materials", Path(__file__).with_name("materials.py")
)
materials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(materials)


def condition(field, operator, value):
    return {"leftFieldName": field, "operator": operator, "rightFieldValue": value}


def rule_fixture(topic=materials.TARGET[0], code=materials.TARGET[1]):
    group = {
        "conditions": [
            condition("status", "Equal", "enabled"),
            condition("event_content", "NotContains", "net1 accounts"),
            condition("unrelated", "NotContains", "keep-this"),
        ],
        "matchType": "and",
        "customRelation": "",
    }
    terms = [
        {
            "alias": "A",
            "type": "Simple",
            "index": 0,
            "config": {"conditions": [condition("type", "Equal", "execution")]},
            "standard_field_config": {
                "conditions": [condition("type", "Equal", "execution")]
            },
        },
        {
            "alias": "B",
            "type": "Simple",
            "index": 1,
            "config": group,
            "standard_field_config": copy.deepcopy(group),
            "created_by": "PRIVATE_AUTHOR",
            "updated_date": "2099-01-01",
        },
    ]
    return {
        "company_code": "company-a",
        "topic": topic,
        "rule_code": code,
        "rule_name": "Synthetic rule",
        "rule_config_version": "csv-row-v1:sha256:original",
        "configuration": {
            "terms": terms,
            "relation": "allMatch",
            "threshold": "1000",
            "standard_field_config": {
                "dimensionField": "id",
                "duplicateRemovalField": "device_id",
            },
            "custom_relation": "",
            "created_by": "PRIVATE_AUTHOR",
            "updated_date": "2099-01-01",
            "remark": "net1 accounts later excluded",
            "id": "private-row-id",
        },
        "raw_record": {"secret": "SHOULD_NOT_REACH_MODEL"},
        "source": {"kind": "csv_snapshot", "file_name": "later_rules.csv"},
    }


class FakeRules:
    file_sha256 = "synthetic-rules-sha"

    def __init__(self, rule=None):
        self.rule = copy.deepcopy(rule or rule_fixture())
        self.catalog = [
            {
                key: self.rule[key]
                for key in ("company_code", "topic", "rule_code", "rule_name")
            }
        ]

    def get_rule(self, code, *, topic, company_code=None):
        return copy.deepcopy(self.rule)

    def list_rules(self):
        return copy.deepcopy(self.catalog)


def row(
    identity="a",
    when="2026-09-17 10:00:00",
    logs=None,
    topic=materials.TARGET[0],
    code=materials.TARGET[1],
):
    return {
        "alert_id": identity,
        "flow_id": "alert_agent",
        "agent_response": "OLD_MODEL_ANSWER",
        "alert_data": json.dumps(
            {
                "alert": {
                    "alertId": identity,
                    "createAt": when,
                    "hitLog": [
                        {
                            "topic": topic,
                            "ruleCode": code,
                            "zeusRawLogs": logs
                            if logs is not None
                            else [{"any_field": "value"}],
                        }
                    ],
                },
                "relatedAlertList": [
                    {"alertId": "unrelated", "raw": "RELATED_SENTINEL"}
                ],
            }
        ),
    }


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rules = FakeRules()

    def csv(self, rows, name="current.csv"):
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def case(self, rows, **kwargs):
        return materials.build_case(self.csv(rows), self.rules, **kwargs)

    def test_simulation_removes_only_later_clause_and_keeps_original(self):
        original = rule_fixture()
        before = copy.deepcopy(original)
        simulated, removed = materials.simulated_rule(original)
        self.assertEqual(original, before)
        self.assertEqual(len(removed), 2)
        self.assertEqual(
            {item["representation"] for item in removed},
            {"config", "standard_field_config"},
        )
        config = simulated["configuration"]
        self.assertEqual(config["terms"][0], original["configuration"]["terms"][0])
        for key in ("config", "standard_field_config"):
            expected = [
                original["configuration"]["terms"][1][key]["conditions"][index]
                for index in (0, 2)
            ]
            self.assertEqual(config["terms"][1][key]["conditions"], expected)
            self.assertEqual(config["terms"][1][key]["matchType"], "and")
        self.assertEqual(config["threshold"], "1000")
        self.assertEqual(config["relation"], "allMatch")
        self.assertEqual(
            config["standard_field_config"],
            original["configuration"]["standard_field_config"],
        )
        serialized = materials.encoded(simulated).decode()
        for private_value in (
            "raw_record",
            "PRIVATE_AUTHOR",
            "2099-01-01",
            "later_rules.csv",
            "net1 accounts",
        ):
            self.assertNotIn(private_value, serialized)
        self.assertTrue(
            simulated["rule_config_version"].startswith("counterfactual-v1:sha256:")
        )
        self.assertEqual(simulated["source"]["kind"], "counterfactual_csv_simulation")

    def test_nested_condition_dictionary_is_preserved_verbatim(self):
        original = rule_fixture()
        value = {
            "id": "condition-id-is-not-row-metadata",
            "created_by": "a meaningful nested value",
        }
        original["configuration"]["terms"][0]["config"]["conditions"][0][
            "rightFieldValue"
        ] = value
        simulated, _ = materials.simulated_rule(original)
        self.assertEqual(
            simulated["configuration"]["terms"][0]["config"]["conditions"][0][
                "rightFieldValue"
            ],
            value,
        )

    def test_missing_duplicate_or_only_remaining_clause_rejected(self):
        for mode in ("missing", "duplicate", "only"):
            with self.subTest(mode=mode):
                original = rule_fixture()
                conditions = original["configuration"]["terms"][1]["config"][
                    "conditions"
                ]
                if mode == "missing":
                    conditions.pop(1)
                elif mode == "duplicate":
                    conditions.append(copy.deepcopy(conditions[1]))
                else:
                    conditions[:] = [conditions[1]]
                with self.assertRaises(ValueError):
                    materials.simulated_rule(original)

    def test_complete_main_logs_retained_without_field_specific_grouping(self):
        logs = [
            {"unusual_object": {"nested": [1, 2]}, "payload": "x" * 160000},
            {"unrelated_rare_behavior": "preserve whole log"},
        ]
        source_row = row(logs=logs)
        original_bytes = self.csv([source_row]).read_bytes()
        case = materials.build_case(self.root / "current.csv", self.rules)
        self.assertEqual(
            case["current_samples"],
            [{"alert_id": "a", "occurred_at": "2026-09-17 10:00:00", "raw_logs": logs}],
        )
        self.assertEqual((self.root / "current.csv").read_bytes(), original_bytes)
        self.assertEqual(case["coverage"]["current_log_count"], 2)
        self.assertTrue(case["coverage"]["all_current_target_logs_retained"])
        visible = materials.encoded(
            {key: case[key] for key in ("rule", "current_samples", "history_samples")}
        ).decode()
        self.assertNotIn("OLD_MODEL_ANSWER", visible)
        self.assertNotIn("RELATED_SENTINEL", visible)
        self.assertNotIn("SHOULD_NOT_REACH_MODEL", visible)
        self.assertNotIn("behavior_groups", case)
        self.assertFalse(case["audit"]["filter_evaluation_performed"])

    def test_default_cutoff_uses_earliest_target_alert_and_no_invented_history(self):
        case = self.case(
            [
                row("newer", "2026-09-17 10:01:00"),
                row("other", "2020-01-01 00:00:00", topic="other-topic"),
                row("earlier", "2026-09-17 10:00:00"),
            ]
        )
        self.assertEqual(case["audit"]["cutoff"], "2026-09-17 10:00:00")
        self.assertEqual(
            [sample["alert_id"] for sample in case["current_samples"]],
            ["earlier", "newer"],
        )
        self.assertEqual(case["history_samples"], [])
        self.assertEqual(case["coverage"]["historical_available_unique_alerts"], 0)
        self.assertIsNone(case["coverage"]["history_first_alert_at"])
        self.assertIsNone(case["coverage"]["history_last_alert_at"])
        self.assertIsNone(case["coverage"]["history_span_seconds"])

    def test_current_order_is_chronological_with_stable_id_ties(self):
        rows = [
            row("later", "2026-09-17 10:02:00"),
            row("b", "2026-09-17 10:01:00"),
            row("earliest", "2026-09-17 10:00:00"),
            row("a", "2026-09-17 10:01:00"),
        ]
        first = self.case(rows)
        reversed_export = self.case(list(reversed(rows)))
        self.assertEqual(first["current_samples"], reversed_export["current_samples"])
        self.assertEqual(
            [sample["alert_id"] for sample in first["current_samples"]],
            ["earliest", "a", "b", "later"],
        )
        coverage = first["coverage"]
        self.assertEqual(coverage["cutoff"], "2026-09-17 10:00:00")
        self.assertEqual(coverage["current_first_alert_at"], "2026-09-17 10:00:00")
        self.assertEqual(coverage["current_last_alert_at"], "2026-09-17 10:02:00")
        self.assertEqual(coverage["current_span_seconds"], 120)
        self.assertEqual(coverage["timestamp_basis"], "main_alert_createAt")
        self.assertEqual(coverage["timezone"], "not_provided")

    def test_history_range_reports_selected_history_and_stable_ties(self):
        history_rows = [
            row("b", "2026-09-16 09:00:00"),
            row("oldest", "2026-09-01 08:00:00"),
            row("a", "2026-09-16 09:00:00"),
        ]
        case = self.case(
            [row("current")], history_path=self.csv(history_rows, "history.csv")
        )
        self.assertEqual(
            [sample["alert_id"] for sample in case["history_samples"]],
            ["a", "b", "oldest"],
        )
        coverage = case["coverage"]
        self.assertEqual(coverage["history_first_alert_at"], "2026-09-01 08:00:00")
        self.assertEqual(coverage["history_last_alert_at"], "2026-09-16 09:00:00")
        self.assertEqual(coverage["history_span_seconds"], (15 * 24 + 1) * 3600)
        self.assertEqual(
            case["audit"]["history_span_seconds"], coverage["history_span_seconds"]
        )
        self.assertEqual(coverage["current_first_alert_at"], "2026-09-17 10:00:00")
        self.assertEqual(coverage["current_span_seconds"], 0)

    def test_history_is_nearest_twenty_distinct_before_cutoff_across_both_files(self):
        cutoff = datetime(2026, 9, 17, 10)
        current_rows = [
            row("current", cutoff.isoformat()),
            row("in-same-file", (cutoff - timedelta(minutes=1)).isoformat()),
        ]
        history_rows = [
            row(f"h-{index}", (cutoff - timedelta(hours=index + 2)).isoformat())
            for index in range(25)
        ]
        history_rows += [
            copy.deepcopy(history_rows[0]),
            row("equal-cutoff", cutoff.isoformat()),
            row("future", (cutoff + timedelta(days=1)).isoformat()),
        ]
        case = self.case(
            current_rows,
            history_path=self.csv(history_rows, "history.csv"),
            cutoff=cutoff,
        )
        selected = [sample["alert_id"] for sample in case["history_samples"]]
        self.assertEqual(
            selected, ["in-same-file"] + [f"h-{index}" for index in range(19)]
        )
        self.assertEqual(case["coverage"]["historical_available_unique_alerts"], 26)
        self.assertEqual(case["coverage"]["history_not_selected_count"], 6)
        self.assertEqual(
            case["coverage"]["history_first_alert_at"], "2026-09-16 14:00:00"
        )
        self.assertEqual(
            case["coverage"]["history_last_alert_at"], "2026-09-17 09:59:00"
        )
        self.assertIsNone(case["coverage"]["history_lookback_duration_limit"])
        self.assertEqual(case["coverage"]["duplicate_identity_rows"], 1)
        self.assertEqual(
            [sample["alert_id"] for sample in case["current_samples"]], ["current"]
        )

    def test_sparse_history_has_no_padding_and_never_reuses_current_id(self):
        current = row("current")
        history = self.csv(
            [copy.deepcopy(current), row("older", "2026-08-01 00:00:00")], "history.csv"
        )
        case = self.case([current], history_path=history)
        self.assertEqual(
            [sample["alert_id"] for sample in case["history_samples"]], ["older"]
        )
        self.assertEqual(case["coverage"]["current_ids_excluded_from_history"], 1)
        self.assertEqual(case["coverage"]["history_selected_unique_alerts"], 1)
        self.assertFalse(case["coverage"]["history_is_complete_previous_window"])

    def test_old_responses_or_related_content_do_not_change_dedup_identity(self):
        first = row("same")
        second = copy.deepcopy(first)
        second["agent_response"] = "DIFFERENT_OLD_ANSWER"
        data = json.loads(second["alert_data"])
        data["relatedAlertList"] = []
        second["alert_data"] = json.dumps(data)
        case = self.case([first, second])
        self.assertEqual(case["coverage"]["current_alert_count"], 1)
        self.assertEqual(case["coverage"]["duplicate_identity_rows"], 1)
        self.assertEqual(case["coverage"]["conflicting_identity_count"], 0)

    def test_conflicting_ids_excluded_from_both_current_and_history(self):
        current_rows = [row("okay"), row("conflict", logs=[{"v": 1}])]
        history = self.csv(
            [
                row("conflict", "2026-09-01 10:00:00", logs=[{"v": 2}]),
                row("old", "2026-09-01 10:00:00"),
            ],
            "history.csv",
        )
        case = self.case(current_rows, history_path=history)
        self.assertEqual(case["audit"]["conflicting_alert_ids"], ["conflict"])
        self.assertEqual(case["coverage"]["excluded_conflicting_target_alerts"], 1)
        self.assertEqual(
            [sample["alert_id"] for sample in case["current_samples"]], ["okay"]
        )
        self.assertEqual(
            [sample["alert_id"] for sample in case["history_samples"]], ["old"]
        )

    def test_conflict_against_other_rule_is_not_silently_accepted(self):
        case = self.case(
            [row("okay"), row("conflict"), row("conflict", code="other-rule")]
        )
        self.assertEqual(case["coverage"]["current_alert_count"], 1)
        self.assertEqual(case["coverage"]["excluded_conflicting_target_alerts"], 1)

    def test_only_conflicting_or_no_current_alerts_report_error(self):
        for rows, kwargs in [
            ([row("conflict"), row("conflict", logs=[{"changed": True}])], {}),
            ([row()], {"cutoff": "2026-09-18 00:00:00"}),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.case(rows, **kwargs)

    def test_mixed_aware_naive_timestamps_rejected(self):
        with self.assertRaisesRegex(ValueError, "mixed_aware_and_naive"):
            self.case([row(), row("other", "2026-09-17T10:00:00+08:00")])
        with self.assertRaisesRegex(ValueError, "mixed_aware_and_naive"):
            self.case([row()], cutoff="2026-09-17T10:00:00Z")

    def test_aware_offsets_are_compared_as_instants(self):
        history = self.csv(
            [
                row("equal", "2026-09-17T02:00:00Z"),
                row("older", "2026-09-17T01:59:59Z"),
            ],
            "history.csv",
        )
        case = self.case(
            [row("current", "2026-09-17T10:00:00+08:00")], history_path=history
        )
        self.assertEqual(
            [sample["alert_id"] for sample in case["history_samples"]], ["older"]
        )

    def test_malformed_non_target_row_is_reported_not_dropped(self):
        for bad in ("{", '{"alert":{},"alert":{}}', '{"v":NaN}', '{"v":1e999}'):
            with self.subTest(bad=bad):
                invalid = row("bad", topic="other")
                invalid["alert_data"] = bad
                with self.assertRaises(ValueError):
                    self.case([row(), invalid])

    def test_invalid_target_log_and_identity_rejected(self):
        for logs in ([{}], [None], [], ["not-json"]):
            with self.subTest(logs=logs), self.assertRaises(ValueError):
                self.case([row(logs=logs)])
        changed = row()
        changed["alert_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            self.case([changed])

    def test_csv_shape_errors_rejected_and_field_limit_restored(self):
        before = csv.field_size_limit()
        path = self.root / "broken.csv"
        path.write_text('alert_id,alert_data\na,"unclosed', encoding="utf-8")
        with self.assertRaises(csv.Error):
            materials.build_case(path, self.rules)
        self.assertEqual(csv.field_size_limit(), before)

    def test_different_topic_fields_supported_with_explicit_unsimulated_rule(self):
        self.rules = FakeRules(rule_fixture(topic="http", code="rule-http"))
        case = self.case(
            [
                row(
                    topic="http",
                    code="rule-http",
                    logs=[{"host": "example.invalid", "request": {"path": "/"}}],
                )
            ],
            topic="http",
            rule_code="rule-http",
            simulate_known_exclusion=False,
        )
        self.assertEqual(case["coverage"]["current_alert_count"], 1)
        self.assertEqual(case["audit"]["removed_conditions"], [])
        self.assertEqual(case["rule"]["source"]["kind"], "csv_snapshot")
        with self.assertRaisesRegex(ValueError, "only_supports_case_rule"):
            self.case(
                [row(topic="http", code="rule-http")],
                topic="http",
                rule_code="rule-http",
            )

    def test_company_scope_must_match_unique_configuration(self):
        with self.assertRaisesRegex(ValueError, "company_mismatch"):
            self.case([row()], company_code="wrong-company")
        self.rules.catalog.append(
            {**self.rules.catalog[0], "company_code": "another-company"}
        )
        with self.assertRaisesRegex(ValueError, "ambiguous_company_scope"):
            self.case([row()], company_code="company-a")

    def test_audit_has_original_sources_and_selected_log_provenance(self):
        rows = [row("one", logs=[{"first": 1}, {"second": 2}]), row("two")]
        path = self.csv(rows)
        before = copy.deepcopy(self.rules.rule)
        case = materials.build_case(path, self.rules)
        self.assertEqual(self.rules.rule, before)
        self.assertEqual(case["original_rule"], before)
        audit = case["audit"]
        self.assertEqual(
            audit["sources"][0]["file_sha256"],
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        self.assertEqual(audit["sources"][0]["parsed_rows"], 2)
        refs = audit["sample_provenance"]["one"]
        self.assertEqual([ref["raw_log_index"] for ref in refs], [0, 1])
        self.assertEqual(refs[0]["source_row_position_zero_based"], 0)
        self.assertEqual(
            refs[0]["log_sha256"],
            hashlib.sha256(materials.encoded({"first": 1})).hexdigest(),
        )
        self.assertEqual(audit["model_calls"], 0)
        self.assertFalse(audit["historical_model_answers_included"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
