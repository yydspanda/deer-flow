"""Conditional LLM verifier for first-pass direction, role, and target claims."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from soc_agent.contracts import (
    AnalysisEvidenceGroundingReport,
    AnalysisProviderInvocation,
    AnalysisProviderPurpose,
    AnalysisResult,
    LLMAnalysisRequest,
    RoleAdjudicationVerificationResult,
    RoleVerificationFailureKind,
    RoleVerificationNodeOutput,
    RoleVerificationStatus,
    RoleVerificationTriggerDecision,
    derive_role_verification_status,
    stable_role_verification_claims_hash,
)
from soc_agent.llm.analyzer import (
    LLMChatClient,
    attach_failed_model_invocation_metadata,
    coerce_chat_response,
    merge_model_usage,
    model_invocation_metadata,
)
from soc_agent.llm.json_parser import (
    ROLE_VERIFICATION_JSON_PARSER_VERSION,
    LLMOutputParseError,
    parse_role_verification_output,
)
from soc_agent.llm.usage import usage_measurement_available
from soc_agent.pipeline.role_verification import (
    DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE,
    build_role_verification_claims,
    evaluate_role_verification_trigger,
)
from soc_agent.prompts import (
    ROLE_VERIFICATION_PROMPT_VERSION,
    build_role_verification_output_repair_prompt,
    build_role_verification_prompt,
    role_verification_response_schema,
)
from soc_agent.protocols import RoleAdjudicationVerifier
from soc_agent.utils.hashing import stable_hash

ROLE_VERIFIER_STEP_NAME = "verify_roles_llm"


class JsonLLMRoleVerifier:
    """Run a narrow adversarial pass only after a deterministic trigger."""

    step_name = ROLE_VERIFIER_STEP_NAME

    def __init__(
        self,
        *,
        client: LLMChatClient,
        model_name: str,
        minimum_confidence: float = DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE,
        output_retry_attempts: int = 1,
    ) -> None:
        if not model_name:
            raise ValueError("model_name is required for JsonLLMRoleVerifier")
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be within [0, 1]")
        self._client = client
        if output_retry_attempts not in {0, 1}:
            raise ValueError("output_retry_attempts must be 0 or 1")
        self.output_retry_attempts = output_retry_attempts
        self.model_name = model_name
        self.prompt_version = ROLE_VERIFICATION_PROMPT_VERSION
        self.parser_version = ROLE_VERIFICATION_JSON_PARSER_VERSION
        self.minimum_confidence = minimum_confidence

    def evaluate_trigger(
        self,
        analysis: AnalysisResult,
        *,
        request: LLMAnalysisRequest,
        grounding: AnalysisEvidenceGroundingReport,
    ) -> RoleVerificationTriggerDecision:
        return evaluate_role_verification_trigger(
            analysis,
            request=request,
            grounding=grounding,
            minimum_confidence=self.minimum_confidence,
        )

    def verify(
        self,
        request: LLMAnalysisRequest,
        analysis: AnalysisResult,
        trigger: RoleVerificationTriggerDecision,
        *,
        primary_model_name: str,
    ) -> RoleVerificationNodeOutput:
        return self._verify(
            request,
            analysis,
            trigger,
            primary_model_name=primary_model_name,
            before_retry=None,
        )

    def verify_with_provider_hook(
        self,
        request: LLMAnalysisRequest,
        analysis: AnalysisResult,
        trigger: RoleVerificationTriggerDecision,
        *,
        primary_model_name: str,
        before_retry: Callable[[AnalysisProviderInvocation], None],
    ) -> RoleVerificationNodeOutput:
        """Verify with an auditable hook before one output retry."""

        return self._verify(
            request,
            analysis,
            trigger,
            primary_model_name=primary_model_name,
            before_retry=before_retry,
        )

    def _verify(
        self,
        request: LLMAnalysisRequest,
        analysis: AnalysisResult,
        trigger: RoleVerificationTriggerDecision,
        *,
        primary_model_name: str,
        before_retry: Callable[[AnalysisProviderInvocation], None] | None,
    ) -> RoleVerificationNodeOutput:
        if not trigger.triggered:
            raise ValueError("role verifier cannot run when its trigger is false")
        claims = build_role_verification_claims(analysis)
        claims_hash = stable_role_verification_claims_hash(claims)
        if claims_hash != trigger.claims_hash or len(claims) != trigger.claim_count:
            raise ValueError("role verification claims changed after trigger evaluation")

        prompt = build_role_verification_prompt(request, analysis, claims)
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
        retry_kind: str | None = None
        try:
            parsed = _parse_role_verification_response(
                response,
                request=request,
                claims=claims,
            )
        except LLMOutputParseError as exc:
            if self.output_retry_attempts == 0:
                attach_failed_model_invocation_metadata(
                    exc,
                    responses=responses,
                    provider_call_count=1,
                    output_retry_attempted=False,
                )
                raise
            initial_parse_error = exc
            if exc.stage == "extract_text":
                retry_prompt = prompt
                retry_kind = "empty_response_retry"
            else:
                retry_prompt = build_role_verification_output_repair_prompt(
                    claims,
                    invalid_candidate=response.content,
                    validation_error=exc,
                    response_schema=role_verification_response_schema(),
                    allowed_reference_catalogs=prompt.context["reference_catalogs"],
                    runtime_constraints=prompt.context["runtime_constraints"],
                )
                retry_kind = "contract_correction"
            if before_retry is not None:
                try:
                    before_retry(
                        AnalysisProviderInvocation(
                            step_name=self.step_name,
                            purpose=AnalysisProviderPurpose.ROLE_VERIFICATION_RETRY,
                            model_name=self.model_name,
                            prompt_version=retry_prompt.prompt_version,
                            parser_version=self.parser_version,
                            optional=True,
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
                        model_name=self.model_name,
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
                parsed = _parse_role_verification_response(
                    retry_response,
                    request=request,
                    claims=claims,
                )
            except LLMOutputParseError as retry_exc:
                attach_failed_model_invocation_metadata(
                    retry_exc,
                    responses=responses,
                    provider_call_count=2,
                    output_retry_attempted=True,
                    output_retry_kind=retry_kind,
                )
                raise
            response = retry_response
        verifier_model_name = response.model_name or self.model_name
        verification = RoleAdjudicationVerificationResult(
            status=derive_role_verification_status(parsed.candidate.claim_reviews),
            trigger=trigger,
            claims=claims,
            claim_reviews=parsed.candidate.claim_reviews,
            primary_model_name=primary_model_name,
            verifier_model_name=verifier_model_name,
            same_model_verification=(primary_model_name.casefold() == verifier_model_name.casefold()),
            prompt_version=prompt.prompt_version,
            parser_version=parsed.parser_version,
            repair_applied=parsed.repair_applied,
            repair_log=parsed.repair_log,
        )
        metadata: dict[str, Any] = {
            "verifier": "json_llm_role_verifier",
            "claims_hash": claims_hash,
            "claim_count": len(claims),
            "trigger_reasons": [reason.value for reason in trigger.reasons],
            "prompt_hash": stable_hash({"messages": prompt.messages()}),
            "candidate_hash": stable_hash({"candidate_text": parsed.candidate_text}),
            "repair_applied": parsed.repair_applied,
            "same_model_verification": verification.same_model_verification,
            "provider_call_count": len(responses),
            "usage_complete": all(usage_measurement_available(item.metadata) for item in responses),
            "output_retry_attempted": initial_parse_error is not None,
            **model_invocation_metadata(
                responses,
                provider_call_count=len(responses),
            ),
        }
        if initial_parse_error is not None:
            metadata.update(
                {
                    "output_retry_kind": retry_kind,
                    "output_retry_prompt_version": retry_prompt.prompt_version,
                    "initial_parse_error_stage": initial_parse_error.stage,
                    "initial_parse_error_type": type(initial_parse_error).__name__,
                }
            )
        if parsed.repair_log:
            metadata["repair_log"] = parsed.repair_log
        usage = merge_model_usage(*(item.usage for item in responses))
        if usage:
            metadata["usage"] = usage
        if response.metadata:
            metadata["response_metadata"] = dict(response.metadata)
        return RoleVerificationNodeOutput(
            verification=verification,
            metadata=metadata,
        )


def _parse_role_verification_response(
    response,
    *,
    request: LLMAnalysisRequest,
    claims,
):
    return parse_role_verification_output(
        response.content,
        claims=claims,
        evidence_catalog=request.evidence_catalog,
        context_catalog=request.context_catalog,
        canonical_network=request.canonical_entities.network,
    )


def unavailable_role_verification(
    *,
    trigger: RoleVerificationTriggerDecision,
    analysis: AnalysisResult,
    primary_model_name: str,
    verifier: RoleAdjudicationVerifier,
    error: Exception,
) -> RoleAdjudicationVerificationResult:
    """Convert optional verifier failure into explicit fail-closed lineage."""

    failure_kind = RoleVerificationFailureKind.OUTPUT_INVALID if isinstance(error, LLMOutputParseError) else RoleVerificationFailureKind.PROVIDER_ERROR
    claims = build_role_verification_claims(analysis)
    return RoleAdjudicationVerificationResult(
        status=RoleVerificationStatus.UNAVAILABLE,
        trigger=trigger,
        claims=claims,
        primary_model_name=primary_model_name,
        verifier_model_name=verifier.model_name,
        same_model_verification=(primary_model_name.casefold() == verifier.model_name.casefold()),
        prompt_version=verifier.prompt_version,
        parser_version=verifier.parser_version,
        failure_kind=failure_kind,
        warnings=[f"role verifier failed closed: {type(error).__name__}"],
    )


__all__ = [
    "ROLE_VERIFIER_STEP_NAME",
    "JsonLLMRoleVerifier",
    "unavailable_role_verification",
]
