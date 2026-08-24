from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import soc_agent.application.analysis as analysis_application
from soc_agent.automation import InMemorySocAutomationRepository
from soc_agent.cli import main as soc_main
from soc_agent.contracts import (
    ActorContext,
    AlertClassification,
    AlertEntitySet,
    AlertEventRef,
    AlertInput,
    AlertSourceRef,
    AlertSourceType,
    AnalysisEvidenceCatalogItem,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationFactRef,
    AuthorizationMatchResult,
    AuthorizationMatchStatus,
    Decision,
    DecisionEvidenceState,
    DetectionRuleRef,
    EvidenceItem,
    EvidenceTrustLevel,
    GovernedContextFactStatus,
    HttpEntityRef,
    HttpObservationRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ServiceRequestContext,
    SocDecisionStageKind,
    SocDecisionStageStatus,
    SocOperationalDisposition,
    TenantDispositionRule,
    TenantPolicyAdvisorStatus,
    TenantPolicyDecisionSource,
    TenantPolicyEvaluationStatus,
    TenantPolicyMode,
    TenantPolicyResponsePosture,
    TenantPolicyReviewEffect,
    TenantPolicyTimeSource,
    Verdict,
)
from soc_agent.core import (
    SocAnalysisService,
    SocAutomationService,
    SocTenantPolicyEvaluationService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.integrations.pingan.tenant_disposition import (
    PINGAN_TENANT_DISPOSITION_POLICY_PATH,
    PINGAN_TENANT_DISPOSITION_SKILL_PATH,
    load_pingan_tenant_disposition_policy,
)
from soc_agent.llm import LLMChatResponse, SocLLMSettings
from soc_agent.tenant_policy import (
    InMemoryTenantPolicyDecisionRepository,
    LLMTenantPolicyAdvisor,
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


def _run(
    *,
    category: str = "弱口令",
    host: str = "ehis-dataplus-stg.paic.com.cn",
    status_code: int | None = 403,
    observation_status_codes: tuple[int, ...] = (),
    labels: dict[str, str] | None = None,
    rule_code: str | None = None,
) -> AnalysisRun:
    classification_labels = dict(labels or {})
    alert = AlertInput(
        tenant_id="pingan",
        alert_id="1965449",
        source=AlertSourceRef(source_type=AlertSourceType.NDR, source_system="sec_guard_apt"),
        detection=DetectionRuleRef(
            rule_code=rule_code,
            rule_name="弱口令登录成功",
            rule_category=category,
            detection_key="pingan:weak-password",
        ),
        classification=AlertClassification(
            category=category,
            severity="high",
            labels=classification_labels,
        ),
        event=AlertEventRef(event_time=datetime(2026, 4, 16, 10, 30, 0)),
        entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="10.28.121.248",
                destination_ip="30.184.42.99",
            ),
            http=HttpEntityRef(
                host=host,
                path="/pws/askbob-gpt",
                status_code=status_code,
                observations=[
                    HttpObservationRef(
                        observation_id=f"HTTP-{index}",
                        evidence_path=f"raw.http[{index}]",
                        status_code=value,
                    )
                    for index, value in enumerate(observation_status_codes)
                ],
            ),
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
            evidence_catalog=[
                AnalysisEvidenceCatalogItem(
                    evidence_ref="E-000000000001",
                    source_path="canonical_entities.http.host",
                    value=host,
                    value_type="string",
                    trust_level=EvidenceTrustLevel.HIGH,
                ),
                *(
                    [
                        AnalysisEvidenceCatalogItem(
                            evidence_ref="E-000000000002",
                            source_path="canonical_entities.http.status_code",
                            value=status_code,
                            value_type="integer",
                            trust_level=EvidenceTrustLevel.HIGH,
                        )
                    ]
                    if status_code is not None
                    else []
                ),
                *[
                    AnalysisEvidenceCatalogItem(
                        evidence_ref=f"E-{index + 10:012d}",
                        source_path=f"classification.labels.{key}",
                        value=value,
                        value_type="string",
                        trust_level=EvidenceTrustLevel.HIGH,
                    )
                    for index, (key, value) in enumerate(sorted(classification_labels.items()))
                ],
            ],
        ),
        analysis=analysis,
        decision=decision,
    )


