from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    AnalysisEvidenceCatalogItem,
    AnalysisNodeOutput,
    AnalysisProviderPurpose,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisRequestJournalStatus,
    AnalysisResult,
    DecisionEvidenceState,
    DecisionReviewReason,
    EvidenceItem,
    EvidenceTrustLevel,
    LLMAnalysisRequest,
    NetworkBoundaryDirection,
    NetworkDirectionAssessment,
    NetworkDirectionAssessmentStatus,
    RoleAdjudicationResult,
    RoleAdjudicationStatus,
    RoleAdjudicationVerificationResult,
    RoleVerificationCandidate,
    RoleVerificationClaimReview,
    RoleVerificationClaimStatus,
    RoleVerificationNodeOutput,
    RoleVerificationStatus,
    RoleVerificationTriggerDecision,
    RuntimeFailureKind,
    TriageActivityStage,
    TriageScenarioAssessment,
    TriageScenarioOrigin,
    Verdict,
)
from soc_agent.core.runtime import analyze_alert, build_analysis_request_for_payload
from soc_agent.core.service import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.llm import (
    ROLE_VERIFICATION_JSON_PARSER_VERSION,
    JsonLLMRoleVerifier,
    LLMChatResponse,
    LLMOutputParseError,
    parse_role_verification_output,
)
from soc_agent.pipeline.evidence_grounding import ground_analysis_evidence
from soc_agent.pipeline.role_verification import (
    build_role_verification_claims,
    evaluate_role_verification_trigger,
)
from soc_agent.prompts import (
    ROLE_VERIFICATION_PROMPT_VERSION,
    build_role_verification_prompt,
)


def _payload() -> dict:
    return {
        "alert_id": "ROLE-VERIFY-001",
        "source": {
            "source_type": "nids",
            "source_system": "role-verifier-test",
        },
        "detection": {
            "rule_code": "NIDS-REVERSE-001",
            "rule_name": "Reverse connection behavior",
        },
        "entities": {
            "network": {
                "source_ip": "10.20.30.40",
                "destination_ip": "198.51.100.20",
                "protocol": "tcp",
                "dst_port": 4444,
            }
        },
        "classification": {
            "category": "network_connection",
            "severity": "high",
        },
    }


def _fact(request: LLMAnalysisRequest, value: object):
    return next(item for item in request.evidence_catalog if item.value == value)


