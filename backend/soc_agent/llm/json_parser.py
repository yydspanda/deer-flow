"""Parse and validate LLM JSON output for SOC analysis nodes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from json_repair import loads as repair_json_loads
from pydantic import ValidationError

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisEvidenceCatalogItem,
    AnalysisOutputSection,
    AnalysisResult,
    RoleVerificationCandidate,
    RoleVerificationClaim,
)

ANALYSIS_JSON_PARSER_VERSION = "soc-analysis-json-parser-v15"
ROLE_VERIFICATION_JSON_PARSER_VERSION = "soc-role-verification-json-parser-v1"
MAX_ANALYSIS_RESPONSE_CHARS = 100_000
MAX_ROLE_VERIFICATION_RESPONSE_CHARS = 80_000
MAX_STRUCTURED_EVIDENCE_VALUE_CHARS = 4_000

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_KNOWLEDGE_DESTINATIONS = frozenset(
    {
        "general_skill",
        "tenant_memory",
        "governed_context",
        "provider_requirement",
        "adapter_mapping",
        "tenant_policy",
        "evaluation_fixture",
        "reject_or_verify",
    }
)
_KNOWLEDGE_DESTINATION_ALIASES = {
    "skill": "general_skill",
    "memory": "tenant_memory",
    "context": "governed_context",
    "provider": "provider_requirement",
    "tool": "provider_requirement",
    "mcp": "provider_requirement",
    "adapter": "adapter_mapping",
    "normalizer": "adapter_mapping",
    "parser": "adapter_mapping",
    "policy": "tenant_policy",
    "evaluation": "evaluation_fixture",
    "eval": "evaluation_fixture",
    "reject": "reject_or_verify",
    "verify": "reject_or_verify",
}
_KNOWLEDGE_SCOPES = frozenset({"global", "tenant", "provider", "source", "detection", "scenario", "event"})
_KNOWLEDGE_SCOPE_ALIASES = {
    "generic": "global",
    "cross_tenant": "global",
    "organization": "tenant",
    "org": "tenant",
    "company": "tenant",
    "vendor": "provider",
    "adapter": "provider",
    "product": "provider",
    "topic": "source",
    "system": "source",
    "rule": "detection",
    "rule_code": "detection",
    "alert": "event",
    "case": "event",
    "current_alert": "event",
}
_EMPTY_CONTEXT_REFERENCE_SENTINELS = frozenset({"", "none", "null", "n/a", "na", "not_applicable", "无", "不适用"})


class LLMOutputParseError(ValueError):
    """Raised when LLM output cannot become a valid ``AnalysisResult``."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        parser_version: str = ANALYSIS_JSON_PARSER_VERSION,
        repair_applied: bool = False,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.parser_version = parser_version
        self.repair_applied = repair_applied


@dataclass(frozen=True)
class ParsedAnalysisResult:
    """Validated analysis output plus parser audit metadata."""

    result: AnalysisResult
    parser_version: str = ANALYSIS_JSON_PARSER_VERSION
    repair_applied: bool = False
    repair_log: list[dict[str, Any]] = field(default_factory=list)
    candidate_text: str = ""


@dataclass(frozen=True)
class AnalysisSectionValidationIssue:
    """In-memory detail used to repair one rejected model-output section."""

    section: AnalysisOutputSection
    stage: str
    error_type: str
    message: str


@dataclass(frozen=True)
class RecoverableAnalysisResult:
    """Valid core plus independently accepted optional output sections."""

    result: AnalysisResult
    accepted_data: dict[str, Any]
    original_data: dict[str, Any]
    accepted_sections: tuple[AnalysisOutputSection, ...]
    invalid_sections: tuple[AnalysisOutputSection, ...]
    issues: tuple[AnalysisSectionValidationIssue, ...]
    repair_applied: bool = False
    repair_log: tuple[dict[str, Any], ...] = ()
    candidate_text: str = ""


@dataclass(frozen=True)
class _DecodedAnalysisCandidate:
    data: dict[str, Any]
    candidate_text: str
    repair_applied: bool
    repair_log: list[dict[str, Any]]


@dataclass(frozen=True)
class ParsedRoleVerificationCandidate:
    """Validated second-pass output plus parser audit metadata."""

    candidate: RoleVerificationCandidate
    parser_version: str = ROLE_VERIFICATION_JSON_PARSER_VERSION
    repair_applied: bool = False
    repair_log: list[dict[str, Any]] = field(default_factory=list)
    candidate_text: str = ""


def parse_analysis_result_output(
    response_content: Any,
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem] = (),
    context_catalog: Sequence[AnalysisContextCatalogItem] = (),
) -> ParsedAnalysisResult:
    """Parse LLM content into a domain-validated ``AnalysisResult``.

    This follows DeerFlow's conservative pattern first: extract text from modern
    content blocks, strip thinking/code fences, and accept a strict JSON object
    when one can be decoded. Only after strict parsing fails do we invoke
    ``json_repair``; the repaired object still has to pass schema and domain
    validation before it can enter runtime decision logic.
    """

    decoded = _decode_analysis_candidate(
        response_content,
        evidence_catalog=evidence_catalog,
        context_catalog=context_catalog,
    )
    result = _validate_analysis_result_data(
        decoded.data,
        repair_applied=decoded.repair_applied,
    )
    _validate_directional_context_references(
        result,
        context_catalog=context_catalog,
        repair_applied=decoded.repair_applied,
    )
    return ParsedAnalysisResult(
        result=result,
        repair_applied=decoded.repair_applied,
        repair_log=decoded.repair_log,
        candidate_text=decoded.candidate_text,
    )


def recover_analysis_result_output(
    response_content: Any,
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem] = (),
    context_catalog: Sequence[AnalysisContextCatalogItem] = (),
) -> RecoverableAnalysisResult | None:
    """Keep a valid core and validate each optional section independently.

    This function never invents replacement security semantics. A rejected
    optional section is replaced only by its inert contract default; callers
    may request one bounded model patch for exactly those sections.
    """

    decoded = _decode_analysis_candidate(
        response_content,
        evidence_catalog=evidence_catalog,
        context_catalog=context_catalog,
    )
    return _recover_analysis_candidate(
        decoded,
        context_catalog=context_catalog,
    )