def _sql_repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _exact_authorization(alert_id: str) -> AuthorizationMatchResult:
    return AuthorizationMatchResult(
        query_id="AAQ-PINGAN-POLICY-1",
        alert_id=alert_id,
        status=AuthorizationMatchStatus.EXACT,
        event_time=datetime(2026, 4, 16, 2, 30, tzinfo=UTC),
        matched_fact_refs=[
            AuthorizationFactRef(
                fact_id="GCF-PINGAN-PENTEST-1",
                fact_version_id="GCFV-PINGAN-PENTEST-1",
                version=1,
                status=GovernedContextFactStatus.ACTIVE,
                content_hash="a" * 64,
            )
        ],
    )


def test_pingan_deterministic_policy_ignores_canonical_non_200() -> None:
    run = _run()
    original = run.model_dump(mode="json")

    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        run,
        environment="dev",
        triggered_by=ActorContext(actor_id="analyst-1", roles=["soc_analyst"]),
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.MATCHED
    assert decision.selected_rule_id == "canonical-http-non-200-ignore"
    assert decision.decision_source is TenantPolicyDecisionSource.DETERMINISTIC_RULE
    assert decision.detection_truth.verdict is Verdict.SUSPICIOUS
    assert decision.response_posture is TenantPolicyResponsePosture.NO_AUTOMATED_RESPONSE
    assert decision.recommended_disposition is SocOperationalDisposition.IGNORED
    assert decision.policy_mode is TenantPolicyMode.ENFORCED
    assert decision.review_effect is TenantPolicyReviewEffect.CLEAR
    assert decision.shadow_only is False
    assert decision.auto_apply_allowed is True
    assert decision.review_queue_impact == "clear"
    assert decision.action_impact == "none"
    assert run.model_dump(mode="json") == original


def test_pingan_policy_does_not_guess_when_nonproduction_conditions_do_not_match() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(
            category="恶意文件",
            host="service.paic.com.cn",
            status_code=200,
        ),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None
    assert decision.response_posture is TenantPolicyResponsePosture.STANDARD_TRIAGE


def test_pingan_http_200_alone_has_no_tenant_disposition() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=200),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None
    assert decision.recommended_disposition is None
    assert decision.review_effect is TenantPolicyReviewEffect.PRESERVE


def test_pingan_provider_success_abstains_for_policy_skill() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=403, labels={"host_state": "攻击成功"}),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None
    assert decision.recommended_disposition is None
    non_200_rule = next(item for item in decision.rule_evaluations if item.rule_id == "canonical-http-non-200-ignore")
    success_guard = next(item for item in non_200_rule.conditions if item.condition == "classification_label_excluded:host_state")
    assert success_guard.matched is False


def test_pingan_forced_transfer_rule_precedes_non_200_ignore() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=403, rule_code="RPAADM_002267"),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.MATCHED
    assert decision.selected_rule_id == "legacy-forced-transfer-rule-code"
    assert decision.recommended_disposition is SocOperationalDisposition.ESCALATED


def test_pingan_provider_request_failure_is_ignored_even_with_http_200() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=200, labels={"host_state": "请求失败"}),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.MATCHED
    assert decision.selected_rule_id == "provider-confirmed-request-failure-ignore"
    assert decision.recommended_disposition is SocOperationalDisposition.IGNORED
    assert decision.review_effect is TenantPolicyReviewEffect.CLEAR


def test_pingan_attack_attempt_is_not_guessed_as_request_failure() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=200, labels={"host_state": "企图"}),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None


def test_pingan_non_http_status_field_cannot_trigger_http_ignore() -> None:
    run = _run(status_code=None)
    run = run.model_copy(
        update={"input_payload": {**run.input_payload, "status": 403}},
    )

    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        run,
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None


def test_pingan_out_of_range_request_code_is_not_canonical_http_status() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=30001),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None


def test_pingan_mixed_http_statuses_do_not_trigger_non_200_ignore() -> None:
    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=403, observation_status_codes=(200,)),
        environment="dev",
    )

    assert decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decision.selected_rule_id is None


class _PolicyAdviceClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.messages = []

    def complete(self, messages, *, model_name: str):
        self.messages.append(messages)
        return LLMChatResponse(
            content=self.payload,
            model_name=model_name,
            usage={"input_tokens": 100, "output_tokens": 50},
        )


