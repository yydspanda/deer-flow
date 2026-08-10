from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.cli import main as soc_main
from soc_agent.contracts import (
    ActorContext,
    AlertClassification,
    AlertEntitySet,
    AlertEventRef,
    AlertInput,
    AlertSourceRef,
    AlertSourceType,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    Decision,
    DecisionEvidenceState,
    DetectionRuleRef,
    EvidenceItem,
    HttpEntityRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ServiceRequestContext,
    TenantDispositionRule,
    TenantPolicyEvaluationStatus,
    TenantPolicyResponsePosture,
    TenantPolicyTimeSource,
    Verdict,
)
from soc_agent.core import SocAnalysisService, SocTenantPolicyEvaluationService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.integrations.pingan.tenant_disposition import (
    PINGAN_TENANT_DISPOSITION_POLICY_PATH,
    load_pingan_tenant_disposition_policy,
)
from soc_agent.tenant_policy import (
    InMemoryTenantPolicyDecisionRepository,
    StaticTenantPolicyResolver,
    TenantPolicyNotApplicableError,
    evaluate_tenant_policy,
)


class _FixedRuntime:
    def __init__(self, run: AnalysisRun) -> None:
        self._run = run

    def analyze(self, payload) -> AnalysisRun:
        return self._run.model_copy(deep=True)


class _FailingObserver:
    def observe(self, run: AnalysisRun, *, context: ServiceRequestContext) -> None:
        raise RuntimeError("shadow observer failed")


def _run(*, category: str = "弱口令", host: str = "ehis-dataplus-stg.paic.com.cn") -> AnalysisRun:
    alert = AlertInput(
        tenant_id="pingan",
        alert_id="1965449",
        source=AlertSourceRef(source_type=AlertSourceType.NDR, source_system="sec_guard_apt"),
        detection=DetectionRuleRef(
            rule_name="弱口令登录成功",
            rule_category=category,
            detection_key="pingan:weak-password",
        ),
        classification=AlertClassification(category=category, severity="high"),
        event=AlertEventRef(event_time=datetime(2026, 4, 16, 10, 30, 0)),
        entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="10.28.121.248",
                destination_ip="30.184.42.99",
            ),
            http=HttpEntityRef(host=host, path="/pws/askbob-gpt"),
        ),
    )
    analysis = AnalysisResult(
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
        summary="Weak-password detection against an internal staging service requires review.",
        evidence=[
            EvidenceItem(
                evidence_ref="E-000000000001",
                source="canonical_entities.http.host",
                description="Observed HTTP host",
                value=host,
            )
        ],
        reasoning=[
            AnalysisReasoningItem(
                reasoning_id="R-01",
                statement="The upstream alert is credible, while authorization and impact remain unresolved.",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=["E-000000000001"],
                confidence=0.62,
            )
        ],
        reason="The upstream alert is credible, while authorization and impact remain unresolved.",
        recommended_action="Escalate and contain the source after analyst confirmation.",
    )
    decision = Decision(
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
        evidence_state=DecisionEvidenceState.PARTIAL,
        suggested_action="Escalate and contain the source after analyst confirmation.",
        needs_review=True,
        reason="Evidence supports suspicion but does not establish authorization or impact.",
    )
    return AnalysisRun(
        run_id="RUN-PINGAN-POLICY-1",
        alert_id=alert.alert_id,
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_payload=alert.model_dump(mode="json"),
        llm_analysis_request=LLMAnalysisRequest(
            alert_id=alert.alert_id,
            tenant_id=alert.tenant_id,
            source=alert.source,
            detection=alert.detection,
            classification=alert.classification,
            canonical_entities=alert.entities,
        ),
        analysis=analysis,
        decision=decision,
    )


def _sql_repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def test_pingan_policy_changes_only_shadow_operational_recommendation() -> None:
    run = _run()
    original = run.model_dump(mode="json")

    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        run,
        environment="dev",
        triggered_by=ActorContext(actor_id="analyst-1", roles=["soc_analyst"]),
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.MATCHED
    assert decision.selected_rule_id == "internal-nonproduction-credential-review"
    assert decision.detection_truth.verdict is Verdict.SUSPICIOUS
    assert decision.response_posture is TenantPolicyResponsePosture.NO_AUTOMATED_RESPONSE
    assert decision.recommended_disposition is None
    assert decision.shadow_only is True
    assert decision.auto_apply_allowed is False
    assert decision.review_queue_impact == "none"
    assert decision.action_impact == "none"
    assert run.model_dump(mode="json") == original


