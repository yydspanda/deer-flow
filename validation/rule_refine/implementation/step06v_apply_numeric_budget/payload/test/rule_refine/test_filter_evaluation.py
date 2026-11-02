"""人工日志的组合条件试算；不加载应用、不连接模型、数据库或 Redis。"""

import copy
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

before_import = set(sys.modules)
source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_evaluation_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
evaluate = importlib.import_module(
    package.__name__ + ".filter_evaluation"
).evaluate_filter


def definition(expression="A"):
    return {
        "topic": "demo-topic",
        "expression": expression,
        **{
            name: {"name": name.lower(), "value": "yes", "operator": "equal"}
            for name in ("A", "B", "C")
        },
    }


def samples(*logs):
    return [{"alert_id": "demo-1", "raw_logs": list(logs)}]


def statuses(result):
    return [row["status"] for row in result["results"]]


def demo():
    return evaluate(
        definition("A && B"),
        samples(
            {"a": "yes", "b": "yes"},
            {"a": "yes", "b": "no"},
            {"a": "no", "b": "yes"},
            {"a": "yes"},
        ),
    )


class FilterEvaluationTests(unittest.TestCase):
    def test_numeric_equal_preserves_string_identity_and_boolean_unknown(self):
        rule = definition()
        rule["A"]["value"] = "443"
        result = evaluate(
            rule,
            samples(
                {"a": 443},
                {"a": 444},
                {"a": "443"},
                {"a": "0443"},
                {"a": True},
                {"a": []},
            ),
        )
        self.assertEqual(
            statuses(result),
            ["matched", "not_matched", "matched", "not_matched", "unknown", "unknown"],
        )

    def test_numeric_and_string_conditions_still_require_one_log(self):
        rule = definition("A && B")
        rule["A"]["value"] = "443"
        result = evaluate(rule, samples({"a": 443, "b": "no"}, {"a": 80, "b": "yes"}))
        self.assertEqual(statuses(result), ["not_matched", "not_matched"])

    def test_boolean_truth_tables_include_unknown(self):
        values = ("yes", "no", None)
        names = {"T": "matched", "F": "not_matched", "U": "unknown"}
        expected = {"&&": "TFUFFFUFU", "||": "TTTTFUTUU"}
        for operator, answers in expected.items():
            logs = [{"a": left, "b": right} for left in values for right in values]
            with self.subTest(operator=operator):
                self.assertEqual(
                    statuses(
                        evaluate(definition("A " + operator + " B"), samples(*logs))
                    ),
                    [names[c] for c in answers],
                )

    def test_precedence_parentheses_and_full_rhs_consumption(self):
        logs = samples({"a": "yes", "b": "no", "c": "no"})
        for expression, expected in (
            (" A || B && C ", "matched"),
            ("(A || B) && C", "not_matched"),
            ("A || (B && C) || A", "matched"),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(
                    statuses(evaluate(definition(expression), logs)), [expected]
                )

    def test_conditions_cannot_be_joined_across_logs(self):
        result = evaluate(
            definition("A && B"),
            samples({"a": "yes", "b": "no"}, {"a": "no", "b": "yes"}),
        )
        self.assertEqual(statuses(result), ["not_matched", "not_matched"])
        self.assertEqual(
            [(row["alert_id"], row["log_index"]) for row in result["results"]],
            [("demo-1", 0), ("demo-1", 1)],
        )

    def test_missing_null_and_non_string_are_unknown_for_negative_condition(self):
        rule = definition()
        rule["A"]["operator"] = "notcontain"
        result = evaluate(
            rule,
            samples(
                {},
                {"a": None},
                {"a": 42},
                {"a": False},
                {"a": ["yes"]},
                {"a": {"x": "yes"}},
            ),
        )
        self.assertEqual(statuses(result), ["unknown"] * 6)
        self.assertEqual(
            [row["unknown_conditions"]["A"] for row in result["results"]],
            ["missing_field", "null_value"] + ["non_string_value"] * 4,
        )

    def test_string_operators_are_case_sensitive_and_do_not_coerce(self):
        expected = {
            "equal": ["matched", "not_matched", "not_matched"],
            "contain": ["matched", "matched", "not_matched"],
            "notcontain": ["not_matched", "not_matched", "matched"],
        }
        for operator, answers in expected.items():
            rule = definition()
            rule["A"]["operator"] = operator
            with self.subTest(operator=operator):
                self.assertEqual(
                    statuses(
                        evaluate(
                            rule, samples({"a": "yes"}, {"a": "yes-long"}, {"a": "YES"})
                        )
                    ),
                    answers,
                )

    def test_empty_string_equal_and_literal_dotted_field(self):
        rule = definition()
        rule["A"].update(name="request.host", value="")
        result = evaluate(
            rule,
            samples(
                {"request.host": ""}, {"request": {"host": ""}}, {"request.host": " "}
            ),
        )
        self.assertEqual(statuses(result), ["matched", "unknown", "not_matched"])
        for operator in ("contain", "notcontain"):
            rule["A"]["operator"] = operator
            with self.assertRaises(ValueError):
                evaluate(rule, [])

    def test_only_used_aliases_report_unknown_even_for_decisive_results(self):
        result = evaluate(definition("A || B"), samples({"a": "yes"}))
        self.assertEqual(statuses(result), ["matched"])
        self.assertEqual(
            result["results"][0]["unknown_conditions"], {"B": "missing_field"}
        )

    def test_invalid_expressions_aliases_and_operator_are_rejected(self):
        expressions = [
            "",
            "A B",
            "A &&",
            "A || X",
            "(A",
            "A)",
            "A and B",
            "__import__('os')",
            "A;B",
            "A | B",
        ]
        expressions += ["(" * 33 + "A" + ")" * 33, " || ".join(["A"] * 129)]
        for expression in expressions:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                evaluate(definition(expression), [])
        for rule in (
            {**definition(), "bad": definition()["A"]},
            {**definition(), "A": {"name": "a", "value": "yes", "operator": "regex"}},
        ):
            with self.assertRaises(ValueError):
                evaluate(rule, [])

    def test_invalid_sample_shapes_ids_and_limits(self):
        invalid = (
            None,
            {},
            [None],
            [{"alert_id": "1"}],
            [{"alert_id": "1", "raw_logs": [None]}],
            samples({}) * 2,
            [{"alert_id": str(i), "raw_logs": []} for i in range(21)],
            samples(*([{}] * 101)),
        )
        for value in invalid:
            with (
                self.subTest(value_type=type(value).__name__),
                self.assertRaises(ValueError),
            ):
                evaluate(definition(), value)
        for alert_id in ("", " 1", "1 ", "x" * 513, 1, None):
            with self.subTest(alert_id=alert_id), self.assertRaises(ValueError):
                evaluate(definition(), [{"alert_id": alert_id, "raw_logs": []}])

    def test_json_size_and_unsupported_values_are_rejected(self):
        for value in ("x" * (2 * 1024 * 1024 + 1), float("nan"), object()):
            with (
                self.subTest(value_type=type(value).__name__),
                self.assertRaises(ValueError),
            ):
                evaluate(definition(), samples({"a": value}))

    def test_empty_evidence_and_scope_do_not_claim_alert_suppression(self):
        for entries, empty_ids in (([], []), (samples(), ["demo-1"])):
            result = evaluate(definition(), entries)
            self.assertEqual(result["status"], "insufficient_evidence")
            self.assertEqual(result["empty_alert_ids"], empty_ids)
            self.assertEqual(result["input_log_count"], 0)
            self.assertEqual(
                result["counts"], {"matched": 0, "not_matched": 0, "unknown": 0}
            )
        self.assertEqual(result["scope"], "provided_logs_only")
        self.assertEqual(result["source_completeness"], "not_asserted")
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertEqual(result["configuration_version_check"], "not_performed")
        self.assertEqual(result["field_catalog_check"], "not_performed")
        self.assertIs(result["automatic_apply"], False)

    def test_unchanged_input_and_reproducible_hash_with_counts(self):
        rule, entries = definition(), samples({"a": "yes"})
        original = copy.deepcopy((rule, entries))
        result = evaluate(rule, entries)
        self.assertEqual((rule, entries), original)
        self.assertEqual(
            result["input_sha256"],
            evaluate(dict(reversed(list(rule.items()))), entries)["input_sha256"],
        )
        entries[0]["raw_logs"][0]["a"] = "no"
        self.assertNotEqual(
            result["input_sha256"], evaluate(rule, entries)["input_sha256"]
        )
        result = demo()
        self.assertEqual(
            result["counts"], {"matched": 1, "not_matched": 2, "unknown": 1}
        )
        self.assertEqual(
            (result["input_alert_count"], result["input_log_count"], result["status"]),
            (1, 4, "evaluated"),
        )

    def test_import_does_not_load_application_or_service_clients(self):
        loaded = set(sys.modules) - before_import
        self.assertFalse(
            any(
                name.split(".")[0]
                in {"my_workflows", "service", "model", "redis", "sqlalchemy", "openai"}
                for name in loaded
            )
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        outcome = demo()
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": "passed",
                    "counts": outcome["counts"],
                    "cross_log_and_matched": any(
                        row["status"] == "matched" for row in outcome["results"][1:3]
                    ),
                    "engine_compatibility": outcome["engine_compatibility"],
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