def _request_failure_advice() -> dict:
    return {
        "schema_version": "soc.tenant_policy_advice.v1",
        "evaluation_status": "matched",
        "response_posture": "no_automated_response",
        "recommended_disposition": "ignored",
        "review_effect": "clear",
        "suggested_action": "保留技术检测结论，不执行自动响应。",
        "summary": "当前证据明确记录请求失败，且没有成功或强制转交标记，按请求失败忽略。",
        "rationale": ["当前请求结果字段明确为失败，未观察到执行或影响结果。"],
        "manual_checks": [],
        "policy_signal_keys": ["request_failure"],
        "evidence_refs": ["E-000000000001", "E-000000000010"],
        "reasoning_refs": ["R-01"],
        "context_refs": [],
    }


def _provider_success_advice() -> dict:
    return {
        "schema_version": "soc.tenant_policy_advice.v1",
        "evaluation_status": "matched",
        "response_posture": "manual_validation_required",
        "recommended_disposition": "escalated",
        "review_effect": "require",
        "suggested_action": "结合响应效果与上游成功断言继续转交研判。",
        "summary": "上游成功断言与当前 HTTP 事务需要组合研判，不能由非 200 规则直接忽略。",
        "rationale": ["当前证据同时包含上游成功断言与 HTTP 响应状态。"],
        "manual_checks": ["核对响应正文、会话、命令、文件或进程效果。"],
        "policy_signal_keys": ["provider_success_with_response_context"],
        "evidence_refs": ["E-000000000002", "E-000000000010"],
        "reasoning_refs": ["R-01"],
        "context_refs": [],
    }


def test_policy_skill_handles_provider_success_after_deterministic_abstention() -> None:
    run = _run(status_code=403, labels={"host_state": "攻击成功"})
    client = _PolicyAdviceClient(_provider_success_advice())
    service = SocTenantPolicyEvaluationService(
        policy_resolver=StaticTenantPolicyResolver([load_pingan_tenant_disposition_policy()]),
        repository=InMemoryTenantPolicyDecisionRepository(),
        environment="dev",
        event_timezone="Asia/Shanghai",
        advisor=LLMTenantPolicyAdvisor(
            client=client,
            model_name="deepseek-v4-flash",
            skill_path=PINGAN_TENANT_DISPOSITION_SKILL_PATH,
        ),
    )

    decision = service.evaluate(
        run,
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="soc-daemon", roles=["soc_daemon"]),
        ),
    )

    assert decision is not None
    assert decision.decision_source is TenantPolicyDecisionSource.LLM_POLICY_SKILL
    assert decision.selected_rule_id == "llm-policy-skill-advice"
    assert decision.recommended_disposition is SocOperationalDisposition.ESCALATED
    assert decision.review_effect is TenantPolicyReviewEffect.REQUIRE
    assert decision.advisor_advice is not None
    assert decision.advisor_advice.policy_signal_keys == ["provider_success_with_response_context"]


def test_policy_skill_applies_explicit_request_failure_with_lineage() -> None:
    run = _run(status_code=None, labels={"request_result": "请求失败"})
    client = _PolicyAdviceClient(_request_failure_advice())
    repository = InMemoryTenantPolicyDecisionRepository()
    service = SocTenantPolicyEvaluationService(
        policy_resolver=StaticTenantPolicyResolver([load_pingan_tenant_disposition_policy()]),
        repository=repository,
        environment="dev",
        event_timezone="Asia/Shanghai",
        advisor=LLMTenantPolicyAdvisor(
            client=client,
            model_name="deepseek-v4-flash",
            skill_path=PINGAN_TENANT_DISPOSITION_SKILL_PATH,
        ),
    )

    decision = service.evaluate(
        run,
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="soc-daemon", roles=["soc_daemon"]),
        ),
    )

    assert decision is not None
    assert decision.decision_source is TenantPolicyDecisionSource.LLM_POLICY_SKILL
    assert decision.selected_rule_id == "llm-policy-skill-advice"
    assert decision.recommended_disposition is SocOperationalDisposition.IGNORED
    assert decision.review_effect is TenantPolicyReviewEffect.CLEAR
    assert decision.auto_apply_allowed is True
    assert decision.advisor_advice is not None
    assert decision.advisor_advice.policy_signal_keys == ["request_failure"]
    assert decision.advisor_provenance is not None
    assert decision.advisor_provenance.model_name == "deepseek-v4-flash"
    assert decision.advisor_provenance.skill_version == "v1.2.0"
    assert "HTTP status other than `200`" in client.messages[0][1]["content"]


