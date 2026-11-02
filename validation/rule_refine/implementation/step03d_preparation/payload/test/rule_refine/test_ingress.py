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
common_module = importlib.import_module(package.__name__ + ".rule_config_source")
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
            self.assertEqual(item["company_scope_source"], "rule_config_unique_match")
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

    def test_live_uses_unique_csv_scope_not_payload_company_fields(self):
        payload = sample()
        payload.update(app_code="another_system", company_code="untrusted_company")
        payload["alert_data"]["alert"]["company_code"] = "untrusted_company"
        payload["alert_data"]["alert"]["asset"] = {"company_code": "asset_company"}
        result = self.call(payload, mode="live")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["observations"]), 2)
        for row in result["observations"]:
            self.assertEqual(row["mode"], "live")
            self.assertEqual(row["source_app"], "another_system")
            self.assertEqual(row["company_code"], "demo_company")
            self.assertEqual(row["company_scope_source"], "rule_config_unique_match")

    def test_live_unknown_and_ambiguous_rules_skip_without_guessing_company(self):
        self.rules = make_rules(
            self.directory,
            [
                ("a", "demo_topic", "DEMO_A", "规则A"),
                ("b", "demo_topic", "DEMO_A", "规则B"),
                ("c", "demo_topic", "DEMO_B", "规则C"),
            ],
        )
        payload = sample()
        payload.update(app_code="a", company_code="a")
        payload["alert_data"]["alert"]["asset"] = {"company_code": "a"}
        payload["alert_data"]["alert"]["hitLog"].append({"topic": "demo_topic", "ruleCode": "MISSING"})
        result = self.call(payload, mode="live")
        self.assertEqual(result["status"], "partial")
        self.assertEqual({issue["code"] for issue in result["issues"]}, {"rule_ambiguous", "rule_not_found"})
        self.assertEqual(len(result["observations"]), 1)
        row = result["observations"][0]
        self.assertEqual(
            (row["rule_code"], row["company_code"], row["company_scope_source"]),
            ("DEMO_B", "c", "rule_config_unique_match"),
        )
        payload["alert_data"]["alert"]["hitLog"] = payload["alert_data"]["alert"]["hitLog"][:1]
        self.assertEqual(self.call(payload, mode="live")["status"], "skipped")

    def test_live_explicit_scope_is_preserved_without_cross_company_fallback(self):
        rows = self.call(mode="live", company_code="demo_company")["observations"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["company_scope_source"] == "explicit" for r in rows))
        result = self.call(mode="live", company_code="another_company")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["observations"], [])
        self.assertTrue(all(issue["code"] == "rule_not_found" for issue in result["issues"]))

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

    def test_provider_can_change_without_changing_identity_or_normalization(self):
        csv_rows = self.call(mode="live")["observations"]
        source = self.rules

        class MockRuleApi:
            def get_rule(self, rule_code, *, company_code=None, topic=None):
                row = source.get_rule(rule_code, company_code=company_code, topic=topic)
                row["source"] = {"kind": "mock_rule_api"}
                return row

        api_rows = normalize(sample(), MockRuleApi(), received_at=NOW, mode="live")["observations"]
        self.assertEqual([r["occurrence_key"] for r in api_rows], [r["occurrence_key"] for r in csv_rows])
        for csv_row, api_row in zip(csv_rows, api_rows):
            self.assertEqual(csv_row["rule_source"]["kind"], "csv_snapshot")
            self.assertEqual(api_row["rule_source"]["kind"], "mock_rule_api")
            self.assertEqual(api_row["company_scope_source"], "rule_config_unique_match")
            self.assertEqual(api_row["company_code"], csv_row["company_code"])
        self.assertIs(csv_module.CsvRuleConfigError, common_module.RuleConfigError)
        self.assertIs(csv_module.RuleNotFoundError, common_module.RuleNotFoundError)
        self.assertIs(csv_module.AmbiguousRuleError, common_module.AmbiguousRuleError)
        for error, code in [
            (common_module.RuleNotFoundError, "rule_not_found"),
            (common_module.AmbiguousRuleError, "rule_ambiguous"),
        ]:
            provider = Mock()
            provider.get_rule.side_effect = error("injected")
            result = normalize(sample(), provider, received_at=NOW, mode="live")
            self.assertEqual(result["status"], "skipped")
            self.assertTrue(all(issue["code"] == code for issue in result["issues"]))

    def test_malformed_or_mismatched_provider_result_never_creates_observations(self):
        original = self.rules.get_rule("DEMO_A", topic="demo_topic")
        cases = [None, [], {}, {**original, "topic": "other_topic"}, {**original, "rule_code": "DEMO_B"}]
        for field, value in [
            ("company_code", ""),
            ("company_code", True),
            ("company_code", "different_company"),
            ("rule_config_version", " "),
            ("rule_config_version", None),
            ("source", None),
            ("source", {}),
            ("source", {"kind": " "}),
            ("rule_name", None),
        ]:
            cases.append({**original, field: value})
        for row in cases:
            provider = Mock()
            provider.get_rule.return_value = row
            with self.subTest(row=row), self.assertRaises(common_module.RuleConfigError):
                normalize(sample(), provider, received_at=NOW, mode="live", company_code="demo_company")

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
