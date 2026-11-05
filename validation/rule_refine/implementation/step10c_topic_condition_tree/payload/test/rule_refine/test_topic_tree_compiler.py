"""Pure compiler checks; boolean assignments are synthetic, not event replay."""

import copy
import importlib
import itertools
import json
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_isolated_topic_tree_compiler"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
tree = importlib.import_module(PACKAGE + ".topic_tree")


def leaf(
    name="action",
    value="synthetic-inventory",
    role="event_field",
    basis="The original event contains this field",
):
    return {
        "name": name,
        "operator": "Eq",
        "value": value,
        "field_role": role,
        "basis": basis,
    }


def wrapped(node):
    return {"topic": "synthetic-topic", "match": node}


def tree_truth(node, assignments):
    if "all" in node:
        return all([tree_truth(child, assignments) for child in node["all"]])
    if "any" in node:
        return any([tree_truth(child, assignments) for child in node["any"]])
    return next(assignments)


def expression_truth(expression, assignments):
    tokens = re.findall(r"[A-P]|&&|\|\||[()]", expression)
    position = 0

    def atom():
        nonlocal position
        token = tokens[position]
        position += 1
        if token != "(":
            return assignments[token]
        value = either()
        if tokens[position] != ")":
            raise AssertionError("unclosed compiled expression")
        position += 1
        return value

    def both():
        nonlocal position
        value = atom()
        while position < len(tokens) and tokens[position] == "&&":
            position += 1
            value = value & atom()
        return value

    def either():
        nonlocal position
        value = both()
        while position < len(tokens) and tokens[position] == "||":
            position += 1
            value = value | both()
        return value

    result = either()
    if position != len(tokens):
        raise AssertionError("unconsumed compiled expression")
    return result


