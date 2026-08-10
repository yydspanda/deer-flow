from __future__ import annotations

import json
from pathlib import Path

from soc_agent.contracts import (
    AnalysisEvidenceCatalogItem,
    AnalysisEvidenceGroundingStatus,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    BoundedAnalysisEvidence,
    EncodedSpanOmission,
    EvidenceItem,
    EvidenceLayer,
    EvidenceTrustLevel,
    SourceFieldSemantic,
    TriageActivityStage,
    TriageScenarioAssessment,
    TriageScenarioOrigin,
    Verdict,
)
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.pipeline.evidence_grounding import ground_analysis_evidence
from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _request():
    payload = json.loads((SAMPLES / "malicious_ioc.json").read_text(encoding="utf-8"))
    return build_analysis_request_for_payload(payload)


def _catalog_item(request, *, path: str | None = None, value: object = None):
    for item in request.evidence_catalog:
        if path is not None and item.source_path != path:
            continue
        if value is not None and (type(item.value) is not type(value) or item.value != value):
            continue
        return item
    raise AssertionError(f"catalog item not found: path={path!r} value={value!r}")


def _evidence(item: AnalysisEvidenceCatalogItem, description: str = "当前告警事实") -> EvidenceItem:
    return EvidenceItem(
        evidence_ref=item.evidence_ref,
        source=item.source_path,
        description=description,
        value=item.value,
    )


def _analysis(
    *evidence: EvidenceItem,
    reasoning: list[AnalysisReasoningItem] | None = None,
    summary: str = "测试分析结果",
    scenarios: list[TriageScenarioAssessment] | None = None,
) -> AnalysisResult:
    evidence_refs = [item.evidence_ref for item in evidence]
    assert all(evidence_refs)
    return AnalysisResult(
        verdict=Verdict.TRUE_POSITIVE,
        confidence=0.9,
        summary=summary,
        evidence=list(evidence),
        reasoning=reasoning
        or [
            AnalysisReasoningItem(
                reasoning_id="R-01",
                statement="当前事实与待调查安全行为相关。",
                basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                evidence_refs=evidence_refs,
                confidence=0.8,
            )
        ],
        scenario_assessments=scenarios or [],
        evidence_gaps=["测试上下文未覆盖完整调查信息。"],
        manual_checks=["人工复核测试证据。"],
        reason="测试证据落地校验",
        recommended_action="review",
    )


def test_grounding_accepts_exact_current_alert_catalog_fact() -> None:
    request = _request()
    item = _catalog_item(request, path="detection.rule_code", value="EDR-IOC-001")

    report = ground_analysis_evidence(_analysis(_evidence(item, "规则编号")), request)

    assert report.grounded_count == 1
    assert report.ungrounded_count == 0
    assert report.reasoning_grounded_count == 1
    assert report.items[0].status is AnalysisEvidenceGroundingStatus.GROUNDED
    assert report.items[0].matched_context_paths == ["detection.rule_code"]


def test_grounding_report_accepts_up_to_forty_selected_evidence_items() -> None:
    request = _request()
    catalog_items = request.evidence_catalog[:30]
    reasoning = AnalysisReasoningItem(
        reasoning_id="R-01",
        statement="所选事实共同构成当前告警的有界调查上下文。",
        basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
        evidence_refs=[catalog_items[0].evidence_ref],
        confidence=0.8,
    )

    report = ground_analysis_evidence(
        _analysis(
            *(_evidence(item) for item in catalog_items),
            reasoning=[reasoning],
        ),
        request,
    )

    assert report.total_count == 30
    assert report.grounded_count == 30


def test_grounding_rejects_unknown_evidence_reference() -> None:
    evidence = EvidenceItem(
        evidence_ref="E-000000000000",
        source="detection.rule_code",
        description="不存在的目录引用",
        value="EDR-IOC-001",
    )

    report = ground_analysis_evidence(_analysis(evidence), _request())

    assert report.items[0].status is AnalysisEvidenceGroundingStatus.REFERENCE_NOT_FOUND
    assert report.reasoning_items[0].status is AnalysisEvidenceGroundingStatus.REFERENCE_NOT_FOUND


def test_grounding_rejects_source_or_value_changed_after_reference_selection() -> None:
    request = _request()
    item = _catalog_item(request, path="detection.rule_code", value="EDR-IOC-001")
    wrong_source = _evidence(item).model_copy(update={"source": "detection.rule_name"})
    wrong_value = _evidence(item).model_copy(update={"value": "RISK-DOES-NOT-EXIST"})

    source_report = ground_analysis_evidence(_analysis(wrong_source), request)
    value_report = ground_analysis_evidence(_analysis(wrong_value), request)

    assert source_report.items[0].status is AnalysisEvidenceGroundingStatus.SOURCE_MISMATCH
    assert value_report.items[0].status is AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND


def test_grounding_allows_declared_general_security_reasoning() -> None:
    request = _request()
    item = _catalog_item(request, path="canonical_entities.process.process_name", value="powershell.exe")
    reasoning = AnalysisReasoningItem(
        reasoning_id="R-01",
        statement="隐藏运行的 PowerShell 常见于攻击脚本执行，但仍需结合上下文确认。",
        basis=[
            AnalysisReasoningBasis.CURRENT_EVIDENCE,
            AnalysisReasoningBasis.GENERAL_SECURITY_KNOWLEDGE,
        ],
        evidence_refs=[item.evidence_ref],
        confidence=0.78,
    )

    report = ground_analysis_evidence(
        _analysis(_evidence(item, "进程名"), reasoning=[reasoning]),
        request,
    )

    assert report.reasoning_grounded_count == 1
    assert report.description_leakage_count == 0


