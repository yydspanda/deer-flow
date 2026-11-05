"""Bounded Python edits for locally extended controller and model-smoke sources.

No target module, class, default expression, or constructor is executed. Contracts
contain syntax hashes only. Unchanged source bytes are retained verbatim.
"""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize

CONTROLLER = "my_workflows/zeus/flows/alert_controller.py"
MODELS = "test/test_all_models.py"
SUPPORTED_PATHS = frozenset({CONTROLLER, MODELS})
RETIRED = frozenset({"DeepSeek_V32", "DeepSeek_V4_Flash_My"})
_OLD_BRANCHES = tuple(
    ast.parse(
        'if model_name == "DeepSeek_V32" and rc.ENV == "STG":\n'
        f'    model_name = "{model}"'
    ).body[0]
    for model in ("PingAnGPT_R", "Qwen38_27B")
)
_NEW_BRANCH = ast.parse("model_name = resolve_alert_model(model_name, rc.ENV)").body[0]
_RC_IMPORT = ast.parse("import util.root_config as rc").body[0]
_HELPER_IMPORT = ast.parse("from config.model_limits import resolve_alert_model").body[
    0
]


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _syntax(node):
    return _digest(ast.dump(node, include_attributes=False).encode("utf-8"))


def _position(source, line, byte_column):
    lines = source.splitlines(keepends=True)
    return sum(len(item) for item in lines[: line - 1]) + len(
        lines[line - 1].encode("utf-8")[:byte_column].decode("utf-8")
    )


def _span(source, node):
    return _position(source, node.lineno, node.col_offset), _position(
        source, node.end_lineno, node.end_col_offset
    )


def _apply_edits(source, edits):
    previous = 0
    chunks, preserved, changed_ranges = [], [], []
    for start, end, replacement in sorted(edits):
        if start < previous or start > end:
            raise ValueError("overlapping_scoped_python_edits")
        chunk = source[previous:start]
        chunks.extend((chunk, replacement))
        preserved.append(chunk)
        changed_ranges.append(
            {"start": start, "end": end, "replacement_characters": len(replacement)}
        )
        previous = end
    chunks.append(source[previous:])
    preserved.append(source[previous:])
    result = "".join(chunks)
    ast.parse(result)
    return result, {
        "preserved_bytes_sha256": _digest("".join(preserved).encode("utf-8")),
        "edit_ranges": changed_ranges,
        "outside_target_bytes_preserved": True,
    }


def _one(nodes, error):
    if len(nodes) != 1:
        raise ValueError(error)
    return nodes[0]


def _controller(source):
    tree = ast.parse(source)
    cls = _one(
        [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DataController"
        ],
        "controller_class_ambiguous",
    )
    init = _one(
        [
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        ],
        "controller_initializer_ambiguous",
    )
    defaults = list(
        zip(
            init.args.posonlyargs + init.args.args,
            [None]
            * (
                len(init.args.posonlyargs)
                + len(init.args.args)
                - len(init.args.defaults)
            )
            + init.args.defaults,
        )
    ) + list(zip(init.args.kwonlyargs, init.args.kw_defaults))
    default = _one(
        [
            value
            for argument, value in defaults
            if argument.arg == "model_name" and value is not None
        ],
        "controller_model_default_ambiguous",
    )
    rc_import = _one(
        [
            node
            for node in tree.body
            if isinstance(node, ast.Import)
            and any(alias.asname == "rc" for alias in node.names)
        ],
        "controller_rc_import_ambiguous",
    )
    helper_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "resolve_alert_model" for alias in node.names)
    ]
    if _syntax(rc_import) != _syntax(_RC_IMPORT):
        raise ValueError("controller_rc_import_changed")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", 1)[0]
                if bound_name == "rc" and node is not rc_import:
                    raise ValueError("controller_rc_name_conflicts")
                if bound_name == "resolve_alert_model" and _syntax(node) != _syntax(
                    _HELPER_IMPORT
                ):
                    raise ValueError("controller_resolver_name_conflicts")
        if (
            isinstance(node, ast.Name)
            and node.id == "rc"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            raise ValueError("controller_rc_name_conflicts")
    stores = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Name)
        and node.id == "model_name"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    old_branch_hashes = {_syntax(node) for node in _OLD_BRANCHES}
    branches = [
        node
        for node in init.body
        if _syntax(node) in old_branch_hashes | {_syntax(_NEW_BRANCH)}
    ]
    branch = _one(branches, "controller_resolution_block_changed_or_ambiguous")
    if len(stores) != 1:
        raise ValueError("controller_has_additional_model_reassignment")
    if stores[0] not in list(ast.walk(branch)):
        raise ValueError("controller_resolution_assignment_changed")
    index = init.body.index(branch)
    if any(
        isinstance(node, ast.Name) and node.id == "model_name"
        for statement in init.body[:index]
        for node in ast.walk(statement)
    ):
        raise ValueError("controller_uses_model_before_resolution")
    self_stores = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Attribute)
        and node.attr == "model_name"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    self_store = _one(self_stores, "controller_self_model_reassignment")
    assignments = [
        node
        for node in init.body[index + 1 :]
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and node.targets[0] is self_store
        and isinstance(node.value, ast.Name)
        and node.value.id == "model_name"
    ]
    _one(assignments, "controller_self_model_assignment_changed")
    calls = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_alert_model"
    ]
    before = _syntax(branch) in old_branch_hashes
    resolver_names = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "resolve_alert_model"
    ]
    resolver_bindings = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == "resolve_alert_model"
        or isinstance(node, ast.arg)
        and node.arg == "resolve_alert_model"
    ]
    if resolver_bindings or len(resolver_names) != (0 if before else 1):
        raise ValueError("controller_resolver_name_conflicts")
    if before:
        if (
            not isinstance(default, ast.Constant)
            or default.value != "DeepSeek_V32"
            or helper_imports
            or calls
        ):
            raise ValueError("controller_partial_or_changed_routing")
    else:
        if (
            not isinstance(default, ast.Constant)
            or default.value is not None
            or len(helper_imports) != 1
            or _syntax(helper_imports[0]) != _syntax(_HELPER_IMPORT)
            or len(calls) != 1
        ):
            raise ValueError("controller_partial_or_changed_routing")
    return tree, init, default, branch, rc_import, before


