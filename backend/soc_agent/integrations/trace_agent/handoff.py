"""Copy-only material preparation; selecting questions belongs to the caller."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from trace_agent.contracts import extract_alert_id, normalize_evidence_gaps, validate_task_id

from soc_agent.contracts import AnalysisRun, SocCaseOutcomeView


class TraceHandoffError(ValueError):
    pass


class TraceTaskBinding(BaseModel):
    """Caller supplies a trusted business-task binding, never inferred from Run."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    run_id: str = Field(min_length=1)
    alert_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    environment: str = Field(min_length=1)

    @field_validator("task_id")
    @classmethod
    def check_task_id(cls, value: str) -> str:
        return validate_task_id(value)

    @field_validator("run_id", "alert_id", "tenant_id", "environment")
    @classmethod
    def check_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Trace binding identities must be nonempty exact values")
        return value


class TraceQuestionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    question: str = Field(min_length=1)
    source: Literal["effective_gap", "effective_next_step", "operator_question"]
    source_index: int | None = Field(default=None, ge=0)

    @field_validator("question")
    @classmethod
    def check_question(cls, value: str) -> str:
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("Each selected question must be one nonempty line")
        return value.strip()

    @model_validator(mode="after")
    def check_source(self) -> TraceQuestionSelection:
        if (self.source == "operator_question") != (self.source_index is None):
            raise ValueError("Existing advice needs an index; operator questions do not")
        return self


class TraceHandoffSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["effective_gap", "effective_next_step"]
    source_index: int
    text: str


class TraceHandoffQuestion(TraceQuestionSelection):
    source_text: str | None = None


class TraceHandoffMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    binding: TraceTaskBinding
    alert: dict[str, Any]
    effective_outcome: SocCaseOutcomeView
    suggestions: list[TraceHandoffSuggestion]
    questions: list[TraceHandoffQuestion]

    @property
    def evidence_gaps(self) -> str:
        return "\n".join(item.question for item in self.questions)


def build_trace_handoff(*, run: AnalysisRun, outcome: SocCaseOutcomeView, binding: TraceTaskBinding, questions: list[TraceQuestionSelection]) -> TraceHandoffMaterial:
    """Use a current service-projected outcome and explicit trusted task scope.

    This function does not resolve authority or query a repository. Its caller must
    obtain binding and current outcome through shared authenticated application
    services. Neither old Base questions nor manual_checks become selected questions.
    """

    if binding.run_id != run.run_id or binding.alert_id != run.alert_id:
        raise TraceHandoffError("Trace business-task binding does not match the selected Run")
    known_tenants = [item for item in (run.normalized_alert.tenant_id if run.normalized_alert else None, run.llm_analysis_request.tenant_id if run.llm_analysis_request else None) if item is not None]
    if any(item != binding.tenant_id for item in known_tenants):
        raise TraceHandoffError("Trace tenant binding conflicts with saved Run context")
    if run.llm_analysis_request and run.llm_analysis_request.environment is not None and run.llm_analysis_request.environment != binding.environment:
        raise TraceHandoffError("Trace environment binding conflicts with saved Run context")
    if run.input_payload is None:
        raise TraceHandoffError("Trace handoff requires the saved original alert")
    try:
        if extract_alert_id(run.input_payload) != binding.alert_id:
            raise ValueError("source identity mismatch")
    except (ValueError, TypeError):
        raise TraceHandoffError("Trace original alert identity is missing or conflicts with its binding") from None
    sources = {"effective_gap": outcome.evidence_gaps, "effective_next_step": outcome.next_steps}
    suggestions = [TraceHandoffSuggestion(source=source, source_index=index, text=text) for source, values in sources.items() for index, text in enumerate(values)]
    selected: list[TraceHandoffQuestion] = []
    for item in questions:
        source_text = None
        if item.source != "operator_question":
            values = sources[item.source]
            if item.source_index is None or item.source_index >= len(values):
                raise TraceHandoffError("Selected question refers to unavailable current advice")
            source_text = values[item.source_index]
        selected.append(TraceHandoffQuestion(**item.model_dump(), source_text=source_text))
    try:
        normalize_evidence_gaps("\n".join(item.question for item in selected))
    except (ValueError, TypeError):
        raise TraceHandoffError("Selected Trace questions exceed the shared input contract") from None
    return TraceHandoffMaterial(binding=binding, alert=deepcopy(run.input_payload), effective_outcome=outcome.model_copy(deep=True), suggestions=suggestions, questions=selected)
