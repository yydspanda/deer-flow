from __future__ import annotations

from datetime import datetime

from app.gateway.routers import soc_operations
from soc_agent.contracts import SocPersistedOperationsMetrics
from soc_agent.core import SocOperationsService
from soc_agent.daemon import KafkaConsumerSettings
from soc_agent.operations import KafkaOperationsProbe


class _Repository:
    def read_persisted_metrics(self) -> SocPersistedOperationsMetrics:
        return SocPersistedOperationsMetrics(
            analysis_run_count=3,
            open_review_count=2,
        )


def test_operations_router_returns_passive_snapshot() -> None:
    service = SocOperationsService(
        repository=_Repository(),
        kafka_probe=KafkaOperationsProbe(KafkaConsumerSettings(enabled=True)),
        database_backend="postgresql",
        clock=lambda: datetime(2026, 8, 2, 10, 0),
    )

    snapshot = soc_operations.get_operations_snapshot(service)

    assert snapshot.persisted.metrics is not None
    assert snapshot.persisted.metrics.analysis_run_count == 3
    assert snapshot.kafka.availability == "not_measured"
    assert snapshot.kafka.checked is False


def test_operations_router_exposes_snapshot_path() -> None:
    assert {route.path for route in soc_operations.router.routes} == {
        "/api/soc/operations/snapshot",
    }
