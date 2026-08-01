"""Versioned read-only contracts for SOC operational visibility."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeInt = Annotated[int, Field(ge=0)]


class SocOperationsAvailability(StrEnum):
    """Whether a component's observation is available in this snapshot."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    NOT_MEASURED = "not_measured"


class SocPersistedOperationsMetrics(BaseModel):
    """Exact lifetime aggregates from SOC-owned persistence tables."""

    model_config = ConfigDict(extra="forbid")

    measurement_scope: Literal["lifetime"] = "lifetime"
    analysis_run_count: NonNegativeInt = 0
    analysis_run_status_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    latest_analysis_started_at: datetime | None = None
    latest_analysis_completed_at: datetime | None = None
    open_review_count: NonNegativeInt = 0
    oldest_open_review_created_at: datetime | None = None
    pending_approval_request_count: NonNegativeInt = 0
    oldest_pending_approval_created_at: datetime | None = None
    open_normalization_issue_count: NonNegativeInt = 0
    critical_open_normalization_issue_count: NonNegativeInt = 0
    active_normalization_baseline_count: NonNegativeInt = 0
    pending_memory_candidate_count: NonNegativeInt = 0


class SocOperationsPersistedSnapshot(BaseModel):
    """Availability and exact metrics for the SOC business store."""

    model_config = ConfigDict(extra="forbid")

    availability: SocOperationsAvailability
    backend: str | None = None
    metrics: SocPersistedOperationsMetrics | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_metrics_availability(self) -> SocOperationsPersistedSnapshot:
        if self.availability is SocOperationsAvailability.AVAILABLE and self.metrics is None:
            raise ValueError("available persisted snapshot requires metrics")
        if self.availability is not SocOperationsAvailability.AVAILABLE and self.metrics is not None:
            raise ValueError("unavailable persisted snapshot cannot include partial metrics")
        return self


class SocOperationsKafkaSnapshot(BaseModel):
    """Secret-free Kafka configuration and optional connectivity observation."""

    model_config = ConfigDict(extra="forbid")

    availability: SocOperationsAvailability
    enabled: bool
    settings_valid: bool
    checked: bool = False
    reachable: bool | None = None
    bootstrap_server_count: NonNegativeInt = 0
    alert_topic_count: NonNegativeInt = 0
    approval_request_topic_count: NonNegativeInt = 0
    dead_letter_configured: bool = False
    consumer_lag_availability: Literal[SocOperationsAvailability.NOT_MEASURED] = SocOperationsAvailability.NOT_MEASURED
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_connectivity_observation(self) -> SocOperationsKafkaSnapshot:
        if not self.checked and self.reachable is not None:
            raise ValueError("unchecked Kafka snapshot cannot report reachability")
        if self.availability is SocOperationsAvailability.AVAILABLE:
            if not self.enabled or not self.settings_valid or not self.checked or self.reachable is not True:
                raise ValueError("available Kafka snapshot requires a successful explicit check")
        return self


class SocOperationsMeasurementGap(BaseModel):
    """A named operational signal that PI-04-A intentionally does not collect."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1)
    availability: Literal[SocOperationsAvailability.NOT_MEASURED] = SocOperationsAvailability.NOT_MEASURED
    reason: str = Field(min_length=1)


class SocOperationsSnapshot(BaseModel):
    """Unified read-only snapshot without an inferred overall health verdict."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.operations_snapshot.v1"] = "soc.operations_snapshot.v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    persisted: SocOperationsPersistedSnapshot
    kafka: SocOperationsKafkaSnapshot
    measurement_gaps: list[SocOperationsMeasurementGap] = Field(default_factory=list)
    production_slo_evidence_available: Literal[False] = False


__all__ = [
    "SocOperationsAvailability",
    "SocOperationsKafkaSnapshot",
    "SocOperationsMeasurementGap",
    "SocOperationsPersistedSnapshot",
    "SocOperationsSnapshot",
    "SocPersistedOperationsMetrics",
]
