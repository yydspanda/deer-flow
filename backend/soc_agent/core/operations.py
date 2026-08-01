"""Read-only service composing SOC persistence and infrastructure observations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from soc_agent.contracts import (
    SocOperationsAvailability,
    SocOperationsKafkaSnapshot,
    SocOperationsMeasurementGap,
    SocOperationsPersistedSnapshot,
    SocOperationsSnapshot,
)
from soc_agent.protocols import (
    SocOperationsKafkaProbe,
    SocOperationsRepository,
    SocOperationsRepositoryError,
)

_PI_04_A_MEASUREMENT_GAPS = (
    SocOperationsMeasurementGap(
        metric="kafka.consumer_lag",
        reason="PI-04-A does not collect consumer-group offsets or broker lag.",
    ),
    SocOperationsMeasurementGap(
        metric="model.compute_utilization",
        reason="PI-04-A does not collect provider-side compute, GPU, or capacity telemetry.",
    ),
    SocOperationsMeasurementGap(
        metric="production.slo_compliance",
        reason="Production SLO thresholds and time-window evidence are not yet approved.",
    ),
)


class SocOperationsService:
    """Build one bounded snapshot without inferring an overall health verdict."""

    def __init__(
        self,
        *,
        repository: SocOperationsRepository | None,
        kafka_probe: SocOperationsKafkaProbe | None,
        database_backend: str | None = None,
        database_error_code: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._kafka_probe = kafka_probe
        self._database_backend = database_backend
        self._database_error_code = database_error_code
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_snapshot(self, *, check_broker: bool = False) -> SocOperationsSnapshot:
        persisted = self._read_persisted_snapshot()
        kafka = self._read_kafka_snapshot(check_broker=check_broker)
        return SocOperationsSnapshot(
            generated_at=self._clock(),
            persisted=persisted,
            kafka=kafka,
            measurement_gaps=list(_PI_04_A_MEASUREMENT_GAPS),
        )

    def _read_persisted_snapshot(self) -> SocOperationsPersistedSnapshot:
        if self._repository is None:
            return SocOperationsPersistedSnapshot(
                availability=SocOperationsAvailability.NOT_CONFIGURED,
                backend=self._database_backend,
                error_code=self._database_error_code or "soc.database.not_configured",
            )
        try:
            metrics = self._repository.read_persisted_metrics()
        except SocOperationsRepositoryError:
            return SocOperationsPersistedSnapshot(
                availability=SocOperationsAvailability.UNAVAILABLE,
                backend=self._database_backend,
                error_code="soc.database.query_failed",
            )
        return SocOperationsPersistedSnapshot(
            availability=SocOperationsAvailability.AVAILABLE,
            backend=self._database_backend,
            metrics=metrics,
        )

    def _read_kafka_snapshot(self, *, check_broker: bool) -> SocOperationsKafkaSnapshot:
        if self._kafka_probe is None:
            return SocOperationsKafkaSnapshot(
                availability=SocOperationsAvailability.NOT_CONFIGURED,
                enabled=False,
                settings_valid=False,
                error_code="soc.kafka.probe_not_configured",
            )
        return self._kafka_probe.snapshot(check_connectivity=check_broker)


__all__ = ["SocOperationsService"]