def _analysis_for_request(
    request: LLMAnalysisRequest,
    *,
    include_target: bool = True,
    marker: str = "PRIMARY-RATIONALE-MUST-NOT-LEAK",
) -> AnalysisResult:
    source = _fact(request, "10.20.30.40")
    destination = _fact(request, "198.51.100.20")
    selected_evidence = [
        EvidenceItem(
            evidence_ref=source.evidence_ref,
            source=source.source_path,
            description="Observed source endpoint",
            value=source.value,
        ),
        EvidenceItem(
            evidence_ref=destination.evidence_ref,
            source=destination.source_path,
            description="Observed destination endpoint",
            value=destination.value,
        ),
    ]
    reasoning = AnalysisReasoningItem(
        reasoning_id="R-01",
        statement="The bounded flow is consistent with a reverse connection hypothesis.",
        basis=[
            AnalysisReasoningBasis.CURRENT_EVIDENCE,
            AnalysisReasoningBasis.GENERAL_SECURITY_KNOWLEDGE,
        ],
        evidence_refs=[source.evidence_ref, destination.evidence_ref],
        context_refs=[],
        confidence=0.91,
    )
    roles = [
        {
            "role": "victim",
            "entity_type": "ip",
            "value": "10.20.30.40",
            "status": "resolved_from_evidence",
            "confidence": 0.91,
            "evidence_refs": [source.evidence_ref],
            "reasoning_refs": ["R-01"],
            "context_refs": [],
            "rationale": marker,
        },
        {
            "role": "attacker",
            "entity_type": "ip",
            "value": "198.51.100.20",
            "status": "resolved_from_evidence",
            "confidence": 0.9,
            "evidence_refs": [destination.evidence_ref],
            "reasoning_refs": ["R-01"],
            "context_refs": [],
            "rationale": marker,
        },
    ]
    proposals = []
    if include_target:
        proposals.append(
            {
                "proposal_id": "RT-01",
                "action_kind": "block_ip",
                "target_type": "ip",
                "target_value": "198.51.100.20",
                "target_role": "attacker",
                "confidence": 0.9,
                "evidence_refs": [destination.evidence_ref],
                "reasoning_refs": ["R-01"],
                "context_refs": [],
                "rationale": marker,
                "policy_review_required": True,
                "automation_allowed": False,
            }
        )
    return AnalysisResult(
        verdict=Verdict.SUSPICIOUS,
        confidence=0.9,
        summary="Reverse connection requires role-aware handling.",
        evidence=selected_evidence,
        reasoning=[reasoning],
        scenario_assessments=[
            TriageScenarioAssessment(
                scenario_name="reverse connection",
                scenario_key="reverse_connection",
                is_primary=True,
                origin=TriageScenarioOrigin.INFERRED,
                confidence=0.9,
                activity_stage=TriageActivityStage.ATTEMPT_OBSERVED,
                evidence_refs=[source.evidence_ref, destination.evidence_ref],
                reasoning_refs=["R-01"],
                rationale=marker,
            )
        ],
        network_direction=NetworkDirectionAssessment(
            status=NetworkDirectionAssessmentStatus.INFERRED,
            observed_flow="source_to_destination",
            boundary_direction=NetworkBoundaryDirection.INTERNAL_TO_EXTERNAL,
            semantic_direction="victim_to_attacker_reverse_connection",
            connection_initiator="10.20.30.40",
            confidence=0.9,
            evidence_refs=[source.evidence_ref, destination.evidence_ref],
            reasoning_refs=["R-01"],
            rationale=marker,
        ),
        role_adjudication=RoleAdjudicationResult.model_validate(
            {
                "status": RoleAdjudicationStatus.RESOLVED_FROM_EVIDENCE,
                "roles": roles,
                "response_target_proposals": proposals,
                "conflicts": [],
                "evidence_gaps": [],
                "rationale": marker,
            }
        ),
        manual_checks=["核对连接发起端与终端进程上下文。"],
        reason=marker,
        recommended_action="review proposed network block",
    )


def _request_and_analysis(
    *,
    include_target: bool = True,
    trigger_verification: bool = True,
):
    request = build_analysis_request_for_payload(_payload())
    analysis = _analysis_for_request(request, include_target=include_target)
    if trigger_verification:
        analysis = analysis.model_copy(update={"network_direction": analysis.network_direction.model_copy(update={"status": NetworkDirectionAssessmentStatus.CONFLICTED})})
    grounding = ground_analysis_evidence(analysis, request)
    return request, analysis, grounding


def _supported_candidate(
    request: LLMAnalysisRequest,
    analysis: AnalysisResult,
) -> RoleVerificationCandidate:
    evidence_ref = _fact(request, "10.20.30.40").evidence_ref
    return RoleVerificationCandidate(
        claim_reviews=[
            RoleVerificationClaimReview(
                claim_ref=claim.claim_ref,
                status=RoleVerificationClaimStatus.SUPPORTED,
                supporting_evidence_refs=[evidence_ref],
                rationale="现有证据支持该声明。",
                counterevidence_assessment="未发现可解析的 bounded 反证。",
            )
            for claim in build_role_verification_claims(analysis)
        ]
    )


