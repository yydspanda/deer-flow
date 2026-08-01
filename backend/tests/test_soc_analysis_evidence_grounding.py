from __future__ import annotations

import json
from pathlib import Path

from soc_agent.contracts import (
    AnalysisEvidenceGroundingStatus,
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

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _request():
    payload = json.loads((SAMPLES / "malicious_ioc.json").read_text(encoding="utf-8"))
    return build_analysis_request_for_payload(payload)


def _analysis(*evidence: EvidenceItem) -> AnalysisResult:
    return AnalysisResult(
        verdict=Verdict.TRUE_POSITIVE,
        confidence=0.9,
        summary="测试分析结果",
        evidence=list(evidence),
        evidence_gaps=["测试上下文未覆盖完整调查信息。"],
        manual_checks=["人工复核测试证据。"],
        reason="测试证据落地校验",
        recommended_action="review",
    )


def test_grounding_accepts_value_from_declared_bounded_source() -> None:
    report = ground_analysis_evidence(
        _analysis(EvidenceItem(source="detection", description="规则编号", value="EDR-IOC-001")),
        _request(),
    )

    assert report.grounded_count == 1
    assert report.ungrounded_count == 0
    assert report.items[0].status is AnalysisEvidenceGroundingStatus.GROUNDED
    assert "detection.rule_code" in report.items[0].matched_context_paths


def test_grounding_rejects_description_that_cites_a_sibling_context_value() -> None:
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="detection",
                description="规则 EDR-IOC-001 命中目标 198.51.100.77",
                value="EDR-IOC-001",
            )
        ),
        _request(),
    )

    assert report.grounded_count == 0
    assert report.ungrounded_count == 1
    assert report.description_leakage_count == 1
    assert report.items[0].status is AnalysisEvidenceGroundingStatus.DESCRIPTION_CONTEXT_LEAKAGE
    assert "detection.rule_code" in report.items[0].matched_context_paths
    assert any(path.endswith("destination_ip") for path in report.items[0].foreign_description_context_paths)


def test_grounding_rejects_description_that_cites_a_sibling_path_key() -> None:
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content=('{"decoded_fields":{"rule_labels":{"0x100604":{"parent_name":"弱口令"},"0x100600":{"name":"弱口令"}}}}'),
            )
        }
    )
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="raw.message#decoded.rule_labels.0x100604.parent_name",
                description="0x100604 为弱口令，父标签 0x100600 也为弱口令",
                value="弱口令",
            )
        ),
        request,
    )

    assert report.items[0].status is AnalysisEvidenceGroundingStatus.DESCRIPTION_CONTEXT_LEAKAGE
    assert report.description_leakage_count == 1
    assert any("0x100600" in path for path in report.items[0].foreign_description_context_paths)


def test_grounding_rejects_uncited_short_port_in_description() -> None:
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content='{"fields":{"dip":"30.184.42.99","dport":80}}',
            )
        }
    )
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="raw.message#parsed.dip",
                description="目标 IP 为 30.184.42.99，目标端口为 80",
                value="30.184.42.99",
            )
        ),
        request,
    )

    assert report.items[0].status is (AnalysisEvidenceGroundingStatus.DESCRIPTION_CONTEXT_LEAKAGE)
    assert any(path.endswith("fields.dport") for path in report.items[0].foreign_description_context_paths)


def test_grounding_accepts_equivalent_punctuation_from_the_quoted_value() -> None:
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) HeadlessChrome/146.0.0.0"
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content=json.dumps(
                    {
                        "decoded_fields": {
                            "payload": {"req_header": {"headers": {"user-agent": [user_agent]}}},
                            "rule_labels": {"0x110A02": {"os": "Mac OS X 10.15"}},
                        }
                    }
                ),
            )
        }
    )
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source=("raw.message#decoded.payload.req_header.headers.user-agent[0]"),
                description="User-Agent 显示 Mac OS X 10.15 和 HeadlessChrome",
                value=user_agent,
            )
        ),
        request,
    )

    assert report.grounded_count == 1
    assert report.description_leakage_count == 0


def test_grounding_ignores_synthetic_entity_keys_in_description_audit() -> None:
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="canonical_entities.network.source_ip",
                description="源 IP 10.0.9.9 是私有地址",
                value="10.0.9.9",
            )
        ),
        _request(),
    )

    assert report.grounded_count == 1
    assert report.description_leakage_count == 0


def test_grounding_does_not_treat_an_explicit_disclaimer_as_a_sibling_claim() -> None:
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content='{"fields":{"host_state":"攻击成功"}}',
            )
        }
    )
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="detection",
                description="该规则名只是检测器分类，并非独立攻击成功证明",
                value="EDR-IOC-001",
            )
        ),
        request,
    )

    assert report.grounded_count == 1
    assert report.description_leakage_count == 0


def test_grounding_rejects_hallucinated_value_and_unknown_source() -> None:
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(source="detection", description="不存在的规则", value="RISK-DOES-NOT-EXIST"),
            EvidenceItem(source="provider_private_state", description="不存在的来源", value="secret"),
        ),
        _request(),
    )

    assert report.grounded_count == 0
    assert report.ungrounded_count == 2
    assert report.items[0].status is AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND
    assert report.items[1].status is AnalysisEvidenceGroundingStatus.SOURCE_MISMATCH


def test_grounding_accepts_composite_values_only_when_every_part_exists() -> None:
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="entities",
                description="通信两端",
                value="10.0.9.9, 198.51.100.77",
            )
        ),
        _request(),
    )

    assert report.grounded_count == 1
    assert len(report.items[0].matched_context_paths) >= 2


