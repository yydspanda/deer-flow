"""Stable hashing helpers for audit and step trace records."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rule_entity_key(detection_key: str) -> str:
    """Preserve the existing rule-entity identity across extraction and scope views."""
    return "rule:" + hashlib.sha256(detection_key.encode("utf-8")).hexdigest()[:16]
