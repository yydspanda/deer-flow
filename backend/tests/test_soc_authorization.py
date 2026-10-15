from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.cli import main
from soc_agent.context_bridge import build_lead_agent_review_context_artifact
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertEntitySet,
    AlertEventRef,
    AlertInput,
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationDimension,
    AuthorizationEnrichmentCommand,
    AuthorizationMatchStatus,
    AuthorizationQuery,
    AuthorizationQueryBehavior,
    AuthorizationQueryConflict,
    AuthorizationQuerySubject,
    AuthorizationQueryTarget,
    AuthorizationSourceFreshness,
    AuthorizedActivityBehaviorKind,
    AuthorizedActivityBehaviorSelector,
    AuthorizedActivityPayload,
    AuthorizedActivityRecurringWindow,
    AuthorizedActivitySubjectKind,
    AuthorizedActivitySubjectSelector,
    AuthorizedActivityTargetKind,
    AuthorizedActivityTargetSelector,
    AuthorizedActivityType,
    EntityKind,
    EntityMention,
    EntrySurface,
    ExtractedEntities,
    GovernedContextFactCreateCommand,
    GovernedContextFactTransitionCommand,
    GovernedContextSource,
    GovernedContextSourceType,
    HostEntityRef,
    ProcessEntityRef,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
)
from soc_agent.core import (
    AuthorizationEnrichmentIdempotencyConflictError,
    SocAuthorizationEnrichmentService,
    SocAuthorizedActivityService,
    SocGovernedContextService,
    SocReviewService,
)
from soc_agent.core.authorization_enrichment import authorization_query_hash
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.governed_context import InMemoryGovernedContextFactRepository
from soc_agent.utils.hashing import stable_hash

TENANT = "tenant-a"
ENVIRONMENT = "production"
BASE_TIME = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def _actor(*roles: str, actor_id: str = "soc-test") -> ActorContext:
    return ActorContext(
        actor_id=actor_id,
        actor_type=ActorType.USER,
        surface=EntrySurface.TEST,
        roles=list(roles),
    )


def _context(*roles: str, actor_id: str = "soc-test") -> ServiceRequestContext:
    return ServiceRequestContext(actor=_actor(*roles, actor_id=actor_id))


def _payload(
    *,
    subjects: list[AuthorizedActivitySubjectSelector] | None = None,
    targets: list[AuthorizedActivityTargetSelector] | None = None,
    behaviors: list[AuthorizedActivityBehaviorSelector] | None = None,
    windows: list[AuthorizedActivityRecurringWindow] | None = None,
) -> AuthorizedActivityPayload:
    return AuthorizedActivityPayload(
        activity_type=AuthorizedActivityType.AUTOMATION,
        subject_scope=subjects
        or [
            AuthorizedActivitySubjectSelector(
                kind=AuthorizedActivitySubjectKind.ASSET_ID,
                value="asset-1",
            )
        ],
        target_scope=targets
        or [
            AuthorizedActivityTargetSelector(
                kind=AuthorizedActivityTargetKind.ASSET_ID,
                value="asset-1",
            )
        ],
        behavior_scope=behaviors
        or [
            AuthorizedActivityBehaviorSelector(
                kind=AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
                value="java->chattr",
            )
        ],
        recurring_windows=windows or [],
    )


def _activate(
    repository,
    payload: AuthorizedActivityPayload,
    *,
    lifecycle_time: datetime = BASE_TIME,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    source: GovernedContextSource | None = None,
):
    command = GovernedContextFactCreateCommand(
        tenant_id=TENANT,
        environment=ENVIRONMENT,
        valid_from=valid_from or lifecycle_time - timedelta(days=1),
        valid_until=valid_until or lifecycle_time + timedelta(days=60),
        source=source
        or GovernedContextSource(
            source_type=GovernedContextSourceType.ANALYST_CONFIRMATION,
            source_ref="ticket:CHG-1001",
            observed_at=lifecycle_time - timedelta(minutes=1),
        ),
        reason="Known scoped activity for deterministic shadow matching.",
        evidence_refs=["ticket:CHG-1001"],
        payload=payload,
    )
    proposed = SocGovernedContextService(
        repository=repository,
        now_provider=lambda: lifecycle_time,
    ).propose(command, context=_context("soc_analyst"))
    return SocGovernedContextService(
        repository=repository,
        now_provider=lambda: lifecycle_time + timedelta(seconds=1),
    ).activate(
        GovernedContextFactTransitionCommand(
            fact_id=proposed.fact_id,
            expected_latest_version=proposed.version,
            reason="Approved for shadow-only authorization matching.",
        ),
        context=_context("soc_context_approver", actor_id="context-approver"),
    )


