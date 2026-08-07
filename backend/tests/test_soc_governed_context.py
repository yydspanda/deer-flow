from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from soc_agent.cli import main
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AuthorizedActivityBehaviorKind,
    AuthorizedActivityBehaviorSelector,
    AuthorizedActivityPayload,
    AuthorizedActivityRecurringWindow,
    AuthorizedActivitySubjectKind,
    AuthorizedActivitySubjectSelector,
    AuthorizedActivityTargetKind,
    AuthorizedActivityTargetSelector,
    AuthorizedActivityType,
    EntrySurface,
    GovernedContextFactCreateCommand,
    GovernedContextFactQuery,
    GovernedContextFactRevisionCommand,
    GovernedContextFactStatus,
    GovernedContextFactTransitionCommand,
    GovernedContextSource,
    GovernedContextSourceType,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
)
from soc_agent.core import SocGovernedContextService, SocServiceError, SocServiceNotFoundError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables, upgrade_soc_schema
from soc_agent.governed_context import InMemoryGovernedContextFactRepository

NOW = datetime(2026, 7, 16, 4, 0, tzinfo=UTC)


class CapturingEventSink:
    def __init__(self) -> None:
        self.events: list[SocEvent] = []

    def emit(self, event: SocEvent) -> None:
        self.events.append(event)


def _actor(*roles: str, actor_id: str = "soc-user") -> ActorContext:
    return ActorContext(
        actor_id=actor_id,
        actor_type=ActorType.USER,
        surface=EntrySurface.TEST,
        roles=list(roles),
    )


def _context(*roles: str, actor_id: str = "soc-user") -> ServiceRequestContext:
    return ServiceRequestContext(actor=_actor(*roles, actor_id=actor_id))


def _payload(*, behavior: str = "java->chattr") -> AuthorizedActivityPayload:
    return AuthorizedActivityPayload(
        activity_type=AuthorizedActivityType.AUTOMATION,
        subject_scope=[
            AuthorizedActivitySubjectSelector(
                kind=AuthorizedActivitySubjectKind.SERVICE_ID,
                value="service:internal-maintenance",
            )
        ],
        target_scope=[
            AuthorizedActivityTargetSelector(
                kind=AuthorizedActivityTargetKind.ASSET_ID,
                value="asset:work04",
            )
        ],
        behavior_scope=[
            AuthorizedActivityBehaviorSelector(
                kind=AuthorizedActivityBehaviorKind.PROCESS,
                value=behavior,
            )
        ],
        recurring_windows=[
            AuthorizedActivityRecurringWindow(
                timezone="Asia/Shanghai",
                days_of_week=[0, 1, 2, 3, 4, 5, 6],
                start_time=time(0, 0),
                end_time=time(0, 30),
            )
        ],
    )


def _source(
    *,
    source_type: GovernedContextSourceType = GovernedContextSourceType.ANALYST_CONFIRMATION,
) -> GovernedContextSource:
    return GovernedContextSource(
        source_type=source_type,
        source_ref="change:CHG-2026-0716",
        observed_at=NOW,
    )


def _create_command(
    *,
    payload: AuthorizedActivityPayload | None = None,
    source: GovernedContextSource | None = None,
    evidence_refs: list[str] | None = None,
    valid_until: datetime | None = None,
) -> GovernedContextFactCreateCommand:
    return GovernedContextFactCreateCommand(
        tenant_id="tenant-a",
        environment="production",
        valid_from=NOW - timedelta(days=1),
        valid_until=valid_until or NOW + timedelta(days=30),
        source=source or _source(),
        reason="Known scheduled internal automation; proposal still requires governance review.",
        evidence_refs=evidence_refs if evidence_refs is not None else ["ticket:CHG-2026-0716"],
        payload=payload or _payload(),
    )


def _transition(fact_id: str, version: int, *, reason: str = "Governance review completed.") -> GovernedContextFactTransitionCommand:
    return GovernedContextFactTransitionCommand(
        fact_id=fact_id,
        expected_latest_version=version,
        reason=reason,
    )


def _service(repository, *, now: datetime = NOW, event_sink=None) -> SocGovernedContextService:
    return SocGovernedContextService(
        repository=repository,
        event_sink=event_sink,
        now_provider=lambda: now,
    )


def _sqlalchemy_repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def test_governed_context_contract_requires_timezone_aware_validity() -> None:
    with pytest.raises(ValidationError, match="valid_from must be timezone-aware"):
        GovernedContextFactCreateCommand(
            tenant_id="tenant-a",
            environment="production",
            valid_from=datetime(2026, 7, 16),
            valid_until=datetime(2026, 7, 17),
            source=_source(),
            reason="Invalid naive timestamps.",
            payload=_payload(),
        )


def test_governed_context_contract_rejects_unknown_fields() -> None:
    payload = _create_command().model_dump(mode="python")
    payload["vendor_rule_code"] = "must-not-be-silently-accepted"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GovernedContextFactCreateCommand.model_validate(payload)


