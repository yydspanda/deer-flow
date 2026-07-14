from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.gateway.routers import soc_normalization
from soc_agent.contracts import (
    NormalizationBaselineAcceptCommand,
    NormalizationMaintenanceIssue,
    NormalizationMaintenanceIssueStatus,
    NormalizationMaintenanceIssueType,
    NormalizationMaintenanceSeverity,
)
from soc_agent.core import SocNormalizationMaintenanceService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables


class _FakeRequest:
    def __init__(self, *, admin: bool = False) -> None:
        self.headers = {"x-soc-surface": "web"}
        self.state = SimpleNamespace(
            user=SimpleNamespace(
                id="normalization-admin" if admin else "normalization-analyst",
                system_role="admin" if admin else "user",
            )
        )


def _service() -> tuple[SocNormalizationMaintenanceService, SqlAlchemyAlertRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    return (
        SocNormalizationMaintenanceService(
            baseline_repository=repository,
            issue_repository=repository,
        ),
        repository,
    )


def test_normalization_api_accepts_baseline_and_reports_metrics() -> None:
    service, _ = _service()
    baseline = soc_normalization.accept_normalization_baseline(
        NormalizationBaselineAcceptCommand(
            source_system="pingan-zeus",
            adapter="pingan_platform",
            parser_name="pingan_delimited_json",
            parser_version="v2",
            accepted_fingerprints=["sha256:fixture"],
            reason="Reviewed fixture",
        ),
        request=_FakeRequest(admin=True),
        service=service,
    )

    listed = soc_normalization.list_normalization_baselines(
        service=service,
        status=baseline.status,
        tenant_id=None,
        source_system=None,
        limit=50,
    )
    metrics = soc_normalization.get_normalization_operations_metrics(service=service)

    assert listed.items == [baseline]
    assert metrics.active_baseline_count == 1
    assert metrics.open_issue_count == 0


def test_normalization_api_lists_and_updates_issue() -> None:
    service, repository = _service()
    issue = NormalizationMaintenanceIssue(
        dedupe_key="normalization:router-test",
        issue_type=NormalizationMaintenanceIssueType.NOVEL_SCHEMA,
        severity=NormalizationMaintenanceSeverity.WARNING,
        source_system="pingan-zeus",
        adapter="pingan_platform",
    )
    repository.save_normalization_issue(issue)

    listed = soc_normalization.list_normalization_issues(
        service=service,
        status=NormalizationMaintenanceIssueStatus.OPEN,
        tenant_id=None,
        source_system=None,
        limit=50,
    )
    updated = soc_normalization.update_normalization_issue(
        issue.issue_id,
        soc_normalization.NormalizationIssueUpdateRequest(
            status="acknowledged",
            reason="Parser owner assigned",
        ),
        request=_FakeRequest(admin=True),
        service=service,
    )
    metrics = soc_normalization.get_normalization_operations_metrics(service=service)

    assert listed.items == [issue]
    assert updated.status is NormalizationMaintenanceIssueStatus.ACKNOWLEDGED
    assert metrics.open_issue_count == 0


def test_normalization_router_exposes_operations_paths() -> None:
    paths = {route.path for route in soc_normalization.router.routes}

    assert "/api/soc/normalization/baselines" in paths
    assert "/api/soc/normalization/issues" in paths
    assert "/api/soc/normalization/issues/{issue_id}" in paths
    assert "/api/soc/normalization/metrics" in paths