def test_claim_projection_excludes_first_pass_rationale_and_confidence() -> None:
    request, analysis, _ = _request_and_analysis()
    claims = build_role_verification_claims(analysis)
    prompt = build_role_verification_prompt(request, analysis, claims)

    assert claims
    assert all("confidence" not in claim.assertion for claim in claims)
    assert "PRIMARY-RATIONALE-MUST-NOT-LEAK" not in json.dumps(
        prompt.context,
        ensure_ascii=False,
    )
    assert "absence of an independent SYN" in prompt.system
    assert "must not by itself make that claim unresolved" in prompt.system
    assert prompt.prompt_version == ROLE_VERIFICATION_PROMPT_VERSION


def test_direction_claims_are_atomic_and_stably_named() -> None:
    _, analysis, _ = _request_and_analysis()

    claims = build_role_verification_claims(analysis)

    direction_claims = [claim for claim in claims if claim.claim_ref.startswith("RC-ND-")]
    assert [(claim.claim_ref, claim.assertion) for claim in direction_claims] == [
        ("RC-ND-01", {"observed_flow": "source_to_destination"}),
        ("RC-ND-02", {"boundary_direction": "internal_to_external"}),
        ("RC-ND-03", {"semantic_direction": "victim_to_attacker_reverse_connection"}),
        ("RC-ND-04", {"connection_initiator": "10.20.30.40"}),
    ]


def test_verifier_projection_excludes_geoip_noise_and_keeps_typed_network_scope() -> None:
    request, analysis, _ = _request_and_analysis()
    geo = AnalysisEvidenceCatalogItem(
        evidence_ref="E-AAAAAAAAAAAA",
        source_path="alert.hitLog[0].zeusRawLogs[0].message#parsed.dip_addr",
        value="美国--蒙大拿州",
        value_type="string",
        trust_level=EvidenceTrustLevel.HIGH,
    )
    network_scope = AnalysisContextCatalogItem(
        context_ref="C-BBBBBBBBBBBB",
        kind=AnalysisContextReferenceKind.GOVERNED_CONTEXT,
        label="PingAn internal address space",
        source_id="pingan.network_direction:pa.internal-address-space",
        summary="Matched addresses are organization-controlled internal addresses.",
        metadata={
            "fact_id": "pa.internal-address-space",
            "fact_kind": "network_scope",
            "matched_values": {"cidrs": ["10.20.30.40", "198.51.100.20"]},
            "network_scope_membership": "organization_controlled",
            "decision_authority": "none",
            "review_status": "reviewed",
        },
    )
    request = request.model_copy(
        update={
            "evidence_catalog": [*request.evidence_catalog, geo],
            "context_catalog": [*request.context_catalog, network_scope],
        }
    )

    prompt = build_role_verification_prompt(
        request,
        analysis,
        build_role_verification_claims(analysis),
    )

    evidence_refs = {item["evidence_ref"] for item in prompt.context["reference_catalogs"]["current_alert_evidence"]}
    context_refs = {item["context_ref"] for item in prompt.context["reference_catalogs"]["reasoning_context"]}
    assert geo.evidence_ref not in evidence_refs
    assert network_scope.context_ref in context_refs
    assert "美国--蒙大拿州" not in json.dumps(prompt.context, ensure_ascii=False)
    assert prompt.context["projection_summary"]["raw_vendor_payload_included"] is False
    assert prompt.context["runtime_constraints"]["organization_boundary"]["implied_boundary_direction"] == "internal_to_internal"


def test_challenged_claim_can_be_grounded_by_typed_context() -> None:
    review = RoleVerificationClaimReview(
        claim_ref="RC-ND-02",
        status=RoleVerificationClaimStatus.CHALLENGED,
        contradicting_context_refs=["C-BBBBBBBBBBBB"],
        rationale="组织边界事实与第一轮边界方向矛盾。",
        counterevidence_assessment="已核对匹配当前 IP 的 network_scope。",
    )

    assert review.contradicting_evidence_refs == []
    assert review.contradicting_context_refs == ["C-BBBBBBBBBBBB"]