def _query(
    *,
    event_time: datetime,
    subject_kind: AuthorizedActivitySubjectKind = AuthorizedActivitySubjectKind.ASSET_ID,
    subject_value: str = "asset-1",
    target_kind: AuthorizedActivityTargetKind = AuthorizedActivityTargetKind.ASSET_ID,
    target_value: str = "asset-1",
    behavior_kind: AuthorizedActivityBehaviorKind = AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
    behavior_value: str | None = "java->chattr",
) -> AuthorizationQuery:
    return AuthorizationQuery(
        alert_id="ALT-AUTH-1",
        tenant_id=TENANT,
        environment=ENVIRONMENT,
        event_time=event_time,
        subjects=[
            AuthorizationQuerySubject(
                kind=subject_kind,
                value=subject_value,
                evidence_path="entities.host.asset_id",
            )
        ],
        targets=[
            AuthorizationQueryTarget(
                kind=target_kind,
                value=target_value,
                evidence_path="entities.host.asset_id",
            )
        ],
        behaviors=(
            [
                AuthorizationQueryBehavior(
                    kind=behavior_kind,
                    value=behavior_value,
                    evidence_path="entities.process",
                )
            ]
            if behavior_value is not None
            else []
        ),
    )


def _pingan_payload(*, message: str, topic: str, topic_name: str) -> dict:
    return {
        "alert": {
            "alertId": "PINGAN-AA-001",
            "alertCode": "PIE-AA-001",
            "alertName": "Authorization shadow fixture",
            "riskLevel": "high",
            "createAt": "2026-07-14T10:05:00+08:00",
            "hitLog": [
                {
                    "topic": topic,
                    "topicName": topic_name,
                    "ruleCode": "RULE-AA-001",
                    "ruleName": "Authorization shadow fixture",
                    "zeusRawLogs": [{"message": message}],
                }
            ],
        },
        "relatedAlertList": [],
    }


def test_authorization_contract_requires_aware_event_time_and_rejects_extra_fields() -> None:
    payload = _query(event_time=BASE_TIME + timedelta(days=2)).model_dump(mode="python")
    payload["event_time"] = datetime(2026, 7, 3)
    with pytest.raises(ValidationError, match="event_time must be timezone-aware"):
        AuthorizationQuery.model_validate(payload)

    payload = _query(event_time=BASE_TIME + timedelta(days=2)).model_dump(mode="python")
    payload["vendor_rule_code"] = "not-allowed"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuthorizationQuery.model_validate(payload)