def test_pingan_policy_does_not_guess_when_nonproduction_conditions_do_not_match() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(category="恶意文件", host="service.paic.com.cn"),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None
    assert decision.response_posture is TenantPolicyResponsePosture.STANDARD_TRIAGE


def test_bounded_policy_requires_alert_event_time() -> None:
    policy = load_pingan_tenant_disposition_policy().model_copy(update={"effective_from": datetime(2026, 1, 1, tzinfo=UTC)})

    with pytest.raises(
        TenantPolicyNotApplicableError,
        match="requires alert event time",
    ):
        evaluate_tenant_policy(policy, _run(), environment="dev")


def test_authorization_condition_cannot_bypass_governed_disposition_proposal() -> None:
    with pytest.raises(ValueError, match="governed enrichment/proposal path"):
        TenantDispositionRule.model_validate(
            {
                "rule_id": "invalid-auth-disposition",
                "name": "Invalid direct authorization disposition",
                "match": {"authorization_statuses": ["exact"]},
                "recommendation": {
                    "response_posture": "no_automated_response",
                    "recommended_disposition": "closed_benign_true_positive",
                    "summary": "Invalid direct recommendation.",
                    "rationale": ["Must use DP-01."],
                },
            }
        )


def test_sql_repository_round_trips_tenant_policy_decision() -> None:
    repository = _sql_repository()
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(),
        environment="dev",
    )

    repository.save_tenant_policy_decision(decision)

    assert repository.get_tenant_policy_decision(decision.decision_id) == decision
    assert repository.find_tenant_policy_decision_by_key(decision.decision_key) == decision
    assert repository.list_tenant_policy_decisions(run_id=decision.run_id) == [decision]


def test_analysis_service_runs_policy_after_persistence_and_deduplicates_retry() -> None:
    repository = _sql_repository()
    run = _run()
    policy_repository = InMemoryTenantPolicyDecisionRepository()
    policy_observer = SocTenantPolicyEvaluationService(
        policy_resolver=StaticTenantPolicyResolver([load_pingan_tenant_disposition_policy()]),
        repository=policy_repository,
        environment="dev",
        event_timezone="Asia/Shanghai",
    )
    service = SocAnalysisService(
        runtime=_FixedRuntime(run),
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
        post_analysis_observers=[policy_observer],
    )
    context = ServiceRequestContext(
        idempotency_key="tenant-policy-analysis-1",
        actor=ActorContext(actor_id="analyst-1", roles=["soc_analyst"]),
    )

    first = service.analyze(deepcopy(run.input_payload), context=context)
    second = service.analyze(deepcopy(run.input_payload), context=context)

    assert first.run_id == second.run_id
    assert repository.get_run(first.run_id).decision.verdict is Verdict.SUSPICIOUS
    decisions = policy_repository.list_tenant_policy_decisions(run_id=first.run_id)
    assert len(decisions) == 1
    assert decisions[0].selected_rule_id == "internal-nonproduction-credential-review"
    assert decisions[0].policy_time_source is TenantPolicyTimeSource.ALERT_EVENT_TIME_TIMEZONE_ASSUMED
    review = repository.get_open_review_item_by_run(first.run_id)
    assert review is not None
    assert review.status.value == "open"


def test_post_analysis_observer_failure_does_not_fail_persisted_analysis() -> None:
    repository = _sql_repository()
    run = _run()
    service = SocAnalysisService(
        runtime=_FixedRuntime(run),
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
        post_analysis_observers=[_FailingObserver()],
    )

    result = service.analyze(run.input_payload)

    assert result.status is AnalysisRunStatus.NEEDS_REVIEW
    assert repository.get_run(result.run_id) is not None


def test_tenant_policy_cli_evaluates_and_lists_persisted_decision(
    tmp_path,
    capsys,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'tenant-policy.db'}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    repository.save_run(_run())

    assert (
        soc_main(
            [
                "tenant-policy",
                "evaluate",
                "RUN-PINGAN-POLICY-1",
                "--policy-path",
                str(PINGAN_TENANT_DISPOSITION_POLICY_PATH),
                "--environment",
                "dev",
                "--event-timezone",
                "Asia/Shanghai",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    evaluated = capsys.readouterr().out
    assert '"selected_rule_id":"internal-nonproduction-credential-review"' in evaluated

    assert (
        soc_main(
            [
                "tenant-policy",
                "list",
                "--run-id",
                "RUN-PINGAN-POLICY-1",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    listed = capsys.readouterr().out
    assert '"policy_id": "pingan.soc.disposition"' in listed
