"""仅测试规则建议契约，不加载原应用、数据库或模型。"""

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

source = (
    Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine/contracts.py"
)
spec = importlib.util.spec_from_file_location("_rule_refine_contracts", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
RuleRecommendation = module.RuleRecommendation
FilterDefinition = module.FilterDefinition


def sample():
    return {
        "company_code": "demo",
        "rule_code": "DEMO_RULE",
        "rule_name": "人工测试规则",
        "rule_config_version": "demo-v1",
        "issue": "人工构造的重复场景，用于格式测试",
        "recommendation": "确认正常业务后，评估此组合排除条件",
        "certainty": "人工示例，不是实际告警结论",
        "evidence": {
            "window_start": "2026-10-27T09:00:00+08:00",
            "window_end": "2026-10-27T10:00:00+08:00",
            "alert_count": 2,
            "sample_alert_ids": ["DEMO-1", "DEMO-2"],
        },
        "filter": {
            "topic": "demo_topic",
            "A": {"name": "host", "value": "example.invalid", "operator": "equal"},
            "B": {"name": "api", "value": "/health", "operator": "contain"},
            "C": {"name": "method", "value": "GET", "operator": "equal"},
            "expression": "A&&(B||C)",
        },
    }


class ContractTests(unittest.TestCase):
    def test_flat_json_roundtrip(self):
        record = RuleRecommendation.model_validate(sample())
        data = json.loads(record.model_dump_json())
        self.assertEqual(data["filter"]["B"]["value"], "/health")
        self.assertFalse(data["automatic_apply"])
        self.assertTrue(data["recommendation_id"])
        self.assertEqual(
            RuleRecommendation.model_validate_json(record.model_dump_json()), record
        )

    def test_condition_value_is_not_trimmed(self):
        data = sample()
        data["filter"]["A"]["value"] = " value with spaces "
        self.assertEqual(
            RuleRecommendation.model_validate(data).model_dump()["filter"]["A"][
                "value"
            ],
            " value with spaces ",
        )

    def test_bad_expressions(self):
        for expression in (
            "",
            "()",
            "A B",
            "A&&",
            "||A",
            "(A",
            "A)",
            "!A",
            "A;foo()",
            "A||Z",
            "A & B",
            "A(B)",
            "(" * 33 + "A" + ")" * 33,
        ):
            with self.subTest(expression=expression):
                data = sample()["filter"]
                data["expression"] = expression
                with self.assertRaises(ValidationError):
                    FilterDefinition.model_validate(data)

    def test_bad_condition(self):
        for update in (
            {"operator": "execute"},
            {"value": 123},
            {"name": " "},
            {"value": "", "operator": "contain"},
            {"value": "", "operator": "notcontain"},
            {"unexpected": True},
        ):
            with self.subTest(update=update):
                data = sample()["filter"]
                data["A"].update(update)
                with self.assertRaises(ValidationError):
                    FilterDefinition.model_validate(data)

    def test_bad_alias_and_missing_conditions(self):
        for data in (
            {"topic": "demo", "expression": "A"},
            {**sample()["filter"], "bad_key": sample()["filter"]["A"]},
        ):
            with self.subTest(data=data):
                with self.assertRaises(ValidationError):
                    FilterDefinition.model_validate(data)

    def test_automatic_apply_must_be_false(self):
        for value in (True, 0, "false", None):
            with self.subTest(value=value):
                data = sample()
                data["automatic_apply"] = value
                with self.assertRaises(ValidationError):
                    RuleRecommendation.model_validate(data)

    def test_other_change_type_is_not_silently_accepted(self):
        data = sample()
        data["change_type"] = "modify_conditions"
        with self.assertRaises(ValidationError):
            RuleRecommendation.model_validate(data)

    def test_bad_evidence(self):
        changes = (
            {"alert_count": -1},
            {"alert_count": True},
            {"alert_count": "2"},
            {"alert_count": 1},
            {"sample_alert_ids": []},
            {"sample_alert_ids": ["DEMO-1", "DEMO-1"]},
            {"window_end": "2026-10-27T08:00:00+08:00"},
            {"window_start": "2026-10-27T09:00:00"},
        )
        for update in changes:
            with self.subTest(update=update):
                data = sample()
                data["evidence"].update(copy.deepcopy(update))
                with self.assertRaises(ValidationError):
                    RuleRecommendation.model_validate(data)

    def test_missing_identity_and_extra_fields(self):
        for key in ("company_code", "rule_code", "rule_config_version", "evidence"):
            with self.subTest(key=key):
                data = sample()
                del data[key]
                with self.assertRaises(ValidationError):
                    RuleRecommendation.model_validate(data)
        with self.assertRaises(ValidationError):
            RuleRecommendation.model_validate({**sample(), "unexpected": True})

    def test_schema_can_be_exported(self):
        schema = RuleRecommendation.model_json_schema()
        self.assertIn("FilterDefinition", schema["$defs"])
        json.dumps(schema)


if __name__ == "__main__":
    unittest.main(verbosity=2)