def parse_analysis_section_patch_output(
    response_content: Any,
    *,
    recovery: RecoverableAnalysisResult,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem] = (),
    context_catalog: Sequence[AnalysisContextCatalogItem] = (),
) -> ParsedAnalysisResult:
    """Merge a strict patch for exactly the previously rejected sections."""

    text = _strip_markdown_code_fence(_strip_think_blocks(_extract_text(response_content))).strip()
    if not text:
        raise LLMOutputParseError("analysis section patch is empty", stage="extract_text")
    if len(text) > MAX_ANALYSIS_RESPONSE_CHARS:
        raise LLMOutputParseError(
            f"analysis section patch exceeds {MAX_ANALYSIS_RESPONSE_CHARS} characters",
            stage="output_size",
        )

    strict = _parse_strict_json_object_for_schema(
        text,
        schema_version="soc.analysis_section_patch.v1",
    )
    if strict is not None:
        patch, candidate_text = strict
        syntactic_repair_log: list[dict[str, Any]] = []
    else:
        candidate_text = _extract_repair_candidate(text)
        repaired = _repair_json_object(candidate_text)
        patch = repaired.data
        syntactic_repair_log = repaired.log

    if set(patch) != {"schema_version", "sections"}:
        raise LLMOutputParseError(
            "analysis section patch must contain only schema_version and sections",
            stage="section_patch_validation",
            repair_applied=bool(syntactic_repair_log),
        )
    sections = patch.get("sections")
    if not isinstance(sections, dict):
        raise LLMOutputParseError(
            "analysis section patch sections must be a JSON object",
            stage="section_patch_validation",
            repair_applied=bool(syntactic_repair_log),
        )
    expected_names = {_ANALYSIS_OPTIONAL_SECTION_FIELDS[section] for section in recovery.invalid_sections}
    if set(sections) != expected_names:
        raise LLMOutputParseError(
            f"analysis section patch must replace exactly these sections: {sorted(expected_names)}",
            stage="section_patch_validation",
            repair_applied=bool(syntactic_repair_log),
        )

    merged = dict(recovery.accepted_data)
    merged.update(sections)
    normalized, semantic_repair_log = _normalize_analysis_result_shape(
        merged,
        evidence_catalog=evidence_catalog,
        context_catalog=context_catalog,
    )
    repair_log = [*syntactic_repair_log, *semantic_repair_log]
    result = _validate_analysis_result_data(
        normalized,
        repair_applied=True,
    )
    _validate_directional_context_references(
        result,
        context_catalog=context_catalog,
        repair_applied=True,
    )
    return ParsedAnalysisResult(
        result=result,
        repair_applied=True,
        repair_log=repair_log,
        candidate_text=candidate_text,
    )


_ANALYSIS_CORE_FIELDS = frozenset(
    {
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
)
_ANALYSIS_OPTIONAL_SECTION_FIELDS = {
    AnalysisOutputSection.SCENARIO_ASSESSMENTS: "scenario_assessments",
    AnalysisOutputSection.NETWORK_DIRECTION: "network_direction",
    AnalysisOutputSection.ROLE_ADJUDICATION: "role_adjudication",
    AnalysisOutputSection.KNOWLEDGE_CANDIDATES: "knowledge_candidates",
}


def _decode_analysis_candidate(
    response_content: Any,
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
    context_catalog: Sequence[AnalysisContextCatalogItem],
) -> _DecodedAnalysisCandidate:
    text = _strip_markdown_code_fence(_strip_think_blocks(_extract_text(response_content))).strip()
    if not text:
        raise LLMOutputParseError("LLM output is empty", stage="extract_text")
    if len(text) > MAX_ANALYSIS_RESPONSE_CHARS:
        raise LLMOutputParseError(
            f"LLM output exceeds {MAX_ANALYSIS_RESPONSE_CHARS} characters",
            stage="output_size",
        )

    strict = _parse_strict_json_object(text)
    if strict is not None:
        data, candidate_text = strict
        repaired_data = data
        syntactic_repair_log: list[dict[str, Any]] = []
        syntactic_repair_applied = False
    else:
        candidate_text = _extract_repair_candidate(text)
        repaired = _repair_json_object(candidate_text)
        repaired_data = repaired.data
        syntactic_repair_log = repaired.log
        syntactic_repair_applied = True

    normalized, semantic_repair_log = _normalize_analysis_result_shape(
        repaired_data,
        evidence_catalog=evidence_catalog,
        context_catalog=context_catalog,
    )
    repair_log = [*syntactic_repair_log, *semantic_repair_log]
    if syntactic_repair_applied and not syntactic_repair_log:
        repair_log.insert(
            0,
            {
                "stage": "json_repair",
                "repair": "json_repair_applied",
            },
        )
    return _DecodedAnalysisCandidate(
        data=normalized,
        candidate_text=candidate_text,
        repair_applied=syntactic_repair_applied or bool(semantic_repair_log),
        repair_log=repair_log,
    )


def _recover_analysis_candidate(
    decoded: _DecodedAnalysisCandidate,
    *,
    context_catalog: Sequence[AnalysisContextCatalogItem],
) -> RecoverableAnalysisResult | None:
    defaults = _analysis_optional_section_defaults()
    accepted_data = {key: value for key, value in decoded.data.items() if key in _ANALYSIS_CORE_FIELDS}
    accepted_data.update(defaults)
    try:
        core_result = _validate_analysis_result_data(
            accepted_data,
            repair_applied=True,
        )
        _validate_directional_context_references(
            core_result,
            context_catalog=context_catalog,
            repair_applied=True,
        )
    except LLMOutputParseError:
        return None

    accepted_sections = [AnalysisOutputSection.CORE]
    invalid_sections: list[AnalysisOutputSection] = []
    issues: list[AnalysisSectionValidationIssue] = []
    for section, field_name in _ANALYSIS_OPTIONAL_SECTION_FIELDS.items():
        if field_name not in decoded.data:
            invalid_sections.append(section)
            issues.append(
                AnalysisSectionValidationIssue(
                    section=section,
                    stage="schema_validation",
                    error_type="MissingAnalysisSection",
                    message=f"model output omitted required section {field_name}",
                )
            )
            continue

        candidate = dict(accepted_data)
        candidate[field_name] = decoded.data[field_name]
        try:
            result = _validate_analysis_result_data(
                candidate,
                repair_applied=True,
            )
            _validate_directional_context_references(
                result,
                context_catalog=context_catalog,
                repair_applied=True,
            )
        except LLMOutputParseError as exc:
            invalid_sections.append(section)
            issues.append(
                AnalysisSectionValidationIssue(
                    section=section,
                    stage=exc.stage,
                    error_type=type(exc.__cause__ or exc).__name__,
                    message=str(exc),
                )
            )
            continue
        accepted_data[field_name] = decoded.data[field_name]
        accepted_sections.append(section)

    result = _validate_analysis_result_data(
        accepted_data,
        repair_applied=True,
    )
    _validate_directional_context_references(
        result,
        context_catalog=context_catalog,
        repair_applied=True,
    )
    repair_log = list(decoded.repair_log)
    unknown_fields = sorted(set(decoded.data) - _ANALYSIS_CORE_FIELDS - set(_ANALYSIS_OPTIONAL_SECTION_FIELDS.values()))
    if unknown_fields:
        repair_log.append(
            {
                "stage": "section_recovery",
                "repair": "remove_unsupported_top_level_fields",
                "fields": unknown_fields,
            }
        )
    return RecoverableAnalysisResult(
        result=result,
        accepted_data=accepted_data,
        original_data=decoded.data,
        accepted_sections=tuple(accepted_sections),
        invalid_sections=tuple(invalid_sections),
        issues=tuple(issues),
        repair_applied=True,
        repair_log=tuple(repair_log),
        candidate_text=decoded.candidate_text,
    )


def _analysis_optional_section_defaults() -> dict[str, Any]:
    return {
        "scenario_assessments": [],
        "network_direction": {
            "schema_version": "soc.network_direction_assessment.v1",
            "status": "not_assessed",
            "observed_flow": "not_available",
            "boundary_direction": "not_applicable",
            "semantic_direction": None,
            "connection_initiator": None,
            "intermediaries": [],
            "confidence": 0.0,
            "evidence_refs": [],
            "reasoning_refs": [],
            "context_refs": [],
            "rationale": "Model output section unavailable after validation.",
            "evidence_gaps": [],
        },
        "role_adjudication": {
            "schema_version": "soc.role_adjudication_result.v1",
            "status": "not_assessed",
            "roles": [],
            "response_target_proposals": [],
            "conflicts": [],
            "evidence_gaps": [],
            "rationale": "Model output section unavailable after validation.",
        },
        "knowledge_candidates": [],
    }


def parse_role_verification_output(
    response_content: Any,
    *,
    claims: Sequence[RoleVerificationClaim],
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem] = (),
    context_catalog: Sequence[AnalysisContextCatalogItem] = (),
) -> ParsedRoleVerificationCandidate:
    """Parse a narrow verifier response and prove complete catalog coverage."""

    text = _strip_markdown_code_fence(_strip_think_blocks(_extract_text(response_content))).strip()
    if not text:
        raise LLMOutputParseError(
            "role verification output is empty",
            stage="extract_text",
            parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
        )
    if len(text) > MAX_ROLE_VERIFICATION_RESPONSE_CHARS:
        raise LLMOutputParseError(
            f"role verification output exceeds {MAX_ROLE_VERIFICATION_RESPONSE_CHARS} characters",
            stage="output_size",
            parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
        )

    strict = _parse_strict_json_object_for_schema(
        text,
        schema_version="soc.role_verification_candidate.v1",
    )
    if strict is not None:
        data, candidate_text = strict
        return ParsedRoleVerificationCandidate(
            candidate=_validate_role_verification_candidate(
                data,
                claims=claims,
                evidence_catalog=evidence_catalog,
                context_catalog=context_catalog,
                repair_applied=False,
            ),
            candidate_text=candidate_text,
        )

    candidate_text = _extract_repair_candidate(text)
    repaired = _repair_json_object(
        candidate_text,
        parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
    )
    return ParsedRoleVerificationCandidate(
        candidate=_validate_role_verification_candidate(
            repaired.data,
            claims=claims,
            evidence_catalog=evidence_catalog,
            context_catalog=context_catalog,
            repair_applied=True,
        ),
        repair_applied=True,
        repair_log=repaired.log,
        candidate_text=candidate_text,
    )


