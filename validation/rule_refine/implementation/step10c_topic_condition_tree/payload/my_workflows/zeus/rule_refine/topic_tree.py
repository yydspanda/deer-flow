"""Compile explicit model boolean structure into the unchanged flat ZEUS format.

No term is dropped, merged, inferred or evaluated. Every leaf receives its own
alias, and every group retains its explicit all/any relation and parentheses.
"""

import copy
import hashlib
import json

from .operators import OPERATORS

CONTRACT = "topic-condition-tree-v1"
MAX_LEAVES = 16
MAX_DEPTH = 8
LEAF_KEYS = {"name", "operator", "value", "field_role", "basis"}
ROLES = {"event_field", "detection_metadata", "uncertain"}


class TreeContractError(ValueError):
    def __init__(self, code, path=()):
        self.code = code
        self.path = tuple(path)
        super().__init__(code)


def _text(value, maximum, code, path, *, literal=False):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or (not literal and not value.strip())
    ):
        raise TreeContractError(code, path)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise TreeContractError(code, path) from None
    return value


def _hash(value):
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compile_filter(filter_tree):
    """Validate and translate one tree, keeping every predicate literal intact."""
    if not isinstance(filter_tree, dict) or set(filter_tree) != {"topic", "match"}:
        raise TreeContractError("topic_tree_filter_required", ("filter",))
    topic = _text(
        filter_tree["topic"], 100, "topic_tree_topic_invalid", ("filter", "topic")
    )
    if topic != topic.strip():
        raise TreeContractError("topic_tree_topic_invalid", ("filter", "topic"))
    flat = {"topic": topic}
    roles_by_name = {}
    annotations = []

    def visit(node, path, depth):
        if depth > MAX_DEPTH:
            raise TreeContractError("topic_tree_depth_limit", path)
        if not isinstance(node, dict):
            raise TreeContractError("topic_tree_node_required", path)
        if set(node) in ({"all"}, {"any"}):
            kind = next(iter(node))
            children = node[kind]
            if not isinstance(children, list) or not 2 <= len(children) <= MAX_LEAVES:
                raise TreeContractError("topic_tree_group_size", path + (kind,))
            expressions = [
                visit(child, path + (kind, index), depth + 1)
                for index, child in enumerate(children)
            ]
            operator = " && " if kind == "all" else " || "
            return "(" + operator.join(expressions) + ")"
        if set(node) != LEAF_KEYS:
            raise TreeContractError("topic_tree_leaf_shape", path)
        if len(annotations) >= MAX_LEAVES:
            raise TreeContractError("topic_tree_leaf_limit", path)
        name = _text(node["name"], 256, "topic_tree_field_invalid", path + ("name",))
        value = _text(
            node["value"],
            2048,
            "topic_tree_value_invalid",
            path + ("value",),
            literal=True,
        )
        operator = node["operator"]
        if not isinstance(operator, str) or operator not in OPERATORS:
            raise TreeContractError("topic_tree_operator_invalid", path + ("operator",))
        role = node["field_role"]
        if not isinstance(role, str) or role not in ROLES:
            raise TreeContractError(
                "topic_tree_field_role_invalid", path + ("field_role",)
            )
        basis = _text(node["basis"], 600, "topic_tree_basis_invalid", path + ("basis",))
        previous = roles_by_name.get(name)
        if previous is not None and previous["role"] != role:
            raise TreeContractError(
                "topic_tree_field_role_conflict", path + ("field_role",)
            )
        alias = chr(ord("A") + len(annotations))
        flat[alias] = {"name": name, "operator": operator, "value": value}
        annotations.append({"alias": alias, "name": name, "role": role, "basis": basis})
        if previous is None:
            # The unique-field view is for existing reports. Every occurrence's
            # role and basis was checked and remains in leaf_annotations.
            roles_by_name[name] = {"name": name, "role": role, "basis": basis}
        return alias

    flat["expression"] = visit(filter_tree["match"], ("filter", "match"), 0)
    return {
        "filter": flat,
        "field_roles": list(roles_by_name.values()),
        "leaf_annotations": annotations,
        "source_tree_sha256": _hash(filter_tree),
        "compiled_filter_sha256": _hash(flat),
    }


def projection(filter_tree):
    """Provide the reviewer only predicates and explicit boolean relations.

    Generated role classifications and reasons are excluded from review context.
    Validation runs first, so projection never conceals an invalid source tree.
    """
    compile_filter(filter_tree)

    def project(node):
        if set(node) in ({"all"}, {"any"}):
            kind = next(iter(node))
            return {kind: [project(child) for child in node[kind]]}
        return {key: copy.deepcopy(node[key]) for key in ("name", "operator", "value")}

    return {"topic": filter_tree["topic"], "match": project(filter_tree["match"])}
