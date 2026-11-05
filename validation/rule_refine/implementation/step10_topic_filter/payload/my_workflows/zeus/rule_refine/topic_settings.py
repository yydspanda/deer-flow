"""Opt-in topic suggestions for LOCAL/PRD; no environment-derived executeType."""

import json
import os
from pathlib import Path

from .surge_settings import validate_settings as validate_budget

CONFIG = "config/rule_refine.topic.json"
DEFAULTS = {
    "enabled": False,
    "model": "Pub_DeepSeek_V4_Flash",
    "csv_path": "",
    "namespace": "",
    "context_tokens": 262144,
    "input_fraction": 0.8,
    "timeout_seconds": 600,
    "operator_context": "",
}


def profile(environ):
    values = {
        str(environ[key]).upper()
        for key in ("env_profile", "ENV_PROFILE")
        if key in environ
    }
    return next(iter(values)) if len(values) == 1 else "LOCAL" if not values else None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_topic_configuration_field")
        result[key] = value
    return result


def load_settings(root, environ=None):
    env = os.environ if environ is None else environ
    if profile(env) not in {"LOCAL", "PRD"}:
        return None
    path = Path(root) / CONFIG
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError("invalid_topic_configuration_file")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("invalid_topic_configuration_fields")
    value = {**DEFAULTS, **value}
    if type(value["enabled"]) is not bool:
        raise ValueError("invalid_topic_enabled")
    if not value["enabled"]:
        return None
    for key, limit in (("model", 128), ("namespace", 512), ("csv_path", 4096)):
        item = value[key]
        if (
            not isinstance(item, str)
            or not 1 <= len(item) <= limit
            or item != item.strip()
            or not item.isprintable()
        ):
            raise ValueError("invalid_topic_" + key)
    validate_budget(
        {
            key: value[key]
            for key in (
                "enabled",
                "context_tokens",
                "input_fraction",
                "timeout_seconds",
                "operator_context",
            )
        }
    )
    csv_path = Path(value["csv_path"]).expanduser()
    value["csv_path"] = str(
        csv_path if csv_path.is_absolute() else Path(root) / csv_path
    )
    return value


def topic_enabled(root, environ=None):
    """Import-time schedule check must never break Celery startup."""
    env = os.environ if environ is None else environ
    if env.get("RULE_REFINE_MONITOR_ENABLED") == "0":
        return False
    try:
        return load_settings(root, env) is not None
    except Exception:
        return False