@dataclass(frozen=True)
class _RepairedJson:
    data: dict[str, Any]
    log: list[dict[str, Any]]


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)

        flush_pending_str_parts()
        return "\n".join(pieces)
    return str(content)


def _strip_think_blocks(text: str) -> str:
    text = _THINK_BLOCK_RE.sub("", text)
    open_match = _OPEN_THINK_RE.search(text)
    if open_match:
        text = text[: open_match.start()]
    return text.strip()


def _strip_markdown_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _parse_strict_json_object(text: str) -> tuple[dict[str, Any], str] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        candidate = text[match.start() :]
        try:
            parsed, end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and _looks_like_analysis_result_object(parsed):
            return parsed, candidate[:end]
    return None


def _parse_strict_json_object_for_schema(
    text: str,
    *,
    schema_version: str,
) -> tuple[dict[str, Any], str] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        candidate = text[match.start() :]
        try:
            parsed, end = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("schema_version") == schema_version:
            return parsed, candidate[:end]
    return None


def _looks_like_analysis_result_object(data: dict[str, Any]) -> bool:
    return {
        "verdict",
        "confidence",
        "summary",
        "evidence",
        "reasoning",
        "reason",
        "recommended_action",
    }.issubset(data)


def _extract_repair_candidate(text: str) -> str:
    start = text.find("{")
    if start == -1:
        return text
    end = text.rfind("}")
    if end == -1 or end < start:
        return text[start:]
    return text[start : end + 1]


def _repair_json_object(
    candidate_text: str,
    *,
    parser_version: str = ANALYSIS_JSON_PARSER_VERSION,
) -> _RepairedJson:
    try:
        repaired, repair_log = repair_json_loads(
            candidate_text,
            logging=True,
            skip_json_loads=True,
        )
    except Exception as exc:  # noqa: BLE001 - normalize third-party parser failures
        raise LLMOutputParseError(
            f"LLM output JSON repair failed: {exc}",
            stage="json_repair",
            parser_version=parser_version,
            repair_applied=True,
        ) from exc

    if not isinstance(repaired, dict):
        raise LLMOutputParseError(
            "LLM output did not repair to a JSON object",
            stage="json_repair",
            parser_version=parser_version,
            repair_applied=True,
        )

    normalized_log = [item for item in repair_log if isinstance(item, dict)] if isinstance(repair_log, list) else []
    return _RepairedJson(data=repaired, log=normalized_log)


def _validate_role_verification_candidate(
    data: dict[str, Any],
    *,
    claims: Sequence[RoleVerificationClaim],
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
    context_catalog: Sequence[AnalysisContextCatalogItem],
    repair_applied: bool,
) -> RoleVerificationCandidate:
    try:
        candidate = RoleVerificationCandidate.model_validate(data)
    except ValidationError as exc:
        raise LLMOutputParseError(
            f"role verification output failed schema validation: {exc}",
            stage="schema_validation",
            parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
            repair_applied=repair_applied,
        ) from exc

    expected_claims = {claim.claim_ref: claim for claim in claims}
    actual_refs = {review.claim_ref for review in candidate.claim_reviews}
    if actual_refs != set(expected_claims):
        raise LLMOutputParseError(
            f"role verification output must review every RC-* claim exactly once; missing={sorted(set(expected_claims) - actual_refs)}, unexpected={sorted(actual_refs - set(expected_claims))}",
            stage="claim_coverage",
            parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
            repair_applied=repair_applied,
        )

    evidence_refs = {item.evidence_ref for item in evidence_catalog}
    context_refs = {item.context_ref for item in context_catalog}
    for review in candidate.claim_reviews:
        missing_evidence = sorted((set(review.supporting_evidence_refs) | set(review.contradicting_evidence_refs)) - evidence_refs)
        missing_context = sorted(set(review.context_refs) - context_refs)
        if missing_evidence or missing_context:
            raise LLMOutputParseError(
                f"role verification output contains unresolved references; claim={review.claim_ref}, missing_evidence={missing_evidence}, missing_context={missing_context}",
                stage="reference_validation",
                parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
                repair_applied=repair_applied,
            )
        if review.alternative is not None:
            expected_keys = set(expected_claims[review.claim_ref].assertion)
            actual_keys = set(review.alternative.assertion)
            if actual_keys != expected_keys:
                raise LLMOutputParseError(
                    f"role verification alternative must preserve the original claim keys; claim={review.claim_ref}, expected={sorted(expected_keys)}, actual={sorted(actual_keys)}",
                    stage="alternative_validation",
                    parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
                    repair_applied=repair_applied,
                )
    return candidate