def _model_lists(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "models"
            and node.func.attr
            in {
                "append",
                "extend",
                "insert",
                "clear",
                "pop",
                "remove",
                "sort",
                "reverse",
                "__setitem__",
                "__iadd__",
            }
        ):
            raise ValueError("model_list_dynamic_write")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "models"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            raise ValueError("model_list_dynamic_write")
        if (
            isinstance(node, ast.NamedExpr)
            and isinstance(node.target, ast.Name)
            and node.target.id == "models"
        ):
            raise ValueError("model_list_dynamic_write")
    assignments = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            if isinstance(node, (ast.AugAssign, ast.Delete)) and any(
                isinstance(value, ast.Name) and value.id == "models"
                for value in ast.walk(node)
            ):
                raise ValueError("model_list_dynamic_write")
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "models" for target in targets
        ):
            continue
        if len(targets) != 1 or not isinstance(node.value, ast.List):
            raise ValueError("model_list_not_standalone_literal")
        values = node.value.elts
        if any(
            not isinstance(value, ast.Constant) or not isinstance(value.value, str)
            for value in values
        ):
            raise ValueError("model_list_contains_dynamic_value")
        names = [value.value for value in values]
        if len(set(names)) != len(names):
            raise ValueError("model_list_duplicate_alias")
        if not [name for name in names if name not in RETIRED]:
            raise ValueError("model_list_would_be_empty")
        assignments.append(node.value)
    if not assignments:
        raise ValueError("model_list_missing")
    allowed_stores = {
        id(node.targets[0] if isinstance(node, ast.Assign) else node.target)
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value in assignments
    }
    if any(
        isinstance(node, ast.Name)
        and node.id == "models"
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and id(node) not in allowed_stores
        for node in ast.walk(tree)
    ):
        raise ValueError("model_list_dynamic_write")
    return assignments


def build_contract(path, baselinebytes):
    source = baselinebytes.decode("utf-8")
    if path == CONTROLLER:
        _, _, default, branch, rc_import, before = _controller(source)
        if not before:
            raise ValueError("controller_baseline_must_precede_patch")
        return {
            "schema": "sec_know.scoped_python.v1",
            "path": path,
            "default_ast_sha256": _syntax(default),
            "branch_ast_sha256": _syntax(branch),
            "reviewed_branch_ast_sha256s": sorted(
                _syntax(node) for node in _OLD_BRANCHES
            ),
            "rc_import_ast_sha256": _syntax(rc_import),
        }
    if path == MODELS:
        lists = _model_lists(source)
        seen = {value.value for sequence in lists for value in sequence.elts}
        if not RETIRED.issubset(seen):
            raise ValueError("model_baseline_missing_retired_names")
        return {
            "schema": "sec_know.scoped_python.v1",
            "path": path,
            "policy": "literal_models_lists_remove_retired_preserve_other_bytes_v1",
        }
    raise ValueError("unsupported_scoped_python_path")


