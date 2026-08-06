from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.cli import main
from soc_agent.contracts import (
    SocOperationsAvailability,
    SocPersistedOperationsMetrics,
)
from soc_agent.core import SocOperationsService
from soc_agent.daemon import KafkaConsumerSettings
from soc_agent.db import SqlAlchemySocOperationsRepository, create_soc_tables
from soc_agent.db.models import (
    SocAnalysisRunRow,
    SocApprovalRequestRow,
    SocMemoryCandidateRow,
    SocNormalizationMaintenanceIssueRow,
    SocNormalizationSchemaBaselineRow,
    SocReviewQueueRow,
)
from soc_agent.operations import KafkaOperationsProbe
from soc_agent.protocols import SocOperationsRepositoryError


def _seed_operations_rows(session_factory) -> tuple[datetime, datetime]:
    started_at = datetime(2026, 8, 2, 8, 0)
    latest_started_at = started_at + timedelta(minutes=15)
    completed_at = latest_started_at + timedelta(minutes=2)
    with session_factory() as session:
        session.add_all(
            [
                SocAnalysisRunRow(
                    run_id="RUN-OPS-1",
                    alert_id="ALT-OPS-1",
                    status="success",
                    pipeline_version="test",
                    model_name="stub",
                    prompt_version="test",
                    started_at=started_at,
                    ended_at=started_at + timedelta(minutes=1),
                    run_payload={},
                    created_at=started_at,
                    updated_at=started_at,
                ),
                SocAnalysisRunRow(
                    run_id="RUN-OPS-2",
                    alert_id="ALT-OPS-2",
                    status="failed",
                    pipeline_version="test",
                    model_name="stub",
                    prompt_version="test",
                    started_at=latest_started_at,
                    ended_at=completed_at,
                    run_payload={},
                    created_at=latest_started_at,
                    updated_at=completed_at,
                ),
                _review_row("REV-OPS-OPEN", "open", started_at),
                _review_row("REV-OPS-CLOSED", "closed", latest_started_at),
                _approval_row("APR-OPS-PENDING", "pending", started_at),
                _approval_row("APR-OPS-APPROVED", "approved", latest_started_at),
                _normalization_issue_row("NMI-OPS-WARNING", "warning", "open", started_at),
                _normalization_issue_row("NMI-OPS-CRITICAL", "critical", "open", latest_started_at),
                _normalization_issue_row("NMI-OPS-RESOLVED", "critical", "resolved", latest_started_at),
                _normalization_baseline_row("NSB-OPS-ACTIVE", "active", started_at),
                _normalization_baseline_row("NSB-OPS-OLD", "superseded", started_at),
                _memory_candidate_row("MEMC-OPS-PENDING", "pending_review", started_at),
                _memory_candidate_row("MEMC-OPS-CONFIRMED", "confirmed", latest_started_at),
            ]
        )
        session.commit()
    return latest_started_at, completed_at


def _review_row(queue_id: str, status: str, created_at: datetime) -> SocReviewQueueRow:
    return SocReviewQueueRow(
        queue_id=queue_id,
        run_id=f"RUN-{queue_id}",
        alert_id=f"ALT-{queue_id}",
        status=status,
        priority="medium",
        reason="test",
        source_type="apt",
        entity_keys=[],
        created_at=created_at,
        updated_at=created_at,
        item_payload={},
    )


def _approval_row(request_id: str, status: str, created_at: datetime) -> SocApprovalRequestRow:
    return SocApprovalRequestRow(
        approval_request_id=request_id,
        permission_decision_id=f"PERM-{request_id}",
        route="endpoint.isolate",
        action="endpoint.isolate",
        risk_level="high_risk",
        status=status,
        requested_by_actor_id="analyst-1",
        reason="test",
        created_at=created_at,
        request_payload={},
    )


def _normalization_issue_row(
    issue_id: str,
    severity: str,
    status: str,
    created_at: datetime,
) -> SocNormalizationMaintenanceIssueRow:
    return SocNormalizationMaintenanceIssueRow(
        issue_id=issue_id,
        dedupe_key=f"dedupe:{issue_id}",
        issue_type="novel_schema",
        severity=severity,
        status=status,
        adapter="test",
        occurrence_count=1,
        first_seen_at=created_at,
        last_seen_at=created_at,
        issue_payload={},
    )


def _normalization_baseline_row(
    baseline_id: str,
    status: str,
    created_at: datetime,
) -> SocNormalizationSchemaBaselineRow:
    return SocNormalizationSchemaBaselineRow(
        baseline_id=baseline_id,
        version=1,
        status=status,
        adapter="test",
        parser_name="test",
        parser_version="v1",
        accepted_fingerprints=["sha256:test"],
        approved_by_actor_id="engineer-1",
        reason="test",
        created_at=created_at,
        updated_at=created_at,
        baseline_payload={},
    )


def _memory_candidate_row(
    candidate_id: str,
    status: str,
    created_at: datetime,
) -> SocMemoryCandidateRow:
    return SocMemoryCandidateRow(
        candidate_id=candidate_id,
        candidate_type="benign_pattern",
        target_artifact="tenant_memory",
        status=status,
        tenant_scope="test",
        source_type="manual_note",
        confidence=0.6,
        decision_impact="review_hint",
        runtime_decision_allowed=False,
        review_required=True,
        summary="test",
        content="test",
        created_at=created_at,
        updated_at=created_at,
        candidate_payload={},
    )