def test_authorized_activity_contract_validates_network_and_schedule_values() -> None:
    with pytest.raises(ValidationError, match="does not appear to be an IPv4 or IPv6 network"):
        AuthorizedActivityTargetSelector(
            kind=AuthorizedActivityTargetKind.CIDR,
            value="not-a-cidr",
        )
    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        AuthorizedActivityRecurringWindow(
            timezone="Invalid/Timezone",
            days_of_week=[1],
            start_time=time(0, 0),
            end_time=time(0, 30),
        )
    with pytest.raises(ValidationError, match="times must be local naive times"):
        AuthorizedActivityRecurringWindow(
            timezone="Asia/Shanghai",
            days_of_week=[1],
            start_time=time(0, 0, tzinfo=UTC),
            end_time=time(0, 30),
        )


def test_authoritative_source_requires_version_freshness_and_authority() -> None:
    with pytest.raises(ValidationError, match="authoritative=true"):
        GovernedContextSource(
            source_type=GovernedContextSourceType.AUTHORITATIVE_SYSTEM,
            source_ref="scanner:task-1",
            observed_at=NOW,
        )

    source = GovernedContextSource(
        source_type=GovernedContextSourceType.AUTHORITATIVE_SYSTEM,
        source_ref="scanner:task-1",
        source_version="17",
        observed_at=NOW,
        fresh_until=NOW + timedelta(hours=1),
        authoritative=True,
    )
    assert source.authoritative is True


def test_governed_context_service_appends_lifecycle_versions_and_events() -> None:
    repository = InMemoryGovernedContextFactRepository()
    events = CapturingEventSink()
    service = _service(repository, event_sink=events)

    proposed = service.propose(_create_command(), context=_context("soc_analyst"))
    active = service.activate(
        _transition(proposed.fact_id, proposed.version),
        context=_context("soc_context_approver", actor_id="context-approver"),
    )
    suspended = service.suspend(
        _transition(active.fact_id, active.version, reason="Temporarily pause this context."),
        context=_context("soc_context_approver", actor_id="context-approver"),
    )
    resumed = service.activate(
        _transition(suspended.fact_id, suspended.version, reason="Resume after source verification."),
        context=_context("soc_admin", actor_id="soc-admin"),
    )

    assert proposed.status is GovernedContextFactStatus.PROPOSED
    assert active.status is GovernedContextFactStatus.ACTIVE
    assert suspended.status is GovernedContextFactStatus.SUSPENDED
    assert resumed.status is GovernedContextFactStatus.ACTIVE
    assert resumed.version == 4
    assert resumed.content_hash == proposed.content_hash
    assert resumed.reviewed_by is not None
    assert resumed.reviewed_by.actor_id == "soc-admin"
    assert [item.version for item in service.list_versions(proposed.fact_id)] == [4, 3, 2, 1]
    assert service.get(proposed.fact_id, version=1).is_latest is False
    assert service.get(proposed.fact_id).is_latest is True
    assert [event.event_type for event in events.events] == [
        SocEventType.GOVERNED_CONTEXT_FACT_PROPOSED,
        SocEventType.GOVERNED_CONTEXT_FACT_ACTIVATED,
        SocEventType.GOVERNED_CONTEXT_FACT_SUSPENDED,
        SocEventType.GOVERNED_CONTEXT_FACT_ACTIVATED,
    ]


def test_governed_context_service_is_role_gated_and_fail_closed() -> None:
    service = _service(InMemoryGovernedContextFactRepository())

    with pytest.raises(SocServiceError, match="proposing governed context facts requires"):
        service.propose(_create_command(), context=_context())

    proposed = service.propose(
        _create_command(evidence_refs=[]),
        context=_context("soc_analyst"),
    )
    with pytest.raises(SocServiceError, match="requires one of roles"):
        service.activate(_transition(proposed.fact_id, 1), context=_context("soc_analyst"))
    with pytest.raises(SocServiceError, match="at least one evidence reference"):
        service.activate(
            _transition(proposed.fact_id, 1),
            context=_context("soc_context_approver"),
        )


def test_governed_context_service_rejects_stale_writer_and_invalid_transition() -> None:
    service = _service(InMemoryGovernedContextFactRepository())
    proposed = service.propose(_create_command(), context=_context("soc_analyst"))
    active = service.activate(
        _transition(proposed.fact_id, 1),
        context=_context("soc_context_approver"),
    )

    with pytest.raises(SocServiceError, match="expected latest version 1, found 2"):
        service.revoke(
            _transition(active.fact_id, 1),
            context=_context("soc_context_approver"),
        )
    with pytest.raises(SocServiceError, match="cannot activate governed fact in status active"):
        service.activate(
            _transition(active.fact_id, 2),
            context=_context("soc_context_approver"),
        )


