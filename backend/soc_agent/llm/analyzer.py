"""LLM-backed SOC analysis node adapters.

This module does not choose runtime control flow. It only implements the
bounded analysis node contract: build the versioned prompt, call a supplied
chat client, parse/repair JSON, and return an auditable node output.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from soc_agent.contracts import (
    AnalysisNodeOutput,
    AnalysisOutputIssue,
    AnalysisOutputQuality,
    AnalysisOutputQualityStatus,
    AnalysisOutputSection,
    AnalysisProviderInvocation,
    AnalysisProviderPurpose,
    LLMAnalysisRequest,
)
from soc_agent.llm.json_parser import (
    ANALYSIS_JSON_PARSER_VERSION,
    LLMOutputParseError,
    ParsedAnalysisResult,
    RecoverableAnalysisResult,
    parse_analysis_result_output,
    parse_analysis_section_patch_output,
    recover_analysis_result_output,
)
from soc_agent.llm.usage import (
    normalize_provider_usage,
    resolve_chat_usage,
    usage_measurement_available,
    usage_measurement_summary,
)
from soc_agent.pipeline.analyzer import (
    STUB_ANALYZER_MODEL_NAME,
    STUB_ANALYZER_PROMPT_VERSION,
    StubLLMAnalyzer,
    analyze_output_protocol_fallback,
)
from soc_agent.prompts import (
    ANALYSIS_PROMPT_VERSION,
    analysis_response_schema,
    build_analysis_output_repair_prompt,
    build_analysis_prompt,
)
from soc_agent.utils.hashing import stable_hash

LLM_ANALYZER_STEP_NAME = "analyze_llm"


@dataclass(frozen=True)
class LLMChatResponse:
    """Normalized response shape returned by SOC LLM chat clients."""

    content: Any
    model_name: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class LLMChatClient(Protocol):
    """Small adapter protocol for DeerFlow/OpenAI/local model clients."""

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model_name: str,
    ) -> LLMChatResponse | str: ...


class JsonLLMAnalyzer:
    """Prompt + JSON parser backed SOC analysis node.

    Entry adapters select this analyzer explicitly through ``SocLLMSettings``.
    Direct ``SocAnalysisService`` construction remains deterministic for tests.
    """

    step_name = LLM_ANALYZER_STEP_NAME
    parser_version = ANALYSIS_JSON_PARSER_VERSION

    def __init__(
        self,
        *,
        client: LLMChatClient,
        model_name: str,
        output_retry_attempts: int = 1,
        output_fallback_model_name: str | None = None,
    ) -> None:
        if not model_name:
            raise ValueError("model_name is required for JsonLLMAnalyzer")
        self._client = client
        if output_retry_attempts not in {0, 1}:
            raise ValueError("output_retry_attempts must be 0 or 1")
        self.output_retry_attempts = output_retry_attempts
        self.model_name = model_name
        self.output_fallback_model_name = output_fallback_model_name or None
        self.prompt_version = ANALYSIS_PROMPT_VERSION

    def analyze(self, request: LLMAnalysisRequest) -> AnalysisNodeOutput:
        return self._analyze(request, before_retry=None)

    def analyze_with_provider_hook(
        self,
        request: LLMAnalysisRequest,
        *,
        before_retry: Callable[[AnalysisProviderInvocation], None],
    ) -> AnalysisNodeOutput:
        """Analyze with an auditable hook immediately before an output retry."""

        return self._analyze(request, before_retry=before_retry)

    def _analyze(
        self,
        request: LLMAnalysisRequest,
        *,
        before_retry: Callable[[AnalysisProviderInvocation], None] | None,
    ) -> AnalysisNodeOutput:
        prompt = build_analysis_prompt(request)
        prompt_messages = prompt.messages()
        try:
            response = coerce_chat_response(
                self._client.complete(
                    prompt_messages,
                    model_name=self.model_name,
                ),
                messages=prompt_messages,
            )
        except Exception as exc:
            attach_failed_model_invocation_metadata(
                exc,
                responses=(),
                provider_call_count=1,
                output_retry_attempted=False,
            )
            raise
        responses = [response]
        initial_parse_error: LLMOutputParseError | None = None
        retry_parse_error: Exception | None = None
        retry_kind: str | None = None
        retry_prompt = None
        recovery: RecoverableAnalysisResult | None = None
        parsed: ParsedAnalysisResult | None = None
        output_quality: AnalysisOutputQuality
        analysis = None
        effective_analyzer_step_name: str | None = None
        result_model_name = response.model_name or self.model_name
        result_prompt_version = prompt.prompt_version
        try:
            parsed = _parse_analysis_response(response, request=request)
        except LLMOutputParseError as exc:
            initial_parse_error = exc
            recovery = _recover_analysis_response(response, request=request)
            if recovery is not None and not recovery.invalid_sections:
                parsed = _parsed_from_recovery(recovery)
                output_quality = _repaired_output_quality(
                    issues=(),
                )
                retry_kind = "local_section_recovery"
            elif recovery is not None:
                parsed = _parsed_from_recovery(recovery)
                output_quality = _degraded_output_quality(
                    recovery,
                    repair_attempted=recovery.repair_applied,
                )
                retry_kind = "optional_sections_degraded_without_retry"
            elif self.output_retry_attempts == 0:
                retry_kind = "retry_disabled"
                analysis = analyze_output_protocol_fallback(request)
                output_quality = _fallback_output_quality(
                    issues=[_quality_issue(AnalysisOutputSection.CORE, exc, attempt=1)],
                    repair_attempted=False,
                )
                effective_analyzer_step_name = "analyze_stub"
            else:
                try:
                    if exc.stage == "extract_text":
                        retry_prompt = prompt
                        retry_kind = "empty_response_retry"
                        retry_purpose = AnalysisProviderPurpose.PRIMARY_ANALYSIS_RETRY
                    else:
                        retry_prompt = build_analysis_output_repair_prompt(
                            request,
                            invalid_candidate=response.content,
                            validation_error=exc,
                            response_schema=analysis_response_schema(),
                        )
                        retry_kind = "contract_correction"
                        retry_purpose = AnalysisProviderPurpose.PRIMARY_ANALYSIS_RETRY
                except Exception as prompt_exc:  # noqa: BLE001 - degrade without another provider call
                    retry_parse_error = prompt_exc
                    if recovery is not None:
                        parsed = _parsed_from_recovery(recovery)
                        output_quality = _degraded_output_quality(
                            recovery,
                            repair_attempted=True,
                            additional_issues=[
                                _quality_issue(
                                    recovery.invalid_sections[0],
                                    prompt_exc,
                                    attempt=1,
                                )
                            ],
                        )
                    else:
                        analysis = analyze_output_protocol_fallback(request)
                        output_quality = _fallback_output_quality(
                            issues=[
                                _quality_issue(AnalysisOutputSection.CORE, exc, attempt=1),
                                _quality_issue(AnalysisOutputSection.CORE, prompt_exc, attempt=1),
                            ],
                            repair_attempted=True,
                        )
                        effective_analyzer_step_name = "analyze_stub"

                if retry_prompt is not None:
                    retry_model_name = self.output_fallback_model_name or self.model_name
                    if before_retry is not None:
                        try:
                            before_retry(
                                AnalysisProviderInvocation(
                                    step_name=self.step_name,
                                    purpose=retry_purpose,
                                    model_name=retry_model_name,
                                    prompt_version=retry_prompt.prompt_version,
                                    parser_version=self.parser_version,
                                )
                            )
                        except Exception as hook_exc:
                            attach_failed_model_invocation_metadata(
                                hook_exc,
                                responses=responses,
                                provider_call_count=1,
                                output_retry_attempted=False,
                                output_retry_kind=retry_kind,
                            )
                            raise
                    try:
                        retry_messages = retry_prompt.messages()
                        retry_response = coerce_chat_response(
                            self._client.complete(
                                retry_messages,
                                model_name=retry_model_name,
                            ),
                            messages=retry_messages,
                        )
                    except Exception as retry_exc:
                        attach_failed_model_invocation_metadata(
                            retry_exc,
                            responses=responses,
                            provider_call_count=2,
                            output_retry_attempted=True,
                            output_retry_kind=retry_kind,
                        )
                        raise
                    responses.append(retry_response)
                    try:
                        if recovery is not None:
                            parsed = parse_analysis_section_patch_output(
                                retry_response.content,
                                recovery=recovery,
                                evidence_catalog=request.evidence_catalog,
                                context_catalog=request.context_catalog,
                            )
                        else:
                            parsed = _parse_analysis_response(
                                retry_response,
                                request=request,
                            )
                        output_quality = _repaired_output_quality(
                            issues=(_quality_issues_from_recovery(recovery) if recovery is not None else [_quality_issue(AnalysisOutputSection.CORE, exc, attempt=1)]),
                        )
                        response = retry_response
                        result_model_name = retry_response.model_name or retry_model_name
                    except LLMOutputParseError as retry_exc:
                        retry_parse_error = retry_exc
                        if recovery is None:
                            retry_recovery = _recover_analysis_response(
                                retry_response,
                                request=request,
                            )
                        else:
                            retry_recovery = None
                        if retry_recovery is not None:
                            parsed = _parsed_from_recovery(retry_recovery)
                            if retry_recovery.invalid_sections:
                                output_quality = _degraded_output_quality(
                                    retry_recovery,
                                    repair_attempted=True,
                                    additional_issues=[
                                        _quality_issue(
                                            retry_recovery.invalid_sections[0],
                                            retry_exc,
                                            attempt=2,
                                        )
                                    ],
                                )
                            else:
                                output_quality = _repaired_output_quality(
                                    issues=[
                                        _quality_issue(AnalysisOutputSection.CORE, exc, attempt=1),
                                        _quality_issue(AnalysisOutputSection.CORE, retry_exc, attempt=2),
                                    ],
                                )
                            response = retry_response
                            result_model_name = retry_response.model_name or retry_model_name
                        elif recovery is not None:
                            parsed = _parsed_from_recovery(recovery)
                            output_quality = _degraded_output_quality(
                                recovery,
                                repair_attempted=True,
                                additional_issues=[
                                    _quality_issue(
                                        recovery.invalid_sections[0],
                                        retry_exc,
                                        attempt=2,
                                    )
                                ],
                            )
                        else:
                            analysis = analyze_output_protocol_fallback(request)
                            output_quality = _fallback_output_quality(
                                issues=[
                                    _quality_issue(AnalysisOutputSection.CORE, exc, attempt=1),
                                    _quality_issue(AnalysisOutputSection.CORE, retry_exc, attempt=2),
                                ],
                                repair_attempted=True,
                            )
                            effective_analyzer_step_name = "analyze_stub"

        if parsed is not None:
            analysis = parsed.result
            if initial_parse_error is None:
                output_quality = _repaired_output_quality(issues=()) if parsed.repair_applied else AnalysisOutputQuality()

        if analysis is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("SOC analyzer did not resolve a valid result or fallback")

        if output_quality.status is AnalysisOutputQualityStatus.DETERMINISTIC_FALLBACK:
            result_model_name = STUB_ANALYZER_MODEL_NAME
            result_prompt_version = STUB_ANALYZER_PROMPT_VERSION

        metadata: dict[str, Any] = {
            "analyzer": "json_llm",
            "repair_applied": output_quality.status is not AnalysisOutputQualityStatus.ACCEPTED,
            "prompt_hash": stable_hash({"messages": prompt.messages()}),
            "skill_context_hash": stable_hash(request.skill_context.model_dump(mode="json", exclude_none=True)),
            "selected_skills": [item.skill_name for item in request.skill_context.selected_skills],
            "candidate_hash": stable_hash({"candidate_text": (parsed.candidate_text if parsed is not None else str(response.content)[:100_000])}),
            "provider_call_count": len(responses),
            "usage_complete": all(usage_measurement_available(item.metadata) for item in responses),
            "output_retry_attempted": len(responses) > 1,
            "output_quality": output_quality.model_dump(mode="json"),
            **model_invocation_metadata(
                responses,
                provider_call_count=len(responses),
            ),
        }
        if initial_parse_error is not None:
            metadata.update(
                {
                    "output_retry_kind": retry_kind,
                    "initial_parse_error_stage": initial_parse_error.stage,
                    "initial_parse_error_type": type(initial_parse_error).__name__,
                    "initial_parse_error_field_paths": list(initial_parse_error.field_paths),
                    "initial_parse_error_issue_codes": list(initial_parse_error.issue_codes),
                }
            )
            if retry_prompt is not None:
                metadata["output_retry_prompt_version"] = retry_prompt.prompt_version
        if retry_parse_error is not None:
            metadata.update(
                {
                    "retry_parse_error_stage": getattr(
                        retry_parse_error,
                        "stage",
                        "output_repair",
                    ),
                    "retry_parse_error_type": type(retry_parse_error).__name__,
                    "retry_parse_error_field_paths": list(getattr(retry_parse_error, "field_paths", ())),
                    "retry_parse_error_issue_codes": list(getattr(retry_parse_error, "issue_codes", ())),
                }
            )
        if parsed is not None and parsed.repair_log:
            metadata["repair_log"] = parsed.repair_log
        if parsed is not None:
            metadata["model_output_schema_version"] = parsed.model_output_schema_version
            if parsed.hydration_log:
                metadata["hydration_log"] = parsed.hydration_log
        usage = merge_model_usage(*(item.usage for item in responses))
        if usage:
            metadata["usage"] = usage
        if response.metadata:
            metadata["response_metadata"] = dict(response.metadata)

        return AnalysisNodeOutput(
            analysis=analysis,
            model_name=result_model_name,
            prompt_version=result_prompt_version,
            parser_version=(parsed.parser_version if parsed is not None else self.parser_version),
            output_quality=output_quality,
            effective_analyzer_step_name=effective_analyzer_step_name,
            metadata=metadata,
        )


def _parse_analysis_response(
    response: LLMChatResponse,
    *,
    request: LLMAnalysisRequest,
):
    return parse_analysis_result_output(
        response.content,
        evidence_catalog=request.evidence_catalog,
        context_catalog=request.context_catalog,
    )


def _recover_analysis_response(
    response: LLMChatResponse,
    *,
    request: LLMAnalysisRequest,
) -> RecoverableAnalysisResult | None:
    try:
        return recover_analysis_result_output(
            response.content,
            evidence_catalog=request.evidence_catalog,
            context_catalog=request.context_catalog,
        )
    except LLMOutputParseError:
        return None


def _parsed_from_recovery(
    recovery: RecoverableAnalysisResult,
) -> ParsedAnalysisResult:
    return ParsedAnalysisResult(
        result=recovery.result,
        repair_applied=recovery.repair_applied,
        repair_log=list(recovery.repair_log),
        hydration_log=list(recovery.hydration_log),
        model_output_schema_version=recovery.model_output_schema_version,
        candidate_text=recovery.candidate_text,
    )


def _quality_issue(
    section: AnalysisOutputSection,
    error: Exception,
    *,
    attempt: int,
) -> AnalysisOutputIssue:
    return AnalysisOutputIssue(
        section=section,
        stage=str(getattr(error, "stage", "output_repair"))[:128],
        error_type=type(error.__cause__ or error).__name__[:256],
        attempt=attempt,
        field_paths=list(getattr(error, "field_paths", ())),
        issue_codes=list(getattr(error, "issue_codes", ())),
    )


def _quality_issues_from_recovery(
    recovery: RecoverableAnalysisResult,
) -> list[AnalysisOutputIssue]:
    return [
        AnalysisOutputIssue(
            section=issue.section,
            stage=issue.stage[:128],
            error_type=issue.error_type[:256],
            attempt=1,
            field_paths=list(issue.field_paths),
            issue_codes=list(issue.issue_codes),
        )
        for issue in recovery.issues
    ]


def _repaired_output_quality(
    *,
    issues: Sequence[AnalysisOutputIssue],
) -> AnalysisOutputQuality:
    return AnalysisOutputQuality(
        status=AnalysisOutputQualityStatus.REPAIRED,
        accepted_sections=list(AnalysisOutputSection),
        repair_attempted=True,
        issues=list(issues),
    )


def _degraded_output_quality(
    recovery: RecoverableAnalysisResult,
    *,
    repair_attempted: bool,
    additional_issues: Sequence[AnalysisOutputIssue] = (),
) -> AnalysisOutputQuality:
    return AnalysisOutputQuality(
        status=AnalysisOutputQualityStatus.DEGRADED,
        accepted_sections=list(recovery.accepted_sections),
        degraded_sections=list(recovery.invalid_sections),
        repair_attempted=repair_attempted,
        issues=[
            *_quality_issues_from_recovery(recovery),
            *additional_issues,
        ],
    )


def _fallback_output_quality(
    *,
    issues: Sequence[AnalysisOutputIssue],
    repair_attempted: bool,
) -> AnalysisOutputQuality:
    return AnalysisOutputQuality(
        status=AnalysisOutputQualityStatus.DETERMINISTIC_FALLBACK,
        accepted_sections=[],
        degraded_sections=list(AnalysisOutputSection),
        repair_attempted=repair_attempted,
        deterministic_fallback_used=True,
        issues=list(issues),
    )


def merge_model_usage(*values: Mapping[str, Any]) -> dict[str, Any]:
    """Sum integer usage counters while preserving non-numeric final metadata."""

    merged: dict[str, Any] = {}
    for value in values:
        for key, item in value.items():
            if isinstance(item, int) and not isinstance(item, bool):
                merged[key] = int(merged.get(key) or 0) + item
            else:
                merged[key] = item
    return merged


def attach_failed_model_invocation_metadata(
    error: Exception,
    *,
    responses: Sequence[LLMChatResponse],
    provider_call_count: int,
    output_retry_attempted: bool,
    output_retry_kind: str | None = None,
) -> None:
    """Attach bounded usage lineage to an exception without retaining output text."""

    metadata: dict[str, Any] = {
        "provider_call_count": provider_call_count,
        "usage_complete": (len(responses) == provider_call_count and all(usage_measurement_available(item.metadata) for item in responses)),
        "output_retry_attempted": output_retry_attempted,
        **model_invocation_metadata(
            responses,
            provider_call_count=provider_call_count,
            failed_call_measurement=getattr(
                error,
                "soc_llm_client_measurement",
                None,
            ),
        ),
    }
    if output_retry_kind is not None:
        metadata["output_retry_kind"] = output_retry_kind
    usage = merge_model_usage(*(item.usage for item in responses))
    if usage:
        metadata["usage"] = usage
    error.soc_model_invocation_metadata = metadata  # type: ignore[attr-defined]


def model_invocation_metadata(
    responses: Sequence[LLMChatResponse],
    *,
    provider_call_count: int,
    failed_call_measurement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build bounded per-call timing and usage-origin metadata."""

    calls: list[dict[str, Any]] = []
    usage_measurements: list[dict[str, Any]] = []
    for index, response in enumerate(responses, start=1):
        response_metadata = response.metadata if isinstance(response.metadata, Mapping) else {}
        raw_measurement = response_metadata.get("usage_measurement")
        if isinstance(raw_measurement, Mapping):
            measurement = dict(raw_measurement)
        elif response.usage:
            measurement = {
                "status": "reported",
                "method": "client_usage",
                "estimated": False,
            }
        else:
            measurement = {
                "status": "unavailable",
                "method": None,
                "estimated": False,
            }
        usage_measurements.append(measurement)
        call = {
            "call_index": index,
            "status": "completed",
            "usage_measurement": measurement,
        }
        for key in (
            "admission_wait_duration_ms",
            "provider_duration_ms",
            "client_total_duration_ms",
        ):
            value = response_metadata.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                call[key] = value
        calls.append(call)

    if isinstance(failed_call_measurement, Mapping) and len(calls) < provider_call_count:
        measurement = failed_call_measurement.get("usage_measurement")
        normalized_measurement = (
            dict(measurement)
            if isinstance(measurement, Mapping)
            else {
                "status": "unavailable",
                "method": None,
                "estimated": False,
            }
        )
        usage_measurements.append(normalized_measurement)
        failed_call = {
            "call_index": len(calls) + 1,
            "status": "failed",
            "usage_measurement": normalized_measurement,
        }
        for key in (
            "admission_wait_duration_ms",
            "provider_duration_ms",
            "client_total_duration_ms",
        ):
            value = failed_call_measurement.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                failed_call[key] = value
        calls.append(failed_call)

    summary = usage_measurement_summary(
        usage_measurements,
        expected_call_count=provider_call_count,
    )
    measured_call_durations = [float(call["client_total_duration_ms"]) for call in calls if isinstance(call.get("client_total_duration_ms"), (int, float)) and not isinstance(call.get("client_total_duration_ms"), bool)]
    return {
        "usage_measurement": summary,
        "provider_calls": calls,
        "provider_call_measured_duration_ms": (round(sum(measured_call_durations), 3) if measured_call_durations else None),
    }