def test_parser_rejects_boundary_status_that_conflicts_with_typed_scope() -> None:
    request, analysis, _ = _request_and_analysis()
    claims = build_role_verification_claims(analysis)
    network_scope = AnalysisContextCatalogItem(
        context_ref="C-BBBBBBBBBBBB",
        kind=AnalysisContextReferenceKind.GOVERNED_CONTEXT,
        label="Reviewed organization scope",
        source_id="tenant.network:internal-scope",
        summary="Both current endpoints are organization-controlled.",
        metadata={
            "fact_kind": "network_scope",
            "matched_values": {"cidrs": ["10.20.30.40", "198.51.100.20"]},
            "network_scope_membership": "organization_controlled",
            "decision_authority": "none",
        },
    )
    bad_candidate = _supported_candidate(request, analysis)

    with pytest.raises(
        LLMOutputParseError,
        match="contradicts typed organization ownership",
    ) as error:
        parse_role_verification_output(
            bad_candidate.model_dump_json(),
            claims=claims,
            evidence_catalog=request.evidence_catalog,
            context_catalog=[network_scope],
            canonical_network=request.canonical_entities.network,
        )

    assert error.value.stage == "semantic_consistency"
    assert error.value.issue_codes == ("typed_network_scope_boundary_conflict",)

    corrected = bad_candidate.model_dump(mode="json")
    boundary_review = next(item for item in corrected["claim_reviews"] if item["claim_ref"] == "RC-ND-02")
    boundary_review.update(
        {
            "status": "challenged",
            "supporting_evidence_refs": [],
            "contradicting_context_refs": [network_scope.context_ref],
            "alternative": {"assertion": {"boundary_direction": "internal_to_internal"}},
            "rationale": "两个端点均为组织受控地址。",
            "counterevidence_assessment": "类型化网段事实反驳第一轮边界方向。",
        }
    )

    parsed = parse_role_verification_output(
        json.dumps(corrected),
        claims=claims,
        evidence_catalog=request.evidence_catalog,
        context_catalog=[network_scope],
        canonical_network=request.canonical_entities.network,
    )

    assert next(item for item in parsed.candidate.claim_reviews if item.claim_ref == "RC-ND-02").status is RoleVerificationClaimStatus.CHALLENGED


def test_trigger_reviews_only_core_direction_and_attacker_victim_conflicts() -> None:
    request, analysis, grounding = _request_and_analysis()
    triggered = evaluate_role_verification_trigger(
        analysis,
        request=request,
        grounding=grounding,
    )
    assert triggered.triggered is True
    assert {reason.value for reason in triggered.reasons} == {"primary_direction_conflicted"}
    claims = build_role_verification_claims(analysis)
    assert [claim.claim_ref for claim in claims] == [
        "RC-ND-01",
        "RC-ND-02",
        "RC-ND-03",
        "RC-ND-04",
        "RC-R-01",
        "RC-R-02",
    ]
    assert all(claim.claim_ref != "RC-T-01" for claim in claims)

    inferred_without_target = _analysis_for_request(request, include_target=False)
    inferred_trigger = evaluate_role_verification_trigger(
        inferred_without_target,
        request=request,
        grounding=ground_analysis_evidence(inferred_without_target, request),
    )
    assert inferred_trigger.triggered is False
    assert inferred_trigger.reasons == []
    high_threshold_trigger = evaluate_role_verification_trigger(
        inferred_without_target,
        request=request,
        grounding=ground_analysis_evidence(inferred_without_target, request),
        minimum_confidence=0.99,
    )
    assert high_threshold_trigger.triggered is False

    clean_analysis = _analysis_for_request(request, include_target=False)
    clean_analysis = clean_analysis.model_copy(
        update={
            "network_direction": clean_analysis.network_direction.model_copy(
                update={
                    "status": NetworkDirectionAssessmentStatus.OBSERVED,
                    "semantic_direction": None,
                }
            )
        }
    )
    clean_grounding = ground_analysis_evidence(clean_analysis, request)
    not_triggered = evaluate_role_verification_trigger(
        clean_analysis,
        request=request,
        grounding=clean_grounding,
    )
    assert not_triggered.triggered is False
    assert not_triggered.reasons == []