@pytest.mark.parametrize("source", ["command_line", "parent_command_line", "extracted_behavior", "rule_name"])
def test_oversized_behavior_is_unavailable_without_truncating_evidence(source: str) -> None:
    command = "cscript \\\\192.0.2.1\\share\\script.vbs " + "a" * 600
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-LONG-COMMAND",
        event=AlertEventRef(event_time=BASE_TIME + timedelta(days=2)),
        entities=AlertEntitySet(
            host=HostEntityRef(asset_id="asset-1"),
            process=ProcessEntityRef(process_name="chattr", parent_process_name="java"),
        ),
    )
    entities = None
    if source == "extracted_behavior":
        entities = ExtractedEntities(mentions=[EntityMention(kind=EntityKind.BEHAVIOR, value=command, key="behavior:long", evidence_path="raw.command")])
        expected_path = "raw.command"
    elif source == "rule_name":
        alert.detection.rule_name = command
        expected_path = "detection.rule_name"
    else:
        setattr(alert.entities.process, source, command)
        expected_path = f"entities.process.{source}"
    original = alert.model_dump(mode="json")
    repository = InMemoryGovernedContextFactRepository()
    _activate(repository, _payload())
    # A clipped command must never become a valid exact-match selector either.
    _activate(repository, _payload(behaviors=[AuthorizedActivityBehaviorSelector(kind=AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE, value=command[:512])]))
    service = SocAuthorizedActivityService(repository=repository)

    query = service.build_query(alert, entities=entities, environment=ENVIRONMENT)
    result = service.match(query)

    assert query.unavailable_dimensions == [AuthorizationDimension.BEHAVIOR]
    assert all(item.value not in {command, command[:512]} for item in query.behaviors)
    assert any("authorization_behavior_value_too_long" in warning and expected_path in warning for warning in query.warnings)
    assert result.status is AuthorizationMatchStatus.UNAVAILABLE
    assert result.matched_fact_refs == []
    assert result.missing_dimensions == [AuthorizationDimension.BEHAVIOR]
    assert result.warnings == [*query.warnings, "authorization_query_missing_required_context"]
    assert alert.model_dump(mode="json") == original
    if entities:
        assert entities.mentions[0].value == command


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("command_line", " "),
        ("command_line", "\t&nbsp;\r\n"),
        ("parent_command_line", "\u00a0"),
        ("technique", ""),
        ("rule_name", " \t"),
        ("extracted_behavior", " \n"),
    ],
)
def test_empty_behavior_is_unavailable_without_mutating_evidence(source: str, value: str) -> None:
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-BLANK-COMMAND",
        event=AlertEventRef(event_time=BASE_TIME + timedelta(days=2)),
        entities=AlertEntitySet(
            host=HostEntityRef(asset_id="asset-1"),
            process=ProcessEntityRef(process_name="chattr", parent_process_name="java"),
        ),
    )
    entities = None
    if source == "extracted_behavior":
        entities = ExtractedEntities(mentions=[EntityMention(kind=EntityKind.BEHAVIOR, value=value, key="behavior:blank", evidence_path="raw.command")])
        expected_path = "raw.command"
    elif source == "technique":
        alert.classification.technique = [value]
        expected_path = "classification.technique[0]"
    elif source == "rule_name":
        alert.detection.rule_name = value
        expected_path = "detection.rule_name"
    else:
        setattr(alert.entities.process, source, value)
        expected_path = f"entities.process.{source}"
    original = alert.model_dump(mode="json")
    original_entities = entities.model_dump(mode="json") if entities else None
    repository = InMemoryGovernedContextFactRepository()
    # Valid sibling process features must not authorize the incomplete query.
    _activate(repository, _payload())
    service = SocAuthorizedActivityService(repository=repository)

    query = service.build_query(alert, entities=entities, environment=ENVIRONMENT)
    result = service.match(query)

    assert query.unavailable_dimensions == [AuthorizationDimension.BEHAVIOR]
    assert all(item.value.strip() for item in query.behaviors)
    assert any("authorization_behavior_value_empty" in warning and expected_path in warning for warning in query.warnings)
    assert result.status is AuthorizationMatchStatus.UNAVAILABLE
    assert result.matched_fact_refs == []
    assert result.missing_dimensions == [AuthorizationDimension.BEHAVIOR]
    assert alert.model_dump(mode="json") == original
    if entities:
        assert entities.model_dump(mode="json") == original_entities


def test_many_empty_and_oversized_behaviors_keep_bounded_diagnostics() -> None:
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-INVALID-BEHAVIORS",
        event=AlertEventRef(event_time=BASE_TIME + timedelta(days=2)),
        entities=AlertEntitySet(host=HostEntityRef(asset_id="asset-1")),
    )
    alert.classification.technique = [" "] * 101 + ["x" * 513] * 101
    service = SocAuthorizedActivityService(repository=InMemoryGovernedContextFactRepository())

    query = service.build_query(alert, environment=ENVIRONMENT)

    assert query.behaviors == []
    assert query.unavailable_dimensions == [AuthorizationDimension.BEHAVIOR]
    for code in ("empty", "too_long"):
        warnings = [warning for warning in query.warnings if f"authorization_behavior_value_{code}:" in warning]
        assert len(warnings) == 1
        assert "omitted_count=101" in warnings[0]
    assert service.match(query).status is AuthorizationMatchStatus.UNAVAILABLE


@pytest.mark.parametrize("length", [1, 512])
def test_behavior_at_character_boundaries_still_matches_exactly(length: int) -> None:
    command = "a" * length
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-BOUNDARY-COMMAND",
        event=AlertEventRef(event_time=BASE_TIME + timedelta(days=2)),
        entities=AlertEntitySet(host=HostEntityRef(asset_id="asset-1"), process=ProcessEntityRef(command_line=command)),
    )
    repository = InMemoryGovernedContextFactRepository()
    _activate(repository, _payload(behaviors=[AuthorizedActivityBehaviorSelector(kind=AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE, value=command)]))
    service = SocAuthorizedActivityService(repository=repository)

    query = service.build_query(alert, environment=ENVIRONMENT)

    assert query.unavailable_dimensions == []
    assert service.match(query).status is AuthorizationMatchStatus.EXACT
    assert query.behaviors[0].value == command