def test_governed_context_revision_requires_reapproval_and_preserves_identity() -> None:
    service = _service(InMemoryGovernedContextFactRepository())
    proposed = service.propose(_create_command(), context=_context("soc_analyst"))
    active = service.activate(
        _transition(proposed.fact_id, 1),
        context=_context("soc_context_approver"),
    )
    revision = service.revise(
        GovernedContextFactRevisionCommand(
            fact_id=active.fact_id,
            expected_latest_version=active.version,
            tenant_id=active.tenant_id,
            environment=active.environment,
            valid_from=active.valid_from,
            valid_until=active.valid_until + timedelta(days=30),
            source=active.source,
            reason="Extend validity and narrow the process signature.",
            evidence_refs=active.evidence_refs,
            payload=_payload(behavior="java(3065)->chattr"),
        ),
        context=_context("soc_engineer"),
    )

    assert revision.fact_id == active.fact_id
    assert revision.version == 3
    assert revision.status is GovernedContextFactStatus.PROPOSED
    assert revision.reviewed_by is None
    assert revision.content_hash != active.content_hash


def test_governed_context_expire_requires_end_of_validity() -> None:
    repository = InMemoryGovernedContextFactRepository()
    service = _service(repository)
    proposed = service.propose(
        _create_command(valid_until=NOW + timedelta(hours=1)),
        context=_context("soc_analyst"),
    )
    with pytest.raises(SocServiceError, match="not due to expire"):
        service.expire(
            _transition(proposed.fact_id, 1),
            context=_context("soc_context_service"),
        )

    due_service = _service(repository, now=NOW + timedelta(hours=2))
    expired = due_service.expire(
        _transition(proposed.fact_id, 1),
        context=_context("soc_context_service", actor_id="context-expirer"),
    )
    assert expired.status is GovernedContextFactStatus.EXPIRED


@pytest.mark.parametrize(
    "repository_factory",
    [InMemoryGovernedContextFactRepository, _sqlalchemy_repository],
)
def test_governed_context_repository_query_and_version_history(repository_factory) -> None:
    repository = repository_factory()
    service = _service(repository)
    proposed = service.propose(_create_command(), context=_context("soc_analyst"))
    active = service.activate(
        _transition(proposed.fact_id, 1),
        context=_context("soc_context_approver"),
    )

    results = service.list(
        GovernedContextFactQuery(
            tenant_id="tenant-a",
            environment="production",
            status=GovernedContextFactStatus.ACTIVE,
            valid_at=NOW,
        )
    )
    all_versions = service.list(GovernedContextFactQuery(fact_id=active.fact_id, latest_only=False))

    assert [item.fact_version_id for item in results] == [active.fact_version_id]
    assert {item.version for item in all_versions} == {1, 2}
    assert service.get(active.fact_id, version=1).is_latest is False
    with pytest.raises(SocServiceNotFoundError):
        service.get("GCF-MISSING")


def test_soc_migration_head_creates_governance_and_approval_lifecycle_schema(tmp_path) -> None:
    database_path = tmp_path / "soc-migrations.db"
    database_url = f"sqlite:///{database_path}"

    upgrade_soc_schema(database_url)

    engine = create_engine(database_url)
    try:
        assert "soc_governed_context_facts" in inspect(engine).get_table_names()
        assert "soc_authorization_enrichments" in inspect(engine).get_table_names()
        assert "soc_disposition_proposals" in inspect(engine).get_table_names()
        assert "soc_disposition_sample_manifests" in inspect(engine).get_table_names()
        assert "soc_disposition_outcomes" in inspect(engine).get_table_names()
        assert "soc_enrichment_executions" in inspect(engine).get_table_names()
        assert "soc_enrichment_action_attempts" in inspect(engine).get_table_names()
        assert "soc_skill_feedback_observations" in inspect(engine).get_table_names()
        assert "soc_skill_improvement_candidates" in inspect(engine).get_table_names()
        assert "soc_memory_pattern_observations" in inspect(engine).get_table_names()
        approval_request_columns = {column["name"] for column in inspect(engine).get_columns("soc_approval_requests")}
        assert {
            "resolved_at",
            "resolved_by_actor_id",
            "resolution_reason",
            "resolution_idempotency_key",
            "approval_grant_id",
        }.issubset(approval_request_columns)
        approval_grant_constraints = {constraint["name"] for constraint in inspect(engine).get_unique_constraints("soc_approval_grants")}
        assert "uq_soc_approval_grants_request" in approval_grant_constraints
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM soc_alembic_version")).scalar_one()
        assert revision == "0021_memory_pattern_observations"
    finally:
        engine.dispose()


def test_governed_context_cli_propose_activate_and_show_history(tmp_path, capsys) -> None:
    database_path = tmp_path / "soc-context-cli.db"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    engine.dispose()
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(_create_command().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "context",
                "propose",
                str(proposal_path),
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    proposed = json.loads(capsys.readouterr().out)

    assert (
        main(
            [
                "context",
                "activate",
                proposed["fact_id"],
                "--expected-version",
                "1",
                "--reason",
                "Approved for shadow-only governed context use.",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    active = json.loads(capsys.readouterr().out)
    assert active["status"] == "active"
    assert active["version"] == 2

    assert (
        main(
            [
                "context",
                "get",
                proposed["fact_id"],
                "--history",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    history = json.loads(capsys.readouterr().out)
    assert [item["version"] for item in history] == [2, 1]
    assert [item["is_latest"] for item in history] == [True, False]
