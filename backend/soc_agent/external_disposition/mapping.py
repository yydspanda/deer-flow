"""Mapping helpers for vendor-neutral external disposition feedback."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from soc_agent.contracts import (
    SocExternalDispositionAdapterConfig,
    SocExternalDispositionEvent,
    SocExternalDispositionMappingConfig,
    SocExternalDispositionStatusMapping,
)
from soc_agent.utils.hashing import stable_hash


def build_external_disposition_event(
    payload: Mapping[str, Any],
    config: SocExternalDispositionAdapterConfig | Mapping[str, Any],
) -> SocExternalDispositionEvent:
    """Map one external payload into ``SocExternalDispositionEvent``."""

    adapter_config = SocExternalDispositionAdapterConfig.model_validate(config)
    values = {
        "tenant_id": adapter_config.tenant_id,
        "external_system": adapter_config.external_system,
        "raw_payload_hash": stable_hash(payload),
        "metadata": {
            "adapter_config_schema_version": adapter_config.schema_version,
            **adapter_config.metadata,
        },
    }
    for field_name, path in adapter_config.field_paths.items():
        value = _value_at_path(payload, path)
        if value is not None:
            values[field_name] = _normalize_field_value(field_name, value)
    return SocExternalDispositionEvent.model_validate(values)


def build_external_disposition_idempotency_key(event: SocExternalDispositionEvent) -> str:
    """Build the stable idempotency key required by the external feedback lane."""

    source_ref = event.source_event_id or event.source_version
    if not source_ref:
        source_ref = stable_hash(
            {
                "updated_at": event.updated_at.isoformat(),
                "raw_payload_hash": event.raw_payload_hash,
            }
        )
    return ":".join(
        [
            "external_disposition",
            event.tenant_id or "default",
            event.external_system,
            event.external_case_id,
            source_ref,
        ]
    )


def resolve_external_disposition_status(
    event: SocExternalDispositionEvent,
    config: SocExternalDispositionMappingConfig | Mapping[str, Any] | None = None,
) -> SocExternalDispositionStatusMapping:
    """Resolve an external status through configurable canonical mappings."""

    mapping_config = SocExternalDispositionMappingConfig.model_validate(config or {})
    event_system = _normalize_key(event.external_system)
    event_status = _normalize_key(event.external_status)
    for item in mapping_config.status_mappings:
        system_matches = item.external_system is None or _normalize_key(item.external_system) == event_system
        if system_matches and _normalize_key(item.external_status) == event_status:
            return item
    return SocExternalDispositionStatusMapping(
        external_system=event.external_system,
        external_status=event.external_status,
        canonical_status=mapping_config.default_canonical_status,
        trust_level="low",
        apply_to_review=False,
        notes="status did not match any configured mapping",
    )


def _value_at_path(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if isinstance(value, Mapping):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            value = value[index] if 0 <= index < len(value) else None
        else:
            return None
        if value is None:
            return None
    return value


def _normalize_field_value(field_name: str, value: Any) -> Any:
    if field_name == "external_tags":
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]
    if field_name == "operator":
        if isinstance(value, Mapping):
            return dict(value)
        return {"name": str(value)}
    if field_name == "updated_at" and isinstance(value, datetime):
        return value
    return value


def _normalize_key(value: str) -> str:
    return " ".join(value.strip().lower().split())
