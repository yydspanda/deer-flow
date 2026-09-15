"""Compact serialization for SOC-owned model inputs, not storage or signing."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def model_json(value: Any, *, sort_keys: bool = False, default: Callable[[Any], Any] | None = None) -> str:
    """Remove JSON layout whitespace without changing strings or structured values."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=sort_keys, default=default)
