"""Remove reviewed registry fragments while preserving unrelated local bytes.

No target module is imported and no configuration expression is evaluated. The
YAML reader intentionally recognizes only a narrow single-line mapping layout.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re

RETIRED = ("DeepSeek_V32", "DeepSeek_V4_Flash_My")
PUB = "Pub_DeepSeek_V4_Flash"
YAML_PATH = "llm_service/litellm_proxy_config.prd.yaml"
PYTHON_PATH = "llm_service/openai_completion.py"
SUPPORTED_PATHS = frozenset({YAML_PATH, PYTHON_PATH})
SCHEMA = "sec_know.scoped_proxy_regions.v1"


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _lines(data):
    text = data.decode("utf-8")
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line.encode("utf-8")))
    return text, lines, offsets


def _scalar(value):
    if not value or "\t" in value:
        raise ValueError("unsupported_yaml_scalar")
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except ValueError:
            raise ValueError("unsupported_yaml_scalar") from None
        if not isinstance(decoded, str):
            raise ValueError("unsupported_yaml_scalar")
    elif value.startswith("'"):
        if not re.fullmatch(r"'(?:[^']|'')*'", value):
            raise ValueError("unsupported_yaml_scalar")
    elif (
        value[0] in "&*!|>{[}],?%@`"
        or value.startswith("- ")
        or ": " in value
        or " #" in value
    ):
        raise ValueError("unsupported_yaml_scalar")


def _yaml_regions(data):
    _, lines, offsets = _lines(data)
    declarations = [
        i for i, line in enumerate(lines) if re.match(r"model_list\s*:", line)
    ]
    if len(declarations) != 1 or lines[declarations[0]].rstrip("\r\n") != "model_list:":
        raise ValueError("expected_unique_top_level_model_list")
    first = declarations[0] + 1
    last = first
    while last < len(lines):
        content = lines[last].rstrip("\r\n")
        if (
            content.strip()
            and not content.lstrip().startswith("#")
            and not content.startswith(" ")
        ):
            break
        last += 1
    markers = []
    names = set()
    root_keys = set()
    leaf_keys = set()
    parent_mapping = False
    parent_name = None
    parameter_model_seen = False
    current = None
    for index in range(first, last):
        line = lines[index].rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            raise ValueError("unsupported_yaml_indentation")
        header = re.fullmatch(r"  - model_name: ([A-Za-z_][A-Za-z0-9_.-]*)", line)
        if header:
            if current is not None and (
                "litellm_params" not in root_keys or not parameter_model_seen
            ):
                raise ValueError("missing_litellm_params")
            current = header[1]
            if current in names:
                raise ValueError("duplicate_yaml_model_alias")
            names.add(current)
            markers.append((current, index))
            root_keys, leaf_keys, parent_mapping = {"model_name"}, set(), False
            parent_name, parameter_model_seen = None, False
            continue
        if current is None:
            raise ValueError("unexpected_yaml_model_list_content")
        root = re.fullmatch(r"    ([A-Za-z_][A-Za-z0-9_]*):(?: (.*))?", line)
        if root:
            if root[1] in root_keys:
                raise ValueError("duplicate_yaml_mapping_key")
            root_keys.add(root[1])
            parent_mapping = not root[2]
            parent_name = root[1]
            leaf_keys = set()
            if root[1] == "litellm_params" and not parent_mapping:
                raise ValueError("expected_litellm_params_mapping")
            if root[2]:
                _scalar(root[2])
            continue
        leaf = re.fullmatch(r"      ([A-Za-z_][A-Za-z0-9_]*): (.+)", line)
        if leaf and parent_mapping:
            if leaf[1] in leaf_keys:
                raise ValueError("duplicate_yaml_mapping_key")
            leaf_keys.add(leaf[1])
            _scalar(leaf[2])
            if parent_name == "litellm_params" and leaf[1] == "model":
                parameter_model_seen = True
            continue
        raise ValueError("unsupported_yaml_model_mapping")
    if (
        current is None
        or "litellm_params" not in root_keys
        or not parameter_model_seen
        or PUB not in names
    ):
        raise ValueError("missing_public_model_registry")
    regions = {}
    for number, (name, start) in enumerate(markers):
        end = markers[number + 1][1] if number + 1 < len(markers) else last
        regions[name] = (offsets[start], offsets[end])
    return regions, {
        "yaml_envelope_sha256": _hash(data[: offsets[first]] + data[offsets[last] :])
    }


def _targets_name(node, name):
    if isinstance(node, ast.Name):
        return node.id == name
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_targets_name(item, name) for item in node.elts)
    if isinstance(node, (ast.Subscript, ast.Attribute)):
        return _targets_name(node.value, name)
    return False


def _python_regions(data):
    text, lines, offsets = _lines(data)
    tree = ast.parse(text)
    declarations = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(_targets_name(target, "PRD_MODEL_CONFIGS") for target in targets):
                if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                    raise ValueError("unsupported_prd_registry_write")
                declarations.append(node)
        elif isinstance(node, (ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, ast.Delete) else [node.target]
            if any(_targets_name(target, "PRD_MODEL_CONFIGS") for target in targets):
                raise ValueError("unsupported_prd_registry_write")
    if len(declarations) != 1 or not isinstance(declarations[0].value, ast.Dict):
        raise ValueError("expected_unique_literal_prd_registry")
    declaration = declarations[0]
    # Reject additional writes even when hidden in an if/function; this still
    # does not claim to detect arbitrary reflective mutations elsewhere.
    for node in ast.walk(tree):
        if node is declaration:
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = (
                node.targets
                if isinstance(node, (ast.Assign, ast.Delete))
                else [node.target]
            )
            if any(_targets_name(target, "PRD_MODEL_CONFIGS") for target in targets):
                raise ValueError("additional_prd_registry_write")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if _targets_name(
                node.func.value, "PRD_MODEL_CONFIGS"
            ) and node.func.attr in {
                "update",
                "setdefault",
                "pop",
                "popitem",
                "clear",
                "__setitem__",
                "__delitem__",
            }:
                raise ValueError("additional_prd_registry_write")
    regions = {}
    previous_end = -1
    for key, value in zip(declaration.value.keys, declaration.value.values):
        if (
            not isinstance(key, ast.Constant)
            or not isinstance(key.value, str)
            or not key.value
        ):
            raise ValueError("nonliteral_prd_registry_key")
        if key.value in regions:
            raise ValueError("duplicate_prd_registry_key")
        start, end = key.lineno - 1, value.end_lineno
        if start < previous_end:
            raise ValueError("shared_registry_entry_line")
        prefix = lines[start].encode("utf-8")[: key.col_offset]
        suffix = lines[end - 1].encode("utf-8")[value.end_col_offset :]
        if prefix.strip() or not re.fullmatch(rb"[ \t]*,[ \t]*(?:\r?\n)?", suffix):
            raise ValueError("unsupported_registry_entry_boundary")
        regions[key.value] = (offsets[start], offsets[end])
        previous_end = end
    if PUB not in regions:
        raise ValueError("missing_public_model_registry")
    return regions, {}


def _regions(path, data):
    if path == YAML_PATH:
        return _yaml_regions(data)
    if path == PYTHON_PATH:
        return _python_regions(data)
    raise ValueError("unsupported_scoped_proxy_path")


def build_contract(path, baseline_bytes):
    regions, extra = _regions(path, baseline_bytes)
    if not all(name in regions for name in RETIRED):
        raise ValueError("baseline_missing_retired_regions")
    return {
        "schema": SCHEMA,
        "path": path,
        "retired_region_sha256": {
            name: _hash(baseline_bytes[slice(*regions[name])]) for name in RETIRED
        },
        **extra,
    }


def apply(path, data, contract):
    if contract.get("schema") != SCHEMA or contract.get("path") != path:
        raise ValueError("scoped_proxy_contract_mismatch")
    regions, extra = _regions(path, data)
    if extra.get("yaml_envelope_sha256") != contract.get("yaml_envelope_sha256"):
        raise ValueError("yaml_envelope_changed")
    present = [name for name in RETIRED if name in regions]
    if present and len(present) != len(RETIRED):
        raise ValueError("partial_retirement")
    ranges = []
    for name in present:
        start, end = regions[name]
        if _hash(data[start:end]) != contract["retired_region_sha256"].get(name):
            raise ValueError("retired_region_changed")
        ranges.append((start, end))
    result = data
    for start, end in sorted(ranges, reverse=True):
        result = result[:start] + result[end:]
    remaining, after_extra = _regions(path, result)
    if any(name in remaining for name in RETIRED) or after_extra != extra:
        raise ValueError("unexpected_registry_result")
    return result, {
        "state": "before" if present else "after",
        "validation": "reviewed_retired_registry_regions",
        "scope": "static_registry_initialization_only",
        "source_sha256": _hash(data),
        "result_sha256": _hash(result),
        "removed_byte_ranges": [list(item) for item in sorted(ranges)],
        "retired_regions_verified": list(present),
        "non_target_bytes_preserved": True,
    }
