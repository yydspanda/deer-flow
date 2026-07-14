from __future__ import annotations

import json
from pathlib import Path

from soc_agent.contracts import (
    AnalysisEvidenceGroundingStatus,
    AnalysisResult,
    BoundedAnalysisEvidence,
    EvidenceItem,
    EvidenceLayer,
    EvidenceTrustLevel,
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


def test_grounding_accepts_approved_section_shorthand_and_structured_punctuation() -> None:
    request = _request().model_copy(
        update={
            "primary_evidence": BoundedAnalysisEvidence(
                source_path="raw.message",
                layer=EvidenceLayer.RAW_MESSAGE,
                trust_level=EvidenceTrustLevel.HIGH,
                content='{"fields": {"attack_sip": "10.0.9.9", "rsp_status": 200}}',
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
        ),
        request,
    )

    assert report.grounded_count == 4
    assert report.ungrounded_count == 0