def test_trigger_contract_reads_v1_history_but_defaults_new_records_to_v2() -> None:
    historical = RoleVerificationTriggerDecision.model_validate(
        {
            "policy_version": "soc.role_verification_trigger_policy.v1",
            "triggered": False,
            "reasons": [],
            "claim_count": 0,
            "claims_hash": "0" * 64,
            "minimum_confidence": 0.65,
        }
    )

    assert historical.policy_version == "soc.role_verification_trigger_policy.v1"
    current = RoleVerificationTriggerDecision(
        triggered=False,
        reasons=[],
        claim_count=0,
        claims_hash="0" * 64,
        minimum_confidence=0.35,
    )
    assert current.policy_version == "soc.role_verification_trigger_policy.v2"


def test_role_verification_parser_requires_exact_claim_and_reference_coverage() -> None:
    request, analysis, _ = _request_and_analysis()
    claims = build_role_verification_claims(analysis)
    candidate = _supported_candidate(request, analysis)

    parsed = parse_role_verification_output(
        candidate.model_dump_json(),
        claims=claims,
        evidence_catalog=request.evidence_catalog,
        context_catalog=request.context_catalog,
    )
    assert parsed.candidate == candidate
    assert parsed.parser_version == ROLE_VERIFICATION_JSON_PARSER_VERSION

    incomplete = candidate.model_dump(mode="json")
    incomplete["claim_reviews"] = incomplete["claim_reviews"][:-1]
    with pytest.raises(LLMOutputParseError, match=r"every RC-\* claim"):
        parse_role_verification_output(
            json.dumps(incomplete),
            claims=claims,
            evidence_catalog=request.evidence_catalog,
            context_catalog=request.context_catalog,
        )


class _RecordingClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[list[Mapping[str, str]], str]] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        self.calls.append((list(messages), model_name))
        return LLMChatResponse(content=self.response, model_name=model_name)


class _SequencedVerifierClient:
    def __init__(self, responses: Sequence[str | LLMChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[Mapping[str, str]], str]] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        self.calls.append((list(messages), model_name))
        response = self.responses.pop(0)
        if isinstance(response, LLMChatResponse):
            return response
        return LLMChatResponse(content=response, model_name=model_name)


def test_json_role_verifier_runs_narrow_prompt_and_persists_provenance() -> None:
    request, analysis, grounding = _request_and_analysis()
    candidate = _supported_candidate(request, analysis)
    client = _RecordingClient(candidate.model_dump_json())
    verifier = JsonLLMRoleVerifier(client=client, model_name="soc-model")
    trigger = verifier.evaluate_trigger(
        analysis,
        request=request,
        grounding=grounding,
    )

    output = verifier.verify(
        request,
        analysis,
        trigger,
        primary_model_name="soc-model",
    )

    assert output.verification.status is RoleVerificationStatus.CONFIRMED
    assert output.verification.same_model_verification is True
    assert output.verification.automation_allowed is False
    assert client.calls[0][1] == "soc-model"
    assert output.metadata["claim_count"] == trigger.claim_count


