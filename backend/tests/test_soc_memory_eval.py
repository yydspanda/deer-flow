from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from soc_agent.application import build_soc_memory_profile_registry
from soc_agent.cli import main
from soc_agent.contracts import (
    SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION,
    ActorContext,
    ActorType,
    AdjudicatedRoleType,
    AlertClassification,
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    ConfidenceLabelReviewSource,
    ConfidenceLabelReviewStatus,
    Decision,
    DecisionEvidenceState,
    DetectionRuleRef,
    EntityKind,
    EntityMention,
    EntrySurface,
    EvidenceItem,
    ExtractedEntities,
    FactReconstructionResult,
    HttpEntityRef,
    LLMAnalysisRequest,
    NetworkBoundaryDirection,
    NetworkEntityRef,
    ScenarioHypothesis,
    SocEvaluationDataClass,
    SocMemoryApplicabilitySpec,
    SocMemoryBusinessLesson,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionDirective,
    SocMemoryDecisionEffect,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    SocMemoryReviewEffect,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.eval.memory import (
    DEFAULT_MEMORY_EVAL_FIXTURE,
    MemoryEvalCaseFixture,
    MemoryEvalHumanTruth,
    MemoryEvalPredictionSnapshot,
    MemoryEvalRecordFixture,
    MemoryEvalRelationship,
    MemoryEvalRoleLabel,
    MemoryEvalVerifierOutcome,
    MemoryHeldOutEvalFixture,
    build_memory_eval_fixture,
    load_memory_eval_fixture,
    run_memory_eval,
)
from soc_agent.memory import render_memory_business_lesson
from soc_agent.utils.hashing import stable_hash

_NOW = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
_ASKBOB_URL = "https://paic.com.cn/pws/askbob-gpt"
_ASKBOB_ENTITY_KEY = f"url:{_ASKBOB_URL}"
_ASKBOB_LESSON = SocMemoryBusinessLesson(
    conclusion="该模式是平安内部 AskBob LLM 服务调用，不是真实反弹 Shell。",
    business_rationale=["canonical URL 为 https://paic.com.cn/pws/askbob-gpt，并命中经审核的内部系统知识；检测规则真实触发，但该业务通信不代表主机被控。"],
    applicability_conditions=["PRD 环境、相同 rule_code 与检测器签名、相同强行为指纹，并且当前告警包含完全一致的 AskBob canonical URL 实体。"],
    generalization_boundaries=["源和目的 IP 可以变化，IP 不是该业务服务身份的一部分。"],
    invalidation_conditions=["URL 不同或缺失、目标为未知或外部服务、行为指纹变化，或者出现真实 Shell 进程、命令执行、恶意载荷及其他反证。"],
    handling_guidance=["只有全部必需条件满足时才复用 false_positive；否则仅作参考或完全不召回，并按当前告警重新研判。"],
)
_ASKBOB_MEMORY_SUMMARY = _ASKBOB_LESSON.conclusion
_ASKBOB_MEMORY_CONTENT = render_memory_business_lesson(_ASKBOB_LESSON)
_WEB_LESSON = SocMemoryBusinessLesson(
    conclusion="该模式与已审核的 Webshell 上传攻击一致，应保持 suspicious 风险结论。",
    business_rationale=["运营专家审核了重复出现的上传行为和检测证据。"],
    applicability_conditions=["相同检测规则、检测器签名、强行为指纹和 PRD 环境。"],
    generalization_boundaries=["非必需实体可以变化，但不能单独证明模式适用。"],
    invalidation_conditions=["检测器或行为变化时重新研判，不得仅凭规则名称复制结论。"],
    handling_guidance=["精确适用时保持 suspicious；否则按当前告警重新研判。"],
)


def _request(
    alert_id: str,
    *,
    scenario: str,
    techniques: list[str],
    rule_name: str = "Reverse connection detector",
    service_url: str | None = None,
) -> LLMAnalysisRequest:
    parsed_url = urlsplit(service_url) if service_url else None
    service_host = parsed_url.hostname if parsed_url is not None else None
    service_path = parsed_url.path if parsed_url is not None else None
    mentions = []
    if service_host:
        mentions.append(
            EntityMention(
                kind=EntityKind.DOMAIN,
                value=service_host,
                key=f"domain:{service_host}",
                role="http_host",
            )
        )
    if service_url:
        normalized_url = service_url.casefold()
        mentions.append(
            EntityMention(
                kind=EntityKind.URL,
                value=normalized_url,
                key=f"url:{normalized_url}",
                role="http_url",
            )
        )
    return LLMAnalysisRequest(
        alert_id=alert_id,
        tenant_id="pingan",
        environment="prd",
        source=AlertSourceRef(
            source_type=AlertSourceType.NIDS,
            source_system="ptp-nids",
            vendor="pingan",
            product="ndr",
            integration_name="pingan_legacy_alert_platform",
        ),
        detection=DetectionRuleRef(
            rule_code="rpaadm_002638",
            rule_name=rule_name,
            detection_key="ptp-nids:rule_code:rpaadm_002638",
        ),
        classification=AlertClassification(
            category="command_and_control",
            severity="high",
            technique=techniques,
        ),
        canonical_entities=AlertEntitySet(
            network=NetworkEntityRef(
                protocol="tcp",
                domain=service_host,
                url=service_url,
            ),
            http=HttpEntityRef(
                host=service_host,
                path=service_path,
                url=service_url,
            ),
        ),
        extracted_entities=ExtractedEntities(mentions=mentions),
        fact_reconstruction=FactReconstructionResult(
            scenario_hypotheses=[
                ScenarioHypothesis(
                    scenario_type=scenario,
                    status="confirmed",
                    confidence=0.9,
                    rationale="Simulation fixture scenario.",
                )
            ]
        ),
    )


def _actor() -> ActorContext:
    return ActorContext(
        actor_id="memory-eval-fixture",
        actor_type=ActorType.USER,
        surface=EntrySurface.TEST,
        roles=["soc_memory_reviewer"],
    )


def _record(
    memory_id: str,
    *,
    source_alert_id: str,
    request: LLMAnalysisRequest,
    verdict: Verdict,
    summary: str,
    content: str,
    business_lesson: SocMemoryBusinessLesson,
    directive_rationale: str,
    required_entity_key: str | None = None,
) -> SocMemoryRecord:
    profile = build_soc_memory_profile_registry().resolve_request(request)
    facets = profile.project_query_facets(request)
    applicability = profile.build_applicability(
        consensus_facets=facets,
        strong_anchor_facets=facets,
    )
    assert applicability is not None
    if required_entity_key is not None:
        if required_entity_key not in facets.get("entity", []):
            raise AssertionError("required Memory entity is absent from canonical query facets")
        applicability_payload = applicability.model_dump(mode="json")
        required_facets = dict(applicability.required_facets)
        required_facets["entity"] = [required_entity_key]
        optional_facets = dict(applicability.optional_facets)
        # Applicability groups are key-disjoint. Requiring one reviewed URL
        # intentionally removes all broader domain/entity optional matches.
        optional_facets.pop("entity", None)
        applicability_payload.update(
            {
                "required_facets": required_facets,
                "optional_facets": optional_facets,
                "context_only_required_facet_keys": sorted(
                    {
                        *applicability.context_only_required_facet_keys,
                        "entity",
                    }
                ),
            }
        )
        applicability = SocMemoryApplicabilitySpec.model_validate(applicability_payload)
    return SocMemoryRecord(
        memory_id=memory_id,
        memory_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        tenant_scope="pingan",
        tenant_id="pingan",
        source_candidate_id=f"MC-{memory_id}",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.EVAL_FIXTURE,
            source_id=f"fixture:{memory_id}",
            alert_id=source_alert_id,
            metadata={"source_alert_ids": [source_alert_id]},
        ),
        summary=summary,
        content=content,
        business_lesson=business_lesson,
        facets=facets,
        applicability=applicability,
        evidence_refs=[f"E-{memory_id[-12:].upper():0>12}"],
        validity=SocMemoryCandidateValidity(
            valid_from=_NOW - timedelta(days=1),
            valid_until=_NOW + timedelta(days=90),
            review_after_days=30,
            notes="Simulation-only Memory evaluation validity window.",
        ),
        confidence=0.95,
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        decision_directive=SocMemoryDecisionDirective(
            effect=SocMemoryDecisionEffect.OVERRIDE,
            target_verdict=verdict,
            review_effect=SocMemoryReviewEffect.CLEAR,
            suggested_action="reuse the reviewed business conclusion only within its exact scope",
            minimum_match_score=5.0,
            required_facet_keys=sorted(applicability.required_facets),
            rationale=directive_rationale,
        ),
        content_hash=f"sha256:{stable_hash(content)}",
        facets_hash=f"sha256:{stable_hash(facets)}",
        retrieval_enabled=True,
        retrieval_policy_version=SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION,
        retrieval_valid_until=_NOW + timedelta(days=30),
        retrieval_review_due_at=_NOW + timedelta(days=7),
        retrieval_updated_by=_actor(),
        retrieval_updated_at=_NOW - timedelta(hours=1),
        retrieval_reason="Simulation fixture activation.",
        created_by=_actor(),
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW - timedelta(hours=1),
        metadata={
            "memory_profile_id": profile.identity.profile_id,
            "memory_profile_version": profile.identity.profile_version,
            "memory_feature_schema_version": profile.identity.feature_schema_version,
            "source_alert_ids": [source_alert_id],
        },
    )