def test_grounding_rejects_composite_source_paths() -> None:
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="detection, entities",
                description="来源必须是一个精确路径",
                value="EDR-IOC-001",
            )
        ),
        _request(),
    )

    assert report.items[0].status is AnalysisEvidenceGroundingStatus.SOURCE_MISMATCH


def test_grounding_flags_http_success_claim_without_outcome_artifact() -> None:
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content='{"decoded_fields": {"response": {"status_code": 200}}}',
            )
        }
    )
    analysis = _analysis(EvidenceItem(source="primary_evidence", description="HTTP响应", value="200")).model_copy(update={"summary": "目标返回 HTTP 200，说明漏洞利用成功"})

    report = ground_analysis_evidence(analysis, request)

    assert "outcome-success claim" in " ".join(report.warnings)


def test_grounding_accepts_approved_section_shorthand_and_structured_punctuation() -> None:
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content=('{"fields": {"attack_sip": "10.0.9.9", "rsp_status": 200}, "decoded_fields": {"response": {"server": "nginx/1.21.3"}}}'),
            )
        }
    )
    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="primary_evidence.content",
                description="厂商声明攻击源",
                value="attack_sip: 10.0.9.9",
            ),
            EvidenceItem(
                source="evidence.primary_evidence.content",
                description="响应状态",
                value="rsp_status: 200",
            ),
            EvidenceItem(
                source="raw.message#parsed.attack_sip",
                description="原始消息解析字段",
                value="10.0.9.9",
            ),
            EvidenceItem(
                source="raw.message#parsed.fields.rsp_status",
                description="显式 parsed root",
                value="200",
            ),
            EvidenceItem(
                source="raw.message#decoded.response.server",
                description="显式 decoded 路径",
                value="nginx/1.21.3",
            ),
        ),
        request,
    )

    assert report.grounded_count == 5
    assert report.ungrounded_count == 0


def test_grounding_does_not_flag_explicitly_unconfirmed_outcome() -> None:
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content='{"decoded_fields": {"response": {"status_code": 200}}}',
            )
        }
    )
    analysis = _analysis(EvidenceItem(source="primary_evidence", description="HTTP响应", value="200")).model_copy(update={"summary": "HTTP 200 无法确认代码执行或文件写入是否成功"})

    report = ground_analysis_evidence(analysis, request)

    assert "outcome-success claim" not in " ".join(report.warnings)


def test_grounding_flags_impact_confirmed_stage_without_outcome_artifact() -> None:
    request = _request()
    analysis = _analysis(
        EvidenceItem(
            source="detection",
            description="规则命中",
            value="EDR-IOC-001",
        )
    ).model_copy(
        update={
            "scenario_assessments": [
                TriageScenarioAssessment(
                    scenario_name="终端失陷",
                    scenario_key="endpoint_compromise",
                    is_primary=True,
                    origin=TriageScenarioOrigin.INFERRED,
                    confidence=0.9,
                    activity_stage=TriageActivityStage.IMPACT_CONFIRMED,
                    evidence_indices=[0],
                    rationale="规则名称指向终端失陷。",
                )
            ]
        }
    )

    report = ground_analysis_evidence(analysis, request)

    assert "outcome-success claim" in " ".join(report.warnings)


def test_grounding_accepts_visible_encoded_omission_marker_but_rejects_private_sidecar_hash() -> None:
    digest = "a" * 64
    marker = f"<ENCODED:base64_like:320:sha256={digest[:12]}:OMITTED>"
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content=f'{{"fields": {{"payload": "{marker}"}}}}',
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

    report = ground_analysis_evidence(
        _analysis(
            EvidenceItem(
                source="raw.message#parsed.payload",
                description="模型边界中的编码占位符",
                value=marker,
            ),
            EvidenceItem(
                source="primary_evidence",
                description="省略内容的审计哈希",
                value=digest,
            ),
        ),
        request,
    )

    assert report.grounded_count == 1
    assert report.ungrounded_count == 1
    assert report.items[0].status is AnalysisEvidenceGroundingStatus.GROUNDED
    assert "grounds only the visible field presence" in report.items[0].reason
    assert report.items[1].status is AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND


def test_grounding_accepts_only_high_trust_bounded_provider_outcome_assertions() -> None:
    field_path = "raw.message#parsed.host_state"
    primary = BoundedAnalysisEvidence(
        source_path="raw.message",
        layer=EvidenceLayer.RAW_MESSAGE,
        trust_level=EvidenceTrustLevel.HIGH,
        content='{"fields":{"host_state":"攻击成功"}}',
        projected_field_paths=[field_path],
    )
    request = _request().model_copy(
        update={
            "primary_evidence": primary,
            "source_field_semantics": [
                SourceFieldSemantic(
                    field_path=field_path,
                    semantic_type="provider_detection_outcome_assertion",
                    meaning="reviewed provider outcome",
                    participates_in_reasoning=True,
                )
            ],
        }
    )
    analysis = _analysis(
        EvidenceItem(
            source=field_path,
            description="上游检测结果标记为攻击成功",
            value="攻击成功",
        )
    ).model_copy(update={"summary": "上游检测结果为攻击成功"})

    report = ground_analysis_evidence(analysis, request)
    low_trust_report = ground_analysis_evidence(
        analysis,
        request.model_copy(update={"primary_evidence": primary.model_copy(update={"trust_level": EvidenceTrustLevel.LOW})}),
    )

    assert "outcome-success claim" not in " ".join(report.warnings)
    assert "outcome-success claim" in " ".join(low_trust_report.warnings)
