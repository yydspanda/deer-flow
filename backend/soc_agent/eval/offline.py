"""Offline stub-vs-LLM replay evaluation for SOC analysis."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from soc_agent.contracts import AnalysisRun, AnalysisRunStatus, PipelineStepStatus, Verdict
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.llm import JsonLLMAnalyzer, LLMChatResponse


class OfflineEvalResponse(BaseModel):
    """Replayable LLM response bound to one eval sample."""

    sample_id: str
    content: Any
    model_name: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OfflineEvalSampleResult(BaseModel):
    """One sample's stub-vs-LLM replay comparison."""

    sample_id: str
    path: str
    stub_run_id: str
    llm_run_id: str
    stub_status: AnalysisRunStatus
    llm_status: AnalysisRunStatus
    stub_verdict: Verdict | None = None
    llm_verdict: Verdict | None = None
    verdict_changed: bool = False
    stub_confidence: float | None = None
    llm_confidence: float | None = None
    confidence_delta: float | None = None
    stub_needs_review: bool | None = None
    llm_needs_review: bool | None = None
    needs_review_changed: bool = False
    parse_success: bool = False
    repair_applied: bool = False
    parser_version: str | None = None
    prompt_version: str | None = None
    model_name: str | None = None
    conflict_count: int = Field(default=0, ge=0)
    conflict_types: list[str] = Field(default_factory=list)
    error: str | None = None


class OfflineEvalReport(BaseModel):
    """Aggregate report for offline SOC analyzer evaluation."""

    schema_version: str = "soc.offline_eval_report.v1"
    sample_count: int = Field(default=0, ge=0)
    stub_success_count: int = Field(default=0, ge=0)
    llm_success_count: int = Field(default=0, ge=0)
    parse_success_count: int = Field(default=0, ge=0)
    repair_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    verdict_diff_count: int = Field(default=0, ge=0)
    needs_review_diff_count: int = Field(default=0, ge=0)
    average_abs_confidence_delta: float = Field(default=0.0, ge=0.0)
    results: list[OfflineEvalSampleResult] = Field(default_factory=list)


class _StaticLLMChatClient:
    def __init__(self, response: OfflineEvalResponse) -> None:
        self._response = response

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse:
        return LLMChatResponse(
            content=self._response.content,
            model_name=self._response.model_name or model_name,
            usage=self._response.usage,
            metadata=self._response.metadata,
        )


def run_offline_eval(
    samples: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    responses: Mapping[str, OfflineEvalResponse] | None = None,
    model_name: str = "replay-llm",
) -> OfflineEvalReport:
    """Run each sample through stub and replayable LLM analyzer, then diff."""

    results: list[OfflineEvalSampleResult] = []
    response_by_sample = responses or {}

    for path, payload in samples:
        sample_id = _sample_id(path)
        stub_run = SocAnalysisService().analyze(payload)
        response = response_by_sample.get(sample_id) or _response_from_stub(sample_id, stub_run)
        llm_analyzer = JsonLLMAnalyzer(client=_StaticLLMChatClient(response), model_name=model_name)
        llm_run = SocAnalysisService(runtime=DeterministicAnalysisRuntime(analyzer=llm_analyzer)).analyze(payload)
        results.append(_sample_result(sample_id=sample_id, path=path, stub_run=stub_run, llm_run=llm_run))

    return _report(results)


def load_eval_responses_jsonl(path: str | Path) -> dict[str, OfflineEvalResponse]:
    """Load replayable LLM responses from JSONL.

    Each line must be an object with ``sample_id`` and ``content``. ``content``
    can be a string or a JSON object; object content is serialized before being
    handed to the LLM JSON parser so golden files stay readable.
    """

    response_path = Path(path)
    responses: dict[str, OfflineEvalResponse] = {}
    try:
        lines = response_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read eval response file: {exc}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"eval response line {line_number} must be an object")
        if "content" not in data:
            raise ValueError(f"eval response line {line_number} missing content")
        if isinstance(data["content"], dict):
            data["content"] = json.dumps(data["content"], ensure_ascii=False)
        response = OfflineEvalResponse.model_validate(data)
        responses[response.sample_id] = response

    if not responses:
        raise ValueError(f"no eval responses found in {response_path}")
    return responses