def _decision(*, verdict: Verdict, needs_review: bool) -> Decision:
    return Decision(
        verdict=verdict,
        confidence=0.72,
        evidence_state=DecisionEvidenceState.SUFFICIENT,
        suggested_action="review current alert",
        needs_review=needs_review,
        reason="Simulation base decision.",
    )


def _analysis_run(request: LLMAnalysisRequest) -> AnalysisRun:
    evidence_ref = "E-000000000001"
    return AnalysisRun(
        run_id=f"RUN-{request.alert_id}",
        alert_id=request.alert_id,
        status=AnalysisRunStatus.NEEDS_REVIEW,
        model_name="simulation-model",
        prompt_version="simulation-prompt-v1",
        input_hash=stable_hash({"alert_id": request.alert_id}),
        llm_analysis_request=request,
        analysis=AnalysisResult(
            verdict=Verdict.SUSPICIOUS,
            confidence=0.72,
            summary="Simulation analyzer result for Memory fixture preparation.",
            evidence=[
                EvidenceItem(
                    evidence_ref=evidence_ref,
                    source="simulation",
                    description="Simulation detector hit.",
                    value=request.alert_id,
                )
            ],
            reasoning=[
                AnalysisReasoningItem(
                    reasoning_id="R-01",
                    statement="Simulation reasoning for fixture preparation.",
                    basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                    evidence_refs=[evidence_ref],
                    confidence=0.72,
                )
            ],
            decision_evidence_refs=[evidence_ref],
            decision_reasoning_refs=["R-01"],
            reason="Simulation result must still receive independent review.",
            recommended_action="review",
        ),
        decision=_decision(verdict=Verdict.SUSPICIOUS, needs_review=True),
    )


