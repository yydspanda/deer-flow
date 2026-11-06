"""Offline contracts for the development Trace service boundary."""

import pytest
from pydantic import ValidationError
from trace_agent.contracts import (
    TraceCreateRequest,
    TraceOperation,
    TraceResult,
    TraceStatus,
    TraceTaskResponse,
    extract_alert_id,
    normalize_evidence_gaps,
    validate_task_id,
)


def success():
    return dict(alert_id="42", report="# Report", verdict="待核实", verdict_reason="仍需验证来源。", riskLevel="", completeness=0.8, confidence=1)


@pytest.mark.parametrize("status", [value for value in TraceStatus if value != TraceStatus.SUCCESS])
def test_non_success_retains_identity_and_all_result_nulls(status):
    reply = TraceTaskResponse(taskId="task-1", status=status, result=TraceResult(alert_id="42"))
    assert reply.model_dump(mode="json")["result"] == dict(alert_id="42", report=None, verdict=None, verdict_reason=None, riskLevel=None, completeness=None, confidence=None)
    with pytest.raises(ValidationError):
        TraceTaskResponse(taskId="task-1", status=status, result=TraceResult(**success()))


def test_success_requires_complete_formal_result_and_preserves_unicode_reason():
    fields = success()
    fields["verdict_reason"] = "核" * 1000
    assert TraceTaskResponse(taskId="task-1", status="SUCCESS", result=fields).result.verdict_reason == fields["verdict_reason"]
    for field in ("report", "verdict", "verdict_reason", "riskLevel", "completeness", "confidence"):
        with pytest.raises(ValidationError):
            TraceTaskResponse(taskId="task-1", status="SUCCESS", result={**fields, field: None})
    for reason in ("", "  ", "核" * 1001):
        with pytest.raises(ValidationError):
            TraceTaskResponse(taskId="task-1", status="SUCCESS", result={**fields, "verdict_reason": reason})


@pytest.mark.parametrize("field", ["completeness", "confidence"])
@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), True, "0.9"])
def test_metrics_reject_out_of_range_nonfinite_or_non_numeric(field, value):
    with pytest.raises(ValidationError):
        TraceTaskResponse(taskId="task-1", status="SUCCESS", result={**success(), field: value})


def test_old_binary_verdict_and_unknown_status_are_not_silently_mapped():
    with pytest.raises(ValidationError):
        TraceTaskResponse(taskId="task-1", status="SUCCESS", result={**success(), "verdict": "误报"})
    with pytest.raises(ValidationError):
        TraceTaskResponse(taskId="task-1", status="completed", result=TraceResult(alert_id="42"))


@pytest.mark.parametrize("task_id", ["", "  ", "../task", "a/b", "a\\b", "a\x00b", " task", "a\nb"])
def test_unsafe_task_ids_are_refused_without_normalization(task_id):
    with pytest.raises(ValueError):
        validate_task_id(task_id)


def test_identity_and_gaps_keep_input_order_without_inventing_identifiers():
    assert validate_task_id("TASK-001") == "TASK-001"
    assert extract_alert_id({"alertId": "00042"}) == "00042"
    assert extract_alert_id({"alert": {"alertId": 42}}) == "42"
    assert extract_alert_id({"alertId": 42, "alert": {"alertId": "42"}}) == "42"
    for payload in ({}, {"alertId": ""}, {"alertId": True}, {"alertId": 2.5}, {"alertId": "42", "alert": {"alertId": "43"}}):
        with pytest.raises(ValueError):
            extract_alert_id(payload)
    assert normalize_evidence_gaps("  first? \n\n second? \n first? ") == ["first?", "second?", "first?"]
    with pytest.raises(ValueError):
        normalize_evidence_gaps("x" * 32001)
    assert TraceOperation(operation_id="retry-1", expected_version=1).expected_version == 1
    with pytest.raises(ValidationError):
        TraceOperation(operation_id="retry-1", expected_version=0)


@pytest.mark.parametrize("questions", [[""], ["  "], [" not trimmed"], ["first\nsecond"], ["first\rsecond"], ["x" * 16000, "y" * 16000]])
def test_engine_create_request_requires_already_normalized_question_lines(questions):
    with pytest.raises(ValidationError):
        TraceCreateRequest(task_id="task-1", alert={"alertId": "42"}, alert_id="42", evidence_gaps=questions, operation_id="submit-1")