def test_json_role_verifier_retries_one_invalid_contract() -> None:
    request, analysis, grounding = _request_and_analysis()
    candidate = _supported_candidate(request, analysis)
    incomplete = candidate.model_dump(mode="json")
    incomplete["claim_reviews"] = incomplete["claim_reviews"][:-1]
    client = _SequencedVerifierClient([json.dumps(incomplete), candidate.model_dump_json()])
    verifier = JsonLLMRoleVerifier(client=client, model_name="soc-model")
    trigger = verifier.evaluate_trigger(
        analysis,
        request=request,
        grounding=grounding,
    )
    purposes = []

    output = verifier.verify_with_provider_hook(
        request,
        analysis,
        trigger,
        primary_model_name="soc-model",
        before_retry=lambda invocation: purposes.append(invocation.purpose),
    )

    assert output.verification.status is RoleVerificationStatus.CONFIRMED
    assert purposes == ["role_verification_retry"]
    assert len(client.calls) == 2
    assert "schema-correction node" in client.calls[1][0][0]["content"]
    assert output.metadata["provider_call_count"] == 2
    assert output.metadata["usage_complete"] is True
    assert output.metadata["usage_measurement"]["status"] == "estimated"
    assert output.metadata["output_retry_kind"] == "contract_correction"


def test_role_verifier_failed_correction_retains_bounded_usage_metadata() -> None:
    request, analysis, grounding = _request_and_analysis()
    candidate = _supported_candidate(request, analysis).model_dump(mode="json")
    candidate["claim_reviews"] = candidate["claim_reviews"][:-1]
    client = _SequencedVerifierClient(
        [
            LLMChatResponse(
                content=json.dumps(candidate),
                model_name="soc-model",
                usage={"input_tokens": 100, "output_tokens": 20},
            ),
            LLMChatResponse(
                content=json.dumps(candidate),
                model_name="soc-model",
                usage={"input_tokens": 80, "output_tokens": 10},
            ),
        ]
    )
    verifier = JsonLLMRoleVerifier(client=client, model_name="soc-model")
    trigger = verifier.evaluate_trigger(
        analysis,
        request=request,
        grounding=grounding,
    )

    run = analyze_alert(
        _payload(),
        analyzer=_PrimaryAnalyzer(),
        role_verifier=verifier,
    )

    assert trigger.triggered is True
    verifier_step = next(step for step in run.steps if step.step_name == "verify_roles_llm")
    assert verifier_step.status.value == "failed"
    assert verifier_step.metadata["provider_call_count"] == 2
    assert verifier_step.metadata["usage_complete"] is True
    assert verifier_step.metadata["output_retry_attempted"] is True
    assert verifier_step.metadata["output_retry_kind"] == "contract_correction"
    assert verifier_step.metadata["usage"] == {
        "input_tokens": 180,
        "output_tokens": 30,
        "total_tokens": 210,
    }


class _PrimaryAnalyzer:
    step_name = "analyze_llm"
    model_name = "primary-model"
    prompt_version = "primary-prompt"

    def __init__(self, *, include_target: bool = True) -> None:
        self.include_target = include_target

    def analyze(self, request: LLMAnalysisRequest) -> AnalysisNodeOutput:
        analysis = _analysis_for_request(
            request,
            include_target=self.include_target,
        )
        if not self.include_target:
            analysis = analysis.model_copy(
                update={
                    "network_direction": analysis.network_direction.model_copy(
                        update={
                            "status": NetworkDirectionAssessmentStatus.OBSERVED,
                            "semantic_direction": None,
                        }
                    )
                }
            )
        else:
            analysis = analysis.model_copy(update={"network_direction": analysis.network_direction.model_copy(update={"status": NetworkDirectionAssessmentStatus.CONFLICTED})})
        return AnalysisNodeOutput(
            analysis=analysis,
            model_name=self.model_name,
            prompt_version=self.prompt_version,
            parser_version="primary-parser",
        )