def _truth(
    *,
    verdict: Verdict,
    scenario: str,
    review_required: bool,
    relationships: dict[str, MemoryEvalRelationship],
) -> MemoryEvalHumanTruth:
    return MemoryEvalHumanTruth(
        review_status=ConfidenceLabelReviewStatus.ACCEPTED,
        review_source=ConfidenceLabelReviewSource.SIMULATION_FIXTURE,
        reviewer_id="simulation-reviewer",
        reviewed_at=_NOW,
        review_reason="Deterministic simulation label for evaluator wiring.",
        actual_verdict=verdict,
        actual_scenario_keys=[scenario],
        actual_boundary_direction=NetworkBoundaryDirection.INTERNAL_TO_INTERNAL,
        actual_roles=[
            MemoryEvalRoleLabel(
                role=AdjudicatedRoleType.ATTACKER,
                entity_type="ip",
                value="30.20.20.20",
            ),
            MemoryEvalRoleLabel(
                role=AdjudicatedRoleType.VICTIM,
                entity_type="ip",
                value="30.10.10.10",
            ),
        ],
        expected_review_required=review_required,
        expected_memory_relationships=relationships,
    )


def _prediction(
    scenario: str,
    *,
    verifier_outcome: MemoryEvalVerifierOutcome,
) -> MemoryEvalPredictionSnapshot:
    return MemoryEvalPredictionSnapshot(
        scenario_keys=[scenario],
        boundary_direction=NetworkBoundaryDirection.INTERNAL_TO_INTERNAL,
        roles=[
            MemoryEvalRoleLabel(
                role=AdjudicatedRoleType.ATTACKER,
                entity_type="ip",
                value="30.20.20.20",
            ),
            MemoryEvalRoleLabel(
                role=AdjudicatedRoleType.VICTIM,
                entity_type="ip",
                value="30.10.10.10",
            ),
        ],
        verifier_outcome=verifier_outcome,
        verifier_failure_kind=("provider_error" if verifier_outcome is MemoryEvalVerifierOutcome.UNAVAILABLE else None),
    )