def _normalize_analysis_result_shape(
    data: dict[str, Any],
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
    context_catalog: Sequence[AnalysisContextCatalogItem],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply audited repairs; only inert candidate hints may degrade safely."""

    normalized = dict(data)
    repair_log: list[dict[str, Any]] = []
    verdict = data.get("verdict")
    if isinstance(verdict, list) and len(verdict) == 1 and isinstance(verdict[0], str):
        normalized["verdict"] = verdict[0]
        repair_log.append(
            {
                "stage": "schema_normalization",
                "field": "verdict",
                "repair": "single_item_array_to_scalar",
            }
        )

    scenario_assessments = data.get("scenario_assessments")
    if isinstance(scenario_assessments, list):
        normalized_assessments = list(scenario_assessments)
        for index, item in enumerate(scenario_assessments):
            if not isinstance(item, Mapping):
                continue
            normalized_item = dict(item)
            changed = False
            is_primary = item.get("is_primary")
            if isinstance(is_primary, str) and is_primary.casefold() in {
                "true",
                "false",
            }:
                normalized_item["is_primary"] = is_primary.casefold() == "true"
                repair_log.append(
                    {
                        "stage": "schema_normalization",
                        "field": f"scenario_assessments[{index}].is_primary",
                        "repair": "json_boolean_string_to_boolean",
                        "original_value": is_primary,
                        "normalized_value": normalized_item["is_primary"],
                    }
                )
                changed = True
            rationale = item.get("rationale")
            if (rationale is None or rationale == "") and isinstance(item.get("evidence_refs"), list) and item["evidence_refs"] and isinstance(item.get("reasoning_refs"), list) and item["reasoning_refs"]:
                normalized_item["rationale"] = "Model omitted a separate scenario rationale; rely only on the cited E-* facts and R-* reasoning."
                repair_log.append(
                    {
                        "stage": "schema_normalization",
                        "field": f"scenario_assessments[{index}].rationale",
                        "repair": "missing_redundant_rationale_to_explicit_placeholder",
                    }
                )
                changed = True
            if changed:
                normalized_assessments[index] = normalized_item
        normalized["scenario_assessments"] = normalized_assessments

    evidence = data.get("evidence")
    if isinstance(evidence, list):
        normalized_evidence = list(evidence)
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            normalized_item = dict(item)
            if isinstance(value, list) and len(value) == 1 and _is_evidence_scalar(value[0]):
                normalized_item["value"] = value[0]
                repair_name = "single_item_array_to_scalar"
            elif isinstance(value, (dict, list)):
                serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if len(serialized) > MAX_STRUCTURED_EVIDENCE_VALUE_CHARS:
                    continue
                normalized_item["value"] = serialized
                repair_name = "structured_value_to_json_string"
            else:
                continue
            normalized_evidence[index] = normalized_item
            repair_log.append(
                {
                    "stage": "schema_normalization",
                    "field": f"evidence[{index}].value",
                    "repair": repair_name,
                }
            )
        normalized["evidence"] = normalized_evidence

    normalized = _deduplicate_exact_evidence_items(normalized, repair_log=repair_log)
    normalized = _drop_empty_reasoning_context_references(
        normalized,
        repair_log=repair_log,
    )

    candidates = data.get("knowledge_candidates")
    if isinstance(candidates, list):
        normalized_candidates = list(candidates)
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            changed = False
            destination = item.get("destination_hint")
            if isinstance(destination, str) and destination not in _KNOWLEDGE_DESTINATIONS:
                normalized_destination = _KNOWLEDGE_DESTINATION_ALIASES.get(
                    destination.casefold(),
                    "reject_or_verify",
                )
                normalized_item["destination_hint"] = normalized_destination
                repair_log.append(
                    {
                        "stage": "candidate_hint_normalization",
                        "field": f"knowledge_candidates[{index}].destination_hint",
                        "repair": "unsupported_hint_to_governed_destination",
                        "original_value": destination,
                        "normalized_value": normalized_destination,
                    }
                )
                changed = True
            scope = item.get("scope_hint")
            if isinstance(scope, str) and scope not in _KNOWLEDGE_SCOPES:
                normalized_scope = _KNOWLEDGE_SCOPE_ALIASES.get(
                    scope.casefold(),
                    "event",
                )
                normalized_item["scope_hint"] = normalized_scope
                repair_log.append(
                    {
                        "stage": "candidate_hint_normalization",
                        "field": f"knowledge_candidates[{index}].scope_hint",
                        "repair": "unsupported_hint_to_bounded_scope",
                        "original_value": scope,
                        "normalized_value": normalized_scope,
                    }
                )
                changed = True
            if changed:
                normalized_candidates[index] = normalized_item
        normalized["knowledge_candidates"] = normalized_candidates

    normalized = _normalize_catalog_references(
        normalized,
        evidence_catalog=evidence_catalog,
        context_catalog=context_catalog,
        repair_log=repair_log,
    )
    normalized = _deduplicate_exact_reference_lists(
        normalized,
        repair_log=repair_log,
    )
    normalized = _deduplicate_exact_evidence_items(
        normalized,
        repair_log=repair_log,
    )
    normalized = _materialize_context_reference_basis(
        normalized,
        context_catalog=context_catalog,
        repair_log=repair_log,
    )
    normalized = _materialize_referenced_catalog_evidence(
        normalized,
        evidence_catalog=evidence_catalog,
        repair_log=repair_log,
    )
    normalized = _drop_response_targets_without_adjudicated_entities(
        normalized,
        repair_log=repair_log,
    )

    return (normalized, repair_log) if repair_log else (data, [])


def _drop_response_targets_without_adjudicated_entities(
    data: dict[str, Any],
    *,
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Drop optional action targets whose entity was never adjudicated.

    A proposal's target role describes why that entity fits the proposed action
    and may differ from its global semantic role. This repair therefore matches
    only the exact entity type and value. It never creates or changes a role,
    target, verdict, or action authority.
    """

    raw_adjudication = data.get("role_adjudication")
    if not isinstance(raw_adjudication, Mapping):
        return data
    roles = raw_adjudication.get("roles")
    proposals = raw_adjudication.get("response_target_proposals")
    if not isinstance(roles, list) or not isinstance(proposals, list):
        return data

    entity_keys: set[tuple[str, str]] = set()
    for role in roles:
        if not isinstance(role, Mapping):
            continue
        values = (role.get("entity_type"), role.get("value"))
        if all(isinstance(value, str) for value in values):
            entity_keys.add(tuple(value.casefold() for value in values))

    retained: list[Any] = []
    removed: list[dict[str, Any]] = []
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, Mapping):
            retained.append(proposal)
            continue
        values = (
            proposal.get("target_type"),
            proposal.get("target_value"),
        )
        if not all(isinstance(value, str) for value in values):
            retained.append(proposal)
            continue
        if tuple(value.casefold() for value in values) in entity_keys:
            retained.append(proposal)
            continue
        removed.append(
            {
                "index": index,
                "proposal_id": proposal.get("proposal_id"),
                "target_role": proposal.get("target_role"),
                "target_type": proposal.get("target_type"),
                "target_value": proposal.get("target_value"),
                "reason": "target_entity_not_adjudicated",
            }
        )

    if not removed:
        return data
    normalized_adjudication = dict(raw_adjudication)
    normalized_adjudication["response_target_proposals"] = retained
    normalized = dict(data)
    normalized["role_adjudication"] = normalized_adjudication
    repair_log.append(
        {
            "stage": "schema_normalization",
            "field": "role_adjudication.response_target_proposals",
            "repair": "remove_response_targets_without_adjudicated_entities",
            "removed": removed,
        }
    )
    return normalized