class _ConfirmingVerifier:
    step_name = "verify_roles_llm"
    model_name = "verifier-model"
    prompt_version = "verifier-prompt"
    parser_version = "verifier-parser"
    minimum_confidence = 0.65

    def __init__(
        self,
        *,
        fail: bool = False,
        result_status: RoleVerificationStatus = RoleVerificationStatus.CONFIRMED,
    ) -> None:
        self.fail = fail
        self.result_status = result_status
        self.verify_calls = 0

    def evaluate_trigger(self, analysis, *, request, grounding):
        return evaluate_role_verification_trigger(
            analysis,
            request=request,
            grounding=grounding,
            minimum_confidence=self.minimum_confidence,
        )

    def verify(
        self,
        request,
        analysis,
        trigger,
        *,
        primary_model_name,
    ):
        self.verify_calls += 1
        if self.fail:
            raise TimeoutError("verification provider timeout")
        candidate = _supported_candidate(request, analysis)
        if self.result_status is not RoleVerificationStatus.CONFIRMED:
            first = candidate.claim_reviews[0]
            if self.result_status is RoleVerificationStatus.CHALLENGED:
                replacement = RoleVerificationClaimReview(
                    claim_ref=first.claim_ref,
                    status=RoleVerificationClaimStatus.CHALLENGED,
                    contradicting_evidence_refs=first.supporting_evidence_refs,
                    rationale="反证要求重新裁决该声明。",
                    counterevidence_assessment="bounded 事实与原声明相反。",
                )
            else:
                replacement = RoleVerificationClaimReview(
                    claim_ref=first.claim_ref,
                    status=RoleVerificationClaimStatus.UNRESOLVED,
                    rationale="当前证据无法独立确认该声明。",
                    counterevidence_assessment="支持与反对该声明的证据均不足。",
                    evidence_gaps=["缺少独立方向证据。"],
                )
            candidate = candidate.model_copy(
                update={
                    "claim_reviews": [
                        replacement,
                        *candidate.claim_reviews[1:],
                    ]
                }
            )
        return RoleVerificationNodeOutput(
            verification=RoleAdjudicationVerificationResult(
                status=self.result_status,
                trigger=trigger,
                claims=build_role_verification_claims(analysis),
                claim_reviews=candidate.claim_reviews,
                primary_model_name=primary_model_name,
                verifier_model_name=self.model_name,
                same_model_verification=False,
                prompt_version=self.prompt_version,
                parser_version=self.parser_version,
            )
        )


def test_runtime_calls_verifier_only_when_triggered_and_journals_both_calls() -> None:
    verifier = _ConfirmingVerifier()
    provider_steps: list[str] = []
    run = analyze_alert(
        _payload(),
        analyzer=_PrimaryAnalyzer(),
        role_verifier=verifier,
        before_provider=lambda _run, _request, invocation: provider_steps.append(invocation.step_name),
    )

    assert verifier.verify_calls == 1
    assert run.pipeline_version == "soc-runtime-v8"
    assert provider_steps == ["analyze_llm", "verify_roles_llm"]
    assert run.role_verification_trigger is not None
    assert run.role_verification_trigger.triggered is True
    assert run.role_adjudication_verification is not None
    assert run.role_adjudication_verification.status is RoleVerificationStatus.CONFIRMED
    assert run.total_duration_ms is not None
    assert all(step.duration_ms is not None for step in run.steps)

    clean_verifier = _ConfirmingVerifier()
    clean_run = analyze_alert(
        _payload(),
        analyzer=_PrimaryAnalyzer(include_target=False),
        role_verifier=clean_verifier,
    )
    assert clean_verifier.verify_calls == 0
    assert clean_run.pipeline_version == "soc-runtime-v8"
    assert clean_run.role_verification_trigger is not None
    assert clean_run.role_verification_trigger.triggered is False
    assert clean_run.role_adjudication_verification is None