def test_many_oversized_behaviors_keep_unavailable_query_and_bounded_warnings() -> None:
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-MANY-LONG-BEHAVIORS",
        event=AlertEventRef(event_time=BASE_TIME + timedelta(days=2)),
        entities=AlertEntitySet(host=HostEntityRef(asset_id="asset-1")),
    )
    entities = ExtractedEntities(mentions=[EntityMention(kind=EntityKind.BEHAVIOR, value=f"cscript script-{index}.vbs " + "a" * 600, key=f"behavior:long:{index}", evidence_path=f"raw.behaviors[{index}]") for index in range(101)])
    original = entities.model_dump(mode="json")
    service = SocAuthorizedActivityService(repository=InMemoryGovernedContextFactRepository())

    query = service.build_query(alert, entities=entities, environment=ENVIRONMENT)
    result = service.match(query)

    assert query.unavailable_dimensions == [AuthorizationDimension.BEHAVIOR]
    assert query.behaviors == []
    overflow_warnings = [warning for warning in query.warnings if "authorization_behavior_value_too_long" in warning]
    assert len(overflow_warnings) == 1
    assert "raw.behaviors[0]" in overflow_warnings[0]
    assert "omitted_count=101" in overflow_warnings[0]
    assert result.status is AuthorizationMatchStatus.UNAVAILABLE
    assert result.missing_dimensions == [AuthorizationDimension.BEHAVIOR]
    assert entities.model_dump(mode="json") == original


def test_legacy_authorization_query_defaults_to_no_unavailable_dimensions() -> None:
    payload = _query(event_time=BASE_TIME + timedelta(days=2)).model_dump(mode="json")
    payload.pop("unavailable_dimensions", None)
    query = AuthorizationQuery.model_validate(payload)
    assert query.unavailable_dimensions == []
    assert query.model_dump(mode="json") == payload
    assert authorization_query_hash(query) == stable_hash({key: value for key, value in payload.items() if key != "query_id"})


