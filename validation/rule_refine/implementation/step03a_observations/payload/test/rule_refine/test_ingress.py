"""人工告警归一化测试；不导入原应用或连接任何服务。"""

import copy
import csv
import importlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_ingress_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
csv_module = importlib.import_module(package.__name__ + ".csv_config_source")
normalize = importlib.import_module(package.__name__ + ".ingress").normalize_received_alert
NOW = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))


def sample():
    hit = {"topic": "demo_topic", "ruleCode": "DEMO_A"}
    return {
        "app_code": "zeus",
        "alert_id": "101",
        "session_id": "first",
        "alert_data": {
            "alert": {
                "alertId": 101,
                "profileCode": "NOT_A_RULE",
                "hitLog": [hit, dict(hit), {"topic": "demo_topic", "ruleCode": "DEMO_B"}],
            },
            "relatedAlertList": [{"alertId": 999, "hitLog": [hit]}],
        },
    }


def make_rules(directory, rows=None, filename="rules.csv"):
    path = Path(directory) / filename
    rows = (
        rows
        if rows is not None
        else [
            ("demo_company", "demo_topic", "DEMO_A", "人工规则A"),
            ("demo_company", "demo_topic", "DEMO_B", "人工规则B"),
        ]
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["company_code", "topic", "rule_code", "rule_name", "terms", "standard_field_config"])
        writer.writerows([*row, "[]", ""] for row in rows)
    return csv_module.CsvRuleConfigSource(path)


class IngressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.rules = make_rules(self.directory)

    def call(self, payload=None, **options):
        return normalize(
            sample() if payload is None else payload, self.rules, received_at=options.pop("received_at", NOW), **options
        )

    def test_multiple_rules_duplicate_hits_and_input_preserved(self):
        payload = sample()
        original = copy.deepcopy(payload)
        result = self.call(payload)
        self.assertEqual(payload, original)
        self.assertEqual(result["schema_version"], "sec_know.rule_observations.v1")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["duplicate_hit_count"], 1)
        observations = result["observations"]
        self.assertEqual([r["rule_code"] for r in observations], ["DEMO_A", "DEMO_B"])
        for item in observations:
            self.assertEqual(item["alert_id"], "101")
            self.assertEqual(item["source_app"], "zeus")
            self.assertEqual(item["company_code"], "demo_company")
            self.assertEqual(item["company_scope_source"], "csv_unique_match")
            self.assertEqual(item["mode"], "demo")
            self.assertEqual(datetime.fromisoformat(item["received_at"]), NOW)
            self.assertEqual(datetime.fromisoformat(item["received_at"]).utcoffset(), timedelta(0))

    def test_repush_time_session_flow_and_config_revision_keep_keys(self):
        original = self.call()["observations"]
        payload = sample()
        payload.update(session_id="retry", flow_id="different")
        changed_rules = make_rules(
            self.directory,
            [("demo_company", "demo_topic", "DEMO_A", "新名称"), ("demo_company", "demo_topic", "DEMO_B", "新名称")],
            "changed.csv",
        )
        second = normalize(payload, changed_rules, received_at=NOW + timedelta(hours=1))["observations"]
        self.assertEqual([x["occurrence_key"] for x in original], [x["occurrence_key"] for x in second])
        self.assertNotEqual(original[0]["rule_config_version"], second[0]["rule_config_version"])

    def test_keys_isolate_source_alert_topic_rule_company_and_mode(self):
        keys = {r["occurrence_key"] for r in self.call()["observations"]}
        self.assertEqual(len(keys), 2)
        variants = []
        for field, value in [("app_code", "another_source"), ("alert_id", "102")]:
            payload = sample()
            payload[field] = value
            if field == "alert_id":
                payload["alert_data"]["alert"]["alertId"] = 102
            variants.append(self.call(payload)["observations"][0])
        variants.append(self.call(mode="replay")["observations"][0])
        for company, topic in [("other_company", "demo_topic"), ("demo_company", "other_topic")]:
            payload = sample()
            payload["alert_data"]["alert"]["hitLog"] = [{"topic": topic, "ruleCode": "DEMO_A"}]
            rules = make_rules(self.directory, [(company, topic, "DEMO_A", "人工规则")], "isolation.csv")
            variants.append(normalize(payload, rules, received_at=NOW)["observations"][0])
        for item in variants:
            self.assertNotIn(item["occurrence_key"], keys)
            keys.add(item["occurrence_key"])

    def test_related_alerts_and_profile_code_never_supply_current_hits(self):
        payload = sample()
        payload["alert_data"]["alert"]["hitLog"] = []
        result = self.call(payload)
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["issues"][0]["code"], "missing_hit_logs")

    def test_invalid_envelopes_or_mismatched_ids_produce_no_observations(self):
        cases = [[], "payload", {}, {**sample(), "app_code": True}, {**sample(), "alert_data": None}]
        for value in [True, -1, 1.5, [], "", "x" * 513]:
            cases.append({**sample(), "alert_id": value})
        for inner in [True, "102", None, [], ""]:
            payload = sample()
            payload["alert_data"]["alert"]["alertId"] = inner
            cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload):
                result = self.call(payload)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["observations"], [])
                self.assertTrue(result["issues"])

    def test_integer_and_string_ids_normalize_without_losing_leading_zeros(self):
        payload = sample()
        payload["alert_id"] = 101
        self.assertEqual(self.call(payload)["observations"][0]["alert_id"], "101")
        payload["alert_id"] = "0101"
        self.assertEqual(self.call(payload)["issues"][0]["code"], "alert_identity_mismatch")

    def test_invalid_unknown_hits_are_visible_and_valid_hits_survive(self):
        payload = sample()
        payload["alert_data"]["alert"]["hitLog"] = [
            None,
            {"topic": "demo_topic", "ruleCode": "待定"},
            {"topic": "demo_topic", "ruleCode": "MISSING"},
            {"topic": "demo_topic", "ruleCode": "DEMO_A"},
        ]
        result = self.call(payload)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(
            {i["code"] for i in result["issues"]}, {"invalid_hit", "invalid_rule_identity", "rule_not_found"}
        )
        self.assertTrue(all(i["location"] for i in result["issues"]))

    def test_ambiguous_csv_scope_requires_explicit_company(self):
        self.rules = make_rules(
            self.directory, [("a", "demo_topic", "DEMO_A", "规则A"), ("b", "demo_topic", "DEMO_A", "规则B")]
        )
        payload = sample()
        payload["alert_data"]["alert"]["hitLog"] = payload["alert_data"]["alert"]["hitLog"][:1]
        self.assertEqual(self.call(payload)["issues"][0]["code"], "rule_ambiguous")
        row = self.call(payload, company_code="b")["observations"][0]
        self.assertEqual((row["company_code"], row["company_scope_source"]), ("b", "explicit"))
        self.assertEqual(self.call(payload, company_code="missing")["observations"], [])

    def test_live_requires_explicit_company_and_does_not_guess_from_app_code(self):
        self.assertEqual(self.call(mode="live")["issues"][0]["code"], "company_scope_required")
        rows = self.call(mode="live", company_code="demo_company")["observations"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["company_scope_source"] == "explicit" for r in rows))

    def test_missing_malformed_and_excessive_hit_lists(self):
        for hits, code in [
            (None, "missing_hit_logs"),
            ([], "missing_hit_logs"),
            ({}, "invalid_hit_logs"),
            ("[]", "invalid_hit_logs"),
            ([{}] * 1001, "too_many_hit_logs"),
        ]:
            payload = sample()
            payload["alert_data"]["alert"]["hitLog"] = hits
            result = self.call(payload)
            self.assertEqual(result["observations"], [])
            self.assertEqual(result["issues"][0]["code"], code)

    def test_invalid_caller_options_raise(self):
        for options in [
            {"received_at": datetime(2026, 1, 1)},
            {"received_at": "2026-01-01"},
            {"mode": "unknown"},
            {"mode": []},
            {"company_code": " "},
            {"company_code": False},
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.call(**options)

    def test_provider_failure_is_not_silently_reported_as_missing_rule(self):
        rules = Mock()
        rules.get_rule.side_effect = OSError("injected source failure")
        with self.assertRaises(OSError):
            normalize(sample(), rules, received_at=NOW)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        with tempfile.TemporaryDirectory() as temporary:
            rules = make_rules(temporary)
            first = normalize(sample(), rules, received_at=NOW)
            repeated = sample()
            repeated["session_id"] = "second"
            second = normalize(repeated, rules, received_at=NOW + timedelta(minutes=1))
            print(
                json.dumps(
                    {
                        "mode": "demo",
                        "status": first["status"],
                        "input_hit_count": 3,
                        "unique_rule_count": len(first["observations"]),
                        "duplicate_hit_count": first["duplicate_hit_count"],
                        "repush_keys_unchanged": [r["occurrence_key"] for r in first["observations"]]
                        == [r["occurrence_key"] for r in second["observations"]],
                        "company_scope_source": [r["company_scope_source"] for r in first["observations"]],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    else:
        unittest.main(verbosity=2)