def apply(path, data, contract):
    if (
        contract.get("schema") != "sec_know.scoped_python.v1"
        or contract.get("path") != path
    ):
        raise ValueError("scoped_python_contract_mismatch")
    source = data.decode("utf-8")
    edits = []
    if path == CONTROLLER:
        _, _, default, branch, rc_import, before = _controller(source)
        if before:
            branch_hashes = contract.get(
                "reviewed_branch_ast_sha256s", [contract.get("branch_ast_sha256")]
            )
            if (
                not isinstance(branch_hashes, list)
                or not branch_hashes
                or not all(isinstance(value, str) for value in branch_hashes)
                or not set(branch_hashes).issubset(
                    {_syntax(node) for node in _OLD_BRANCHES}
                )
                or _syntax(branch) not in branch_hashes
            ):
                raise ValueError("controller_target_contract_mismatch")
            if any(
                contract.get(key) != _syntax(node)
                for key, node in (
                    ("default_ast_sha256", default),
                    ("rc_import_ast_sha256", rc_import),
                )
            ):
                raise ValueError("controller_target_contract_mismatch")
            edits.append((*_span(source, default), "None"))
            edits.append(
                (
                    *_span(source, branch),
                    "model_name = resolve_alert_model(model_name, rc.ENV)",
                )
            )
            lines = source.splitlines(keepends=True)
            start, end = _span(source, rc_import)
            line_start = _position(source, rc_import.lineno, 0)
            line_end = sum(map(len, lines[: rc_import.end_lineno]))
            if source[line_start:start].strip() or source[
                end:line_end
            ].strip().startswith(";"):
                raise ValueError("controller_import_not_separate_line")
            if source[end:line_end].strip() and not source[
                end:line_end
            ].lstrip().startswith("#"):
                raise ValueError("controller_import_tail_changed")
            newline = (
                "\r\n" if lines[rc_import.end_lineno - 1].endswith("\r\n") else "\n"
            )
            edits.append(
                (
                    line_end,
                    line_end,
                    "from config.model_limits import resolve_alert_model" + newline,
                )
            )
    elif path == MODELS:
        if (
            contract.get("policy")
            != "literal_models_lists_remove_retired_preserve_other_bytes_v1"
        ):
            raise ValueError("scoped_model_policy_mismatch")
        sequences = _model_lists(source)
        commas = []
        try:
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.OP and token.string == ",":
                    # tokenize columns count Unicode characters, unlike AST offsets.
                    lines = source.splitlines(keepends=True)
                    start = sum(map(len, lines[: token.start[0] - 1])) + token.start[1]
                    end = sum(map(len, lines[: token.end[0] - 1])) + token.end[1]
                    commas.append((start, end))
        except (IndentationError, tokenize.TokenError):
            raise ValueError("model_list_tokenization_failed") from None
        removed = set()
        for sequence in sequences:
            list_start, list_end = _span(source, sequence)
            for index, value in enumerate(sequence.elts):
                if value.value not in RETIRED:
                    continue
                start, end = _span(source, value)
                removed.add((start, end))
                next_start = (
                    _span(source, sequence.elts[index + 1])[0]
                    if index + 1 < len(sequence.elts)
                    else list_end
                )
                following = [pair for pair in commas if end <= pair[0] < next_start]
                if following:
                    removed.add(following[0])
                else:
                    prior_end = (
                        _span(source, sequence.elts[index - 1])[1]
                        if index
                        else list_start
                    )
                    previous = [pair for pair in commas if prior_end <= pair[0] < start]
                    if not previous:
                        raise ValueError("model_list_separator_ambiguous")
                    removed.add(previous[-1])
        edits.extend((start, end, "") for start, end in removed)
        before = bool(edits)
    else:
        raise ValueError("unsupported_scoped_python_path")
    changed, proof = _apply_edits(source, edits)
    if path == CONTROLLER:
        if _controller(changed)[-1]:
            raise ValueError("controller_target_not_installed")
    elif any(
        value.value in RETIRED
        for sequence in _model_lists(changed)
        for value in sequence.elts
    ):
        raise ValueError("retired_model_literal_remaining")
    result = changed.encode("utf-8")
    proof.update(
        {
            "state": "before" if before else "after",
            "validation_scope": "reviewed_target_syntax_only_other_source_preserved",
            "before_sha256": _digest(data),
            "after_sha256": _digest(result),
            "target_source_executed": False,
        }
    )
    return result, proof