def test_unavailable_behavior_survives_enrichment_storage_and_replay() -> None:
    engine = create_engine("sqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = AnalysisRun(run_id="RUN-AUTH-LONG", alert_id="ALT-AUTH-1", status=AnalysisRunStatus.NEEDS_REVIEW)
    repository.save_run(run)
    _activate(repository, _payload())
    service = SocAuthorizationEnrichmentService(
        authorization_service=SocAuthorizedActivityService(repository=repository),
        repository=repository,
        alert_repository=repository,
    )
    query = _query(event_time=BASE_TIME + timedelta(days=2))
    old_hash = authorization_query_hash(query)
    query.unavailable_dimensions = [AuthorizationDimension.BEHAVIOR]
    command = AuthorizationEnrichmentCommand(run_id=run.run_id, query=query, idempotency_key="authorization:long:initial")
    try:
        initial = service.enrich(command, context=_context("soc_analyst"))
        saved = repository.get_authorization_enrichment(initial.record.enrichment_id)
        replay = service.replay(initial.record.enrichment_id, idempotency_key="authorization:long:replay", context=_context("soc_analyst"))

        assert saved.query.unavailable_dimensions == [AuthorizationDimension.BEHAVIOR]
        assert saved.query_hash != old_hash
        assert replay.record.query_hash == saved.query_hash
        assert replay.record.query.unavailable_dimensions == [AuthorizationDimension.BEHAVIOR]
        assert saved.match_result.status is AuthorizationMatchStatus.UNAVAILABLE
        assert replay.record.match_result.status is AuthorizationMatchStatus.UNAVAILABLE
        assert service.enrich(command, context=_context("soc_analyst")).idempotent is True
    finally:
        engine.dispose()


def test_hids_java_chattr_matches_scoped_governed_fact() -> None:
    repository = InMemoryGovernedContextFactRepository()
    _activate(
        repository,
        _payload(
            subjects=[
                AuthorizedActivitySubjectSelector(
                    kind=AuthorizedActivitySubjectKind.ASSET_ID,
                    value="AGENT-HIDS-001",
                )
            ],
            targets=[
                AuthorizedActivityTargetSelector(
                    kind=AuthorizedActivityTargetKind.ASSET_ID,
                    value="AGENT-HIDS-001",
                )
            ],
            behaviors=[
                AuthorizedActivityBehaviorSelector(
                    kind=AuthorizedActivityBehaviorKind.SCENARIO,
                    value="command_execution",
                ),
                AuthorizedActivityBehaviorSelector(
                    kind=AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
                    value="java->chattr",
                ),
            ],
        ),
    )
    message = (
        "2026-07-14T10:00:05+08:00 HOST-SENSOR qtAlert[679] "
        'datatype="web_command" agent_ip="30.232.21.35" host_name="work04" '
        'internal_ip="30.232.21.35" external_ip="1.1.1.1" agent_id="AGENT-HIDS-001" '
        'event_type="web_command" event_name="LinuxWeb命令执行" '
        'event_content="java进程发现异常执行行为，其进程树为：java(3065)-&gt;chattr(3287784)"'
    )

    result = SocAuthorizedActivityService(repository=repository).match_payload(
        _pingan_payload(message=message, topic="security_qthids", topic_name="HIDS"),
        tenant_id=TENANT,
        environment=ENVIRONMENT,
    )

    assert result.status is AuthorizationMatchStatus.EXACT
    assert result.shadow_only is True
    assert len(result.matched_fact_refs) == 1
    assert AuthorizationDimension.BEHAVIOR in result.matched_dimensions
    behavior = next(item for item in result.fact_evaluations[0].dimension_results if item.dimension is AuthorizationDimension.BEHAVIOR)
    assert set(behavior.required_selector_groups) == {"scenario", "behavior_signature"}
    assert {item.query_value for item in behavior.matched_selectors} >= {
        "command_execution",
        "java->chattr",
    }


def test_edr_remote_registry_matches_with_explicit_tenant_timezone() -> None:
    repository = InMemoryGovernedContextFactRepository()
    _activate(
        repository,
        _payload(
            subjects=[
                AuthorizedActivitySubjectSelector(
                    kind=AuthorizedActivitySubjectKind.IP,
                    value="30.162.29.85",
                )
            ],
            targets=[
                AuthorizedActivityTargetSelector(
                    kind=AuthorizedActivityTargetKind.IP,
                    value="10.43.107.39",
                )
            ],
            behaviors=[
                AuthorizedActivityBehaviorSelector(
                    kind=AuthorizedActivityBehaviorKind.SCENARIO,
                    value="lateral_movement",
                ),
                AuthorizedActivityBehaviorSelector(
                    kind=AuthorizedActivityBehaviorKind.PROCESS,
                    value="svchost.exe",
                ),
            ],
        ),
    )
    message = (
        "<14>[SourceIP:30.99.16.122][AuditDB.tbl_ud_pe_threat_alert]"
        "str_source_ip=10.43.107.39,str_threat_value=30.162.29.85,"
        "str_attack_ip=30.162.29.85,t_detect_time=2026-07-14 10:00:00,"
        "str_title=横向移动(开启远程注册表服务),str_process_short=svchost.exe,"
        "str_cmd=C:\\Windows\\System32\\svchost.exe -k localService -p -s RemoteRegistry,"
        "str_parent_path_full=C:\\Windows\\System32\\services.exe,"
        "str_agent_id=AGENT-EDR-001,str_source_host=HOST-EDR-001"
    )

    result = SocAuthorizedActivityService(repository=repository).match_payload(
        _pingan_payload(message=message, topic="leagsoft-edr", topic_name="EDR"),
        tenant_id=TENANT,
        environment=ENVIRONMENT,
        event_timezone="Asia/Shanghai",
    )

    assert result.status is AuthorizationMatchStatus.EXACT
    assert "authorization_event_time_timezone_assumed:Asia/Shanghai" in result.warnings
    subject = next(item for item in result.fact_evaluations[0].dimension_results if item.dimension is AuthorizationDimension.SUBJECT)
    assert subject.matched_selectors[0].query_value == "30.162.29.85"


def test_naive_event_time_without_explicit_timezone_is_unavailable() -> None:
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-NAIVE-TIME",
        event=AlertEventRef(event_time=datetime(2026, 7, 3, 10, 0)),
        entities=AlertEntitySet(
            host=HostEntityRef(asset_id="asset-1"),
            process=ProcessEntityRef(process_name="chattr", parent_process_name="java"),
        ),
    )
    result = SocAuthorizedActivityService(
        repository=InMemoryGovernedContextFactRepository(),
    ).match_alert(alert, environment=ENVIRONMENT)

    assert result.status is AuthorizationMatchStatus.UNAVAILABLE
    assert result.missing_dimensions == [AuthorizationDimension.EVENT_TIME]
    assert "authorization_event_time_timezone_missing" in result.warnings


def test_selector_groups_are_anded_and_values_inside_one_group_are_ored() -> None:
    repository = InMemoryGovernedContextFactRepository()
    _activate(
        repository,
        _payload(
            behaviors=[
                AuthorizedActivityBehaviorSelector(
                    kind=AuthorizedActivityBehaviorKind.SCENARIO,
                    value="command_execution",
                ),
                AuthorizedActivityBehaviorSelector(
                    kind=AuthorizedActivityBehaviorKind.PROCESS,
                    value="chattr",
                ),
                AuthorizedActivityBehaviorSelector(
                    kind=AuthorizedActivityBehaviorKind.PROCESS,
                    value="chmod",
                ),
            ]
        ),
    )
    query = _query(
        event_time=BASE_TIME + timedelta(days=2),
        behavior_kind=AuthorizedActivityBehaviorKind.PROCESS,
        behavior_value="chattr",
    )
    query = query.model_copy(
        update={
            "behaviors": [
                *query.behaviors,
                AuthorizationQueryBehavior(
                    kind=AuthorizedActivityBehaviorKind.SCENARIO,
                    value="command_execution",
                    evidence_path="fact_reconstruction.scenario_hypotheses[0]",
                ),
            ]
        }
    )

    exact = SocAuthorizedActivityService(repository=repository).match(query)
    missing_scenario = SocAuthorizedActivityService(repository=repository).match(query.model_copy(update={"behaviors": query.behaviors[:1]}))

    assert exact.status is AuthorizationMatchStatus.EXACT
    assert missing_scenario.status is AuthorizationMatchStatus.PARTIAL