def _fixture() -> MemoryHeldOutEvalFixture:
    shell_training = _request(
        "TRAIN-SHELL",
        scenario="reverse_shell",
        techniques=["T1059", "T1071"],
        service_url=_ASKBOB_URL,
    )
    web_training = _request(
        "TRAIN-WEB",
        scenario="webshell",
        techniques=["T1505.003", "T1190"],
        rule_name="Webshell upload detector",
    )
    shell_record = _record(
        "MEM-SHELL-0001",
        source_alert_id="TRAIN-SHELL",
        request=shell_training,
        verdict=Verdict.FALSE_POSITIVE,
        summary=_ASKBOB_MEMORY_SUMMARY,
        content=_ASKBOB_MEMORY_CONTENT,
        business_lesson=_ASKBOB_LESSON,
        directive_rationale=("运营专家确认该精确检测、行为和 AskBob 服务身份组合属于内部 LLM 调用误报；该授权不覆盖其他 URL、服务或行为。"),
        required_entity_key=_ASKBOB_ENTITY_KEY,
    )
    web_record = _record(
        "MEM-WEB-000001",
        source_alert_id="TRAIN-WEB",
        request=web_training,
        verdict=Verdict.SUSPICIOUS,
        summary=_WEB_LESSON.conclusion,
        content=render_memory_business_lesson(_WEB_LESSON),
        business_lesson=_WEB_LESSON,
        directive_rationale="仿真审核员确认该精确 Webshell 行为模式应维持风险结论。",
    )
    exact_request = _request(
        "HELDOUT-SHELL-CROSS-IP",
        scenario="reverse_shell",
        techniques=["T1059", "T1071"],
        service_url=_ASKBOB_URL,
    )
    context_request = _request(
        "HELDOUT-SHELL-CONTEXT",
        scenario="command_execution",
        techniques=["T1059", "T1105"],
        service_url=_ASKBOB_URL,
    )
    different_service_request = _request(
        "HELDOUT-SHELL-DIFFERENT-SERVICE",
        scenario="reverse_shell",
        techniques=["T1059", "T1071"],
        service_url="https://unreviewed.example/pws/askbob-gpt",
    )
    relationships_exact = {
        shell_record.memory_id: MemoryEvalRelationship.DECISION_APPLICABLE,
        web_record.memory_id: MemoryEvalRelationship.UNRELATED,
    }
    relationships_context = {
        shell_record.memory_id: MemoryEvalRelationship.CONTEXT_ONLY,
        web_record.memory_id: MemoryEvalRelationship.UNRELATED,
    }
    relationships_different_service = {
        shell_record.memory_id: MemoryEvalRelationship.UNRELATED,
        web_record.memory_id: MemoryEvalRelationship.UNRELATED,
    }
    return MemoryHeldOutEvalFixture(
        fixture_set_id="pingan-memory-profile-v4-simulation",
        description=("Simulation-only exact, context-only, and different-service rejection Memory evaluation."),
        data_class=SocEvaluationDataClass.SIMULATION,
        mocked=True,
        tenant_id="pingan",
        environment="prd",
        memory_profile_id="pingan.soc",
        memory_profile_version="4",
        memory_feature_schema_version="pingan.soc.memory_features.v4",
        evaluated_at=_NOW,
        source_refs=["backend/tests/test_soc_memory_eval.py"],
        records=[
            MemoryEvalRecordFixture(
                record=shell_record,
                source_alert_ids=["TRAIN-SHELL"],
            ),
            MemoryEvalRecordFixture(
                record=web_record,
                source_alert_ids=["TRAIN-WEB"],
            ),
        ],
        cases=[
            MemoryEvalCaseFixture(
                case_id="exact-cross-ip",
                run_id="RUN-HELDOUT-EXACT",
                input_hash="a" * 64,
                request=exact_request,
                base_decision=_decision(
                    verdict=Verdict.SUSPICIOUS,
                    needs_review=True,
                ),
                prediction=_prediction(
                    "reverse_shell",
                    verifier_outcome=MemoryEvalVerifierOutcome.NOT_TRIGGERED,
                ),
                truth=_truth(
                    verdict=Verdict.FALSE_POSITIVE,
                    scenario="reverse_shell",
                    review_required=False,
                    relationships=relationships_exact,
                ),
            ),
            MemoryEvalCaseFixture(
                case_id="strong-context-only",
                run_id="RUN-HELDOUT-CONTEXT",
                input_hash="b" * 64,
                request=context_request,
                base_decision=_decision(
                    verdict=Verdict.SUSPICIOUS,
                    needs_review=False,
                ),
                prediction=_prediction(
                    "command_execution",
                    verifier_outcome=MemoryEvalVerifierOutcome.UNAVAILABLE,
                ),
                truth=_truth(
                    verdict=Verdict.SUSPICIOUS,
                    scenario="command_execution",
                    review_required=False,
                    relationships=relationships_context,
                ),
            ),
            MemoryEvalCaseFixture(
                case_id="same-pattern-different-service",
                run_id="RUN-HELDOUT-DIFFERENT-SERVICE",
                input_hash="c" * 64,
                request=different_service_request,
                base_decision=_decision(
                    verdict=Verdict.SUSPICIOUS,
                    needs_review=False,
                ),
                prediction=_prediction(
                    "reverse_shell",
                    verifier_outcome=MemoryEvalVerifierOutcome.NOT_TRIGGERED,
                ),
                truth=_truth(
                    verdict=Verdict.SUSPICIOUS,
                    scenario="reverse_shell",
                    review_required=False,
                    relationships=relationships_different_service,
                ),
            ),
        ],
    )


