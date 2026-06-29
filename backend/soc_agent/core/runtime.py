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
    EntityKind,
    ExtractedEntities,
    ExtractionReport,
    NormalizationInspectionResult,
    NormalizationReport,
    PipelineStepStatus,
    PipelineStepTrace,
)
from soc_agent.core.validator import validate_analysis_result, validate_decision
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.pipeline.analyzer import analyze_stub
from soc_agent.pipeline.extractor import extract_entities
from soc_agent.utils.hashing import stable_hash


class SocRuntimeError(RuntimeError):
    """Raised when the deterministic runtime cannot complete a run."""


def inspect_alert_normalization(payload: Mapping[str, Any]) -> NormalizationInspectionResult:
    """Run deterministic normalization and entity extraction without analysis."""

    alert = _normalize_alert(payload)
    entities = extract_entities(alert)
    return NormalizationInspectionResult(
        alert=alert,
        entities=entities,
        normalization_report=_normalization_report(alert),
        extraction_report=_extraction_report(entities),
    )


def analyze_alert(payload: Mapping[str, Any]) -> AnalysisRun:
    """Analyze one alert through the fixed Phase 1 pipeline."""

    input_payload = _jsonable(payload)
    run = AnalysisRun(
        alert_id="unknown",
        status=AnalysisRunStatus.RUNNING,
        input_payload=input_payload,
        input_hash=stable_hash(input_payload),
    )

    try:
        alert = _run_step(run, "normalize", payload, _normalize_alert)
        run.alert_id = alert.alert_id
        run.normalization_report = _normalization_report(alert)
        entities = _run_step(run, "entity_extract", alert, extract_entities)
        run.entities = entities
        run.extraction_report = _extraction_report(entities)
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
    return normalize_alert_payload(payload)


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


def _normalization_report(alert: AlertInput) -> NormalizationReport:
    normalized_fields = _present_canonical_fields(alert)
    missing_fields = [
        field
        for field in [
            "source.source_type",
            "detection.rule_code_or_name",
            "entities.network.source_ip",
            "entities.network.destination_ip",
        ]
        if field not in normalized_fields
    ]
    warnings = [f"missing normalized field: {field}" for field in missing_fields]
    return NormalizationReport(
        adapter="pingan_platform" if "legacy_platform" in alert.extensions else "generic",
        source_type=alert.source.source_type,
        source_system=alert.source.source_system,
        missing_fields=missing_fields,
        normalized_fields=normalized_fields,
        unmapped_fields=[],
        unmapped_field_count=0,
        warnings=warnings,
    )


def _present_canonical_fields(alert: AlertInput) -> list[str]:
    fields: list[str] = []
    if alert.source.source_type.value != "unknown":
        fields.append("source.source_type")
    if alert.source.source_system:
        fields.append("source.source_system")
    if alert.detection.rule_code or alert.detection.rule_name:
        fields.append("detection.rule_code_or_name")
    if alert.detection.rule_code:
        fields.append("detection.rule_code")
    if alert.detection.rule_name:
        fields.append("detection.rule_name")
    if alert.detection.detection_key:
        fields.append("detection.detection_key")
    if alert.classification.severity:
        fields.append("classification.severity")
    if alert.classification.category:
        fields.append("classification.category")
    if alert.entities.network.source_ip:
        fields.append("entities.network.source_ip")
    if alert.entities.network.destination_ip:
        fields.append("entities.network.destination_ip")
    if alert.entities.http.x_forwarded_for:
        fields.append("entities.http.x_forwarded_for")
    if alert.entities.user.username:
        fields.append("entities.user.username")
    if alert.entities.user.user_id:
        fields.append("entities.user.user_id")
    if alert.entities.user.um_account:
        fields.append("entities.user.um_account")
    if alert.entities.host.host_name:
        fields.append("entities.host.host_name")
    if alert.entities.process.process_name:
        fields.append("entities.process.process_name")
    if alert.entities.process.command_line:
        fields.append("entities.process.command_line")
    return fields


def _extraction_report(entities: ExtractedEntities) -> ExtractionReport:
    entity_counts = {kind.value: 0 for kind in EntityKind}
    for mention in entities.mentions:
        entity_counts[mention.kind.value] = entity_counts.get(mention.kind.value, 0) + 1
    entity_counts = {key: value for key, value in entity_counts.items() if value}
    missing_entity_kinds = [kind.value for kind in [EntityKind.IP, EntityKind.PROCESS, EntityKind.USER, EntityKind.HOST] if entity_counts.get(kind.value, 0) == 0]
    return ExtractionReport(
        mention_count=len(entities.mentions),
        entity_counts=entity_counts,
        missing_entity_kinds=missing_entity_kinds,
        warnings=entities.warnings,
    )


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
