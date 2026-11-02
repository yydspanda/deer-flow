"""Shared resource budgets; independent of application/service configuration."""

import json
from dataclasses import dataclass
from pathlib import Path

CANDIDATE_BYTES = 32 * 1024 * 1024
RECORD_BYTES = 40 * 1024 * 1024
ALERT_BYTES = 2 * 1024 * 1024
TASK_TOTAL_BYTES = CANDIDATE_BYTES
TASK_ROWS = 100
JSON_MAX_DEPTH = 64
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Limits:
    input_kib: int = 128
    output_kib: int = 64
    timeout_seconds: int = 240
    retry_attempts: int = 3
    retry_delay_seconds: int = 60

    def __post_init__(self):
        for name, low, high in (
            ("input_kib", 64, 2048),
            ("output_kib", 16, 1024),
            ("timeout_seconds", 1, 600),
            ("retry_attempts", 1, 5),
            ("retry_delay_seconds", 1, 3600),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid_rule_refine_limit:" + name)

    @property
    def input_bytes(self):
        return self.input_kib * 1024

    @property
    def output_bytes(self):
        return self.output_kib * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_rule_refine_limit")
        result[key] = value
    return result


def get_limits(root=None):
    """Missing file means defaults; a malformed file never partially applies."""
    root = PROJECT_ROOT if root is None else Path(root)
    path = root / "config/rule_refine.limits.local.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(16385)
    except FileNotFoundError:
        return Limits()
    if len(raw) > 16384:
        raise ValueError("rule_refine_limits_file_over_budget")
    try:
        values = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (ValueError, UnicodeError):
        raise ValueError("invalid_rule_refine_limits_json") from None
    if not isinstance(values, dict) or set(values) - set(Limits.__dataclass_fields__):
        raise ValueError("invalid_rule_refine_limits_fields")
    return Limits(**values)
