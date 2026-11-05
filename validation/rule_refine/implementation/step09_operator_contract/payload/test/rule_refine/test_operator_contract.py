"""六种新操作符的结构、比较、生成与保存回读；全部使用人工材料。"""

import copy
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from pydantic import ValidationError

spec = importlib.util.spec_from_file_location(
    "operator_contract_fixtures", Path(__file__).with_name("test_analysis.py")
)
fixtures = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixtures
spec.loader.exec_module(fixtures)
analysis = fixtures.analysis
contracts = fixtures.contracts
package = fixtures.fixtures.package.__name__
operators = importlib.import_module(package + ".operators")
values = importlib.import_module(package + ".condition_values")
evaluation = importlib.import_module(package + ".filter_evaluation")
saved = importlib.import_module(package + ".saved_delivery")


class OperatorContractTests(unittest.TestCase):
    def test_new_conditions_require_nonempty_string_values(self):
        for operator in operators.OPERATORS:
            for value in ("", None, 1, False, [], {}):
                with self.subTest(operator=operator, value=value):
                    with self.assertRaises(ValidationError):
                        contracts.FilterCondition(
                            name="field", value=value, operator=operator
                        )
                    answer = fixtures.draft()
                    answer["filter"]["A"].update(operator=operator, value=value)
                    result = analysis.analyze_materials(
                        fixtures.materials(), Mock(return_value=json.dumps(answer))
                    )
                    self.assertEqual(result["status"], "analysis_failed")

    def test_schema_and_prompt_use_exactly_new_enums(self):
        expected = {"Eq", "Ne", "Contains", "NotContains", "StartsWith", "EndsWith"}
        schema = contracts.FilterCondition.model_json_schema()
        self.assertEqual(set(schema["properties"]["operator"]["enum"]), expected)
        self.assertEqual(operators.OPERATORS, expected)
        for op in expected:
            self.assertIn(op, analysis.SYSTEM)
        self.assertIn('"operator":"Eq"', analysis.SYSTEM)
        for op in operators.LEGACY_OPERATORS:
            self.assertNotIn('"operator":"' + op + '"', analysis.SYSTEM)

    def test_all_six_operators_generate_new_records(self):
        cases = {
            "Eq": "synthetic.invalid",
            "Ne": "different.invalid",
            "Contains": "thetic",
            "NotContains": "malware",
            "StartsWith": "synthetic.",
            "EndsWith": ".invalid",
        }
        for op, value in cases.items():
            with self.subTest(operator=op):
                answer = fixtures.draft()
                answer["filter"]["A"].update(operator=op, value=value)
                result = analysis.analyze_materials(
                    fixtures.materials(), Mock(return_value=json.dumps(answer))
                )
                self.assertEqual(result["status"], "proposal")
                self.assertEqual(
                    result["recommendation"]["filter"]["A"]["operator"], op
                )
                self.assertEqual(
                    result["operator_contract_version"],
                    operators.OPERATOR_CONTRACT_VERSION,
                )
                record = contracts.RuleRecommendation.model_validate_json(
                    json.dumps(result["recommendation"])
                )
                self.assertEqual(record.filter.__pydantic_extra__["A"].operator, op)

    def test_old_values_cannot_enter_new_contract_or_model_answer(self):
        for op in [*operators.LEGACY_OPERATORS, "eq", "contains", "EQ", "Regex"]:
            with self.subTest(operator=op):
                with self.assertRaises(ValidationError):
                    contracts.FilterCondition(name="host", value="x", operator=op)
                answer = fixtures.draft()
                answer["filter"]["A"]["operator"] = op
                result = analysis.analyze_materials(
                    fixtures.materials(), Mock(return_value=json.dumps(answer))
                )
                self.assertEqual(result["status"], "analysis_failed")
                self.assertIsNone(result["recommendation"])

    def test_string_operators_are_case_sensitive_and_not_regex(self):
        cases = [
            ("Eq", "Prefix.text", True),
            ("Eq", "prefix.text", False),
            ("Ne", "Prefix.text", False),
            ("Ne", "prefix.text", True),
            ("Contains", "fix.", True),
            ("Contains", "^Prefix", False),
            ("NotContains", "prefix", True),
            ("NotContains", "Prefix", False),
            ("StartsWith", "Prefix.", True),
            ("StartsWith", "text", False),
            ("EndsWith", ".text", True),
            ("EndsWith", ".TEXT", False),
        ]
        for op, target, expected in cases:
            with self.subTest(operator=op, target=target):
                condition = contracts.FilterCondition(
                    name="field", value=target, operator=op
                )
                self.assertEqual(
                    values.evaluate_condition(condition, {"field": "Prefix.text"}),
                    (expected, None),
                )

    def test_negative_operators_never_match_missing_null_or_containers(self):
        for op in ("Ne", "NotContains"):
            condition = contracts.FilterCondition(
                name="field", value="blocked", operator=op
            )
            for log in (
                {},
                {"field": None},
                {"field": False},
                {"field": []},
                {"field": {}},
            ):
                with self.subTest(operator=op, log=log):
                    self.assertIsNone(values.evaluate_condition(condition, log)[0])
                    result = evaluation.evaluate_filter(
                        {
                            "topic": "synthetic",
                            "A": condition.model_dump(),
                            "expression": "A",
                        },
                        [{"alert_id": "synthetic-1", "raw_logs": [log]}],
                    )
                    self.assertEqual(result["results"][0]["status"], "unknown")

    def test_numeric_ne_preserves_strict_eq_type_and_precision_rules(self):
        for actual, target, expected in (
            (443, "443", False),
            (443, "4.43e2", False),
            (443, "444", True),
            (2**100 + 1, str(2**100), True),
        ):
            condition = contracts.FilterCondition(
                name="field", value=target, operator="Ne"
            )
            self.assertEqual(
                values.evaluate_condition(condition, {"field": actual}),
                (expected, None),
            )
        for actual, target in (
            (443, "0443"),
            (443, " 443"),
            (True, "1"),
            (float("inf"), "1"),
            (float(2**60), str(2**60)),
        ):
            condition = SimpleNamespace(name="field", value=target, operator="Ne")
            self.assertIsNone(
                values.evaluate_condition(condition, {"field": actual})[0]
            )
        for op in ("Contains", "NotContains", "StartsWith", "EndsWith"):
            condition = contracts.FilterCondition(name="field", value="4", operator=op)
            self.assertIsNone(values.evaluate_condition(condition, {"field": 443})[0])

    def test_legacy_projection_changes_only_operator_in_copy(self):
        source = {
            "topic": "equal",
            "A": {"name": "contain", "value": "equal", "operator": "equal"},
            "expression": "A",
        }
        frozen = copy.deepcopy(source)
        converted = operators.convert_legacy_filter(source)
        self.assertEqual(source, frozen)
        self.assertEqual(converted["A"]["operator"], "Eq")
        self.assertEqual(converted["A"]["value"], "equal")
        self.assertEqual(converted["topic"], "equal")
        source["A"]["operator"] = "regex"
        with self.assertRaises(ValueError):
            operators.convert_legacy_filter(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
