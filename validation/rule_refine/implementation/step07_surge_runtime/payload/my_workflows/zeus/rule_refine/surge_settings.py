"""Operator configuration for the LOCAL surge workflow; no model output cap."""

import json
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    "context_tokens": 262144,
    "input_fraction": 0.8,
    "timeout_seconds": 600,
    "operator_context": "",
}
CONFIG = "config/rule_refine.surge.local.json"


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("invalid_surge_configuration_fields")
    result = {**DEFAULTS, **value}
    if type(result["enabled"]) is not bool:
        raise ValueError("invalid_surge_enabled")
    if type(result["context_tokens"]) is not int or result["context_tokens"] < 1:
        raise ValueError("invalid_context_tokens")
    if (
        type(result["input_fraction"]) not in (float, int)
        or not 0 < result["input_fraction"] < 1
    ):
        raise ValueError("invalid_input_fraction")
    if int(result["context_tokens"] * result["input_fraction"]) < 1:
        raise ValueError("invalid_input_fraction")
    if (
        type(result["timeout_seconds"]) is not int
        or not 1 <= result["timeout_seconds"] <= 600
    ):
        raise ValueError("invalid_timeout_seconds")
    if not isinstance(result["operator_context"], str):
        raise ValueError("invalid_operator_context")
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_surge_configuration_field")
        result[key] = value
    return result


def load_settings(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return {**DEFAULTS, "enabled": False}
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError("invalid_surge_configuration_file")
    return validate_settings(
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    )
