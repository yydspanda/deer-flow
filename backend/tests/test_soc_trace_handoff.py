"""Handoff snapshots do not infer triggers or revive superseded Base questions."""

from __future__ import annotations

import pytest

from soc_agent.contracts import AlertInput, AnalysisResult, AnalysisRun, AnalysisRunStatus, LLMAnalysisRequest, SocCaseOutcomeView, Verdict
from soc_agent.integrations.trace_agent import TraceHandoffError, TraceQuestionSelection, TraceTaskBinding, build_trace_handoff


def materials():
    run = AnalysisRun(
        run_id="RUN-SYNTHETIC-1",
        alert_id="synthetic-alert-7",
        status=AnalysisRunStatus.SUCCESS,
        input_payload={"alert": {"alertId": "synthetic-alert-7", "event_content": "原始  日志"}},
        normalized_alert=AlertInput(alert_id="synthetic-alert-7", tenant_id="synthetic-tenant"),
        llm_analysis_request=LLMAnalysisRequest(alert_id="synthetic-alert-7", tenant_id="synthetic-tenant", environment="dev"),
        analysis=AnalysisResult(
            verdict=Verdict.FALSE_POSITIVE,
            confidence=0.8,
            summary="合成历史分析",
            evidence=[{"evidence_ref": "E-000000000001", "source": "event_content", "description": "合成日志"}],
            reasoning=[{"reasoning_id": "R-01", "statement": "合成推断", "basis": ["current_evidence"], "evidence_refs": ["E-000000000001"], "confidence": 0.8}],
            evidence_gaps=["旧模型的缺口"],
            manual_checks=["向业务负责人核实"],
            reason="保留既有分析供审计",
            recommended_action="按当前有效结论处理",
        ),
    )
    outcome = SocCaseOutcomeView(
        event_summary="合成示例", security_verdict=Verdict.FALSE_POSITIVE, closure_status="closed", evidence_gaps=[], prior_analysis_gaps=["旧模型的缺口"], next_steps=["按已确认业务流程处理。"], memory_directive_applied=True
    )
    binding = TraceTaskBinding(task_id="trusted-upstream-task-1", run_id=run.run_id, alert_id=run.alert_id, tenant_id="synthetic-tenant", environment="dev")
    return run, outcome, binding


def test_sampling_handoff_uses_effective_outcome_without_turning_suggestions_into_questions():
    run, outcome, binding = materials()
    before_run, before_outcome = run.model_dump_json(), outcome.model_dump_json()
    handoff = build_trace_handoff(run=run, outcome=outcome, binding=binding, questions=[])
    assert handoff.binding.task_id == "trusted-upstream-task-1"
    assert handoff.binding.task_id != run.run_id
    assert handoff.alert == run.input_payload
    assert handoff.effective_outcome.evidence_gaps == []
    assert all(item.text != "旧模型的缺口" for item in handoff.suggestions)
    assert all(item.text != "向业务负责人核实" for item in handoff.suggestions)
    assert handoff.questions == [] and handoff.evidence_gaps == ""
    assert run.model_dump_json() == before_run and outcome.model_dump_json() == before_outcome
    handoff.alert["alert"]["event_content"] = "returned copy only"
    assert run.input_payload["alert"]["event_content"] == "原始  日志"


def test_selected_questions_keep_source_and_are_never_replaced_with_all_manual_checks():
    run, outcome, binding = materials()
    outcome.evidence_gaps = ["缺少同一账号的后续登录记录。"]
    questions = [TraceQuestionSelection(question="查询同一账号后续登录", source="effective_gap", source_index=0), TraceQuestionSelection(question="是否有其他关联主机？", source="operator_question")]
    handoff = build_trace_handoff(run=run, outcome=outcome, binding=binding, questions=questions)
    assert handoff.evidence_gaps == "查询同一账号后续登录\n是否有其他关联主机？"
    assert handoff.questions[0].source_text == outcome.evidence_gaps[0]
    assert handoff.questions[0].source == "effective_gap"
    assert handoff.questions[1].source == "operator_question"


@pytest.mark.parametrize("field,value", [("run_id", "RUN-other"), ("alert_id", "other-alert"), ("tenant_id", "other-tenant"), ("environment", "stg")])
def test_binding_mismatches_are_rejected(field, value):
    run, outcome, binding = materials()
    changed = binding.model_copy(update={field: value})
    with pytest.raises(TraceHandoffError):
        build_trace_handoff(run=run, outcome=outcome, binding=changed, questions=[])


def test_explicit_trusted_task_id_is_preserved_even_if_its_value_equals_run_id():
    run, outcome, binding = materials()
    binding = binding.model_copy(update={"task_id": run.run_id})
    handoff = build_trace_handoff(run=run, outcome=outcome, binding=binding, questions=[])
    assert handoff.binding.task_id == run.run_id


def test_unknown_or_missing_original_identity_is_not_repaired_from_the_run_id():
    run, outcome, binding = materials()
    for alert in ({"alert": {"event_content": "no source ID"}}, {"alertId": "wrong-alert"}):
        run.input_payload = alert
        with pytest.raises(TraceHandoffError):
            build_trace_handoff(run=run, outcome=outcome, binding=binding, questions=[])


def test_cannot_reference_superseded_or_missing_advice_as_current_question_source():
    run, outcome, binding = materials()
    with pytest.raises(TraceHandoffError):
        build_trace_handoff(run=run, outcome=outcome, binding=binding, questions=[TraceQuestionSelection(question="旧模型的缺口", source="effective_gap", source_index=0)])


def test_questions_must_be_explicit_but_an_empty_list_is_valid_for_sampling():
    run, outcome, binding = materials()
    with pytest.raises(TypeError):
        build_trace_handoff(run=run, outcome=outcome, binding=binding)
    with pytest.raises(ValueError):
        TraceQuestionSelection(question="line 1\nline 2", source="operator_question")
