"""Source-role boundaries; no models, services, or predicate execution."""

import copy
import importlib
import sys
import types
import unittest
from pathlib import Path

_package = types.ModuleType("_surge_field_usage_tests")
_package.__path__ = [
    str(Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine")
]
sys.modules[_package.__name__] = _package
usage = importlib.import_module(_package.__name__ + ".surge_field_usage")


class FieldUsageTests(unittest.TestCase):
    def build(self, fields, topic="security_qthids", **rule):
        return usage.build_field_usage({"topic": topic, **rule}, fields)

    def test_known_detection_result_is_context_only(self):
        fields = ["detail_hit_rule_name", "event_content", "detail_process_name"]
        original = copy.deepcopy(fields)
        contract = self.build(fields)
        self.assertEqual(fields, original)
        self.assertEqual(
            contract["candidate_field_names"],
            ["detail_process_name", "event_content"],
        )
        self.assertEqual(
            contract["context_only_fields"][0]["name"], "detail_hit_rule_name"
        )
        issue = usage.issue_for_field("detail_hit_rule_name", contract)
        self.assertEqual(issue["code"], "condition_uses_detection_metadata")
        self.assertEqual(
            issue["evidence"][0]["source_description"], "命中的青藤云的规则名"
        )

    def test_other_same_topic_schemas_keep_their_own_fields(self):
        contract = self.build(["task_source", "command", "network.request", "user_id"])
        self.assertEqual(contract["context_only_fields"], [])
        self.assertEqual(len(contract["candidate_field_names"]), 4)
        self.assertFalse(contract["candidate_engine_availability_verified"])
        self.assertFalse(contract["unclassified_fields_are_verified_event_fields"])

    def test_same_spelling_in_unreviewed_topic_is_not_guessed(self):
        contract = self.build(
            ["rule_name", "detail_hit_rule_name"], topic="custom-topic"
        )
        self.assertEqual(
            contract["candidate_field_names"], ["detail_hit_rule_name", "rule_name"]
        )
        self.assertEqual(contract["context_only_fields"], [])

    def test_reviewed_topics_have_exact_roles(self):
        cases = {
            "security-qthids-stg": ["detail_hit_rule_name", "detail_rule"],
            "sec_guard_apt": ["rule_name", "rule_id"],
            "sec_guard_apt_detail": ["rule_name", "rule_id"],
            "leagsoft-edr": ["str_rule_id"],
            "edr-core-xc": ["rule_name", "rule_id", "rule_desc"],
            "ptp-nids": ["alert_rule", "alert_signature", "alert_signature_id"],
        }
        for topic, fields in cases.items():
            with self.subTest(topic=topic):
                contract = self.build(fields + ["new_event_field"], topic=topic)
                self.assertEqual(contract["candidate_field_names"], ["new_event_field"])
                self.assertEqual(
                    {item["name"] for item in contract["context_only_fields"]},
                    set(fields),
                )
                self.assertTrue(
                    all(item["evidence"] for item in contract["context_only_fields"])
                )

    def test_new_visible_field_need_not_exist_in_original_rule(self):
        rule = {"configuration": {"A": {"name": "old_condition_field"}}}
        contract = self.build(["new_useful_field"], **rule)
        self.assertEqual(contract["candidate_field_names"], ["new_useful_field"])

    def test_original_rule_cannot_override_known_metadata_role(self):
        contract = self.build(
            ["detail_hit_rule_name"],
            configuration={"A": {"name": "detail_hit_rule_name"}},
            field_usage={"candidate_field_names": ["detail_hit_rule_name"]},
        )
        self.assertEqual(contract["candidate_field_names"], [])

    def test_dotted_hids_origin_excludes_actual_parent_not_sibling(self):
        fields = ["detail", "detail.hit_rule_name", "detail.command"]
        contract = self.build(fields)
        self.assertEqual(contract["candidate_field_names"], ["detail.command"])
        self.assertEqual(
            usage.issue_for_field("detail", contract)["reason"], "contains_metadata"
        )
        self.assertEqual(
            usage.issue_for_field("detail.hit_rule_name", contract)["reason"], "exact"
        )
        self.assertIn(
            "normalization_source",
            usage.issue_for_field("detail.hit_rule_name", contract)["evidence"][0],
        )

    def test_parent_without_visible_metadata_is_not_rejected(self):
        contract = self.build(["detail", "detail.command"])
        self.assertEqual(contract["context_only_fields"], [])
        self.assertEqual(
            contract["candidate_field_names"], ["detail", "detail.command"]
        )

    def test_metadata_subpaths_cannot_be_used_to_bypass_role(self):
        contract = self.build(["detail_rule", "detail_rule.id", "detail_rule.label"])
        self.assertEqual(contract["candidate_field_names"], [])
        self.assertEqual(
            usage.issue_for_field("detail_rule.id", contract)["reason"],
            "within_metadata",
        )

    def test_no_fuzzy_names_or_global_rule_word_filter(self):
        fields = [
            "Detail_hit_rule_name",
            "detail__hit_rule_name",
            "ruleset_task",
            "rule_name",
        ]
        contract = self.build(fields)
        self.assertEqual(contract["candidate_field_names"], sorted(fields))

    def test_unknown_field_remains_callers_visibility_check(self):
        contract = self.build(["event_content"])
        self.assertIsNone(usage.issue_for_field("not_present", contract))
        self.assertIsNone(usage.issue_for_field("event_content", contract))

    def test_fields_with_embedded_metadata_text_are_not_semantically_certified(self):
        contract = self.build(["message", "event_content"])
        self.assertEqual(contract["classification_scope"], "reviewed_source_roles_only")
        self.assertFalse(contract["candidate_engine_availability_verified"])
        self.assertFalse(contract["unclassified_fields_are_verified_event_fields"])

    def test_contract_is_stable_and_does_not_keep_mutable_inputs(self):
        fields = ["event_content", "detail_hit_rule_name", "event_content"]
        rule = {"topic": "security_qthids"}
        contract = usage.build_field_usage(rule, fields)
        fields.append("new")
        rule["topic"] = "custom"
        self.assertEqual(
            contract, self.build(["detail_hit_rule_name", "event_content"])
        )
        issue = usage.issue_for_field("detail_hit_rule_name", contract)
        issue["evidence"].clear()
        self.assertTrue(contract["context_only_fields"][0]["evidence"])

    def test_frozen_contract_tampering_is_rejected(self):
        fields = ["detail_hit_rule_name", "event_content"]
        rule = {"topic": "security_qthids"}
        contract = usage.build_field_usage(rule, fields)
        self.assertEqual(usage.validate_field_usage(contract, rule, fields), contract)
        tampered = copy.deepcopy(contract)
        tampered["candidate_field_names"].append("detail_hit_rule_name")
        tampered["context_only_fields"] = []
        with self.assertRaisesRegex(ValueError, "field_usage_contract_mismatch"):
            usage.validate_field_usage(tampered, rule, fields)

    def test_frozen_contract_cannot_switch_topic_or_fields(self):
        fields = ["event_content"]
        contract = self.build(fields)
        with self.assertRaisesRegex(ValueError, "field_usage_contract_mismatch"):
            usage.validate_field_usage(contract, {"topic": "custom"}, fields)
        with self.assertRaisesRegex(ValueError, "field_usage_contract_mismatch"):
            usage.validate_field_usage(
                contract, {"topic": "security_qthids"}, fields + ["command"]
            )

    def test_unknown_policy_and_bad_inputs_fail_explicitly(self):
        contract = self.build([])
        contract["field_usage_policy_version"] = "future"
        with self.assertRaisesRegex(
            ValueError, "field_usage_policy_version_unsupported"
        ):
            usage.issue_for_field("event_content", contract)
        for rule, fields in [(None, []), ({}, []), ({"topic": ""}, [])]:
            with self.subTest(rule=rule):
                with self.assertRaisesRegex(ValueError, "field_usage_topic_required"):
                    usage.build_field_usage(rule, fields)
        for fields in ["field", [None], [""], {"field": "value"}]:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(
                    ValueError, "field_usage_visible_fields_invalid"
                ):
                    self.build(fields)


if __name__ == "__main__":
    unittest.main()