def build_optional_llm_analyzer(
    *,
    enabled: bool,
    client: LLMChatClient | None = None,
    model_name: str = "stub",
) -> StubLLMAnalyzer | JsonLLMAnalyzer:
    """Feature-flagged analyzer factory.

    ``enabled=False`` is the safe default and returns the deterministic stub.
    ``enabled=True`` requires an injected client so tests and entry adapters
    can choose the model provider explicitly.
    """

    if not enabled:
        return StubLLMAnalyzer()
    if client is None:
        raise ValueError("client is required when SOC LLM analyzer is enabled")
    return JsonLLMAnalyzer(client=client, model_name=model_name)


def coerce_chat_response(
    response: LLMChatResponse | str,
    *,
    messages: Sequence[Mapping[str, str]] = (),
) -> LLMChatResponse:
    """Normalize usage even for minimal intranet-compatible chat clients."""

    normalized = response if isinstance(response, LLMChatResponse) else LLMChatResponse(content=response)
    metadata = dict(normalized.metadata)
    usage = normalize_provider_usage(normalized.usage)
    existing_measurement = metadata.get("usage_measurement")
    if usage and isinstance(existing_measurement, Mapping):
        measurement = dict(existing_measurement)
    else:
        usage, measurement = resolve_chat_usage(
            messages=messages,
            response_content=normalized.content,
            provider_usage=usage,
        )
    metadata["usage_measurement"] = measurement
    return LLMChatResponse(
        content=normalized.content,
        model_name=normalized.model_name,
        usage=usage,
        metadata=metadata,
    )
