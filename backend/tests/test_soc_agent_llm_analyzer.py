from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from soc_agent.contracts import AnalysisRunStatus, DecisionConfidenceSource, DecisionReviewReason, Verdict
from soc_agent.core.service import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.llm import (
    ANALYSIS_JSON_PARSER_VERSION,
    LLM_ANALYZER_STEP_NAME,
    JsonLLMAnalyzer,
    LLMChatResponse,
    build_optional_llm_analyzer,
)
from soc_agent.pipeline.analyzer import StubLLMAnalyzer
from soc_agent.pipeline.reference_catalog import evidence_ref_for
from soc_agent.prompts import ANALYSIS_PROMPT_VERSION

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class RecordingChatClient:
    def __init__(self, response: str | LLMChatResponse) -> None:
        self.response = response
        self.calls: list[tuple[list[Mapping[str, str]], str]] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse | str:
        self.calls.append((list(messages), model_name))
        return self.response


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _analysis_json(*, trailing_comma: bool = False) -> str:
    suffix = "," if trailing_comma else ""
    evidence_ref = evidence_ref_for("detection.rule_code", "EDR-IOC-001")
    return f"""
    {{
      "schema_version": "soc.analysis_result.v4",
      "verdict": "true_positive",
      "confidence": 0.91,
      "summary": "LLM 判断该告警包含高危外联线索。",
      "evidence": [
        {{"evidence_ref": "{evidence_ref}", "source": "detection.rule_code", "description": "规则编号", "value": "EDR-IOC-001"}}
      ],
      "reasoning": [
        {{
          "schema_version": "soc.analysis_reasoning_item.v1",
          "reasoning_id": "R-01",
          "statement": "该规则与当前行为共同支持高危外联研判。",
          "basis": ["current_evidence", "general_security_knowledge"],
          "evidence_refs": ["{evidence_ref}"],
          "context_refs": [],
          "confidence": 0.84
        }}
      ],
      "scenario_assessments": [
        {{
          "schema_version": "soc.triage_scenario_assessment.v2",
          "scenario_name": "恶意外联",
          "scenario_key": "malicious_outbound",
          "is_primary": true,
          "origin": "inferred",
          "confidence": 0.84,
          "activity_stage": "attempt_observed",
          "evidence_refs": ["{evidence_ref}"],
          "reasoning_refs": ["R-01"],
          "rationale": "规则命中高危外联行为。",
          "competing_explanations": ["授权安全测试"]
        }}
      ],
      "network_direction": {{
        "schema_version": "soc.network_direction_assessment.v1",
        "status": "not_assessed",
        "observed_flow": "not_available",
        "boundary_direction": "not_applicable",
        "semantic_direction": null,
        "connection_initiator": null,
        "intermediaries": [],
        "confidence": 0.0,
        "evidence_refs": [],
        "reasoning_refs": [],
        "context_refs": [],
        "rationale": "测试响应不包含方向裁决。",
        "evidence_gaps": []
      }},
      "role_adjudication": {{
        "schema_version": "soc.role_adjudication_result.v1",
        "status": "not_assessed",
        "roles": [],
        "response_target_proposals": [],
        "conflicts": [],
        "evidence_gaps": [],
        "rationale": "测试响应不包含角色裁决。"
      }},
      "evidence_gaps": ["缺少终端进程与网络连接关联。"],
      "manual_checks": ["查询源主机同时间窗的进程网络连接。"],
      "reason": "存在可解释的高危行为证据，需要升级复核。",
      "recommended_action": "escalate_to_analyst",
      "knowledge_candidates": []{suffix}
    }}
    """


def test_default_optional_analyzer_returns_stub() -> None:
    analyzer = build_optional_llm_analyzer(enabled=False)

    assert isinstance(analyzer, StubLLMAnalyzer)


def test_enabled_optional_analyzer_requires_client() -> None:
    with pytest.raises(ValueError, match="client is required"):
        build_optional_llm_analyzer(enabled=True, model_name="soc-model")