def _deduplicate_exact_evidence_items(
    data: dict[str, Any],
    *,
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = data.get("evidence")
    if not isinstance(evidence, list):
        return data
    first_by_ref: dict[str, Mapping[str, Any]] = {}
    normalized_evidence: list[Any] = []
    removed_indexes: list[int] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, Mapping):
            normalized_evidence.append(item)
            continue
        reference = item.get("evidence_ref")
        if not isinstance(reference, str):
            normalized_evidence.append(item)
            continue
        first = first_by_ref.get(reference)
        if first is None:
            first_by_ref[reference] = item
            normalized_evidence.append(item)
            continue
        if item.get("source") == first.get("source") and type(item.get("value")) is type(first.get("value")) and item.get("value") == first.get("value"):
            removed_indexes.append(index)
            continue
        normalized_evidence.append(item)
    if not removed_indexes:
        return data
    normalized = dict(data)
    normalized["evidence"] = normalized_evidence
    repair_log.append(
        {
            "stage": "schema_normalization",
            "field": "evidence",
            "repair": "remove_exact_duplicate_evidence_refs",
            "removed_indexes": removed_indexes,
        }
    )
    return normalized


def _materialize_context_reference_basis(
    data: dict[str, Any],
    *,
    context_catalog: Sequence[AnalysisContextCatalogItem],
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive redundant basis labels from explicit governed context references."""

    if not context_catalog:
        return data
    basis_by_ref = {
        item.context_ref: {
            "skill": "skill",
            "adapter_contract": "adapter_contract",
            "confirmed_memory": "confirmed_memory",
            "governed_context": "governed_context",
            "tool_result": "tool_result",
        }[item.kind.value]
        for item in context_catalog
    }
    reasoning = data.get("reasoning")
    if not isinstance(reasoning, list):
        return data
    normalized_reasoning = list(reasoning)
    changed = False
    for index, item in enumerate(reasoning):
        if not isinstance(item, Mapping):
            continue
        references = item.get("context_refs")
        basis = item.get("basis")
        if not isinstance(references, list) or not isinstance(basis, list):
            continue
        required = [basis_by_ref[reference] for reference in references if isinstance(reference, str) and reference in basis_by_ref]
        additions = [value for value in dict.fromkeys(required) if value not in basis]
        if not additions:
            continue
        normalized_item = dict(item)
        normalized_item["basis"] = [*basis, *additions]
        normalized_reasoning[index] = normalized_item
        repair_log.append(
            {
                "stage": "catalog_reference_normalization",
                "field": f"reasoning[{index}].basis",
                "repair": "derive_basis_from_explicit_context_refs",
                "normalized_value": additions,
            }
        )
        changed = True
    if not changed:
        return data
    normalized = dict(data)
    normalized["reasoning"] = normalized_reasoning
    return normalized


def _deduplicate_exact_reference_lists(
    data: dict[str, Any],
    *,
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Remove repeated identical IDs without changing reference meaning."""

    normalized = dict(data)
    changed = False
    for collection_name, field_names in (
        ("reasoning", ("evidence_refs", "context_refs")),
        ("scenario_assessments", ("evidence_refs", "reasoning_refs")),
        ("knowledge_candidates", ("evidence_refs", "reasoning_refs")),
    ):
        collection = normalized.get(collection_name)
        if not isinstance(collection, list):
            continue
        normalized_collection = list(collection)
        for index, item in enumerate(collection):
            if not isinstance(item, Mapping):
                continue
            normalized_item = dict(item)
            item_changed = False
            for field_name in field_names:
                references = normalized_item.get(field_name)
                if not isinstance(references, list):
                    continue
                seen: set[str] = set()
                deduplicated: list[Any] = []
                for reference in references:
                    if isinstance(reference, str) and reference in seen:
                        continue
                    deduplicated.append(reference)
                    if isinstance(reference, str):
                        seen.add(reference)
                if len(deduplicated) == len(references):
                    continue
                normalized_item[field_name] = deduplicated
                repair_log.append(
                    {
                        "stage": "catalog_reference_normalization",
                        "field": f"{collection_name}[{index}].{field_name}",
                        "repair": "remove_exact_duplicate_references",
                        "removed_count": len(references) - len(deduplicated),
                    }
                )
                item_changed = True
            if item_changed:
                normalized_collection[index] = normalized_item
                changed = True
        normalized[collection_name] = normalized_collection
    return normalized if changed else data


def _drop_empty_reasoning_context_references(
    data: dict[str, Any],
    *,
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    reasoning = data.get("reasoning")
    if not isinstance(reasoning, list):
        return data
    normalized_reasoning = list(reasoning)
    changed = False
    for index, item in enumerate(reasoning):
        if not isinstance(item, Mapping):
            continue
        references = item.get("context_refs")
        if not isinstance(references, list):
            continue
        retained = [reference for reference in references if not (reference is None or (isinstance(reference, str) and reference.strip().casefold() in _EMPTY_CONTEXT_REFERENCE_SENTINELS))]
        if len(retained) == len(references):
            continue
        normalized_item = dict(item)
        normalized_item["context_refs"] = retained
        normalized_reasoning[index] = normalized_item
        repair_log.append(
            {
                "stage": "schema_normalization",
                "field": f"reasoning[{index}].context_refs",
                "repair": "remove_empty_context_reference_sentinels",
                "removed_count": len(references) - len(retained),
            }
        )
        changed = True
    if not changed:
        return data
    normalized = dict(data)
    normalized["reasoning"] = normalized_reasoning
    return normalized


def _normalize_catalog_references(
    data: dict[str, Any],
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
    context_catalog: Sequence[AnalysisContextCatalogItem],
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    if not evidence_catalog and not context_catalog:
        return data

    normalized = dict(data)
    evidence_by_ref = {item.evidence_ref: item for item in evidence_catalog}
    evidence_ref_rewrites: dict[str, str] = {}
    raw_evidence = data.get("evidence")
    if isinstance(raw_evidence, list):
        normalized_evidence = list(raw_evidence)
        for index, item in enumerate(raw_evidence):
            if not isinstance(item, Mapping):
                continue
            original_ref = item.get("evidence_ref")
            if isinstance(original_ref, str) and original_ref in evidence_by_ref:
                bound_item = evidence_by_ref[original_ref]
                if item.get("source") == bound_item.source_path and type(item.get("value")) is type(bound_item.value) and item.get("value") == bound_item.value:
                    continue
            corrected_ref = _resolve_evidence_item_reference(
                item,
                evidence_catalog=evidence_catalog,
            )
            if corrected_ref is None or corrected_ref == original_ref:
                continue
            normalized_item = dict(item)
            normalized_item["evidence_ref"] = corrected_ref
            normalized_evidence[index] = normalized_item
            if isinstance(original_ref, str):
                evidence_ref_rewrites.setdefault(original_ref, corrected_ref)
            repair_log.append(
                {
                    "stage": "catalog_reference_normalization",
                    "field": f"evidence[{index}].evidence_ref",
                    "repair": "exact_catalog_fact_to_reference",
                    "original_value": original_ref,
                    "normalized_value": corrected_ref,
                }
            )
        normalized["evidence"] = normalized_evidence

    evidence_refs = set(evidence_by_ref)
    context_refs = {item.context_ref for item in context_catalog}
    for collection_name, fields in (
        ("reasoning", ("evidence_refs", "context_refs")),
        ("scenario_assessments", ("evidence_refs", "reasoning_refs")),
        ("knowledge_candidates", ("evidence_refs", "reasoning_refs")),
    ):
        values = normalized.get(collection_name)
        if not isinstance(values, list):
            continue
        normalized_values = list(values)
        for index, item in enumerate(values):
            if not isinstance(item, Mapping):
                continue
            normalized_item = dict(item)
            changed = False
            for field_name in fields:
                references = item.get(field_name)
                if not isinstance(references, list):
                    continue
                if field_name == "evidence_refs":
                    allowed = evidence_refs
                    rewrites = evidence_ref_rewrites
                elif field_name == "context_refs":
                    allowed = context_refs
                    rewrites = {}
                else:
                    continue
                repaired_refs: list[Any] = []
                field_changed = False
                for reference in references:
                    repaired = _resolve_reference(
                        reference,
                        allowed=allowed,
                        explicit_rewrites=rewrites,
                    )
                    repaired_refs.append(repaired)
                    field_changed = field_changed or repaired != reference
                if field_changed:
                    normalized_item[field_name] = repaired_refs
                    repair_log.append(
                        {
                            "stage": "catalog_reference_normalization",
                            "field": f"{collection_name}[{index}].{field_name}",
                            "repair": "unique_catalog_reference_expansion",
                            "original_value": references,
                            "normalized_value": repaired_refs,
                        }
                    )
                    changed = True
            if changed:
                normalized_values[index] = normalized_item
        normalized[collection_name] = normalized_values
    normalized = _normalize_directional_catalog_references(
        normalized,
        evidence_refs=evidence_refs,
        context_refs=context_refs,
        evidence_ref_rewrites=evidence_ref_rewrites,
        repair_log=repair_log,
    )
    return normalized


def _normalize_directional_catalog_references(
    data: dict[str, Any],
    *,
    evidence_refs: set[str],
    context_refs: set[str],
    evidence_ref_rewrites: Mapping[str, str],
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(data)

    def repair_item(item: Any, path: str) -> Any:
        if not isinstance(item, Mapping):
            return item
        repaired_item = dict(item)
        for field_name, allowed, rewrites in (
            ("evidence_refs", evidence_refs, evidence_ref_rewrites),
            ("context_refs", context_refs, {}),
        ):
            references = item.get(field_name)
            if not isinstance(references, list):
                continue
            repaired = [_resolve_reference(reference, allowed=allowed, explicit_rewrites=rewrites) for reference in references]
            if repaired == references:
                continue
            repaired_item[field_name] = repaired
            repair_log.append(
                {
                    "stage": "catalog_reference_normalization",
                    "field": f"{path}.{field_name}",
                    "repair": "unique_catalog_reference_expansion",
                    "original_value": references,
                    "normalized_value": repaired,
                }
            )
        return repaired_item

    normalized["network_direction"] = repair_item(
        normalized.get("network_direction"),
        "network_direction",
    )
    raw_adjudication = normalized.get("role_adjudication")
    if isinstance(raw_adjudication, Mapping):
        adjudication = dict(raw_adjudication)
        for collection_name in ("roles", "response_target_proposals"):
            collection = raw_adjudication.get(collection_name)
            if isinstance(collection, list):
                adjudication[collection_name] = [repair_item(item, f"role_adjudication.{collection_name}[{index}]") for index, item in enumerate(collection)]
        normalized["role_adjudication"] = adjudication
    return normalized


def _resolve_evidence_item_reference(
    item: Mapping[str, Any],
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
) -> str | None:
    source = item.get("source")
    value = item.get("value")
    exact_matches = [catalog_item.evidence_ref for catalog_item in evidence_catalog if source == catalog_item.source_path and type(value) is type(catalog_item.value) and value == catalog_item.value]
    if len(exact_matches) == 1:
        return exact_matches[0]
    reference = item.get("evidence_ref")
    resolved = _resolve_reference(
        reference,
        allowed={catalog_item.evidence_ref for catalog_item in evidence_catalog},
        explicit_rewrites={},
    )
    return resolved if isinstance(resolved, str) and resolved in {catalog_item.evidence_ref for catalog_item in evidence_catalog} else None


def _materialize_referenced_catalog_evidence(
    data: dict[str, Any],
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
    repair_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Dereference valid E-* IDs that the model cited but did not copy to evidence[]."""

    if not evidence_catalog:
        return data
    catalog_by_ref = {item.evidence_ref: item for item in evidence_catalog}
    raw_evidence = data.get("evidence")
    if not isinstance(raw_evidence, list):
        return data
    existing_refs = {item.get("evidence_ref") for item in raw_evidence if isinstance(item, Mapping) and isinstance(item.get("evidence_ref"), str)}
    referenced_refs: list[str] = []
    for collection_name in (
        "reasoning",
        "scenario_assessments",
        "knowledge_candidates",
    ):
        collection = data.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, Mapping):
                continue
            references = item.get("evidence_refs")
            if not isinstance(references, list):
                continue
            referenced_refs.extend(reference for reference in references if isinstance(reference, str))
    network_direction = data.get("network_direction")
    if isinstance(network_direction, Mapping) and isinstance(network_direction.get("evidence_refs"), list):
        referenced_refs.extend(reference for reference in network_direction["evidence_refs"] if isinstance(reference, str))
    role_adjudication = data.get("role_adjudication")
    if isinstance(role_adjudication, Mapping):
        for collection_name in ("roles", "response_target_proposals"):
            collection = role_adjudication.get(collection_name)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if isinstance(item, Mapping) and isinstance(item.get("evidence_refs"), list):
                    referenced_refs.extend(reference for reference in item["evidence_refs"] if isinstance(reference, str))

    materialized: list[str] = []
    normalized_evidence = list(raw_evidence)
    for reference in dict.fromkeys(referenced_refs):
        if reference in existing_refs:
            continue
        catalog_item = catalog_by_ref.get(reference)
        if catalog_item is None:
            continue
        normalized_evidence.append(
            {
                "evidence_ref": catalog_item.evidence_ref,
                "source": catalog_item.source_path,
                "description": "Referenced current-alert catalog fact",
                "value": catalog_item.value,
            }
        )
        existing_refs.add(reference)
        materialized.append(reference)
    if not materialized:
        return data
    normalized = dict(data)
    normalized["evidence"] = normalized_evidence
    repair_log.append(
        {
            "stage": "catalog_reference_normalization",
            "field": "evidence",
            "repair": "materialize_referenced_catalog_facts",
            "normalized_value": materialized,
        }
    )
    return normalized


def _resolve_reference(
    reference: Any,
    *,
    allowed: set[str],
    explicit_rewrites: Mapping[str, str],
) -> Any:
    if not isinstance(reference, str):
        return reference
    if reference in explicit_rewrites:
        return explicit_rewrites[reference]
    normalized = reference.upper()
    if normalized in allowed:
        return normalized
    if not re.fullmatch(r"(?:E|S|A|M|C|T)-[A-F0-9]{8,11}", normalized):
        return reference
    matches = [candidate for candidate in allowed if candidate.startswith(normalized)]
    return matches[0] if len(matches) == 1 else reference


def _is_evidence_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _validate_analysis_result_data(data: dict[str, Any], *, repair_applied: bool) -> AnalysisResult:
    from soc_agent.core.validator import validate_analysis_result

    _validate_raw_analysis_shape(data, repair_applied=repair_applied)
    try:
        result = AnalysisResult.model_validate(data)
    except ValidationError as exc:
        raise LLMOutputParseError(
            f"LLM output failed AnalysisResult schema validation: {exc}",
            stage="schema_validation",
            repair_applied=repair_applied,
        ) from exc

    try:
        return validate_analysis_result(result)
    except Exception as exc:  # noqa: BLE001 - normalize domain validation failures
        raise LLMOutputParseError(
            f"LLM output failed analysis domain validation: {exc}",
            stage="domain_validation",
            repair_applied=repair_applied,
        ) from exc


def _validate_directional_context_references(
    result: AnalysisResult,
    *,
    context_catalog: Sequence[AnalysisContextCatalogItem],
    repair_applied: bool,
) -> None:
    """Require every direct direction/role context citation to exist in this request."""

    available = {item.context_ref for item in context_catalog}
    directional_items: list[Any] = [result.network_direction]
    directional_items.extend(result.role_adjudication.roles)
    directional_items.extend(result.role_adjudication.response_target_proposals)
    missing = sorted({reference for item in directional_items for reference in item.context_refs if reference not in available})
    if missing:
        raise LLMOutputParseError(
            f"direction/role context_refs must resolve in the request context catalog; missing_context={missing}",
            stage="reference_validation",
            repair_applied=repair_applied,
        )


def _validate_raw_analysis_shape(data: dict[str, Any], *, repair_applied: bool) -> None:
    required_fields = {
        "schema_version",
        "verdict",
        "confidence",
        "summary",
        "evidence",
        "reasoning",
        "scenario_assessments",
        "network_direction",
        "role_adjudication",
        "evidence_gaps",
        "manual_checks",
        "reason",
        "recommended_action",
        "knowledge_candidates",
    }
    allowed_fields = set(required_fields)
    missing_fields = sorted(required_fields - data.keys())
    if missing_fields:
        raise LLMOutputParseError(
            f"LLM output is missing required fields: {', '.join(missing_fields)}",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    unknown_fields = sorted(data.keys() - allowed_fields)
    if unknown_fields:
        raise LLMOutputParseError(
            f"LLM output contains unsupported fields: {', '.join(unknown_fields)}",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    if data.get("schema_version") != "soc.analysis_result.v4":
        raise LLMOutputParseError(
            "LLM output schema_version must be 'soc.analysis_result.v4'",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise LLMOutputParseError(
            "LLM output confidence must be a JSON number",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    scenario_assessments = data.get("scenario_assessments")
    if not isinstance(scenario_assessments, list):
        raise LLMOutputParseError(
            "LLM output scenario_assessments must be a JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    for index, assessment in enumerate(scenario_assessments):
        if not isinstance(assessment, dict):
            raise LLMOutputParseError(
                f"LLM output scenario_assessments[{index}] must be a JSON object",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
        allowed_scenario_fields = {
            "schema_version",
            "scenario_name",
            "scenario_key",
            "is_primary",
            "origin",
            "confidence",
            "activity_stage",
            "evidence_refs",
            "reasoning_refs",
            "rationale",
            "competing_explanations",
        }
        unknown_scenario_fields = sorted(assessment.keys() - allowed_scenario_fields)
        if unknown_scenario_fields:
            raise LLMOutputParseError(
                f"LLM output scenario_assessments[{index}] contains unsupported fields: {', '.join(unknown_scenario_fields)}",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
        scenario_confidence = assessment.get("confidence")
        if isinstance(scenario_confidence, bool) or not isinstance(
            scenario_confidence,
            (int, float),
        ):
            raise LLMOutputParseError(
                f"LLM output scenario_assessments[{index}].confidence must be a JSON number",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
        for reference_field in ("evidence_refs", "reasoning_refs"):
            references = assessment.get(reference_field)
            if not isinstance(references, list) or not references or any(not isinstance(item, str) for item in references):
                raise LLMOutputParseError(
                    f"LLM output scenario_assessments[{index}].{reference_field} must be a non-empty JSON string array",
                    stage="schema_validation",
                    repair_applied=repair_applied,
                )
        if not isinstance(assessment.get("is_primary"), bool):
            raise LLMOutputParseError(
                f"LLM output scenario_assessments[{index}].is_primary must be a JSON boolean",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
    _validate_directional_shape(data, repair_applied=repair_applied)
    for field_name in ("evidence_gaps", "manual_checks"):
        values = data.get(field_name)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise LLMOutputParseError(
                f"LLM output {field_name} must be a JSON string array",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise LLMOutputParseError(
            "LLM output evidence must be a non-empty JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    allowed_evidence_fields = {"evidence_ref", "source", "description", "value"}
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise LLMOutputParseError(
                f"LLM output evidence[{index}] must be a JSON object",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
        unknown = sorted(item.keys() - allowed_evidence_fields)
        missing = sorted(allowed_evidence_fields - item.keys())
        if unknown or missing:
            raise LLMOutputParseError(
                f"LLM output evidence[{index}] has missing={missing} unsupported={unknown}",
                stage="schema_validation",
                repair_applied=repair_applied,
            )

    reasoning = data.get("reasoning")
    if not isinstance(reasoning, list) or not reasoning:
        raise LLMOutputParseError(
            "LLM output reasoning must be a non-empty JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    allowed_reasoning_fields = {
        "schema_version",
        "reasoning_id",
        "statement",
        "basis",
        "evidence_refs",
        "context_refs",
        "confidence",
    }
    for index, item in enumerate(reasoning):
        if not isinstance(item, dict):
            raise LLMOutputParseError(
                f"LLM output reasoning[{index}] must be a JSON object",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
        unknown = sorted(item.keys() - allowed_reasoning_fields)
        missing = sorted(allowed_reasoning_fields - item.keys())
        if unknown or missing:
            raise LLMOutputParseError(
                f"LLM output reasoning[{index}] has missing={missing} unsupported={unknown}",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
        for reference_field in ("basis", "evidence_refs", "context_refs"):
            references = item.get(reference_field)
            if not isinstance(references, list) or any(not isinstance(reference, str) for reference in references):
                raise LLMOutputParseError(
                    f"LLM output reasoning[{index}].{reference_field} must be a JSON string array",
                    stage="schema_validation",
                    repair_applied=repair_applied,
                )
        reasoning_confidence = item.get("confidence")
        if isinstance(reasoning_confidence, bool) or not isinstance(
            reasoning_confidence,
            (int, float),
        ):
            raise LLMOutputParseError(
                f"LLM output reasoning[{index}].confidence must be a JSON number",
                stage="schema_validation",
                repair_applied=repair_applied,
            )

    candidates = data.get("knowledge_candidates")
    if not isinstance(candidates, list):
        raise LLMOutputParseError(
            "LLM output knowledge_candidates must be a JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
        )
    allowed_candidate_fields = {
        "schema_version",
        "candidate_id",
        "statement",
        "destination_hint",
        "scope_hint",
        "evidence_refs",
        "reasoning_refs",
        "rationale",
    }
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            raise LLMOutputParseError(
                f"LLM output knowledge_candidates[{index}] must be a JSON object",
                stage="schema_validation",
                repair_applied=repair_applied,
            )
        unknown = sorted(item.keys() - allowed_candidate_fields)
        missing = sorted(allowed_candidate_fields - item.keys())
        if unknown or missing:
            raise LLMOutputParseError(
                f"LLM output knowledge_candidates[{index}] has missing={missing} unsupported={unknown}",
                stage="schema_validation",
                repair_applied=repair_applied,
            )


def _validate_directional_shape(data: dict[str, Any], *, repair_applied: bool) -> None:
    network_fields = {
        "schema_version",
        "status",
        "observed_flow",
        "boundary_direction",
        "intermediaries",
        "confidence",
        "evidence_refs",
        "reasoning_refs",
        "context_refs",
        "rationale",
        "evidence_gaps",
    }
    network = _require_exact_object(
        data.get("network_direction"),
        path="network_direction",
        fields=network_fields,
        optional_fields={"semantic_direction", "connection_initiator"},
        repair_applied=repair_applied,
    )
    _require_json_number(network.get("confidence"), path="network_direction.confidence", repair_applied=repair_applied)
    for field_name in ("intermediaries", "evidence_refs", "reasoning_refs", "context_refs", "evidence_gaps"):
        _require_string_array(network.get(field_name), path=f"network_direction.{field_name}", repair_applied=repair_applied)

    adjudication_fields = {
        "schema_version",
        "status",
        "roles",
        "response_target_proposals",
        "conflicts",
        "evidence_gaps",
        "rationale",
    }
    adjudication = _require_exact_object(
        data.get("role_adjudication"),
        path="role_adjudication",
        fields=adjudication_fields,
        repair_applied=repair_applied,
    )
    for field_name in ("conflicts", "evidence_gaps"):
        _require_string_array(adjudication.get(field_name), path=f"role_adjudication.{field_name}", repair_applied=repair_applied)

    role_fields = {
        "role",
        "entity_type",
        "value",
        "status",
        "confidence",
        "evidence_refs",
        "reasoning_refs",
        "context_refs",
        "rationale",
    }
    roles = adjudication.get("roles")
    if not isinstance(roles, list):
        _raise_shape("role_adjudication.roles must be a JSON array", repair_applied)
    for index, value in enumerate(roles):
        role = _require_exact_object(value, path=f"role_adjudication.roles[{index}]", fields=role_fields, repair_applied=repair_applied)
        _require_json_number(role.get("confidence"), path=f"role_adjudication.roles[{index}].confidence", repair_applied=repair_applied)
        for field_name in ("evidence_refs", "reasoning_refs", "context_refs"):
            _require_string_array(role.get(field_name), path=f"role_adjudication.roles[{index}].{field_name}", repair_applied=repair_applied)

    proposal_fields = {
        "proposal_id",
        "action_kind",
        "target_type",
        "target_value",
        "target_role",
        "confidence",
        "evidence_refs",
        "reasoning_refs",
        "context_refs",
        "rationale",
        "policy_review_required",
        "automation_allowed",
    }
    proposals = adjudication.get("response_target_proposals")
    if not isinstance(proposals, list):
        _raise_shape("role_adjudication.response_target_proposals must be a JSON array", repair_applied)
    for index, value in enumerate(proposals):
        path = f"role_adjudication.response_target_proposals[{index}]"
        proposal = _require_exact_object(value, path=path, fields=proposal_fields, repair_applied=repair_applied)
        _require_json_number(proposal.get("confidence"), path=f"{path}.confidence", repair_applied=repair_applied)
        for field_name in ("evidence_refs", "reasoning_refs", "context_refs"):
            _require_string_array(proposal.get(field_name), path=f"{path}.{field_name}", repair_applied=repair_applied)
        if proposal.get("policy_review_required") is not True or proposal.get("automation_allowed") is not False:
            _raise_shape(f"{path} must keep policy_review_required=true and automation_allowed=false", repair_applied)


def _require_exact_object(
    value: Any,
    *,
    path: str,
    fields: set[str],
    optional_fields: set[str] | None = None,
    repair_applied: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _raise_shape(f"{path} must be a JSON object", repair_applied)
    missing = sorted(fields - value.keys())
    allowed_fields = fields | (optional_fields or set())
    unknown = sorted(value.keys() - allowed_fields)
    if missing or unknown:
        _raise_shape(f"{path} has missing={missing} unsupported={unknown}", repair_applied)
    return value


def _require_string_array(value: Any, *, path: str, repair_applied: bool) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _raise_shape(f"{path} must be a JSON string array", repair_applied)


def _require_json_number(value: Any, *, path: str, repair_applied: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _raise_shape(f"{path} must be a JSON number", repair_applied)


def _raise_shape(message: str, repair_applied: bool) -> None:
    raise LLMOutputParseError(
        f"LLM output {message}",
        stage="schema_validation",
        repair_applied=repair_applied,
    )