def test_sqlalchemy_operations_repository_reads_exact_unpaginated_aggregates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    latest_started_at, completed_at = _seed_operations_rows(session_factory)

    metrics = SqlAlchemySocOperationsRepository(session_factory).read_persisted_metrics()

    assert metrics.analysis_run_count == 2
    assert metrics.analysis_run_status_counts == {"failed": 1, "success": 1}
    assert metrics.latest_analysis_started_at == latest_started_at
    assert metrics.latest_analysis_completed_at == completed_at
    assert metrics.open_review_count == 1
    assert metrics.pending_approval_request_count == 1
    assert metrics.open_normalization_issue_count == 2
    assert metrics.critical_open_normalization_issue_count == 1
    assert metrics.active_normalization_baseline_count == 1
    assert metrics.pending_memory_candidate_count == 1


def test_sqlalchemy_operations_repository_sanitizes_query_failure() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    repository = SqlAlchemySocOperationsRepository(sessionmaker(bind=engine, expire_on_commit=False))

    try:
        repository.read_persisted_metrics()
    except SocOperationsRepositoryError as exc:
        assert str(exc) == "SOC operations database query failed"
        assert "no such table" not in str(exc)
    else:
        raise AssertionError("missing SOC tables must fail the aggregate query")


def test_kafka_operations_probe_distinguishes_disabled_unchecked_and_checked() -> None:
    disabled = KafkaOperationsProbe(KafkaConsumerSettings()).snapshot()
    unchecked = KafkaOperationsProbe(KafkaConsumerSettings(enabled=True)).snapshot()
    reachable = KafkaOperationsProbe(
        KafkaConsumerSettings(enabled=True),
        broker_checker=lambda _settings: None,
    ).snapshot(check_connectivity=True)

    assert disabled.availability is SocOperationsAvailability.NOT_CONFIGURED
    assert disabled.settings_valid is True
    assert unchecked.availability is SocOperationsAvailability.NOT_MEASURED
    assert unchecked.checked is False
    assert unchecked.reachable is None
    assert reachable.availability is SocOperationsAvailability.AVAILABLE
    assert reachable.checked is True
    assert reachable.reachable is True
    assert reachable.consumer_lag_availability is SocOperationsAvailability.NOT_MEASURED


def test_kafka_operations_probe_hides_configuration_and_raw_failure_details() -> None:
    def fail(_settings: KafkaConsumerSettings) -> None:
        raise RuntimeError("broker.internal:9092 secret diagnostic")

    settings = KafkaConsumerSettings(
        enabled=True,
        bootstrap_servers=["broker.internal:9092"],
        sasl_username="secret-user",
    )
    snapshot = KafkaOperationsProbe(settings, broker_checker=fail).snapshot(check_connectivity=True)
    payload = snapshot.model_dump_json()

    assert snapshot.availability is SocOperationsAvailability.UNAVAILABLE
    assert snapshot.settings_valid is True
    assert snapshot.error_code == "soc.kafka.broker_unreachable"
    assert snapshot.bootstrap_server_count == 1
    assert "broker.internal" not in payload
    assert "secret-user" not in payload
    assert "secret diagnostic" not in payload


def test_kafka_operations_probe_reports_invalid_environment_without_values(monkeypatch) -> None:
    monkeypatch.setenv("SOC_KAFKA_POLL_TIMEOUT_MS", "not-an-integer")

    snapshot = KafkaOperationsProbe.from_env().snapshot()

    assert snapshot.availability is SocOperationsAvailability.UNAVAILABLE
    assert snapshot.settings_valid is False
    assert snapshot.error_code == "soc.kafka.invalid_configuration"
    assert "not-an-integer" not in snapshot.model_dump_json()


def test_operations_service_keeps_unmeasured_signals_explicit() -> None:
    class Repository:
        def read_persisted_metrics(self) -> SocPersistedOperationsMetrics:
            return SocPersistedOperationsMetrics(analysis_run_count=7)

    service = SocOperationsService(
        repository=Repository(),
        kafka_probe=KafkaOperationsProbe(KafkaConsumerSettings()),
        database_backend="postgresql",
        clock=lambda: datetime(2026, 8, 2, 10, 0),
    )

    snapshot = service.get_snapshot()

    assert snapshot.persisted.availability is SocOperationsAvailability.AVAILABLE
    assert snapshot.persisted.metrics is not None
    assert snapshot.persisted.metrics.analysis_run_count == 7
    assert snapshot.production_slo_evidence_available is False
    assert {gap.metric for gap in snapshot.measurement_gaps} == {
        "kafka.consumer_lag",
        "model.compute_utilization",
        "production.slo_compliance",
    }


def test_cli_ops_snapshot_reads_empty_local_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'soc-ops.db'}"
    output_path = tmp_path / "reports" / "operations.json"
    create_soc_tables(create_engine(database_url))
    monkeypatch.delenv("SOC_KAFKA_ENABLED", raising=False)

    exit_code = main(
        [
            "ops",
            "snapshot",
            "--database-url",
            database_url,
            "--output",
            str(output_path),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["schema_version"] == "soc.operations_snapshot.v1"
    assert payload["persisted"]["availability"] == "available"
    assert payload["persisted"]["backend"] == "sqlite"
    assert payload["persisted"]["metrics"]["analysis_run_count"] == 0
    assert payload["kafka"]["availability"] == "not_configured"
    assert saved == payload
