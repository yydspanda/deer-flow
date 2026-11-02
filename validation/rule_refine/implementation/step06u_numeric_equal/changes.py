"""Construct a replay-only comparator update from the pinned original sources."""

import ast
import hashlib


ANALYSIS_SHA = "c8e73780c98a0ae6e02611220965050be07e0dcb3b30b133d4419a03176f00e0"


def replace_function(source, name, replacement):
    tree = ast.parse(source)
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise ValueError("comparison_patch_location_mismatch")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return (
        "".join(lines[: node.lineno - 1])
        + replacement
        + "".join(lines[node.end_lineno :])
    )


def patched_sources(original_sources, recorded_pins, comparator_source):
    # Check every existing module before compiling a modified variant.
    for name, source in original_sources.items():
        relative = "my_workflows/zeus/rule_refine/" + name + ".py"
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != recorded_pins.get(
            relative
        ):
            raise ValueError("recorded_source_version_mismatch")
    if (
        hashlib.sha256(original_sources["analysis"].encode("utf-8")).hexdigest()
        != ANALYSIS_SHA
    ):
        raise ValueError("analysis_version_mismatch")
    result = dict(original_sources)
    result["condition_values"] = comparator_source
    result["analysis"] = replace_function(
        result["analysis"],
        "_supported",
        """def _supported(condition, logs):
    return any(evaluate_condition(condition, log)[0] is True for log in logs)
""",
    ).replace(
        "from .contracts import Description, Evidence, FilterDefinition, RuleRecommendation, StrictModel, Text",
        "from .condition_values import evaluate_condition\nfrom .contracts import Description, Evidence, FilterDefinition, RuleRecommendation, StrictModel, Text",
        1,
    )
    result["filter_evaluation"] = replace_function(
        result["filter_evaluation"],
        "_condition",
        """def _condition(condition, log):
    return evaluate_condition(condition, log)
""",
    ).replace(
        "from .contracts import TOKEN, FilterDefinition",
        "from .condition_values import evaluate_condition\nfrom .contracts import TOKEN, FilterDefinition",
        1,
    )
    for name, source in result.items():
        compile(source, "<numeric-replay-" + name + ">", "exec")
    return result
