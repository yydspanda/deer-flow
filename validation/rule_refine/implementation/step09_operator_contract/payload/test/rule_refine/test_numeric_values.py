"""Offline tests of exact string and bounded numeric equality semantics."""

import importlib.util
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "my_workflows/zeus/rule_refine/condition_values.py"
spec = importlib.util.spec_from_file_location("numeric_values_test_helper", SOURCE)
values = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = values
spec.loader.exec_module(values)


def condition(value, operator="Eq"):
    return SimpleNamespace(name="field", value=value, operator=operator)


class NumericValuesTests(unittest.TestCase):
    def evaluate(self, actual, expected, operator="Eq"):
        return values.evaluate_condition(
            condition(expected, operator), {"field": actual}
        )

    def assertUnknown(self, result):
        verdict, reason = result
        self.assertIsNone(verdict)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason)

    def test_integer_and_fractional_numeric_values_match_json_numbers(self):
        for actual, target in (
            (443, "443"),
            (0, "0"),
            (-443, "-443"),
            (1.5, "1.5"),
            (-1.5, "-1.5"),
            (443, "4.43e2"),
            (1000, "1E+3"),
            (0, "-0"),
            (-0.0, "0"),
            (443.0, "443"),
        ):
            with self.subTest(actual=actual, target=target):
                self.assertEqual(self.evaluate(actual, target), (True, None))

    def test_different_valid_numeric_values_are_false_not_unknown(self):
        for actual, target in ((443, "444"), (0, "1"), (1.5, "1.6"), (-1, "1")):
            with self.subTest(actual=actual, target=target):
                self.assertEqual(self.evaluate(actual, target), (False, None))

    def test_string_values_still_compare_exactly_without_numeric_coercion(self):
        for actual, target, expected in (
            ("01", "1", False),
            ("1", "1.0", False),
            ("1e3", "1000", False),
            ("443", "443", True),
            (" 443", "443", False),
            ("NaN", "NaN", True),
            ("", "", True),
            ("Probe", "probe", False),
        ):
            with self.subTest(actual=actual, target=target):
                self.assertEqual(self.evaluate(actual, target), (expected, None))

    def test_string_contain_and_notcontain_remain_case_sensitive(self):
        self.assertEqual(self.evaluate("Probe/1.0", "Probe", "Contains"), (True, None))
        self.assertEqual(self.evaluate("Probe/1.0", "probe", "Contains"), (False, None))
        self.assertEqual(
            self.evaluate("Probe/1.0", "probe", "NotContains"), (True, None)
        )
        self.assertEqual(
            self.evaluate("Probe/1.0", "Probe", "NotContains"), (False, None)
        )

    def test_invalid_numeric_target_spellings_are_unknown(self):
        targets = (
            " 443",
            "443 ",
            "+443",
            "0443",
            ".5",
            "1.",
            "NaN",
            "Infinity",
            "-Infinity",
            "0x1bb",
            "４４３",
            "1_000",
            "1e",
            "--1",
            "",
            "1\n",
        )
        for target in targets:
            with self.subTest(target=target):
                self.assertUnknown(self.evaluate(443, target))

    def test_numeric_contain_and_notcontain_are_unknown(self):
        for actual in (443, 0, 1.5):
            for operator in ("Contains", "NotContains"):
                with self.subTest(actual=actual, operator=operator):
                    self.assertUnknown(self.evaluate(actual, "4", operator))

    def test_boolean_is_not_an_integer_and_missing_null_containers_are_unknown(self):
        for actual in (True, False, None, {}, {"field": "443"}, [], [443]):
            with self.subTest(value_type=type(actual).__name__, actual=actual):
                self.assertUnknown(
                    self.evaluate(actual, "1" if actual is True else "0")
                )
        self.assertUnknown(values.evaluate_condition(condition("443"), {}))

    def test_nonfinite_actual_floats_are_unknown(self):
        for actual in (math.nan, math.inf, -math.inf):
            with self.subTest(actual=actual):
                self.assertUnknown(self.evaluate(actual, "443"))

    def test_huge_integers_are_compared_without_float_or_decimal_rounding(self):
        for actual in (2**53 + 1, 2**100 + 1, 10**80 + 1):
            with self.subTest(integer_digits=len(str(actual))):
                self.assertEqual(self.evaluate(actual, str(actual)), (True, None))
                self.assertEqual(self.evaluate(actual, str(actual + 1)), (False, None))
                self.assertEqual(self.evaluate(actual, str(actual - 1)), (False, None))

    def test_integral_float_beyond_exact_integer_boundary_is_unknown(self):
        for actual in (float(2**53 + 2), -float(2**53 + 2), 1e20):
            with self.subTest(actual=actual):
                self.assertUnknown(self.evaluate(actual, str(actual)))
        self.assertEqual(self.evaluate(float(2**53), str(2**53)), (True, None))
        self.assertEqual(self.evaluate(float(2**53), str(2**53 + 1)), (False, None))

    def test_finite_floats_use_standard_string_value_not_binary_decimal_expansion(self):
        self.assertEqual(self.evaluate(0.1, "0.1"), (True, None))
        self.assertEqual(self.evaluate(0.1, "0.10000000000000001"), (False, None))
        calculated = 0.1 + 0.2
        self.assertEqual(self.evaluate(calculated, str(calculated)), (True, None))
        self.assertEqual(self.evaluate(calculated, "0.3"), (False, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