def test_grounding_requires_governed_reference_for_skill_reasoning() -> None:
    request = _request()
    fact = _catalog_item(request, path="detection.rule_code", value="EDR-IOC-001")
    skill = next(item for item in request.context_catalog if item.context_ref.startswith("S-"))
    accepted = AnalysisReasoningItem(
        reasoning_id="R-01",
        statement="按终端研判 Skill，应补查同时间窗子进程。",
        basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE, AnalysisReasoningBasis.SKILL],
        evidence_refs=[fact.evidence_ref],
        context_refs=[skill.context_ref],
        confidence=0.8,
    )
    missing = accepted.model_copy(update={"context_refs": ["S-000000000000"]})
    undeclared = accepted.model_copy(update={"basis": [AnalysisReasoningBasis.CURRENT_EVIDENCE]})

    accepted_report = ground_analysis_evidence(_analysis(_evidence(fact), reasoning=[accepted]), request)
    missing_report = ground_analysis_evidence(_analysis(_evidence(fact), reasoning=[missing]), request)
    undeclared_report = ground_analysis_evidence(_analysis(_evidence(fact), reasoning=[undeclared]), request)

    assert accepted_report.reasoning_grounded_count == 1
    assert missing_report.reasoning_items[0].status is AnalysisEvidenceGroundingStatus.REFERENCE_NOT_FOUND
    assert undeclared_report.reasoning_items[0].status is AnalysisEvidenceGroundingStatus.UNSUPPORTED_REFERENCE


def test_grounding_flags_success_claim_without_outcome_artifact() -> None:
    request = _request()
    item = _catalog_item(request, path="detection.rule_code", value="EDR-IOC-001")
    analysis = _analysis(_evidence(item), summary="规则命中说明漏洞利用成功")

    report = ground_analysis_evidence(analysis, request)

    assert "outcome-success claim" in " ".join(report.warnings)


def test_grounding_does_not_flag_explicitly_unconfirmed_outcome() -> None:
    request = _request()
    item = _catalog_item(request, path="detection.rule_code", value="EDR-IOC-001")
    analysis = _analysis(_evidence(item), summary="规则命中无法确认代码执行是否成功")

    report = ground_analysis_evidence(analysis, request)

    assert "outcome-success claim" not in " ".join(report.warnings)


def test_grounding_flags_impact_confirmed_stage_without_outcome_artifact() -> None:
    request = _request()
    item = _catalog_item(request, path="detection.rule_code", value="EDR-IOC-001")
    scenario = TriageScenarioAssessment(
        scenario_name="终端失陷",
        scenario_key="endpoint_compromise",
        is_primary=True,
        origin=TriageScenarioOrigin.INFERRED,
        confidence=0.9,
        activity_stage=TriageActivityStage.IMPACT_CONFIRMED,
        evidence_refs=[item.evidence_ref],
        reasoning_refs=["R-01"],
        rationale="规则与行为共同指向终端失陷。",
    )

    report = ground_analysis_evidence(_analysis(_evidence(item), scenarios=[scenario]), request)

    assert "outcome-success claim" in " ".join(report.warnings)


def test_grounding_encoded_marker_proves_only_visible_omission() -> None:
    digest = "a" * 64
    marker = f"<ENCODED:base64_like:320:sha256={digest[:12]}:OMITTED>"
    request = finalize_analysis_reference_catalogs(
        _request().model_copy(
            update={
                "primary_evidence": BoundedAnalysisEvidence(
                    source_path="raw.message",
                    layer=EvidenceLayer.RAW_MESSAGE,
                    trust_level=EvidenceTrustLevel.HIGH,
                    content=json.dumps({"fields": {"payload": marker}}),
                    encoded_span_omissions=[
                        EncodedSpanOmission(
                            field_path="raw.message#parsed.payload",
                            kind="base64_like",
                            original_chars=320,
                            sha256=digest,
                        )
                    ],
                )
            }
        )
    )
    marker_item = _catalog_item(request, value=marker)

    report = ground_analysis_evidence(_analysis(_evidence(marker_item)), request)

    assert report.items[0].status is AnalysisEvidenceGroundingStatus.GROUNDED
    assert "proves only visible presence" in report.items[0].reason
    assert not any(item.value == digest for item in request.evidence_catalog)


def test_grounding_accepts_only_high_trust_bounded_provider_outcome_assertion() -> None:
    field_path = "raw.message#parsed.host_state"
    primary = BoundedAnalysisEvidence(
        source_path="raw.message",
        layer=EvidenceLayer.RAW_MESSAGE,
        trust_level=EvidenceTrustLevel.HIGH,
        content='{"host_state":"攻击成功"}',
        projected_field_paths=[field_path],
    )
    semantic = SourceFieldSemantic(
        field_path=field_path,
        semantic_type="provider_detection_outcome_assertion",
        meaning="reviewed provider outcome",
        participates_in_reasoning=True,
    )
    request = finalize_analysis_reference_catalogs(_request().model_copy(update={"primary_evidence": primary, "source_field_semantics": [semantic]}))
    item = _catalog_item(request, value="攻击成功")
    analysis = _analysis(_evidence(item), summary="上游检测结果为攻击成功")
    low_trust_request = finalize_analysis_reference_catalogs(request.model_copy(update={"primary_evidence": primary.model_copy(update={"trust_level": EvidenceTrustLevel.LOW})}))

    report = ground_analysis_evidence(analysis, request)
    low_trust_report = ground_analysis_evidence(analysis, low_trust_request)

    assert "outcome-success claim" not in " ".join(report.warnings)
    assert "outcome-success claim" in " ".join(low_trust_report.warnings)