def _response_from_stub(sample_id: str, run: AnalysisRun) -> OfflineEvalResponse:
    if run.analysis is None:
        return OfflineEvalResponse(
            sample_id=sample_id,
            content='{"verdict":"unknown","confidence":0,"summary":"stub failed","evidence":[],"reason":"stub failed","recommended_action":"needs_human_review"}',
        )
    return OfflineEvalResponse(
        sample_id=sample_id,
        content=run.analysis.model_dump_json(exclude_none=True),
        model_name="stub-replay",
        metadata={"source": "stub_analysis"},
    )


def _sample_result(*, sample_id: str, path: str, stub_run: AnalysisRun, llm_run: AnalysisRun) -> OfflineEvalSampleResult:
    stub_verdict = _verdict(stub_run)
    llm_verdict = _verdict(llm_run)
    stub_confidence = _confidence(stub_run)
    llm_confidence = _confidence(llm_run)
    stub_needs_review = _needs_review(stub_run)
    llm_needs_review = _needs_review(llm_run)
    analyze_step = _find_step(llm_run, "analyze_llm")

    return OfflineEvalSampleResult(
        sample_id=sample_id,
        path=path,
        stub_run_id=stub_run.run_id,
        llm_run_id=llm_run.run_id,
        stub_status=stub_run.status,
        llm_status=llm_run.status,
        stub_verdict=stub_verdict,
        llm_verdict=llm_verdict,
        verdict_changed=stub_verdict is not None and llm_verdict is not None and stub_verdict != llm_verdict,
        stub_confidence=stub_confidence,
        llm_confidence=llm_confidence,
        confidence_delta=_confidence_delta(stub_confidence, llm_confidence),
        stub_needs_review=stub_needs_review,
        llm_needs_review=llm_needs_review,
        needs_review_changed=stub_needs_review is not None and llm_needs_review is not None and stub_needs_review != llm_needs_review,
        parse_success=analyze_step is not None and analyze_step.status is PipelineStepStatus.SUCCESS,
        repair_applied=bool(analyze_step and analyze_step.metadata.get("repair_applied")),
        parser_version=str(analyze_step.metadata["parser_version"]) if analyze_step and "parser_version" in analyze_step.metadata else None,
        prompt_version=llm_run.prompt_version,
        model_name=llm_run.model_name,
        conflict_count=llm_run.llm_analysis_request.conflict_count if llm_run.llm_analysis_request is not None else 0,
        conflict_types=llm_run.llm_analysis_request.conflict_types if llm_run.llm_analysis_request is not None else [],
        error=_run_error(llm_run),
    )


def _report(results: list[OfflineEvalSampleResult]) -> OfflineEvalReport:
    deltas = [abs(result.confidence_delta) for result in results if result.confidence_delta is not None]
    return OfflineEvalReport(
        sample_count=len(results),
        stub_success_count=sum(result.stub_status in {AnalysisRunStatus.SUCCESS, AnalysisRunStatus.NEEDS_REVIEW} for result in results),
        llm_success_count=sum(result.llm_status in {AnalysisRunStatus.SUCCESS, AnalysisRunStatus.NEEDS_REVIEW} for result in results),
        parse_success_count=sum(result.parse_success for result in results),
        repair_count=sum(result.repair_applied for result in results),
        failed_count=sum(result.llm_status is AnalysisRunStatus.FAILED for result in results),
        verdict_diff_count=sum(result.verdict_changed for result in results),
        needs_review_diff_count=sum(result.needs_review_changed for result in results),
        average_abs_confidence_delta=(sum(deltas) / len(deltas)) if deltas else 0.0,
        results=results,
    )


def _sample_id(path: str) -> str:
    return Path(path).name


def _verdict(run: AnalysisRun) -> Verdict | None:
    return run.analysis.verdict if run.analysis is not None else None


def _confidence(run: AnalysisRun) -> float | None:
    return run.analysis.confidence if run.analysis is not None else None


def _needs_review(run: AnalysisRun) -> bool | None:
    return run.decision.needs_review if run.decision is not None else None


def _confidence_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return right - left


def _find_step(run: AnalysisRun, step_name: str):
    for step in run.steps:
        if step.step_name == step_name:
            return step
    return None


def _run_error(run: AnalysisRun) -> str | None:
    for step in reversed(run.steps):
        if step.error:
            return step.error
    return None