def test_policy_skill_failure_records_safe_stage_code() -> None:
    payload = _request_failure_advice()
    payload["evidence_refs"] = ["E-FFFFFFFFFFFF"]
    advisor = LLMTenantPolicyAdvisor(
        client=_PolicyAdviceClient(payload),
        model_name="deepseek-v4-flash",
        skill_path=PINGAN_TENANT_DISPOSITION_SKILL_PATH,
    )

    result = advisor.advise(
        load_pingan_tenant_disposition_policy(),
        _run(status_code=None, labels={"request_result": "请求失败"}),
    )

    assert result.provenance.status is TenantPolicyAdvisorStatus.FAILED_CLOSED
    assert result.provenance.error_code == "reference_validation.ValueError"
    assert result.provenance.usage == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert result.provenance.usage_measurement_status == "reported"
    assert result.provenance.usage_is_estimated is False
    assert result.advice.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH


def test_post_analysis_policy_resolvers_skip_failed_runtime_runs() -> None:
    run = _run().model_copy(update={"status": AnalysisRunStatus.FAILED})
    context = ServiceRequestContext(
        actor=ActorContext(actor_id="soc-daemon", roles=["soc_daemon"]),
    )
    tenant_repository = InMemoryTenantPolicyDecisionRepository()
    tenant_service = SocTenantPolicyEvaluationService(
        policy_resolver=StaticTenantPolicyResolver([load_pingan_tenant_disposition_policy()]),
        repository=tenant_repository,
        environment="dev",
        event_timezone="Asia/Shanghai",
    )
    automation_repository = InMemorySocAutomationRepository()
    automation_service = SocAutomationService(
        repository=automation_repository,
        policy=None,
        environment="dev",
        tenant_policy_repository=tenant_repository,
        tenant_policy_application_enabled=True,
    )

    tenant_service.observe(run, context=context)
    automation_service.observe(run, context=context)

    assert tenant_repository.list_tenant_policy_decisions(run_id=run.run_id) == []
    assert automation_repository.list_decision_transitions(run_id=run.run_id) == []


def test_composition_root_applies_enabled_policy_skill_before_effective_decision(
    monkeypatch,
) -> None:
    repository = _sql_repository()
    run = _run(status_code=None, labels={"request_result": "请求失败"})
    repository.save_run(run)
    client = _PolicyAdviceClient(_request_failure_advice())
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "true")
    monkeypatch.setenv(
        "SOC_TENANT_DISPOSITION_POLICY_PATH",
        str(PINGAN_TENANT_DISPOSITION_POLICY_PATH),
    )
    monkeypatch.setenv("SOC_TENANT_POLICY_ENVIRONMENT", "dev")
    monkeypatch.setenv("SOC_TENANT_POLICY_EVENT_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("SOC_TENANT_POLICY_ADVISOR_MODE", "llm")
    monkeypatch.setenv(
        "SOC_TENANT_POLICY_SKILL_PATH",
        str(PINGAN_TENANT_DISPOSITION_SKILL_PATH),
    )
    for name in (
        "SOC_AUTOMATION_POLICY_PATH",
        "SOC_AUTOMATION_ENVIRONMENT",
        "SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        analysis_application,
        "build_configured_chat_client",
        lambda **_kwargs: (client, "deepseek-v4-flash"),
    )

    observers = analysis_application._build_post_analysis_observers(
        repository,
        settings=SocLLMSettings(),
    )
    context = ServiceRequestContext(
        actor=ActorContext(actor_id="soc-daemon", roles=["soc_daemon"]),
    )
    for observer in observers:
        observer.observe(run, context=context)

    decisions = repository.list_tenant_policy_decisions(run_id=run.run_id)
    transitions = repository.list_decision_transitions(run_id=run.run_id)
    assert len(observers) == 3
    assert len(decisions) == 1
    assert decisions[0].decision_source is TenantPolicyDecisionSource.LLM_POLICY_SKILL
    assert len(transitions) == 1
    assert transitions[0].stages[2].status is SocDecisionStageStatus.APPLIED
    assert transitions[0].effective_disposition is SocOperationalDisposition.IGNORED
    assert repository.list_action_authorizations(run_id=run.run_id) == []
    assert repository.list_action_executions(run_id=run.run_id) == []


def test_bounded_policy_requires_alert_event_time() -> None:
    policy = load_pingan_tenant_disposition_policy().model_copy(update={"effective_from": datetime(2026, 1, 1, tzinfo=UTC)})

    with pytest.raises(
        TenantPolicyNotApplicableError,
        match="requires alert event time",
    ):
        evaluate_tenant_policy(policy, _run(), environment="dev")


def test_authorization_condition_requires_exact_governed_match() -> None:
    with pytest.raises(ValueError, match="exact authorization"):
        TenantDispositionRule.model_validate(
            {
                "rule_id": "invalid-auth-disposition",
                "name": "Invalid direct authorization disposition",
                "match": {"authorization_statuses": ["partial"]},
                "recommendation": {
                    "response_posture": "no_automated_response",
                    "recommended_disposition": "closed_benign_true_positive",
                    "summary": "Invalid direct recommendation.",
                    "rationale": ["Partial authorization cannot close an alert."],
                },
            }
        )


def test_exact_authorization_produces_independent_operational_close() -> None:
    run = _run()

    decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        run,
        environment="dev",
        authorization_result=_exact_authorization(run.alert_id),
    )

    assert decision.selected_rule_id == "authorized-activity-operational-close"
    assert decision.detection_truth.verdict is Verdict.SUSPICIOUS
    assert decision.recommended_disposition is SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE
    assert decision.review_effect is TenantPolicyReviewEffect.CLEAR
    assert decision.auto_apply_allowed is True