def test_memory_eval_replays_retrieval_decision_and_burden() -> None:
    report = run_memory_eval(_fixture())

    assert report.integrity_passed is True
    assert report.evaluation_status == "complete_simulation"
    assert report.retrieval_metrics.precision == 1.0
    assert report.retrieval_metrics.recall == 1.0
    assert report.pattern_lesson_metrics.precision == 1.0
    assert report.pattern_lesson_metrics.recall == 1.0
    assert report.directive_eligibility_metrics.precision == 1.0
    assert report.directive_eligibility_metrics.recall == 1.0
    assert report.base_verdict_accuracy.accuracy == pytest.approx(2 / 3)
    assert report.effective_verdict_accuracy.accuracy == 1.0
    assert report.directive_override_accuracy.accuracy == 1.0
    assert report.review_burden.review_reduction_count == 1
    assert report.review_burden.unsafe_review_clear_count == 0
    assert report.scenario_accuracy.accuracy == 1.0
    assert report.boundary_direction_accuracy.accuracy == 1.0
    assert report.role_accuracy.accuracy == 1.0
    assert report.verifier_metrics.triggered_count == 1
    assert report.verifier_metrics.failed_count == 1
    assert report.verifier_metrics.failure_rate == 1.0
    exact, context, different_service = report.results
    assert exact.retrieved_memory_ids == ["MEM-SHELL-0001"]
    assert exact.directive_applicable_memory_ids == ["MEM-SHELL-0001"]
    assert exact.base_verdict is Verdict.SUSPICIOUS
    assert exact.effective_verdict is Verdict.FALSE_POSITIVE
    assert context.retrieved_memory_ids == ["MEM-SHELL-0001"]
    assert context.context_only_memory_ids == ["MEM-SHELL-0001"]
    assert context.directive_applicable_memory_ids == []
    assert context.effective_verdict is Verdict.SUSPICIOUS
    assert different_service.retrieved_memory_ids == []
    assert different_service.directive_applicable_memory_ids == []
    assert different_service.effective_verdict is Verdict.SUSPICIOUS


def test_default_memory_eval_fixture_is_the_reviewed_simulation_baseline() -> None:
    fixture = load_memory_eval_fixture(DEFAULT_MEMORY_EVAL_FIXTURE)

    report = run_memory_eval(fixture)

    assert fixture.fixture_set_id == "pingan-memory-profile-v4-simulation"
    assert fixture.data_class is SocEvaluationDataClass.SIMULATION
    shell_record = fixture.records[0].record
    assert "平安内部 AskBob LLM 服务调用" in shell_record.content
    assert shell_record.applicability is not None
    assert shell_record.applicability.required_facets["entity"] == [_ASKBOB_ENTITY_KEY]
    assert report.retrieval_metrics.precision == 1.0
    assert report.directive_override_accuracy.accuracy == 1.0
    assert report.real_quality_metrics_available is False
    assert report.rollout_authorized is False


