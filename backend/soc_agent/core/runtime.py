"""Deterministic Phase 1 SOC runtime.

The runtime owns the control flow. LLM-backed nodes can be added later behind
fixed pipeline steps, but they must not choose whether required steps run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import (
    AlertInput,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    Decision,
    ExtractedEntities,
    PipelineStepStatus,
    PipelineStepTrace,
)
from soc_agent.core.validator import validate_analysis_result, validate_decision
from soc_agent.pipeline.analyzer import analyze_stub
from soc_agent.pipeline.extractor import extract_entities
from soc_agent.utils.hashing import stable_hash


class SocRuntimeError(RuntimeError):
    """Raised when the deterministic runtime cannot complete a run."""


def analyze_alert(payload: Mapping[str, Any]) -> AnalysisRun:
    """Analyze one alert through the fixed Phase 1 pipeline."""

    run = AnalysisRun(alert_id="unknown", status=AnalysisRunStatus.RUNNING)

    try:
        alert = _run_step(run, "normalize", payload, _normalize_alert)
        run.alert_id = alert.alert_id
        entities = _run_step(run, "entity_extract", alert, extract_entities)
        run.entities = entities
        analysis = _run_step(
            run,
            "analyze_stub",
            {"alert": alert, "entities": entities},
            lambda _: analyze_stub(alert, entities),
        )
        run.analysis = _run_step(run, "schema_validate", analysis, validate_analysis_result)
        run.decision = _run_step(run, "decide", run.analysis, _decide)
        run.status = AnalysisRunStatus.NEEDS_REVIEW if run.decision.needs_review else AnalysisRunStatus.SUCCESS
    except Exception as exc:  # noqa: BLE001 - convert all runtime failures into run state
        run.status = AnalysisRunStatus.FAILED
        if not run.steps or run.steps[-1].status is not PipelineStepStatus.FAILED:
            run.steps.append(
                PipelineStepTrace(
                    step_name="runtime",
                    status=PipelineStepStatus.FAILED,
                    error=str(exc),
                    ended_at=_utc_now(),
                )
            )
    finally:
        run.ended_at = _utc_now()

    return run


def _normalize_alert(payload: Mapping[str, Any]) -> AlertInput:
    if not isinstance(payload, Mapping):
        raise SocRuntimeError("alert payload must be a JSON object")
    return AlertInput.model_validate(dict(payload))


def _decide(analysis: AnalysisResult) -> Decision:
    needs_review = analysis.confidence < 0.75 or analysis.verdict.value in {
        "unknown",
        "needs_review",
    }
    decision = Decision(
        verdict=analysis.verdict,
        confidence=analysis.confidence,
        suggested_action=analysis.recommended_action,
        needs_review=needs_review,
        reason=analysis.reason,
        automation_allowed=False,
    )
    return validate_decision(decision)


def _run_step[T](
    run: AnalysisRun,
    step_name: str,
    step_input: Any,
    func: Callable[[Any], T],
) -> T:
    trace = PipelineStepTrace(
        step_name=step_name,
        status=PipelineStepStatus.RUNNING,
        input_hash=stable_hash(_jsonable(step_input)),
    )
    run.steps.append(trace)

    try:
        output = func(step_input)
    except (ValidationError, Exception) as exc:
        trace.status = PipelineStepStatus.FAILED
        trace.error = str(exc)
        trace.ended_at = _utc_now()
        trace.duration_ms = _duration_ms(trace.started_at, trace.ended_at)
        raise

    trace.status = PipelineStepStatus.SUCCESS
    trace.output_hash = stable_hash(_jsonable(output))
    trace.ended_at = _utc_now()
    trace.duration_ms = _duration_ms(trace.started_at, trace.ended_at)

    if isinstance(output, ExtractedEntities):
        trace.warnings.extend(output.warnings)

    return output


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return int((ended_at - started_at).total_seconds() * 1000)
