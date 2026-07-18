"""Versioned contracts for external SOC ingestion boundaries."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import SocExternalDispositionEvent

SOC_ALERT_INGRESS_METADATA_KEY = "_soc_ingress"
MAX_SOC_ALERT_RAW_BYTES = 900_000
MAX_SOC_ALERT_ENTITIES_HINT_BYTES = 64_000


class SocAlertRawEnvelope(BaseModel):
    """Strict Kafka envelope around one source-specific raw alert."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.alert.raw.v1"] = "soc.alert.raw.v1"
    source: str = Field(min_length=1, max_length=128)
    alert_id: str = Field(min_length=1, max_length=128)
    dedup_key: str = Field(min_length=1, max_length=512)
    occurred_at: datetime
    severity: str = Field(min_length=1, max_length=32)
    raw: dict[str, Any]
    entities_hint: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_event_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_version: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_bounded_json_payloads(self) -> SocAlertRawEnvelope:
        if SOC_ALERT_INGRESS_METADATA_KEY in self.raw:
            raise ValueError(f"raw payload cannot contain reserved key {SOC_ALERT_INGRESS_METADATA_KEY}")
        _validate_json_size(self.raw, limit=MAX_SOC_ALERT_RAW_BYTES, label="raw")
        _validate_json_size(
            self.entities_hint,
            limit=MAX_SOC_ALERT_ENTITIES_HINT_BYTES,
            label="entities_hint",
        )
        return self

    def to_analysis_payload(self) -> dict[str, Any]:
        """Preserve source payload and add only generic transport fallbacks."""

        payload = dict(self.raw)
        payload.setdefault("alert_id", self.alert_id)
        payload.setdefault("tenant_id", self.tenant_id)
        payload.setdefault("source_type", self.source)
        payload.setdefault("source_system", self.source)
        payload.setdefault("event_time", self.occurred_at.isoformat())
        payload.setdefault("severity", self.severity)
        payload.setdefault("detection_key", self.dedup_key)
        payload[SOC_ALERT_INGRESS_METADATA_KEY] = {
            "schema_version": self.schema_version,
            "source": self.source,
            "alert_id": self.alert_id,
            "dedup_key": self.dedup_key,
            "occurred_at": self.occurred_at.isoformat(),
            "severity": self.severity,
            "tenant_id": self.tenant_id,
            "source_event_id": self.source_event_id,
            "source_version": self.source_version,
            "entities_hint": self.entities_hint,
        }
        return payload


class SocExternalDispositionIngressCommand(BaseModel):
    """Authenticated application command for one canonical feedback event."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.external_disposition_ingress.v1"] = "soc.external_disposition_ingress.v1"
    event: SocExternalDispositionEvent

    @model_validator(mode="after")
    def require_stable_source_event_identity(self) -> SocExternalDispositionIngressCommand:
        if not self.event.source_event_id:
            raise ValueError("external disposition ingress requires event.source_event_id")
        return self


def _validate_json_size(value: dict[str, Any], *, limit: int, label: str) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain only JSON-serializable values") from exc
    if len(encoded) > limit:
        raise ValueError(f"{label} exceeds {limit} UTF-8 JSON bytes")


__all__ = [
    "MAX_SOC_ALERT_ENTITIES_HINT_BYTES",
    "MAX_SOC_ALERT_RAW_BYTES",
    "SOC_ALERT_INGRESS_METADATA_KEY",
    "SocAlertRawEnvelope",
    "SocExternalDispositionIngressCommand",
]