def test_effective_decision_lineage_records_base_memory_tenant_and_final() -> None:
    run = _run()
    tenant_repository = InMemoryTenantPolicyDecisionRepository()
    tenant_decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        run,
        environment="dev",
        authorization_result=_exact_authorization(run.alert_id),
    )
    tenant_repository.save_tenant_policy_decision(tenant_decision)

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=None,
        environment="dev",
        tenant_policy_repository=tenant_repository,
        tenant_policy_application_enabled=True,
    ).evaluate(
        run,
        context=ServiceRequestContext(
            actor=ActorContext(actor_id="soc-daemon", roles=["soc_daemon"]),
        ),
    )

    transition = result.decision_transition
    assert [stage.stage for stage in transition.stages] == [
        SocDecisionStageKind.BASE,
        SocDecisionStageKind.MEMORY,
        SocDecisionStageKind.TENANT_POLICY,
        SocDecisionStageKind.EFFECTIVE,
    ]
    assert transition.stages[1].status is SocDecisionStageStatus.NO_INPUT
    assert transition.stages[2].status is SocDecisionStageStatus.APPLIED
    assert transition.before.verdict is Verdict.SUSPICIOUS
    assert transition.after.verdict is Verdict.SUSPICIOUS
    assert transition.before.needs_review is True
    assert transition.after.needs_review is False
    assert transition.effective_disposition is SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE
    assert result.tenant_policy_decision_id == tenant_decision.decision_id
    assert result.authorization is None
    assert result.execution is None


def test_tenant_policy_master_switch_keeps_persisted_decision_inert() -> None:
    run = _run()
    tenant_repository = InMemoryTenantPolicyDecisionRepository()
    tenant_decision = evaluate_tenant_policy(
        load_pingan_tenant_disposition_policy(),
        run,
        environment="dev",
        authorization_result=_exact_authorization(run.alert_id),
    )
    tenant_repository.save_tenant_policy_decision(tenant_decision)

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=None,
        environment="dev",
        tenant_policy_repository=tenant_repository,
        tenant_policy_application_enabled=False,
    ).evaluate(run, context=ServiceRequestContext())

    tenant_stage = result.decision_transition.stages[2]
    assert tenant_stage.status is SocDecisionStageStatus.DISABLED
    assert result.decision_transition.after == result.decision_transition.before
    assert result.effective_disposition is None
    assert result.tenant_policy_decision_id is None


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
    run = _run(status_code=200)
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
    assert decisions[0].evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
    assert decisions[0].decision_source is TenantPolicyDecisionSource.NO_MATCH
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
    repository.save_run(_run(status_code=200))

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
    assert '"evaluation_status":"no_match"' in evaluated

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
