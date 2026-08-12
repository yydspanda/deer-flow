from __future__ import annotations

import json
from pathlib import Path

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_analyzer_output_review import (
    build_analyzer_output_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_bounded_analysis_input_review import (
    SensitiveEvidenceMode,
    build_bounded_analysis_input_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_entity_extraction_review import (
    build_entity_extraction_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_fact_reconstruction_review import (
    build_fact_reconstruction_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_normalization_review import (
    build_normalization_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_skill_context_review import (
    build_skill_context_review,
)
from validation.compact_zeus.checkpoint_d.test_build_checkpoint_d_bounded_analysis_input_review import (
    _corpus,
)

from soc_agent.llm import JsonLLMAnalyzer
from soc_agent.pipeline.reference_catalog import evidence_ref_for


class _Client:
    def complete(self, messages, *, model_name):
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        evidence_ref = evidence_ref_for("alert_id", "1")
        return json.dumps(
            {
                "schema_version": "soc.analysis_result.v4",
                "verdict": "suspicious",
                "confidence": 0.78,
                "summary": "弱口令攻击尝试存在，但尚无账号失陷证据。",
                "evidence": [
                    {
                        "evidence_ref": evidence_ref,
                        "source": "alert_id",
                        "description": "当前告警进入受控研判节点",
                        "value": "1",
                    }
                ],
                "reasoning": [
                    {
                        "schema_version": "soc.analysis_reasoning_item.v1",
                        "reasoning_id": "R-01",
                        "statement": "上游场景提示与当前告警上下文支持攻击尝试。",
                        "basis": ["current_evidence", "general_security_knowledge"],
                        "evidence_refs": [evidence_ref],
                        "context_refs": [],
                        "confidence": 0.74,
                    }
                ],
                "scenario_assessments": [
                    {
                        "schema_version": ("soc.triage_scenario_assessment.v2"),
                        "scenario_name": "弱口令攻击",
                        "scenario_key": "weak_password_attack",
                        "is_primary": True,
                        "origin": "hybrid",
                        "confidence": 0.74,
                        "activity_stage": "attempt_observed",
                        "evidence_refs": [evidence_ref],
                        "reasoning_refs": ["R-01"],
                        "rationale": "上游场景提示与当前告警上下文支持攻击尝试。",
                        "competing_explanations": ["授权测试或正常登录失败"],
                    }
                ],
                "network_direction": {
                    "schema_version": "soc.network_direction_assessment.v1",
                    "status": "not_assessed",
                    "observed_flow": "not_available",
                    "boundary_direction": "not_applicable",
                    "semantic_direction": None,
                    "connection_initiator": None,
                    "intermediaries": [],
                    "confidence": 0.0,
                    "evidence_refs": [],
                    "reasoning_refs": [],
                    "context_refs": [],
                    "rationale": "检查点测试不包含方向裁决。",
                    "evidence_gaps": [],
                },
                "role_adjudication": {
                    "schema_version": "soc.role_adjudication_result.v1",
                    "status": "not_assessed",
                    "roles": [],
                    "response_target_proposals": [],
                    "conflicts": [],
                    "evidence_gaps": [],
                    "rationale": "检查点测试不包含角色裁决。",
                },
                "evidence_gaps": ["缺少认证结果和后续会话行为。"],
                "manual_checks": ["核对目标账号认证结果和同时间窗会话。"],
                "reason": "当前只能确认场景与尝试，不能确认账号失陷。",
                "recommended_action": "review_authentication_context",
                "knowledge_candidates": [],
            },
            ensure_ascii=False,
        )


def _d5_review() -> dict:
    corpus = _corpus()
    d1_review = build_normalization_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
    )
    d2_review = build_entity_extraction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
    )
    d3_review = build_fact_reconstruction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
        entity_review=d2_review,
    )
    d4_review = build_bounded_analysis_input_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
        entity_review=d2_review,
        fact_review=d3_review,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    return build_skill_context_review(d4_review, alert_id=1)


def test_analyzer_output_review_validates_live_typed_scenario_contract() -> None:
    analyzer = JsonLLMAnalyzer(client=_Client(), model_name="test-live-model")

    review = build_analyzer_output_review(
        _d5_review(),
        alert_id=1,
        analyzer=analyzer,
    )

    assert review["acceptance"]["status"] == "passed"
    assert review["acceptance"]["failed_checks"] == []
    assert all(review["acceptance"]["checks"].values())
    assert review["analysis_result"]["schema_version"] == "soc.analysis_result.v4"
    assert review["scenario_review"]["primary_scenario"]["scenario_name"] == (
        "弱口令攻击"
    )
    assert review["scenario_review"]["primary_scenario"]["activity_stage"] == (
        "attempt_observed"
    )
    assert review["scope"]["not_performed"] == [
        "evidence_grounding",
        "decision_policy",
        "correlation_or_memory_retrieval",
        "tool_or_mcp_invocation",
        "persistence",
        "review_queue_or_action",
    ]
