"""Explicit mapping-file normalizer for simple vendor payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from soc_agent.contracts import AlertInput
from soc_agent.normalizers.alert import build_detection_key

ALLOWED_TARGET_ROOTS = {
    "schema_version",
    "tenant_id",
    "alert_id",
    "source",
    "detection",
    "event",
    "classification",
    "entities",
    "evidence",
    "extensions",
}


def load_mapping_config(path: str | Path) -> dict[str, Any]:
    """Load a SOC normalization mapping config from YAML."""

    mapping_path = Path(path)
    loaded = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"mapping config must be a YAML object: {mapping_path}")
    return loaded


def normalize_with_mapping(payload: Mapping[str, Any], mapping_config: Mapping[str, Any]) -> AlertInput:
    """Normalize a vendor payload using explicit canonical target mappings.

    The mapping config intentionally supports only deterministic field moves.
    It does not infer fields, call an LLM, or mutate the mapping at runtime.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("alert payload must be a JSON object")

    fields = mapping_config.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("mapping config must include a non-empty fields object")

    normalized: dict[str, Any] = {
        "schema_version": str(mapping_config.get("schema_version") or "soc.alert.v1"),
        "source": _source_defaults(mapping_config),
        "extensions": {
            "normalization": {
                "adapter": "mapping",
                "mapping_name": _mapping_name(mapping_config),
                "mapped_fields": sorted(str(target) for target in fields),
                "missing_source_paths": [],
            }
        },
        "raw": dict(payload),
    }

    warnings: list[str] = []
    for target_path, source_expr in fields.items():
        value = _first_present(payload, source_expr)
        if value is _MISSING:
            warnings.append(f"missing mapping source path: {source_expr} -> {target_path}")
            normalized["extensions"]["normalization"]["missing_source_paths"].append(str(source_expr))
            continue
        _set_target(normalized, str(target_path), value)

    if warnings:
        normalized["extensions"]["normalization"]["warnings"] = warnings

    alert = AlertInput.model_validate(normalized)
    alert.detection.detection_key = alert.detection.detection_key or build_detection_key(alert)
    return alert


def _source_defaults(mapping_config: Mapping[str, Any]) -> dict[str, Any]:
    source = mapping_config.get("source")
    if isinstance(source, Mapping):
        return {str(key): value for key, value in source.items() if value is not None}

    return {key: mapping_config[key] for key in ("source_type", "source_system", "vendor", "product", "integration_name") if mapping_config.get(key) is not None}


def _mapping_name(mapping_config: Mapping[str, Any]) -> str:
    name = mapping_config.get("name")
    if name is None:
        return "unnamed"
    return str(name)


class _Missing:
    pass


_MISSING = _Missing()


def _first_present(payload: Mapping[str, Any], source_expr: Any) -> Any | _Missing:
    if isinstance(source_expr, str):
        return _resolve_source_path(payload, source_expr)
    if isinstance(source_expr, Sequence) and not isinstance(source_expr, str):
        for item in source_expr:
            if not isinstance(item, str):
                raise ValueError(f"mapping source path must be a string: {item!r}")
            value = _resolve_source_path(payload, item)
            if value is not _MISSING:
                return value
        return _MISSING
    raise ValueError(f"mapping source path must be a string or list of strings: {source_expr!r}")


def _resolve_source_path(payload: Mapping[str, Any], path: str) -> Any | _Missing:
    if not path.startswith("$."):
        raise ValueError(f"mapping source path must start with '$.': {path}")

    current: Any = payload
    for segment in path[2:].split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return _MISSING
            current = current[segment]
            continue
        if isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                return _MISSING
            current = current[index]
            continue
        return _MISSING
    return current


def _set_target(normalized: dict[str, Any], target_path: str, value: Any) -> None:
    if not target_path or target_path.startswith("$."):
        raise ValueError(f"mapping target path must be canonical, not JSONPath: {target_path}")

    parts = target_path.split(".")
    if parts[0] not in ALLOWED_TARGET_ROOTS:
        raise ValueError(f"unsupported mapping target root: {parts[0]}")

    current = normalized
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"mapping target path conflicts with scalar field: {target_path}")
        current = child
    current[parts[-1]] = value