def test_memory_eval_rejects_train_and_heldout_overlap() -> None:
    fixture = _fixture()
    overlapping = fixture.cases[0].model_copy(update={"request": fixture.cases[0].request.model_copy(update={"alert_id": "TRAIN-SHELL"})})

    with pytest.raises(ValidationError, match="overlap"):
        MemoryHeldOutEvalFixture.model_validate(fixture.model_copy(update={"cases": [overlapping]}).model_dump(mode="json"))


def test_memory_eval_pending_labels_do_not_report_accuracy() -> None:
    fixture = _fixture()
    pending_case = fixture.cases[0].model_copy(update={"truth": MemoryEvalHumanTruth()})
    pending_fixture = fixture.model_copy(update={"cases": [pending_case]})

    report = run_memory_eval(pending_fixture)

    assert report.evaluation_status == "blocked_pending_labels"
    assert report.accepted_count == 0
    assert report.pending_count == 1
    assert report.retrieval_metrics.precision is None
    assert report.base_verdict_accuracy.accuracy is None
    assert report.real_quality_metrics_available is False


def test_cli_eval_memory_run_writes_replayable_report(tmp_path, capsys) -> None:
    fixture_path = tmp_path / "memory-eval-fixture.json"
    report_path = tmp_path / "memory-eval-report.json"
    fixture_path.write_text(
        _fixture().model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "eval",
            "memory",
            "run",
            str(fixture_path),
            "--output",
            str(report_path),
            "--pretty",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert output == persisted
    assert output["schema_version"] == "soc.memory_heldout_eval_report.v1"
    assert output["evaluation_status"] == "complete_simulation"
    assert output["rollout_authorized"] is False


def test_cli_eval_memory_prepare_creates_pending_fixture(tmp_path, capsys) -> None:
    source_fixture = _fixture()
    run_path = tmp_path / "heldout-run.json"
    records_path = tmp_path / "memory-records.json"
    output_path = tmp_path / "pending-memory-eval.json"
    request = _request(
        "HELDOUT-PREPARE",
        scenario="reverse_shell",
        techniques=["T1059", "T1071"],
    )
    run_path.write_text(
        _analysis_run(request).model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    records_path.write_text(
        json.dumps(
            {"records": [item.record.model_dump(mode="json", exclude_none=True) for item in source_fixture.records]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "eval",
            "memory",
            "prepare",
            str(run_path),
            "--memory-records",
            str(records_path),
            "--description",
            "Pending simulation review fixture.",
            "--tenant-id",
            "pingan",
            "--environment",
            "prd",
            "--data-class",
            "simulation",
            "--source-ref",
            "simulation:test",
            "--evaluated-at",
            _NOW.isoformat(),
            "--output",
            str(output_path),
            "--pretty",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    output = json.loads(captured.out)
    assert output == json.loads(output_path.read_text(encoding="utf-8"))
    assert output["cases"][0]["truth"]["review_status"] == "pending_review"
    assert output["cases"][0]["request"]["alert_id"] == "HELDOUT-PREPARE"
    assert output["rollout_authorized"] is False


@pytest.mark.parametrize(
    ("request_update", "error"),
    [
        ({"tenant_id": "another-tenant"}, "tenant conflicts"),
        ({"environment": "stg"}, "environment conflicts"),
    ],
)
def test_memory_eval_prepare_rejects_frozen_scope_conflicts(
    request_update: dict[str, str],
    error: str,
) -> None:
    fixture = _fixture()
    request = _request(
        "HELDOUT-SCOPE-CONFLICT",
        scenario="reverse_shell",
        techniques=["T1059", "T1071"],
    ).model_copy(update=request_update)

    with pytest.raises(ValueError, match=error):
        build_memory_eval_fixture(
            [("heldout.json", _analysis_run(request))],
            [item.record for item in fixture.records],
            fixture_set_id=None,
            description="Scope conflict must fail closed.",
            data_class=SocEvaluationDataClass.SIMULATION,
            tenant_id="pingan",
            environment="prd",
            source_refs=["simulation:test"],
            evaluated_at=_NOW,
        )