def test_runtime_verifier_failure_preserves_primary_result_and_blocks_role_actions() -> None:
    verifier = _ConfirmingVerifier(fail=True)
    run = analyze_alert(
        _payload(),
        analyzer=_PrimaryAnalyzer(),
        role_verifier=verifier,
    )

    assert run.analysis is not None
    assert run.analysis.verdict is Verdict.SUSPICIOUS
    assert run.role_adjudication_verification is not None
    assert run.role_adjudication_verification.status is RoleVerificationStatus.UNAVAILABLE
    assert run.decision is not None
    assert run.decision.evidence_state is DecisionEvidenceState.PARTIAL
    assert DecisionReviewReason.ROLE_VERIFIER_UNAVAILABLE not in run.decision.review_reasons
    assert run.decision.needs_review is False
    assert run.analysis_materiality is not None
    assert all(
        not guard.allowed
        for guard in run.analysis_materiality.capability_guards
        if guard.capability.value
        in {
            "network_direction",
            "attacker_targeting",
            "victim_targeting",
            "impacted_asset_targeting",
        }
    )
    verifier_step = next(step for step in run.steps if step.step_name == "verify_roles_llm")
    assert verifier_step.status.value == "failed"
    assert verifier_step.metadata["fail_closed"] is True
    assert verifier_step.metadata["fail_closed_scope"] == "direction_and_role_capabilities"


@pytest.mark.parametrize(
    ("verification_status", "expected_state", "expected_reason"),
    [
        (
            RoleVerificationStatus.CHALLENGED,
            DecisionEvidenceState.CONFLICTED,
            DecisionReviewReason.ROLE_VERIFICATION_CHALLENGED,
        ),
        (
            RoleVerificationStatus.UNRESOLVED,
            DecisionEvidenceState.PARTIAL,
            None,
        ),
    ],
)
def test_verifier_disagreement_adds_fail_closed_decision_guard(
    verification_status: RoleVerificationStatus,
    expected_state: DecisionEvidenceState,
    expected_reason: DecisionReviewReason | None,
) -> None:
    run = analyze_alert(
        _payload(),
        analyzer=_PrimaryAnalyzer(),
        role_verifier=_ConfirmingVerifier(result_status=verification_status),
    )

    assert run.analysis is not None
    assert run.analysis.verdict is Verdict.SUSPICIOUS
    assert run.decision is not None
    assert run.decision.evidence_state is expected_state
    if expected_reason is None:
        assert run.decision.needs_review is False
    else:
        assert expected_reason in run.decision.review_reasons


class _RunRepository:
    def __init__(self) -> None:
        self.runs = {}

    def save_run(self, run) -> None:
        self.runs[run.run_id] = run.model_copy(deep=True)

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def list_runs(self, *, limit=50):
        return list(self.runs.values())[-limit:]


def test_service_retains_ordered_journals_for_both_model_calls() -> None:
    repository = _RunRepository()
    service = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=_PrimaryAnalyzer(),
            role_verifier=_ConfirmingVerifier(),
        ),
        repository=repository,
    )

    run = service.analyze(_payload())

    assert [item.provider_purpose for item in run.provider_request_journals] == [
        AnalysisProviderPurpose.PRIMARY_ANALYSIS,
        AnalysisProviderPurpose.ROLE_VERIFICATION,
    ]
    assert [item.status for item in run.provider_request_journals] == [
        AnalysisRequestJournalStatus.COMPLETED,
        AnalysisRequestJournalStatus.COMPLETED,
    ]
    assert run.request_journal == run.provider_request_journals[-1]
    assert run.request_journal.optional_provider is True
    assert repository.get_run(run.run_id) == run


def test_service_marks_optional_verifier_journal_failed_without_losing_primary() -> None:
    service = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=_PrimaryAnalyzer(),
            role_verifier=_ConfirmingVerifier(fail=True),
        ),
        repository=_RunRepository(),
    )

    run = service.analyze(_payload())

    assert [item.status for item in run.provider_request_journals] == [
        AnalysisRequestJournalStatus.COMPLETED,
        AnalysisRequestJournalStatus.FAILED,
    ]
    assert run.provider_request_journals[-1].failure_kind is RuntimeFailureKind.ANALYZER_TIMEOUT
    assert run.provider_request_journals[-1].failure_retryable is True
    assert run.role_adjudication_verification is not None
    assert run.role_adjudication_verification.status is RoleVerificationStatus.UNAVAILABLE
