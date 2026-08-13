"""Bounded prompts for one auditable model-output contract correction."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from soc_agent.contracts import LLMAnalysisRequest, RoleVerificationClaim
from soc_agent.pipeline.analysis_context import project_analysis_context

ANALYSIS_OUTPUT_REPAIR_PROMPT_VERSION = "soc-analysis-output-repair-v4"
ANALYSIS_SECTION_OUTPUT_REPAIR_PROMPT_VERSION = "soc-analysis-section-output-repair-v1"
ROLE_VERIFICATION_OUTPUT_REPAIR_PROMPT_VERSION = "soc-role-verification-output-repair-v1"
MAX_OUTPUT_REPAIR_CANDIDATE_CHARS = 100_000
MAX_OUTPUT_REPAIR_ERROR_CHARS = 4_000
MAX_OUTPUT_REPAIR_CONTEXT_CHARS = 190_000


class OutputRepairPromptSizeError(ValueError):
    """Raised when a bounded correction projection exceeds its hard cap."""


@dataclass(frozen=True)
class OutputRepairPrompt:
    prompt_version: str
    system: str
    user: str
    context: Mapping[str, Any]

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


def build_analysis_output_repair_prompt(
    request: LLMAnalysisRequest,
    *,
    invalid_candidate: Any,
    validation_error: Exception,
    response_schema: Mapping[str, Any],
) -> OutputRepairPrompt:
    """Ask the model to repair structure without receiving raw vendor input."""

    projected = project_analysis_context(request)
    context = {
        "schema_version": "soc.analysis_output_repair_request.v1",
        "prompt_version": ANALYSIS_OUTPUT_REPAIR_PROMPT_VERSION,
        "invalid_candidate": _bounded_candidate(invalid_candidate),
        "validation_error": _bounded_error(validation_error),
        "allowed_reference_catalogs": projected.get("reference_catalogs") or {},
        "required_response_schema": response_schema,
    }
    return _build_prompt(
        prompt_version=ANALYSIS_OUTPUT_REPAIR_PROMPT_VERSION,
        object_name="AnalysisModelOutput.v1",
        context=context,
        additional_rules=(
            "Preserve the original verdict, observations, and security reasoning unless the validation error requires a consistency correction.",
            "For duplicate or invalid references, retain only exact catalog-backed references; never invent a replacement fact.",
            (
                "Every response target must exactly match one emitted adjudicated entity by entity type and value. "
                "Its action-specific target_role may differ from the entity's global semantic role; remove the "
                "proposal only when the target entity itself was not adjudicated."
            ),
            "Every direction or role context_ref must be an exact ID from the supplied S/A/M/C/T context catalog; it need not be repeated in a referenced R-* item.",
            "Return E-* references only; do not copy catalog source paths or values.",
            "Do not emit evidence, knowledge_candidates, nested schema_version, proposal_id, policy_review_required, or automation_allowed fields; Runtime owns them.",
        ),
    )


def build_analysis_section_output_repair_prompt(
    request: LLMAnalysisRequest,
    *,
    accepted_analysis: Mapping[str, Any],
    invalid_sections: Sequence[str],
    invalid_section_candidates: Mapping[str, Any],
    validation_issues: Sequence[Mapping[str, Any]],
    response_schema: Mapping[str, Any],
) -> OutputRepairPrompt:
    """Repair only rejected optional sections without regenerating the core."""

    projected = project_analysis_context(request)
    reference_catalogs = projected.get("reference_catalogs") or {}
    accepted_core = {
        key: value
        for key, value in accepted_analysis.items()
        if key
        in {
            "schema_version",
            "verdict",
            "confidence",
            "summary",
            "evidence",
            "reasoning",
            "evidence_gaps",
            "manual_checks",
            "reason",
            "recommended_action",
        }
    }
    section_shapes = {section: response_schema[section] for section in invalid_sections if section in response_schema}
    context = {
        "schema_version": "soc.analysis_section_output_repair_request.v1",
        "prompt_version": ANALYSIS_SECTION_OUTPUT_REPAIR_PROMPT_VERSION,
        "invalid_sections": list(invalid_sections),
        "accepted_core_immutable": accepted_core,
        "invalid_section_candidates": dict(invalid_section_candidates),
        "validation_issues": list(validation_issues),
        "allowed_reference_catalogs": reference_catalogs,
        "required_section_shapes": section_shapes,
        "required_patch_shape": {
            "schema_version": "soc.analysis_section_patch.v1",
            "sections": section_shapes,
        },
    }
    return _build_prompt(
        prompt_version=ANALYSIS_SECTION_OUTPUT_REPAIR_PROMPT_VERSION,
        object_name="AnalysisResult optional-section patch",
        context=context,
        additional_rules=(
            "Return exactly the listed invalid sections under sections; do not return or modify accepted core fields.",
            "Use only E-* IDs already selected in accepted_core_immutable.evidence and R-* IDs already present in accepted_core_immutable.reasoning.",
            "An unresolved semantic role must use value=null; a tentative, resolved_from_evidence, or conflicted role requires a concrete value.",
            "If an optional claim cannot be supported, return the inert not_assessed or empty representation allowed by that section schema.",
        ),
    )


def build_role_verification_output_repair_prompt(
    request: LLMAnalysisRequest,
    claims: Sequence[RoleVerificationClaim],
    *,
    invalid_candidate: Any,
    validation_error: Exception,
    response_schema: Mapping[str, Any],
) -> OutputRepairPrompt:
    """Repair verifier JSON while keeping first-pass claims untrusted."""

    projected = project_analysis_context(request)
    context = {
        "schema_version": "soc.role_verification_output_repair_request.v1",
        "prompt_version": ROLE_VERIFICATION_OUTPUT_REPAIR_PROMPT_VERSION,
        "invalid_candidate": _bounded_candidate(invalid_candidate),
        "validation_error": _bounded_error(validation_error),
        "candidate_claims_untrusted": [claim.model_dump(mode="json") for claim in claims],
        "allowed_reference_catalogs": projected.get("reference_catalogs") or {},
        "required_response_schema": response_schema,
    }
    return _build_prompt(
        prompt_version=ROLE_VERIFICATION_OUTPUT_REPAIR_PROMPT_VERSION,
        object_name="RoleVerificationCandidate.v1",
        context=context,
        additional_rules=(
            "Return exactly one review for every supplied RC-* claim and no others.",
            "Do not change an RC-* assertion; only correct its review object.",
            "Use only exact allowed E-* and S/A/M/C/T references.",
            "Do not output verdict, confidence, disposition, authorization, or execution fields.",
        ),
    )


def _build_prompt(
    *,
    prompt_version: str,
    object_name: str,
    context: Mapping[str, Any],
    additional_rules: Sequence[str],
) -> OutputRepairPrompt:
    encoded = json.dumps(
        context,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if len(encoded) > MAX_OUTPUT_REPAIR_CONTEXT_CHARS:
        raise OutputRepairPromptSizeError(f"bounded output repair context exceeds {MAX_OUTPUT_REPAIR_CONTEXT_CHARS} characters")
    system = "\n".join(
        [
            f"You are a schema-correction node for {object_name}.",
            "Do not perform a fresh alert analysis and do not add security facts.",
            "Correct only JSON shape, required fields, exact reference integrity, and internal structural consistency.",
            "If a claim cannot be made structurally valid from the supplied candidate and catalogs, remove the optional claim or mark the supported status unresolved where the schema permits.",
            "Return one complete JSON object only, without markdown or prose outside it.",
            *additional_rules,
        ]
    )
    user = "\n".join(
        [
            "Correct the invalid model candidate according to the validation error and required schema.",
            "The reference catalogs are authoritative for IDs and scalar facts.",
            "Do not quote or explain the validation error in the output.",
            "",
            "Bounded correction context:",
            json.dumps(context, ensure_ascii=False, indent=2, default=str),
        ]
    )
    return OutputRepairPrompt(
        prompt_version=prompt_version,
        system=system,
        user=user,
        context=context,
    )


def _bounded_candidate(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:MAX_OUTPUT_REPAIR_CANDIDATE_CHARS]


def _bounded_error(error: Exception) -> dict[str, Any]:
    return {
        "error_type": type(error).__name__,
        "stage": getattr(error, "stage", "unknown"),
        "message": str(error)[:MAX_OUTPUT_REPAIR_ERROR_CHARS],
    }


__all__ = [
    "ANALYSIS_OUTPUT_REPAIR_PROMPT_VERSION",
    "ANALYSIS_SECTION_OUTPUT_REPAIR_PROMPT_VERSION",
    "MAX_OUTPUT_REPAIR_CONTEXT_CHARS",
    "OutputRepairPrompt",
    "OutputRepairPromptSizeError",
    "ROLE_VERIFICATION_OUTPUT_REPAIR_PROMPT_VERSION",
    "build_analysis_output_repair_prompt",
    "build_analysis_section_output_repair_prompt",
    "build_role_verification_output_repair_prompt",
]
