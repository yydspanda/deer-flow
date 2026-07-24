"""Deterministic-control-flow SOC runtime.

The runtime owns the control flow. A configured LLM may implement the bounded
analysis node, but it cannot choose whether required steps run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from soc_agent.contracts import (
    AlertInput,
    AlertSourceType,
    AnalysisEvidenceGroundingReport,
    AnalysisNodeOutput,
    AnalysisRun,
    AnalysisRunStatus,
    Decision,
    EntityKind,
    ExtractedEntities,
    ExtractionReport,
    FactReconstructionResult,
    LLMAnalysisRequest,
    MessageSchemaStatus,
    NormalizationInspectionResult,
    NormalizationReport,
    PipelineStepStatus,
    PipelineStepTrace,
    RuntimeFailure,
    RuntimeFailureKind,
    SensitiveEvidenceMode,
    SocSkillContext,
)
from soc_agent.core.decision_policy import SocDecisionPolicy
from soc_agent.core.validator import validate_analysis_result
from soc_agent.normalizers import normalize_alert_payload, normalize_with_mapping
from soc_agent.pipeline.analysis_context import (
    build_llm_analysis_request,
    resolve_skill_context_for_request,
)
from soc_agent.pipeline.analyzer import StubLLMAnalyzer
from soc_agent.pipeline.evidence_coverage import observe_message_schemas
from soc_agent.pipeline.evidence_grounding import ground_analysis_evidence
from soc_agent.pipeline.extractor import extract_entities
from soc_agent.pipeline.fact_reconstructor import reconstruct_facts
from soc_agent.protocols import AnalysisBeforeProviderHook, DecisionPolicy, LLMAnalyzer
from soc_agent.utils.hashing import stable_hash


class SocRuntimeError(RuntimeError):
    """Raised when the deterministic runtime cannot complete a run."""


class SocRuntimeLifecycleError(RuntimeError):
    """Raised when infrastructure fails before the analyzer is invoked."""


def inspect_alert_normalization(
    payload: Mapping[str, Any],
    *,
    mapping_config: Mapping[str, Any] | None = None,
) -> NormalizationInspectionResult:
    """Run deterministic normalization and entity extraction without analysis."""

    alert = _normalize_alert(payload, mapping_config=mapping_config)
    entities = extract_entities(alert)
    return NormalizationInspectionResult(
        alert=alert,
        entities=entities,
        normalization_report=_normalization_report(alert),
        extraction_report=_extraction_report(entities),
    )


def build_analysis_request_for_payload(
    payload: Mapping[str, Any],
    *,
    sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.REDACT,
) -> LLMAnalysisRequest:
    """Build bounded analysis input without running analyzer or decision nodes."""

    alert = _normalize_alert(payload)
    entities = extract_entities(alert)
    fact_reconstruction = reconstruct_facts(alert)
    request = build_llm_analysis_request(
        alert,
        entities,
        fact_reconstruction,
        sensitive_evidence_mode=sensitive_evidence_mode,
    )
    return request.model_copy(update={"skill_context": resolve_skill_context_for_request(request)})


def analyze_alert(
    payload: Mapping[str, Any],
    *,
    analyzer: LLMAnalyzer | None = None,
    decision_policy: DecisionPolicy | None = None,
    before_provider: AnalysisBeforeProviderHook | None = None,
    sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.REDACT,
) -> AnalysisRun:
    """Analyze one alert through the fixed nine-step pipeline."""

    input_payload = _jsonable(payload)
    analysis_node = analyzer or StubLLMAnalyzer()
    policy = decision_policy or SocDecisionPolicy()
    run = AnalysisRun(
        alert_id="unknown",
        status=AnalysisRunStatus.RUNNING,
        model_name=analysis_node.model_name,
        prompt_version=analysis_node.prompt_version,
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
        fact_reconstruction = _run_step(run, "fact_reconstruct", alert, reconstruct_facts)
        run.fact_reconstruction = fact_reconstruction
        analysis_request = _run_step(
            run,
            "build_analysis_input",
            {"alert": alert, "entities": entities, "fact_reconstruction": fact_reconstruction},
            lambda _: build_llm_analysis_request(
                alert,
                entities,
                fact_reconstruction,
                sensitive_evidence_mode=sensitive_evidence_mode,
            ),
        )
        skill_context = _run_step(
            run,
            "skill_context",
            analysis_request,
            resolve_skill_context_for_request,
        )
        analysis_request = analysis_request.model_copy(update={"skill_context": skill_context})
        run.llm_analysis_request = analysis_request
        if before_provider is not None:
            try:
                before_provider(run, analysis_request, analysis_node.step_name)
            except Exception as exc:  # noqa: BLE001 - preserve the no-provider-call boundary
                raise SocRuntimeLifecycleError("failed to persist analysis request journal before provider invocation") from exc
        try:
            analysis_output = _run_step(
                run,
                analysis_node.step_name,
                analysis_request,
                analysis_node.analyze,
            )
        except Exception:
            run.steps[-1].metadata.update(
                {
                    "model_name": analysis_node.model_name,
                    "prompt_version": analysis_node.prompt_version,
                    "analyzer": "json_llm" if analysis_node.step_name == "analyze_llm" else "stub",
                }
            )
            raise
        run.model_name = analysis_output.model_name
        run.prompt_version = analysis_output.prompt_version
        run.analysis = _run_step(run, "schema_validate", analysis_output.analysis, validate_analysis_result)
        run.analysis_evidence_grounding = _run_step(
            run,
            "evidence_grounding",
            {"analysis": run.analysis, "analysis_request": analysis_request},
            lambda _: ground_analysis_evidence(run.analysis, analysis_request),
        )
        run.decision = _run_step(
            run,
            "decide",
            {
                "analysis": run.analysis,
                "analysis_request": analysis_request,
                "analysis_evidence_grounding": run.analysis_evidence_grounding,
                "analyzer_step_name": analysis_node.step_name,
            },
            lambda _: policy.decide(
                run.analysis,
                request=analysis_request,
                grounding=run.analysis_evidence_grounding,
                analyzer_step_name=analysis_node.step_name,
            ),
        )
        run.status = AnalysisRunStatus.NEEDS_REVIEW if run.decision.needs_review else AnalysisRunStatus.SUCCESS
    except SocRuntimeLifecycleError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert all runtime failures into run state
        run.status = AnalysisRunStatus.FAILED
        failed_step = run.steps[-1].step_name if run.steps else "runtime"
        run.failure = _classify_runtime_failure(exc, step_name=failed_step)
        if not run.steps or run.steps[-1].status is not PipelineStepStatus.FAILED:
            run.steps.append(
                PipelineStepTrace(
                    step_name="runtime",
                    status=PipelineStepStatus.FAILED,
                    error=run.failure.message,
                    ended_at=_utc_now(),
                    metadata={
                        "failure_kind": run.failure.kind.value,
                        "retryable": run.failure.retryable,
                        "error_type": run.failure.error_type,
                    },
                )
            )
        else:
            run.steps[-1].metadata.update(
                {
                    "failure_kind": run.failure.kind.value,
                    "retryable": run.failure.retryable,
                    "error_type": run.failure.error_type,
                }
            )
    finally:
        run.ended_at = _utc_now()

    return run


def _normalize_alert(
    payload: Mapping[str, Any],
    *,
    mapping_config: Mapping[str, Any] | None = None,
) -> AlertInput:
    if not isinstance(payload, Mapping):
        raise SocRuntimeError("alert payload must be a JSON object")
    if mapping_config is not None:
        return normalize_with_mapping(payload, mapping_config)
    return normalize_alert_payload(payload)


def _normalization_report(alert: AlertInput) -> NormalizationReport:
    normalized_fields = _present_canonical_fields(alert)
    missing_fields = [field for field in _required_canonical_fields(alert.source.source_type) if field not in normalized_fields]
    mapping_metadata = alert.extensions.get("normalization")
    adapter = "pingan_platform" if "legacy_platform" in alert.extensions else "generic"
    mapping_warnings: list[str] = []
    unmapped_fields: list[str] = []
    if isinstance(mapping_metadata, Mapping):
        adapter_name = mapping_metadata.get("adapter")
        mapping_name = mapping_metadata.get("mapping_name")
        if adapter_name == "mapping":
            adapter = f"mapping:{mapping_name or 'unnamed'}"
        warnings_value = mapping_metadata.get("warnings")
        if isinstance(warnings_value, list):
            mapping_warnings = [str(warning) for warning in warnings_value]
        missing_paths = mapping_metadata.get("missing_source_paths")
        if isinstance(missing_paths, list):
            unmapped_fields = [str(path) for path in missing_paths]

    message_schemas = observe_message_schemas(alert)
    warnings = [f"missing normalized field: {field}" for field in missing_fields]
    warnings.extend(mapping_warnings)
    for observation in message_schemas:
        if observation.status is MessageSchemaStatus.UNSUPPORTED:
            warnings.append(f"unsupported message schema: {observation.source_path}")
        elif observation.status is MessageSchemaStatus.DEGRADED:
            warnings.append(f"degraded message schema: {observation.source_path}")
        warnings.extend(f"{observation.source_path}: {warning}" for warning in observation.warnings)
    return NormalizationReport(
        adapter=adapter,
        source_type=alert.source.source_type,
        source_system=alert.source.source_system,
        missing_fields=missing_fields,
        normalized_fields=normalized_fields,
        unmapped_fields=unmapped_fields,
        unmapped_field_count=len(unmapped_fields),
        message_schemas=message_schemas,
        warnings=list(dict.fromkeys(warnings)),
    )


def _required_canonical_fields(source_type: AlertSourceType) -> list[str]:
    required = ["source.source_type", "detection.rule_code_or_name"]
    if source_type in {
        AlertSourceType.NDR,
        AlertSourceType.NIDS,
        AlertSourceType.WAF,
        AlertSourceType.F5,
        AlertSourceType.THREAT_INTEL,
    }:
        required.extend(["entities.network.source_ip", "entities.network.destination_ip"])
    return required


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
    if alert.entities.host.ip_addresses:
        fields.append("entities.host.ip_addresses")
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
    except Exception as exc:  # noqa: BLE001 - outer runtime classifies the failure
        trace.status = PipelineStepStatus.FAILED
        trace.error = _safe_error_message(exc, step_name=step_name)
        trace.ended_at = _utc_now()
        trace.duration_ms = _duration_ms(trace.started_at, trace.ended_at)
        raise

    trace.status = PipelineStepStatus.SUCCESS
    trace.output_hash = stable_hash(_jsonable(output))
    trace.ended_at = _utc_now()
    trace.duration_ms = _duration_ms(trace.started_at, trace.ended_at)

    if isinstance(output, ExtractedEntities):
        trace.warnings.extend(output.warnings)
    if isinstance(output, FactReconstructionResult):
        trace.warnings.extend(output.warnings)
    if isinstance(output, LLMAnalysisRequest):
        trace.warnings.extend(output.warnings)
    if isinstance(output, SocSkillContext):
        trace.metadata.update(
            {
                "selected_skills": [item.skill_name for item in output.selected_skills],
                "total_token_budget": output.total_token_budget,
            }
        )
    if isinstance(output, AnalysisEvidenceGroundingReport):
        trace.warnings.extend(output.warnings)
        trace.metadata.update(
            {
                "grounded_count": output.grounded_count,
                "ungrounded_count": output.ungrounded_count,
            }
        )
    if isinstance(output, AnalysisNodeOutput):
        trace.metadata.update(
            {
                "model_name": output.model_name,
                "prompt_version": output.prompt_version,
            }
        )
        if output.parser_version is not None:
            trace.metadata["parser_version"] = output.parser_version
        trace.metadata.update(output.metadata)
    if isinstance(output, Decision):
        trace.metadata.update(
            {
                "policy_version": output.policy_version,
                "confidence_source": output.confidence_source.value,
                "confidence_is_calibrated": output.confidence_is_calibrated,
                "evidence_state": output.evidence_state.value,
                "review_reasons": [item.value for item in output.review_reasons],
            }
        )

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


def _classify_runtime_failure(exc: Exception, *, step_name: str) -> RuntimeFailure:
    error_type = type(exc).__name__
    lowered_type = error_type.lower()
    lowered_message = str(exc).lower()

    if step_name == "normalize":
        kind = RuntimeFailureKind.INVALID_INPUT
        retryable = False
    elif "promptsize" in lowered_type or "context exceeds" in lowered_message:
        kind = RuntimeFailureKind.INPUT_LIMIT_EXCEEDED
        retryable = False
    elif step_name == "analyze_llm" and ("admission" in lowered_type or "ratelimit" in lowered_type or "rate_limit" in lowered_type or "capacity" in lowered_type):
        kind = RuntimeFailureKind.ANALYZER_CAPACITY
        retryable = True
    elif step_name == "analyze_llm" and (isinstance(exc, TimeoutError) or "timeout" in lowered_type):
        kind = RuntimeFailureKind.ANALYZER_TIMEOUT
        retryable = True
    elif step_name == "analyze_llm" and hasattr(exc, "stage"):
        kind = RuntimeFailureKind.ANALYZER_OUTPUT_INVALID
        retryable = False
    elif step_name == "analyze_llm":
        kind = RuntimeFailureKind.ANALYZER_UNAVAILABLE
        retryable = True
    elif step_name == "schema_validate":
        kind = RuntimeFailureKind.OUTPUT_VALIDATION_FAILED
        retryable = False
    elif step_name == "decide":
        kind = RuntimeFailureKind.DECISION_POLICY_FAILED
        retryable = False
    else:
        kind = RuntimeFailureKind.INTERNAL_ERROR
        retryable = False

    return RuntimeFailure(
        step_name=step_name,
        kind=kind,
        retryable=retryable,
        error_type=error_type,
        message=_safe_error_message(exc, step_name=step_name),
    )


def _safe_error_message(exc: Exception, *, step_name: str) -> str:
    error_type = type(exc).__name__
    if step_name == "analyze_llm" and not hasattr(exc, "stage"):
        return f"{error_type} while invoking configured SOC analyzer"
    message = " ".join(str(exc).split())
    if not message:
        message = "runtime step failed"
    return message[:1000]