def test_scope_mismatch_and_blocking_fact_conflict_fail_closed() -> None:
    repository = InMemoryGovernedContextFactRepository()
    _activate(repository, _payload())
    service = SocAuthorizedActivityService(repository=repository)

    mismatch = service.match(
        _query(
            event_time=BASE_TIME + timedelta(days=2),
            behavior_value="powershell->rundll32",
        )
    )
    conflict_query = _query(event_time=BASE_TIME + timedelta(days=2)).model_copy(
        update={
            "conflicts": [
                AuthorizationQueryConflict(
                    conflict_type="attacker_role_conflict",
                    reason="canonical attacker role remains ambiguous",
                    evidence_paths=["fact_reconstruction.role_resolutions.attacker"],
                )
            ]
        }
    )
    blocked = service.match(conflict_query)

    assert mismatch.status is AuthorizationMatchStatus.CONFLICT
    assert mismatch.out_of_scope_dimensions == [AuthorizationDimension.BEHAVIOR]
    assert blocked.status is AuthorizationMatchStatus.CONFLICT
    assert blocked.matched_fact_refs == []


def test_matcher_uses_lifecycle_version_effective_at_event_time() -> None:
    repository = InMemoryGovernedContextFactRepository()
    active = _activate(repository, _payload())
    revoke_time = BASE_TIME + timedelta(days=10)
    SocGovernedContextService(
        repository=repository,
        now_provider=lambda: revoke_time,
    ).revoke(
        GovernedContextFactTransitionCommand(
            fact_id=active.fact_id,
            expected_latest_version=active.version,
            reason="Authorization withdrawn.",
        ),
        context=_context("soc_context_approver"),
    )
    service = SocAuthorizedActivityService(repository=repository)

    before_revoke = service.match(_query(event_time=BASE_TIME + timedelta(days=5)))
    after_revoke = service.match(_query(event_time=BASE_TIME + timedelta(days=11)))

    assert before_revoke.status is AuthorizationMatchStatus.EXACT
    assert before_revoke.matched_fact_refs[0].version == 2
    assert after_revoke.status is AuthorizationMatchStatus.EXPIRED
    assert after_revoke.candidate_fact_refs[0].version == 3


def test_stale_authoritative_source_is_expired_not_exact() -> None:
    repository = InMemoryGovernedContextFactRepository()
    _activate(
        repository,
        _payload(),
        source=GovernedContextSource(
            source_type=GovernedContextSourceType.AUTHORITATIVE_SYSTEM,
            source_ref="change-system:CHG-1001",
            source_version="7",
            observed_at=BASE_TIME - timedelta(days=1),
            fresh_until=BASE_TIME + timedelta(days=5),
            authoritative=True,
        ),
    )

    result = SocAuthorizedActivityService(repository=repository).match(_query(event_time=BASE_TIME + timedelta(days=6)))

    assert result.status is AuthorizationMatchStatus.EXPIRED
    assert result.source_freshness == [AuthorizationSourceFreshness.STALE]