def test_json_llm_analyzer_runs_prompt_client_parser_and_runtime_trace() -> None:
    client = RecordingChatClient(
        LLMChatResponse(
            content=_analysis_json(trailing_comma=True),
            model_name="soc-model-response",
            usage={"input_tokens": 100, "output_tokens": 80},
            metadata={"finish_reason": "stop"},
        )
    )
    analyzer = JsonLLMAnalyzer(client=client, model_name="soc-model")
    runtime = DeterministicAnalysisRuntime(analyzer=analyzer)
    service = SocAnalysisService(runtime=runtime)

    run = service.analyze(_sample("malicious_ioc.json"))

    assert run.status == AnalysisRunStatus.NEEDS_REVIEW
    assert run.analysis is not None
    assert run.analysis.verdict == Verdict.TRUE_POSITIVE
    assert run.analysis.scenario_assessments[0].scenario_name == "恶意外联"
    assert run.model_name == "soc-model-response"
    assert run.prompt_version == ANALYSIS_PROMPT_VERSION
    assert run.decision is not None
    assert run.decision.confidence_source is DecisionConfidenceSource.LLM_SELF_REPORT
    assert run.decision.confidence_is_calibrated is False
    assert run.decision.calibrated_probability is None
    assert run.decision.review_reasons == [DecisionReviewReason.CONFIDENCE_NOT_CALIBRATED]
    assert run.analysis_evidence_grounding is not None
    assert run.analysis_evidence_grounding.grounded_count == 1
    assert run.analysis_evidence_grounding.ungrounded_count == 0
    assert [call_model for _, call_model in client.calls] == ["soc-model"]
    assert client.calls[0][0][0]["role"] == "system"
    assert client.calls[0][0][1]["role"] == "user"

    analyze_step = next(step for step in run.steps if step.step_name == LLM_ANALYZER_STEP_NAME)
    assert analyze_step.metadata["analyzer"] == "json_llm"
    assert analyze_step.metadata["parser_version"] == ANALYSIS_JSON_PARSER_VERSION
    assert analyze_step.metadata["repair_applied"] is True
    assert analyze_step.metadata["usage"] == {"input_tokens": 100, "output_tokens": 80}
    assert analyze_step.metadata["response_metadata"] == {"finish_reason": "stop"}
    assert "prompt_hash" in analyze_step.metadata
    assert "skill_context_hash" in analyze_step.metadata
    assert analyze_step.metadata["selected_skills"]
    assert "candidate_hash" in analyze_step.metadata
    decide_step = next(step for step in run.steps if step.step_name == "decide")
    assert decide_step.metadata["policy_version"] == "soc.decision_policy.v3"
    assert decide_step.metadata["confidence_source"] == "llm_self_report"
    assert decide_step.metadata["confidence_is_calibrated"] is False
    assert decide_step.metadata["review_reasons"] == ["confidence_not_calibrated"]


def test_default_runtime_still_uses_stub_analyzer() -> None:
    run = SocAnalysisService().analyze(_sample("approved_scanner.json"))

    assert run.model_name == "stub"
    assert run.prompt_version == "stub"
    assert [step.step_name for step in run.steps] == [
        "normalize",
        "entity_extract",
        "fact_reconstruct",
        "build_analysis_input",
        "skill_context",
        "reference_catalog",
        "analyze_stub",
        "schema_validate",
        "evidence_grounding",
        "decide",
    ]
    analyze_step = next(step for step in run.steps if step.step_name == "analyze_stub")
    assert analyze_step.metadata["analyzer"] == "stub"


def test_llm_evidence_not_present_in_bounded_context_forces_review() -> None:
    client = RecordingChatClient(_analysis_json().replace("EDR-IOC-001", "HALLUCINATED-RULE-999"))
    analyzer = JsonLLMAnalyzer(client=client, model_name="soc-model")

    run = SocAnalysisService(runtime=DeterministicAnalysisRuntime(analyzer=analyzer)).analyze(_sample("malicious_ioc.json"))

    assert run.analysis_evidence_grounding is not None
    assert run.analysis_evidence_grounding.ungrounded_count == 1
    assert run.decision is not None
    assert DecisionReviewReason.UNGROUNDED_ANALYSIS_EVIDENCE in run.decision.review_reasons
    assert DecisionReviewReason.UNGROUNDED_ANALYSIS_REASONING in run.decision.review_reasons


def test_live_model_failure_keeps_requested_model_in_failed_trace() -> None:
    class FailingClient:
        def complete(self, messages, *, model_name):
            raise TimeoutError("provider timeout")

    analyzer = JsonLLMAnalyzer(client=FailingClient(), model_name="deepseek-v4-pro")
    run = SocAnalysisService(runtime=DeterministicAnalysisRuntime(analyzer=analyzer)).analyze(_sample("malicious_ioc.json"))

    assert run.status == AnalysisRunStatus.FAILED
    assert run.model_name == "deepseek-v4-pro"
    assert run.prompt_version == ANALYSIS_PROMPT_VERSION
    step = next(item for item in run.steps if item.step_name == "analyze_llm")
    assert step.status.value == "failed"
    assert step.metadata["model_name"] == "deepseek-v4-pro"
    assert step.metadata["prompt_version"] == ANALYSIS_PROMPT_VERSION
    assert step.error == "TimeoutError while invoking configured SOC analyzer"
    assert run.failure is not None
    assert run.failure.kind.value == "analyzer_timeout"
    assert run.failure.retryable is True