class TopicTreeCompilerTests(unittest.TestCase):
    def test_single_leaf_compiles_to_exact_external_shape(self):
        result = tree.compile_filter(wrapped(leaf()))
        self.assertEqual(
            result["filter"],
            {
                "topic": "synthetic-topic",
                "A": {
                    "name": "action",
                    "operator": "Eq",
                    "value": "synthetic-inventory",
                },
                "expression": "A",
            },
        )
        self.assertEqual(
            result["field_roles"],
            [
                {
                    "name": "action",
                    "role": "event_field",
                    "basis": "The original event contains this field",
                }
            ],
        )
        self.assertRegex(result["source_tree_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["compiled_filter_sha256"], r"^[0-9a-f]{64}$")

    def test_nested_and_or_preserve_truth_table_and_every_leaf(self):
        node = {
            "all": [
                {"any": [leaf(value="first"), leaf(value="second")]},
                {
                    "any": [
                        leaf("owner", "third"),
                        {"all": [leaf("source", "fourth"), leaf("source", "fifth")]},
                    ]
                },
            ]
        }
        result = tree.compile_filter(wrapped(node))["filter"]
        self.assertEqual(set(result), {"topic", "expression", "A", "B", "C", "D", "E"})
        self.assertEqual(re.findall(r"[A-P]", result["expression"]), list("ABCDE"))
        for assignment in itertools.product((False, True), repeat=5):
            with self.subTest(assignment=assignment):
                self.assertEqual(
                    tree_truth(node, iter(assignment)),
                    expression_truth(
                        result["expression"], dict(zip("ABCDE", assignment))
                    ),
                )

    def test_same_field_different_values_are_not_merged(self):
        result = tree.compile_filter(
            wrapped({"any": [leaf(value="first"), leaf(value="second")]})
        )
        self.assertEqual(result["filter"]["A"]["value"], "first")
        self.assertEqual(result["filter"]["B"]["value"], "second")
        self.assertEqual(len(result["field_roles"]), 1)
        self.assertEqual(len(result["leaf_annotations"]), 2)

    def test_identical_leaves_are_not_silently_deduplicated(self):
        result = tree.compile_filter(wrapped({"all": [leaf(), leaf()]}))
        self.assertEqual(result["filter"]["A"], result["filter"]["B"])
        self.assertEqual(result["filter"]["expression"], "(A && B)")

    def test_conflicting_roles_for_same_field_are_rejected(self):
        for role in ("uncertain", "detection_metadata"):
            with self.subTest(role=role), self.assertRaises(tree.TreeContractError):
                tree.compile_filter(wrapped({"any": [leaf(), leaf(role=role)]}))

    def test_every_basis_is_validated_and_retained_by_alias(self):
        result = tree.compile_filter(
            wrapped({"all": [leaf(basis="first reason"), leaf(basis="second reason")]})
        )
        self.assertEqual(result["field_roles"][0]["basis"], "first reason")
        self.assertEqual(
            [item["basis"] for item in result["leaf_annotations"]],
            ["first reason", "second reason"],
        )
        for basis in ("", " ", None, "x" * 601):
            with (
                self.subTest(basis_type=type(basis).__name__),
                self.assertRaises(tree.TreeContractError),
            ):
                tree.compile_filter(wrapped({"all": [leaf(), leaf(basis=basis)]}))

    def test_uncertain_or_metadata_role_is_preserved_not_promoted(self):
        for role in ("uncertain", "detection_metadata"):
            result = tree.compile_filter(wrapped(leaf(role=role)))
            self.assertEqual(result["field_roles"][0]["role"], role)
            self.assertEqual(result["leaf_annotations"][0]["role"], role)

    def test_literal_values_operators_and_source_are_preserved(self):
        for operator in (
            "Eq",
            "Ne",
            "Contains",
            "NotContains",
            "StartsWith",
            "EndsWith",
        ):
            data = wrapped(
                dict(leaf(value="  literal\\path 中文  "), operator=operator)
            )
            before = copy.deepcopy(data)
            result = tree.compile_filter(data)
            self.assertEqual(data, before)
            self.assertEqual(result["filter"]["A"]["value"], "  literal\\path 中文  ")
            self.assertEqual(result["filter"]["A"]["operator"], operator)

    def test_mixed_unknown_empty_and_implicit_logic_nodes_rejected(self):
        for node in (
            {},
            [],
            {"all": []},
            {"any": [leaf()]},
            {"all": [leaf(), leaf()], "any": [leaf(), leaf()]},
            {"not": leaf()},
            {"conditions": [leaf(), leaf()]},
            {**leaf(), "all": [leaf(), leaf()]},
            {**leaf(), "expression": "A"},
        ):
            with (
                self.subTest(node_keys=list(node) if isinstance(node, dict) else None),
                self.assertRaises(tree.TreeContractError),
            ):
                tree.compile_filter(wrapped(node))

    def test_leaf_and_wrapper_contracts_are_strict(self):
        for change in (
            {"name": ""},
            {"name": "a" * 257},
            {"operator": "equal"},
            {"value": 1},
            {"value": ""},
            {"value": "a" * 2049},
            {"field_role": "normal"},
        ):
            with (
                self.subTest(change_keys=list(change)),
                self.assertRaises(tree.TreeContractError),
            ):
                tree.compile_filter(wrapped({**leaf(), **change}))
        for data in (
            {"topic": "x", "A": leaf(), "expression": "A"},
            {**wrapped(leaf()), "executeType": [0]},
            wrapped(leaf()) | {"topic": "x" * 101},
            wrapped(leaf()) | {"topic": ""},
        ):
            with (
                self.subTest(data_keys=list(data)),
                self.assertRaises(tree.TreeContractError),
            ):
                tree.compile_filter(data)

    def test_leaf_and_group_limits(self):
        self.assertEqual(
            len(
                tree.compile_filter(wrapped({"all": [leaf() for _ in range(16)]}))[
                    "leaf_annotations"
                ]
            ),
            16,
        )
        for node in (
            {"all": [leaf() for _ in range(17)]},
            {
                "all": [
                    {"all": [leaf() for _ in range(9)]},
                    {"any": [leaf() for _ in range(8)]},
                ]
            },
        ):
            with self.assertRaises(tree.TreeContractError):
                tree.compile_filter(wrapped(node))

    def test_depth_limit(self):
        node = leaf()
        for _ in range(9):
            node = {"all": [leaf(), node]}
        with self.assertRaises(tree.TreeContractError):
            tree.compile_filter(wrapped(node))

    def test_projection_removes_all_generation_role_judgments(self):
        data = wrapped(
            {
                "all": [
                    leaf(basis="REMOVE-FIRST-BASIS"),
                    {
                        "any": [
                            leaf("origin", basis="REMOVE-SECOND-BASIS"),
                            leaf("origin", basis="REMOVE-THIRD-BASIS"),
                        ]
                    },
                ]
            }
        )
        projected = tree.projection(data)
        self.assertEqual(
            projected,
            {
                "topic": "synthetic-topic",
                "match": {
                    "all": [
                        {
                            "name": "action",
                            "operator": "Eq",
                            "value": "synthetic-inventory",
                        },
                        {
                            "any": [
                                {
                                    "name": "origin",
                                    "operator": "Eq",
                                    "value": "synthetic-inventory",
                                },
                                {
                                    "name": "origin",
                                    "operator": "Eq",
                                    "value": "synthetic-inventory",
                                },
                            ]
                        },
                    ]
                },
            },
        )
        self.assertNotIn("field_role", json.dumps(projected))
        self.assertNotIn("REMOVE-", json.dumps(projected))
        invalid = wrapped({"all": [leaf(), leaf(role="uncertain")]})
        with self.assertRaises(tree.TreeContractError):
            tree.projection(invalid)

    def test_hashes_are_deterministic_and_logic_sensitive(self):
        a = wrapped({"all": [leaf(), leaf("origin")]})
        b = wrapped({"any": [leaf(), leaf("origin")]})
        first = tree.compile_filter(a)
        self.assertEqual(first, tree.compile_filter(copy.deepcopy(a)))
        second = tree.compile_filter(b)
        self.assertNotEqual(first["source_tree_sha256"], second["source_tree_sha256"])
        self.assertNotEqual(
            first["compiled_filter_sha256"], second["compiled_filter_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