def test_cross_midnight_recurring_window_uses_window_start_day() -> None:
    repository = InMemoryGovernedContextFactRepository()
    _activate(
        repository,
        _payload(
            windows=[
                AuthorizedActivityRecurringWindow(
                    timezone="Asia/Shanghai",
                    days_of_week=[0],
                    start_time=time(22, 0),
                    end_time=time(2, 0),
                )
            ]
        ),
        valid_until=datetime(2026, 8, 1, tzinfo=UTC),
    )
    service = SocAuthorizedActivityService(repository=repository)
    tuesday_0100 = datetime(2026, 7, 21, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    tuesday_0300 = tuesday_0100.replace(hour=3)

    inside = service.match(_query(event_time=tuesday_0100))
    outside = service.match(_query(event_time=tuesday_0300))

    assert tuesday_0100.weekday() == 1
    assert inside.status is AuthorizationMatchStatus.EXACT
    assert outside.status is AuthorizationMatchStatus.CONFLICT
    assert AuthorizationDimension.RECURRING_WINDOW in outside.out_of_scope_dimensions


def test_cidr_selectors_match_canonical_ip_candidates() -> None:
    repository = InMemoryGovernedContextFactRepository()
    _activate(
        repository,
        _payload(
            subjects=[
                AuthorizedActivitySubjectSelector(
                    kind=AuthorizedActivitySubjectKind.CIDR,
                    value="10.20.0.0/16",
                )
            ],
            targets=[
                AuthorizedActivityTargetSelector(
                    kind=AuthorizedActivityTargetKind.CIDR,
                    value="10.30.40.0/24",
                )
            ],
        ),
    )

    result = SocAuthorizedActivityService(repository=repository).match(
        _query(
            event_time=BASE_TIME + timedelta(days=2),
            subject_kind=AuthorizedActivitySubjectKind.IP,
            subject_value="10.20.3.4",
            target_kind=AuthorizedActivityTargetKind.IP,
            target_value="10.30.40.9",
        )
    )

    assert result.status is AuthorizationMatchStatus.EXACT


def test_repository_absence_and_candidate_truncation_are_unavailable() -> None:
    query = _query(event_time=BASE_TIME + timedelta(days=2))
    assert SocAuthorizedActivityService().match(query).status is AuthorizationMatchStatus.UNAVAILABLE

    empty = SocAuthorizedActivityService(
        repository=InMemoryGovernedContextFactRepository(),
    ).match(query)
    assert empty.status is AuthorizationMatchStatus.NOT_FOUND

    repository = InMemoryGovernedContextFactRepository()
    _activate(repository, _payload())
    truncated = SocAuthorizedActivityService(repository=repository, candidate_limit=1).match(query)
    assert truncated.status is AuthorizationMatchStatus.UNAVAILABLE
    assert "authorization_candidate_limit_reached:1" in truncated.warnings


def test_context_match_cli_is_read_only_shadow_output(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'soc-authorization.db'}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    now = datetime.now(UTC)
    _activate(
        repository,
        _payload(),
        lifecycle_time=now,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=1),
        source=GovernedContextSource(
            source_type=GovernedContextSourceType.ANALYST_CONFIRMATION,
            source_ref="ticket:CLI-1001",
            observed_at=now - timedelta(minutes=1),
        ),
    )
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-CLI-AUTH",
        event=AlertEventRef(event_time=now + timedelta(minutes=1)),
        entities=AlertEntitySet(
            host=HostEntityRef(asset_id="asset-1"),
            process=ProcessEntityRef(process_name="chattr", parent_process_name="java"),
        ),
    )
    alert_path = tmp_path / "alert.json"
    alert_path.write_text(json.dumps(alert.model_dump(mode="json")), encoding="utf-8")

    try:
        assert (
            main(
                [
                    "context",
                    "match",
                    str(alert_path),
                    "--environment",
                    ENVIRONMENT,
                    "--database-url",
                    database_url,
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
    finally:
        engine.dispose()

    assert result["status"] == "exact"
    assert result["shadow_only"] is True
    assert result["matched_fact_refs"]


def test_authorization_enrichment_is_append_only_idempotent_and_replayable() -> None:
    engine = create_engine("sqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = AnalysisRun(
        run_id="RUN-AUTH-ENRICH-1",
        alert_id="ALT-AUTH-1",
        status=AnalysisRunStatus.NEEDS_REVIEW,
    )
    queue_item = ReviewQueueItem(
        queue_id="REV-AUTH-ENRICH-1",
        run_id=run.run_id,
        alert_id=run.alert_id,
        status=ReviewQueueStatus.OPEN,
        reason="authorization enrichment test",
    )
    repository.save_run(run)
    repository.save_review_item(queue_item)
    _activate(repository, _payload())
    service = SocAuthorizationEnrichmentService(
        authorization_service=SocAuthorizedActivityService(repository=repository),
        repository=repository,
        alert_repository=repository,
        review_queue_repository=repository,
    )
    command = AuthorizationEnrichmentCommand(
        run_id=run.run_id,
        queue_id=queue_item.queue_id,
        query=_query(event_time=BASE_TIME + timedelta(days=2)),
        idempotency_key="authorization:enrichment:test:1",
    )

    initial = service.enrich(command, context=_context("soc_analyst"))
    duplicate = service.enrich(command, context=_context("soc_analyst"))
    replay = service.replay(
        initial.record.enrichment_id,
        idempotency_key="authorization:enrichment:test:replay:1",
        context=_context("soc_analyst"),
    )

    assert initial.idempotent is False
    assert initial.record.match_result.status is AuthorizationMatchStatus.EXACT
    assert initial.record.shadow_only is True
    assert initial.record.decision_impact == "none"
    assert duplicate.idempotent is True
    assert duplicate.record == initial.record
    assert replay.record.enrichment_id != initial.record.enrichment_id
    assert replay.record.replay_of_enrichment_id == initial.record.enrichment_id
    assert replay.record.query_hash == initial.record.query_hash
    assert replay.record.query.query_id != initial.record.query.query_id
    assert [item.enrichment_id for item in service.list(run_id=run.run_id)] == [
        replay.record.enrichment_id,
        initial.record.enrichment_id,
    ]
    assert repository.get_run(run.run_id) == run
    assert repository.get_review_item(queue_item.queue_id) == queue_item

    context = SocReviewService(
        repository=repository,
        review_queue_repository=repository,
        authorization_enrichment_repository=repository,
    ).get_investigation_context(queue_item.queue_id)
    assert context.authorization_enrichments == [replay.record, initial.record]
    assert context.investigation_view is not None
    assert context.investigation_view.counts["authorization_enrichments"] == 2
    assert context.investigation_view.counts["exact_authorization_matches"] == 2
    assert any(item.kind == "authorization_enrichment" for item in context.investigation_view.evidence_timeline)
    artifact = build_lead_agent_review_context_artifact(context)
    assert artifact.authorization_enrichments[0]["status"] == "exact"
    assert artifact.authorization_enrichments[0]["decision_impact"] == "none"
    engine.dispose()


def test_authorization_enrichment_rejects_idempotency_key_reuse_for_new_query() -> None:
    engine = create_engine("sqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = AnalysisRun(
        run_id="RUN-AUTH-ENRICH-CONFLICT",
        alert_id="ALT-AUTH-1",
        status=AnalysisRunStatus.NEEDS_REVIEW,
    )
    repository.save_run(run)
    service = SocAuthorizationEnrichmentService(
        authorization_service=SocAuthorizedActivityService(repository=repository),
        repository=repository,
        alert_repository=repository,
    )
    initial = AuthorizationEnrichmentCommand(
        run_id=run.run_id,
        query=_query(event_time=BASE_TIME + timedelta(days=2)),
        idempotency_key="authorization:enrichment:conflict",
    )
    service.enrich(initial)

    with pytest.raises(
        AuthorizationEnrichmentIdempotencyConflictError,
        match="different input",
    ):
        service.enrich(
            initial.model_copy(
                update={
                    "query": _query(
                        event_time=BASE_TIME + timedelta(days=2),
                        behavior_value="different-behavior",
                    )
                }
            )
        )
    engine.dispose()


def test_context_enrich_cli_persists_run_enrichment(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'soc-authorization-enrichment.db'}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    now = datetime.now(UTC)
    alert = AlertInput(
        tenant_id=TENANT,
        alert_id="ALT-AUTH-CLI-ENRICH",
        event=AlertEventRef(event_time=now + timedelta(minutes=1)),
        entities=AlertEntitySet(
            host=HostEntityRef(asset_id="asset-1"),
            process=ProcessEntityRef(
                process_name="chattr",
                parent_process_name="java",
            ),
        ),
    )
    repository.save_run(
        AnalysisRun(
            run_id="RUN-AUTH-CLI-ENRICH",
            alert_id=alert.alert_id,
            status=AnalysisRunStatus.NEEDS_REVIEW,
            input_payload=alert.model_dump(mode="json"),
        )
    )
    _activate(
        repository,
        _payload(),
        lifecycle_time=now,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=1),
        source=GovernedContextSource(
            source_type=GovernedContextSourceType.ANALYST_CONFIRMATION,
            source_ref="ticket:CLI-ENRICH-1",
            observed_at=now - timedelta(minutes=1),
        ),
    )

    try:
        assert (
            main(
                [
                    "context",
                    "enrich",
                    "RUN-AUTH-CLI-ENRICH",
                    "--environment",
                    ENVIRONMENT,
                    "--database-url",
                    database_url,
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert result["record"]["match_result"]["status"] == "exact"
        assert result["record"]["decision_impact"] == "none"

        assert (
            main(
                [
                    "context",
                    "enrichment",
                    "list",
                    "--run-id",
                    "RUN-AUTH-CLI-ENRICH",
                    "--database-url",
                    database_url,
                ]
            )
            == 0
        )
        listed = json.loads(capsys.readouterr().out)
    finally:
        engine.dispose()

    assert len(listed) == 1
    assert listed[0]["run_id"] == "RUN-AUTH-CLI-ENRICH"
