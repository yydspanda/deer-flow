"""Parse and validate LLM JSON output for SOC analysis nodes."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from json_repair import loads as repair_json_loads
from pydantic import ValidationError

from soc_agent.contracts import (
    ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION,
    AdjudicatedRoleStatus,
    AnalysisContextCatalogItem,
    AnalysisEvidenceCatalogItem,
    AnalysisModelCoreOutputV2,
    AnalysisModelCoreOutputV3,
    AnalysisModelCoreOutputV4,
    AnalysisOutputSection,
    AnalysisReasoningItem,
    AnalysisResult,
    LLMAnalysisRequest,
    NetworkEntityRef,
    RoleAdjudicationStatus,
    RoleCoherenceStatus,
    RoleVerificationCandidate,
    RoleVerificationClaim,
)
from soc_agent.model_reference_aliases import (
    ModelReferenceAliases,
    build_model_reference_aliases,
)

ANALYSIS_JSON_PARSER_VERSION = "soc-analysis-json-parser-v24"
LEGACY_ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION = "soc.analysis_model_output.v1"
LEGACY_ANALYSIS_MODEL_OUTPUT_V2_SCHEMA_VERSION = "soc.analysis_model_output.v2"
LEGACY_ANALYSIS_MODEL_OUTPUT_V3_SCHEMA_VERSION = "soc.analysis_model_output.v3"
ROLE_VERIFICATION_JSON_PARSER_VERSION = "soc-role-verification-json-parser-v2"
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
_OPTIONAL_HYDRATION_REPAIR_OPERATIONS = frozenset(
    {
        "derive_role_entity_from_unique_cited_value",
        "canonicalize_role_entity_reference",
        "discard_entity_ref_for_unresolved_role",
        "discard_untyped_direction_entity_reference",
        "drop_unsupported_optional_fields",
        "materialize_conservative_role_confidence",
        "materialize_conservative_role_status",
        "materialize_role_adjudication_rationale",
        "materialize_role_adjudication_status",
        "materialize_summary_from_reason",
        "materialize_missing_optional_rationale",
        "materialize_conservative_scenario_origin",
        "materialize_scenario_name_from_key",
        "strict_decimal_string_to_number",
        "retain_catalog_backed_core_context_refs",
        "retain_catalog_backed_core_evidence_refs",
        "retain_catalog_backed_optional_context_refs",
        "retain_catalog_backed_optional_evidence_refs",
    }
)
_COMPACT_SCENARIO_ITEM_FIELDS = frozenset(
    {
        "schema_version",
        "scenario_name",
        "scenario_key",
        "is_primary",
        "origin",
        "confidence",
        "activity_stage",
        "evidence_refs",
        "reasoning_refs",
        "context_refs",
        "rationale",
        "reason",
        "competing_explanations",
    }
)
_COMPACT_NETWORK_DIRECTION_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "observed_flow",
        "boundary_direction",
        "semantic_direction",
        "connection_initiator_ref",
        "connection_initiator",
        "intermediaries",
        "confidence",
        "evidence_refs",
        "reasoning_refs",
        "context_refs",
        "rationale",
        "reason",
        "evidence_gaps",
    }
)
_COMPACT_ROLE_ITEM_FIELDS = frozenset(
    {
        "schema_version",
        "role",
        "entity_ref",
        "entity_type",
        "value",
        "status",
        "confidence",
        "evidence_refs",
        "reasoning_refs",
        "context_refs",
        "rationale",
        "reason",
    }
)
_COMPACT_ROLE_SECTION_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "roles",
        "response_target_proposals",
        "conflicts",
        "evidence_gaps",
        "rationale",
        "reason",
    }
)
_RUNTIME_OWNED_REASONING_OUTPUT_VERSIONS = frozenset(
    {
        ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION,
        LEGACY_ANALYSIS_MODEL_OUTPUT_V3_SCHEMA_VERSION,
    }
)


class LLMOutputParseError(ValueError):
    """Raised when LLM output cannot become a valid ``AnalysisResult``."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        parser_version: str = ANALYSIS_JSON_PARSER_VERSION,
        repair_applied: bool = False,
        field_paths: Sequence[str] = (),
        issue_codes: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.parser_version = parser_version
        self.repair_applied = repair_applied
        self.field_paths = tuple(dict.fromkeys(str(path)[:512] for path in field_paths if path))[:20]
        self.issue_codes = tuple(dict.fromkeys(str(code)[:128] for code in issue_codes if code))[:20]


@dataclass(frozen=True)
class ParsedAnalysisResult:
    """Validated analysis output plus parser audit metadata."""

    result: AnalysisResult
    parser_version: str = ANALYSIS_JSON_PARSER_VERSION
    repair_applied: bool = False
    repair_log: list[dict[str, Any]] = field(default_factory=list)
    hydration_log: list[dict[str, Any]] = field(default_factory=list)
    model_output_schema_version: str = "soc.analysis_result.v4"
    candidate_text: str = ""


@dataclass(frozen=True)
class AnalysisSectionValidationIssue:
    """In-memory detail used to repair one rejected model-output section."""

    section: AnalysisOutputSection
    stage: str
    error_type: str
    message: str
    field_paths: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()


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
    hydration_log: tuple[dict[str, Any], ...] = ()
    model_output_schema_version: str = "soc.analysis_result.v4"
    candidate_text: str = ""


@dataclass(frozen=True)
class _DecodedAnalysisCandidate:
    data: dict[str, Any]
    candidate_text: str
    repair_applied: bool
    repair_log: list[dict[str, Any]]
    hydration_log: list[dict[str, Any]]
    model_output_schema_version: str


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
    analysis_request: LLMAnalysisRequest | None = None,
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
    _validate_deterministic_role_coherence(
        result,
        analysis_request=analysis_request,
        repair_applied=decoded.repair_applied,
    )
    return ParsedAnalysisResult(
        result=result,
        repair_applied=decoded.repair_applied,
        repair_log=decoded.repair_log,
        hydration_log=decoded.hydration_log,
        model_output_schema_version=decoded.model_output_schema_version,
        candidate_text=decoded.candidate_text,
    )


def recover_analysis_result_output(
    response_content: Any,
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem] = (),
    context_catalog: Sequence[AnalysisContextCatalogItem] = (),
    analysis_request: LLMAnalysisRequest | None = None,
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
        analysis_request=analysis_request,
    )


def parse_analysis_section_patch_output(
    response_content: Any,
    *,
    recovery: RecoverableAnalysisResult,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem] = (),
    context_catalog: Sequence[AnalysisContextCatalogItem] = (),
    analysis_request: LLMAnalysisRequest | None = None,
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
    non_patchable = [section for section in recovery.invalid_sections if section not in _ANALYSIS_OPTIONAL_SECTION_FIELDS]
    if non_patchable:
        raise LLMOutputParseError(
            "reasoning and guidance sections are locally isolated and are not eligible for provider patching",
            stage="section_patch_validation",
            repair_applied=bool(syntactic_repair_log),
            field_paths=tuple(section.value for section in non_patchable),
            issue_codes=("section_not_patchable",),
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
    _validate_deterministic_role_coherence(
        result,
        analysis_request=analysis_request,
        repair_applied=True,
    )
    return ParsedAnalysisResult(
        result=result,
        repair_applied=True,
        repair_log=repair_log,
        hydration_log=list(recovery.hydration_log),
        model_output_schema_version=recovery.model_output_schema_version,
        candidate_text=candidate_text,
    )


_ANALYSIS_CORE_FIELDS = frozenset(
    {
        "schema_version",
        "verdict",
        "confidence",
        "summary",
        "evidence",
        "decision_evidence_refs",
        "decision_reasoning_refs",
        "reason",
        "recommended_action",
        "knowledge_candidates",
    }
)
_ANALYSIS_RECOVERABLE_TOP_LEVEL_FIELDS = _ANALYSIS_CORE_FIELDS | {
    "reasoning",
    "evidence_gaps",
    "manual_checks",
}
_ANALYSIS_OPTIONAL_SECTION_FIELDS = {
    AnalysisOutputSection.SCENARIO_ASSESSMENTS: "scenario_assessments",
    AnalysisOutputSection.NETWORK_DIRECTION: "network_direction",
    AnalysisOutputSection.ROLE_ADJUDICATION: "role_adjudication",
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

    schema_inference_log: list[dict[str, Any]] = []
    inferred_model_output_version = _unversioned_analysis_model_output_version(repaired_data)
    if inferred_model_output_version is not None:
        repaired_data = {
            **repaired_data,
            "schema_version": inferred_model_output_version,
        }
        schema_inference_log.append(
            {
                "stage": "model_output_schema_normalization",
                "repair": "restore_unambiguous_compact_schema_version",
                "schema_version": inferred_model_output_version,
            }
        )

    model_output_schema_version = str(repaired_data.get("schema_version") or "")
    if model_output_schema_version in {
        ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION,
        LEGACY_ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION,
        LEGACY_ANALYSIS_MODEL_OUTPUT_V2_SCHEMA_VERSION,
        LEGACY_ANALYSIS_MODEL_OUTPUT_V3_SCHEMA_VERSION,
    }:
        hydrated_data, hydration_log = _hydrate_analysis_model_output(
            repaired_data,
            evidence_catalog=evidence_catalog,
            context_catalog=context_catalog,
        )
    else:
        hydrated_data = repaired_data
        hydration_log = []
        model_output_schema_version = "soc.analysis_result.v4"

    normalized, semantic_repair_log = _normalize_analysis_result_shape(
        hydrated_data,
        evidence_catalog=evidence_catalog,
        context_catalog=context_catalog,
    )
    hydration_repair_applied = any(item.get("operation") in _OPTIONAL_HYDRATION_REPAIR_OPERATIONS for item in hydration_log)
    repair_log = [
        *syntactic_repair_log,
        *schema_inference_log,
        *semantic_repair_log,
    ]
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
        repair_applied=(syntactic_repair_applied or bool(schema_inference_log) or hydration_repair_applied or bool(semantic_repair_log)),
        repair_log=repair_log,
        hydration_log=hydration_log,
        model_output_schema_version=model_output_schema_version,
    )


def _recover_analysis_candidate(
    decoded: _DecodedAnalysisCandidate,
    *,
    context_catalog: Sequence[AnalysisContextCatalogItem],
    analysis_request: LLMAnalysisRequest | None,
) -> RecoverableAnalysisResult | None:
    defaults = _analysis_optional_section_defaults()
    accepted_data = {key: value for key, value in decoded.data.items() if key in _ANALYSIS_CORE_FIELDS}
    raw_evidence = decoded.data.get("evidence")
    allowed_evidence_refs = {item.get("evidence_ref") for item in raw_evidence if isinstance(item, Mapping) and isinstance(item.get("evidence_ref"), str)} if isinstance(raw_evidence, list) else set()
    reasoning, reasoning_issues = _recover_reasoning_items(
        decoded.data,
        allowed_evidence_refs=allowed_evidence_refs,
    )
    if not reasoning:
        return None
    valid_reasoning_ids = {item["reasoning_id"] for item in reasoning if isinstance(item.get("reasoning_id"), str)}
    decision_reasoning_refs = accepted_data.get("decision_reasoning_refs")
    if isinstance(decision_reasoning_refs, list):
        accepted_data["decision_reasoning_refs"] = [reference for reference in decision_reasoning_refs if reference in valid_reasoning_ids]
    if not accepted_data.get("decision_reasoning_refs"):
        accepted_data["decision_reasoning_refs"] = [reasoning[0]["reasoning_id"]]
    guidance, guidance_issues = _recover_guidance(decoded.data)
    accepted_data["reasoning"] = reasoning
    accepted_data.update(guidance)
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
        _validate_deterministic_role_coherence(
            core_result,
            analysis_request=analysis_request,
            repair_applied=True,
        )
    except LLMOutputParseError:
        return None

    accepted_sections = [AnalysisOutputSection.CORE]
    invalid_sections: list[AnalysisOutputSection] = []
    local_recovery_log: list[dict[str, Any]] = []
    issues: list[AnalysisSectionValidationIssue] = [
        *reasoning_issues,
        *guidance_issues,
    ]
    if reasoning_issues:
        invalid_sections.append(AnalysisOutputSection.REASONING)
        if decoded.model_output_schema_version in _RUNTIME_OWNED_REASONING_OUTPUT_VERSIONS:
            local_recovery_log.append(
                {
                    "stage": "item_recovery",
                    "repair": "drop_invalid_runtime_materialized_reasoning_items",
                    "item_count": len(reasoning_issues),
                    "field_paths": sorted({path for issue in reasoning_issues for path in issue.field_paths}),
                }
            )
    else:
        accepted_sections.append(AnalysisOutputSection.REASONING)
    if guidance_issues:
        invalid_sections.append(AnalysisOutputSection.GUIDANCE)
    else:
        accepted_sections.append(AnalysisOutputSection.GUIDANCE)
    for section, field_name in _ANALYSIS_OPTIONAL_SECTION_FIELDS.items():
        if field_name not in decoded.data:
            if decoded.model_output_schema_version in _RUNTIME_OWNED_REASONING_OUTPUT_VERSIONS:
                continue
            invalid_sections.append(section)
            issues.append(
                AnalysisSectionValidationIssue(
                    section=section,
                    stage="schema_validation",
                    error_type="MissingAnalysisSection",
                    message=f"model output omitted required section {field_name}",
                    field_paths=(field_name,),
                    issue_codes=("missing",),
                )
            )
            continue

        section_value = decoded.data[field_name]
        section_issues: list[AnalysisSectionValidationIssue] = []
        if decoded.model_output_schema_version in _RUNTIME_OWNED_REASONING_OUTPUT_VERSIONS:
            if section is AnalysisOutputSection.SCENARIO_ASSESSMENTS:
                section_value, section_issues = _recover_compact_scenario_items(
                    accepted_data,
                    section_value,
                    context_catalog=context_catalog,
                    analysis_request=analysis_request,
                )
            elif section is AnalysisOutputSection.ROLE_ADJUDICATION:
                section_value, section_issues = _recover_compact_role_items(
                    accepted_data,
                    section_value,
                    context_catalog=context_catalog,
                    analysis_request=analysis_request,
                )
            issues.extend(section_issues)
            if section_value is None:
                invalid_sections.append(section)
                continue

        candidate = dict(accepted_data)
        candidate[field_name] = section_value
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
            _validate_deterministic_role_coherence(
                result,
                analysis_request=analysis_request,
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
                    field_paths=tuple(exc.field_paths),
                    issue_codes=tuple(exc.issue_codes),
                )
            )
            continue
        accepted_data[field_name] = section_value
        if section_issues:
            invalid_sections.append(section)
            local_recovery_log.append(
                {
                    "stage": "item_recovery",
                    "repair": "retain_valid_optional_items",
                    "section": section.value,
                    "rejected_item_count": len(section_issues),
                    "field_paths": sorted({path for issue in section_issues for path in issue.field_paths}),
                }
            )
        else:
            accepted_sections.append(section)

    result = _validate_analysis_result_data(
        accepted_data,
        repair_applied=decoded.repair_applied,
    )
    _validate_directional_context_references(
        result,
        context_catalog=context_catalog,
        repair_applied=True,
    )
    repair_log = [*decoded.repair_log, *local_recovery_log]
    unknown_fields = sorted(set(decoded.data) - _ANALYSIS_RECOVERABLE_TOP_LEVEL_FIELDS - set(_ANALYSIS_OPTIONAL_SECTION_FIELDS.values()))
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
        repair_applied=decoded.repair_applied,
        repair_log=tuple(repair_log),
        hydration_log=tuple(decoded.hydration_log),
        model_output_schema_version=decoded.model_output_schema_version,
        candidate_text=decoded.candidate_text,
    )


def _recover_compact_scenario_items(
    accepted_data: Mapping[str, Any],
    raw_value: Any,
    *,
    context_catalog: Sequence[AnalysisContextCatalogItem],
    analysis_request: LLMAnalysisRequest | None,
) -> tuple[list[Any] | None, list[AnalysisSectionValidationIssue]]:
    if not isinstance(raw_value, list):
        return None, [
            AnalysisSectionValidationIssue(
                section=AnalysisOutputSection.SCENARIO_ASSESSMENTS,
                stage="schema_validation",
                error_type="InvalidScenarioSection",
                message="scenario_assessments must be a JSON array",
                field_paths=("scenario_assessments",),
                issue_codes=("list_type",),
            )
        ]
    if not raw_value:
        return [], []

    indexed = list(enumerate(raw_value))
    indexed.sort(
        key=lambda pair: (
            not (isinstance(pair[1], Mapping) and pair[1].get("is_primary") is True),
            pair[0],
        )
    )
    accepted: list[Any] = []
    issues: list[AnalysisSectionValidationIssue] = []
    for index, item in indexed:
        candidate_items = [*accepted, item]
        error = _optional_section_error(
            accepted_data,
            field_name="scenario_assessments",
            value=candidate_items,
            context_catalog=context_catalog,
            analysis_request=analysis_request,
        )
        if error is not None:
            issues.append(
                _section_validation_issue(
                    AnalysisOutputSection.SCENARIO_ASSESSMENTS,
                    error,
                    fallback_path=f"scenario_assessments.{index}",
                )
            )
            continue
        accepted.append(item)
    return (accepted or None), issues


def _recover_compact_role_items(
    accepted_data: Mapping[str, Any],
    raw_value: Any,
    *,
    context_catalog: Sequence[AnalysisContextCatalogItem],
    analysis_request: LLMAnalysisRequest | None,
) -> tuple[dict[str, Any] | None, list[AnalysisSectionValidationIssue]]:
    if not isinstance(raw_value, Mapping):
        return None, [
            AnalysisSectionValidationIssue(
                section=AnalysisOutputSection.ROLE_ADJUDICATION,
                stage="schema_validation",
                error_type="InvalidRoleSection",
                message="role_adjudication must be a JSON object",
                field_paths=("role_adjudication",),
                issue_codes=("dict_type",),
            )
        ]
    raw_roles = raw_value.get("roles")
    if not isinstance(raw_roles, list):
        return None, [
            AnalysisSectionValidationIssue(
                section=AnalysisOutputSection.ROLE_ADJUDICATION,
                stage="schema_validation",
                error_type="InvalidRoleSection",
                message="role_adjudication.roles must be a JSON array",
                field_paths=("role_adjudication.roles",),
                issue_codes=("list_type",),
            )
        ]
    if not raw_roles:
        return dict(raw_value), []

    accepted_roles: list[Any] = []
    issues: list[AnalysisSectionValidationIssue] = []
    for index, item in enumerate(raw_roles):
        candidate_value = {
            **raw_value,
            "roles": [*accepted_roles, item],
        }
        error = _optional_section_error(
            accepted_data,
            field_name="role_adjudication",
            value=candidate_value,
            context_catalog=context_catalog,
            analysis_request=analysis_request,
        )
        if error is not None:
            issues.append(
                _section_validation_issue(
                    AnalysisOutputSection.ROLE_ADJUDICATION,
                    error,
                    fallback_path=f"role_adjudication.roles.{index}",
                )
            )
            continue
        accepted_roles.append(item)
    if not accepted_roles:
        return None, issues
    return {**raw_value, "roles": accepted_roles}, issues


def _optional_section_error(
    accepted_data: Mapping[str, Any],
    *,
    field_name: str,
    value: Any,
    context_catalog: Sequence[AnalysisContextCatalogItem],
    analysis_request: LLMAnalysisRequest | None,
) -> LLMOutputParseError | None:
    candidate = dict(accepted_data)
    candidate[field_name] = value
    try:
        result = _validate_analysis_result_data(candidate, repair_applied=True)
        _validate_directional_context_references(
            result,
            context_catalog=context_catalog,
            repair_applied=True,
        )
        _validate_deterministic_role_coherence(
            result,
            analysis_request=analysis_request,
            repair_applied=True,
        )
    except LLMOutputParseError as exc:
        return exc
    return None


def _section_validation_issue(
    section: AnalysisOutputSection,
    error: LLMOutputParseError,
    *,
    fallback_path: str,
) -> AnalysisSectionValidationIssue:
    return AnalysisSectionValidationIssue(
        section=section,
        stage=error.stage,
        error_type=type(error.__cause__ or error).__name__,
        message=str(error),
        field_paths=tuple(error.field_paths) or (fallback_path,),
        issue_codes=tuple(error.issue_codes),
    )


def _recover_reasoning_items(
    data: Mapping[str, Any],
    *,
    allowed_evidence_refs: set[str],
) -> tuple[list[dict[str, Any]], list[AnalysisSectionValidationIssue]]:
    raw_items = data.get("reasoning")
    if not isinstance(raw_items, list):
        return (
            [],
            [
                AnalysisSectionValidationIssue(
                    section=AnalysisOutputSection.REASONING,
                    stage="schema_validation",
                    error_type="InvalidReasoningSection",
                    message="reasoning must be a JSON array",
                    field_paths=("reasoning",),
                    issue_codes=("list_type",),
                )
            ],
        )

    accepted: list[dict[str, Any]] = []
    issues: list[AnalysisSectionValidationIssue] = []
    seen_ids: set[str] = set()
    allowed_fields = {
        "schema_version",
        "reasoning_id",
        "statement",
        "basis",
        "evidence_refs",
        "context_refs",
        "confidence",
    }
    for index, item in enumerate(raw_items):
        path = f"reasoning.{index}"
        if not isinstance(item, Mapping):
            issues.append(
                AnalysisSectionValidationIssue(
                    section=AnalysisOutputSection.REASONING,
                    stage="schema_validation",
                    error_type="InvalidReasoningItem",
                    message=f"{path} must be a JSON object",
                    field_paths=(path,),
                    issue_codes=("dict_type",),
                )
            )
            continue
        unknown = sorted(set(item) - allowed_fields)
        try:
            parsed = AnalysisReasoningItem.model_validate(dict(item))
        except ValidationError as exc:
            field_paths, issue_codes = _validation_error_summary(exc)
            issues.append(
                AnalysisSectionValidationIssue(
                    section=AnalysisOutputSection.REASONING,
                    stage="schema_validation",
                    error_type="InvalidReasoningItem",
                    message=str(exc),
                    field_paths=tuple(f"{path}.{field}" for field in field_paths) or (path,),
                    issue_codes=issue_codes,
                )
            )
            continue
        unknown_evidence_refs = sorted(set(parsed.evidence_refs) - allowed_evidence_refs)
        if unknown_evidence_refs:
            issues.append(
                AnalysisSectionValidationIssue(
                    section=AnalysisOutputSection.REASONING,
                    stage="reference_validation",
                    error_type="UnknownReasoningEvidenceReference",
                    message=(f"{path} references evidence absent from the hydrated current-alert catalog: {unknown_evidence_refs}"),
                    field_paths=(f"{path}.evidence_refs",),
                    issue_codes=("reference_not_found",),
                )
            )
            continue
        if unknown or parsed.reasoning_id in seen_ids:
            issues.append(
                AnalysisSectionValidationIssue(
                    section=AnalysisOutputSection.REASONING,
                    stage="schema_validation",
                    error_type=("DuplicateReasoningId" if parsed.reasoning_id in seen_ids else "UnsupportedReasoningField"),
                    message=(f"duplicate reasoning ID {parsed.reasoning_id}" if parsed.reasoning_id in seen_ids else f"{path} contains unsupported fields {unknown}"),
                    field_paths=(path,),
                    issue_codes=("duplicate" if parsed.reasoning_id in seen_ids else "extra_forbidden",),
                )
            )
            continue
        seen_ids.add(parsed.reasoning_id)
        accepted.append(parsed.model_dump(mode="json"))
    return accepted, issues


def _recover_guidance(
    data: Mapping[str, Any],
) -> tuple[dict[str, list[str]], list[AnalysisSectionValidationIssue]]:
    accepted: dict[str, list[str]] = {}
    issues: list[AnalysisSectionValidationIssue] = []
    for field_name in ("evidence_gaps", "manual_checks"):
        values = data.get(field_name, [])
        if not isinstance(values, list):
            accepted[field_name] = []
            issues.append(
                AnalysisSectionValidationIssue(
                    section=AnalysisOutputSection.GUIDANCE,
                    stage="schema_validation",
                    error_type="InvalidGuidanceSection",
                    message=f"{field_name} must be a JSON array",
                    field_paths=(field_name,),
                    issue_codes=("list_type",),
                )
            )
            continue
        accepted_values = [value for value in values if isinstance(value, str) and value.strip() and len(value) <= 1000]
        accepted[field_name] = accepted_values
        if len(accepted_values) != len(values):
            issues.append(
                AnalysisSectionValidationIssue(
                    section=AnalysisOutputSection.GUIDANCE,
                    stage="schema_validation",
                    error_type="InvalidGuidanceItem",
                    message=f"{field_name} contains invalid entries",
                    field_paths=(field_name,),
                    issue_codes=("string_type",),
                )
            )
    return accepted, issues


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


_MODEL_OUTPUT_V1_CORE_FIELDS = frozenset(
    {
        "schema_version",
        "verdict",
        "confidence",
        "summary",
        "reasoning",
        "evidence_gaps",
        "manual_checks",
        "reason",
        "recommended_action",
    }
)
_MODEL_OUTPUT_V1_OPTIONAL_FIELDS = frozenset(
    {
        "scenario_assessments",
        "network_direction",
        "role_adjudication",
    }
)
_MODEL_OUTPUT_V2_CORE_FIELDS = frozenset(
    {
        "schema_version",
        "verdict",
        "confidence",
        "summary",
        "decision_evidence_refs",
        "decision_context_refs",
        "reason",
        "recommended_action",
    }
)
_MODEL_OUTPUT_V2_OPTIONAL_FIELDS = frozenset(
    {
        "reasoning",
        "scenario_assessments",
        "network_direction",
        "role_adjudication",
        "evidence_gaps",
        "manual_checks",
    }
)
_MODEL_OUTPUT_V3_CORE_FIELDS = _MODEL_OUTPUT_V2_CORE_FIELDS
_MODEL_OUTPUT_V3_OPTIONAL_FIELDS = frozenset(
    {
        "scenario_assessments",
        "network_direction",
        "role_adjudication",
        "evidence_gaps",
        "manual_checks",
    }
)
_MODEL_OUTPUT_V4_CORE_FIELDS = _MODEL_OUTPUT_V3_CORE_FIELDS
_MODEL_OUTPUT_V4_OPTIONAL_FIELDS = _MODEL_OUTPUT_V3_OPTIONAL_FIELDS
_MODEL_OUTPUT_RUNTIME_OWNED_FIELDS = frozenset(
    {
        "evidence",
        "knowledge_candidates",
        "decision_reasoning_refs",
    }
)


def _unversioned_analysis_model_output_version(
    data: Mapping[str, Any],
) -> str | None:
    """Recognize only an otherwise complete compact payload missing its version.

    This is a mechanical protocol repair, not a semantic guess. Legacy
    ``AnalysisResult.v4`` candidates carry Runtime-owned ``evidence`` and
    ``knowledge_candidates`` fields and therefore cannot enter this path.
    """

    if "schema_version" in data:
        return None
    fields = set(data)
    if fields & _MODEL_OUTPUT_RUNTIME_OWNED_FIELDS:
        return None

    required_v4 = _MODEL_OUTPUT_V4_CORE_FIELDS - {
        "schema_version",
        "decision_context_refs",
    }
    allowed_v4 = required_v4 | {"decision_context_refs"} | _MODEL_OUTPUT_V4_OPTIONAL_FIELDS
    if required_v4 <= fields <= allowed_v4:
        return ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION

    required_v2 = _MODEL_OUTPUT_V2_CORE_FIELDS - {
        "schema_version",
        "decision_context_refs",
    }
    allowed_v2 = required_v2 | {"decision_context_refs"} | _MODEL_OUTPUT_V2_OPTIONAL_FIELDS
    if required_v2 <= fields <= allowed_v2:
        return LEGACY_ANALYSIS_MODEL_OUTPUT_V2_SCHEMA_VERSION

    required_v1 = _MODEL_OUTPUT_V1_CORE_FIELDS - {"schema_version"}
    allowed_v1 = required_v1 | _MODEL_OUTPUT_V1_OPTIONAL_FIELDS
    if required_v1 <= fields <= allowed_v1:
        return LEGACY_ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION
    return None


def _restore_model_reference_aliases(
    data: dict[str, Any],
    *,
    aliases: ModelReferenceAliases,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Restore only exact short aliases from the frozen request catalog."""

    evidence_list_fields = frozenset({"decision_evidence_refs", "evidence_refs"})
    context_list_fields = frozenset({"decision_context_refs", "context_refs"})
    evidence_scalar_fields = frozenset({"connection_initiator_ref", "entity_ref"})
    rewrites: list[dict[str, str]] = []

    def restore(reference: Any, *, path: str, evidence: bool) -> Any:
        if not isinstance(reference, str):
            return reference
        stable = aliases.alias_to_stable.get(reference.upper())
        if stable is None:
            return reference
        if evidence and not stable.startswith("E-"):
            return reference
        if not evidence and stable.startswith("E-"):
            return reference
        rewrites.append(
            {
                "field": path,
                "alias": reference[:16],
                "stable_ref": stable,
            }
        )
        return stable

    def visit(value: Any, *, path: str) -> Any:
        if isinstance(value, Mapping):
            normalized: dict[str, Any] = {}
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if key in evidence_list_fields and isinstance(child, list):
                    normalized[key] = [
                        restore(
                            reference,
                            path=f"{child_path}[{index}]",
                            evidence=True,
                        )
                        for index, reference in enumerate(child)
                    ]
                elif key in context_list_fields and isinstance(child, list):
                    normalized[key] = [
                        restore(
                            reference,
                            path=f"{child_path}[{index}]",
                            evidence=False,
                        )
                        for index, reference in enumerate(child)
                    ]
                elif key in evidence_scalar_fields:
                    normalized[key] = restore(
                        child,
                        path=child_path,
                        evidence=True,
                    )
                else:
                    normalized[key] = visit(child, path=child_path)
            return normalized
        if isinstance(value, list):
            return [visit(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    normalized = visit(data, path="")
    if not isinstance(normalized, dict):
        raise TypeError("analysis model output must remain an object")
    if not rewrites:
        return normalized, []
    return normalized, [
        {
            "stage": "runtime_hydration",
            "operation": "restore_model_reference_aliases",
            "rewrite_count": len(rewrites),
            "rewrites": rewrites[:100],
        }
    ]


def _normalize_compact_core_reference_lists(
    data: Mapping[str, Any],
    *,
    evidence_refs: set[str],
    context_refs: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bound the compact core to exact catalog references before validation."""

    normalized = dict(data)
    hydration_log: list[dict[str, Any]] = []
    for field_name, allowed, operation in (
        (
            "decision_evidence_refs",
            evidence_refs,
            "retain_catalog_backed_core_evidence_refs",
        ),
        (
            "decision_context_refs",
            context_refs,
            "retain_catalog_backed_core_context_refs",
        ),
    ):
        values = normalized.get(field_name)
        if not isinstance(values, list):
            continue
        retained, details = _retain_catalog_reference_values(
            values,
            allowed=allowed,
            limit=20,
        )
        if retained == values:
            continue
        normalized[field_name] = retained
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": operation,
                "field": field_name,
                **details,
            }
        )
    return normalized, hydration_log


def _materialize_missing_compact_summary(
    data: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Copy an existing valid reason into a missing display summary."""

    normalized = dict(data)
    summary = normalized.get("summary")
    reason = normalized.get("reason")
    if ("summary" not in normalized or summary is None or summary == "") and isinstance(reason, str) and reason.strip() and len(reason) <= 4_000:
        normalized["summary"] = reason
        return normalized, [
            {
                "stage": "runtime_hydration",
                "operation": "materialize_summary_from_reason",
                "field": "summary",
                "source_field": "reason",
                "exact_copy": True,
            }
        ]
    return normalized, []


def _hydrate_analysis_model_output(
    data: dict[str, Any],
    *,
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem],
    context_catalog: Sequence[AnalysisContextCatalogItem],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build the complete domain result from the compact model-owned payload."""

    model_output_version = str(data.get("schema_version") or "")
    compact_hydration_log: list[dict[str, Any]] = []
    if model_output_version == ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION:
        allowed_fields = _MODEL_OUTPUT_V4_CORE_FIELDS | _MODEL_OUTPUT_V4_OPTIONAL_FIELDS
        data, alias_hydration_log = _restore_model_reference_aliases(
            data,
            aliases=build_model_reference_aliases(
                evidence_catalog,
                context_catalog,
            ),
        )
        compact_hydration_log.extend(alias_hydration_log)
        data, core_reference_log = _normalize_compact_core_reference_lists(
            data,
            evidence_refs={item.evidence_ref for item in evidence_catalog},
            context_refs={item.context_ref for item in context_catalog},
        )
        compact_hydration_log.extend(core_reference_log)
        data, summary_hydration_log = _materialize_missing_compact_summary(data)
        compact_hydration_log.extend(summary_hydration_log)
        try:
            model_core = AnalysisModelCoreOutputV4.model_validate(data)
        except ValidationError as exc:
            field_paths, issue_codes = _validation_error_summary(exc)
            raise LLMOutputParseError(
                f"model output failed core v4 validation: {exc}",
                stage="model_output_core_validation",
                field_paths=field_paths,
                issue_codes=issue_codes,
            ) from exc
    elif model_output_version == LEGACY_ANALYSIS_MODEL_OUTPUT_V3_SCHEMA_VERSION:
        allowed_fields = _MODEL_OUTPUT_V3_CORE_FIELDS | _MODEL_OUTPUT_V3_OPTIONAL_FIELDS
        try:
            model_core = AnalysisModelCoreOutputV3.model_validate(data)
        except ValidationError as exc:
            field_paths, issue_codes = _validation_error_summary(exc)
            raise LLMOutputParseError(
                f"model output failed core v3 validation: {exc}",
                stage="model_output_core_validation",
                field_paths=field_paths,
                issue_codes=issue_codes,
            ) from exc
    elif model_output_version == LEGACY_ANALYSIS_MODEL_OUTPUT_V2_SCHEMA_VERSION:
        allowed_fields = _MODEL_OUTPUT_V2_CORE_FIELDS | _MODEL_OUTPUT_V2_OPTIONAL_FIELDS
        try:
            model_core = AnalysisModelCoreOutputV2.model_validate(data)
        except ValidationError as exc:
            field_paths, issue_codes = _validation_error_summary(exc)
            raise LLMOutputParseError(
                f"model output failed core v2 validation: {exc}",
                stage="model_output_core_validation",
                field_paths=field_paths,
                issue_codes=issue_codes,
            ) from exc
    else:
        allowed_fields = _MODEL_OUTPUT_V1_CORE_FIELDS | _MODEL_OUTPUT_V1_OPTIONAL_FIELDS
        model_core = None
    ignored_runtime_fields = sorted(set(data) & _MODEL_OUTPUT_RUNTIME_OWNED_FIELDS)
    unknown_fields = sorted(set(data) - allowed_fields - _MODEL_OUTPUT_RUNTIME_OWNED_FIELDS)
    if unknown_fields:
        raise LLMOutputParseError(
            "compact model output contains unsupported fields: " + ", ".join(unknown_fields),
            stage="model_output_schema_validation",
            field_paths=unknown_fields,
            issue_codes=("extra_forbidden",),
        )

    hydrated = {key: value for key, value in data.items() if key not in _MODEL_OUTPUT_RUNTIME_OWNED_FIELDS and key != "decision_context_refs"}
    hydrated["schema_version"] = "soc.analysis_result.v4"
    hydrated["knowledge_candidates"] = []
    catalog_by_ref = {item.evidence_ref: item for item in evidence_catalog}

    runtime_owns_optional_reasoning = model_output_version in _RUNTIME_OWNED_REASONING_OUTPUT_VERSIONS
    next_runtime_reasoning_index = 1
    hydrated_reasoning: list[Any] = []
    if model_core is not None:
        decision_basis = ["current_evidence", "general_security_knowledge"]
        for reference in model_core.decision_context_refs:
            basis = {
                "S-": "skill",
                "A-": "adapter_contract",
                "M-": "confirmed_memory",
                "C-": "governed_context",
                "T-": "tool_result",
            }.get(reference[:2])
            if basis is not None and basis not in decision_basis:
                decision_basis.append(basis)
        hydrated_reasoning.append(
            {
                "schema_version": "soc.analysis_reasoning_item.v1",
                "reasoning_id": "R-00",
                "statement": model_core.reason,
                "basis": decision_basis,
                "evidence_refs": list(model_core.decision_evidence_refs),
                "context_refs": list(model_core.decision_context_refs),
                "confidence": model_core.confidence,
            }
        )
        hydrated["decision_evidence_refs"] = list(model_core.decision_evidence_refs)
        hydrated["decision_reasoning_refs"] = ["R-00"]
        compact_hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_core_decision_reasoning",
                "field": "reasoning[0]",
            }
        )
    reasoning = None if runtime_owns_optional_reasoning else data.get("reasoning")
    if isinstance(reasoning, list):
        for index, item in enumerate(reasoning):
            if not isinstance(item, Mapping):
                hydrated_reasoning.append(item)
                continue
            normalized_item = _hydrate_compact_reasoning_item(
                item,
                index=index,
                hydration_log=compact_hydration_log,
            )
            hydrated_reasoning.append(
                {
                    **normalized_item,
                    "schema_version": "soc.analysis_reasoning_item.v1",
                }
            )
    if hydrated_reasoning:
        hydrated["reasoning"] = hydrated_reasoning

    def materialize_optional_reasoning(
        item: Mapping[str, Any],
        *,
        field: str,
    ) -> str | None:
        nonlocal next_runtime_reasoning_index
        if next_runtime_reasoning_index > 19:
            return None
        rationale = item.get("rationale")
        evidence_refs = item.get("evidence_refs")
        context_refs = item.get("context_refs", [])
        confidence = item.get("confidence")
        if (
            not isinstance(rationale, str)
            or not rationale.strip()
            or not isinstance(evidence_refs, list)
            or not evidence_refs
            or any(not isinstance(reference, str) for reference in evidence_refs)
            or not isinstance(context_refs, list)
            or any(not isinstance(reference, str) for reference in context_refs)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
        ):
            return None
        reasoning_id = f"R-{next_runtime_reasoning_index:02d}"
        next_runtime_reasoning_index += 1
        basis = ["current_evidence", "general_security_knowledge"]
        prefix_basis = {
            "S-": "skill",
            "A-": "adapter_contract",
            "M-": "confirmed_memory",
            "C-": "governed_context",
            "T-": "tool_result",
        }
        for reference in context_refs:
            label = prefix_basis.get(reference[:2])
            if label is not None and label not in basis:
                basis.append(label)
        hydrated_reasoning.append(
            {
                "schema_version": "soc.analysis_reasoning_item.v1",
                "reasoning_id": reasoning_id,
                "statement": rationale,
                "basis": basis,
                "evidence_refs": list(evidence_refs),
                "context_refs": list(context_refs),
                "confidence": confidence,
            }
        )
        compact_hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_optional_section_reasoning",
                "field": field,
                "reasoning_id": reasoning_id,
            }
        )
        return reasoning_id

    scenarios = data.get("scenario_assessments")
    if isinstance(scenarios, list):
        hydrated_scenarios: list[Any] = []
        for item in scenarios:
            if not isinstance(item, Mapping):
                hydrated_scenarios.append(item)
                continue
            item_path = f"scenario_assessments[{len(hydrated_scenarios)}]"
            normalized_item = _hydrate_compact_rationale_alias(
                item,
                path=item_path,
                hydration_log=compact_hydration_log,
            )
            normalized_item = _hydrate_compact_scenario_name(
                normalized_item,
                path=item_path,
                hydration_log=compact_hydration_log,
            )
            normalized_item.pop("schema_version", None)
            if runtime_owns_optional_reasoning:
                normalized_item = _retain_supported_optional_fields(
                    normalized_item,
                    allowed_fields=_COMPACT_SCENARIO_ITEM_FIELDS,
                    path=item_path,
                    hydration_log=compact_hydration_log,
                )
                normalized_item = _normalize_runtime_owned_optional_item(
                    normalized_item,
                    path=item_path,
                    allowed_evidence_refs=set(catalog_by_ref),
                    allowed_context_refs={item.context_ref for item in context_catalog},
                    hydration_log=compact_hydration_log,
                )
                normalized_item.pop("reasoning_refs", None)
                reasoning_id = materialize_optional_reasoning(
                    normalized_item,
                    field=item_path,
                )
                normalized_item["reasoning_refs"] = [reasoning_id] if reasoning_id is not None else []
                normalized_item.pop("context_refs", None)
            hydrated_scenarios.append(
                {
                    "scenario_key": None,
                    "competing_explanations": [],
                    **normalized_item,
                    "schema_version": "soc.triage_scenario_assessment.v2",
                }
            )
        hydrated["scenario_assessments"] = hydrated_scenarios

    network_direction = data.get("network_direction")
    if isinstance(network_direction, Mapping):
        normalized_direction = _hydrate_compact_rationale_alias(
            network_direction,
            path="network_direction",
            hydration_log=compact_hydration_log,
        )
        normalized_direction.pop("schema_version", None)
        if runtime_owns_optional_reasoning:
            normalized_direction = _retain_supported_optional_fields(
                normalized_direction,
                allowed_fields=_COMPACT_NETWORK_DIRECTION_FIELDS,
                path="network_direction",
                hydration_log=compact_hydration_log,
            )
            normalized_direction = _normalize_runtime_owned_optional_item(
                normalized_direction,
                path="network_direction",
                allowed_evidence_refs=set(catalog_by_ref),
                allowed_context_refs={item.context_ref for item in context_catalog},
                hydration_log=compact_hydration_log,
            )
            normalized_direction.pop("reasoning_refs", None)
            normalized_direction.pop("connection_initiator", None)
        initiator_ref = normalized_direction.pop("connection_initiator_ref", None)
        if runtime_owns_optional_reasoning:
            initiator_ref = _resolve_reference(
                initiator_ref,
                allowed=set(catalog_by_ref),
                explicit_rewrites={},
            )
        if model_core is not None and initiator_ref is not None:
            typed_initiator = _resolve_typed_entity_reference(
                initiator_ref,
                catalog_by_ref=catalog_by_ref,
            )
            if typed_initiator is None:
                compact_hydration_log.append(
                    {
                        "stage": "runtime_hydration",
                        "operation": "discard_untyped_direction_entity_reference",
                        "field": "network_direction.connection_initiator_ref",
                        "evidence_ref": initiator_ref,
                    }
                )
            else:
                resolved_initiator_ref, initiator_fact, _ = typed_initiator
                if resolved_initiator_ref != initiator_ref:
                    compact_hydration_log.append(
                        {
                            "stage": "runtime_hydration",
                            "operation": "canonicalize_role_entity_reference",
                            "field": "network_direction.connection_initiator_ref",
                            "from_evidence_ref": initiator_ref,
                            "to_evidence_ref": resolved_initiator_ref,
                        }
                    )
                initiator_ref = resolved_initiator_ref
                normalized_direction["connection_initiator"] = str(initiator_fact.value)
                evidence_refs = normalized_direction.get("evidence_refs")
                if not isinstance(evidence_refs, list):
                    normalized_direction["evidence_refs"] = [initiator_ref]
                elif initiator_ref not in evidence_refs:
                    normalized_direction["evidence_refs"] = [
                        *evidence_refs,
                        initiator_ref,
                    ]
                compact_hydration_log.append(
                    {
                        "stage": "runtime_hydration",
                        "operation": "materialize_direction_entity_reference",
                        "field": "network_direction.connection_initiator_ref",
                        "evidence_ref": initiator_ref,
                    }
                )
        if runtime_owns_optional_reasoning:
            reasoning_id = materialize_optional_reasoning(
                normalized_direction,
                field="network_direction",
            )
            normalized_direction["reasoning_refs"] = [reasoning_id] if reasoning_id is not None else []
        hydrated["network_direction"] = {
            "semantic_direction": None,
            "connection_initiator": None,
            "intermediaries": [],
            "context_refs": [],
            "evidence_gaps": [],
            **normalized_direction,
            "schema_version": "soc.network_direction_assessment.v1",
        }

    role_adjudication = data.get("role_adjudication")
    if isinstance(role_adjudication, Mapping):
        role_section = dict(role_adjudication)
        if runtime_owns_optional_reasoning:
            role_section = _retain_supported_optional_fields(
                role_section,
                allowed_fields=_COMPACT_ROLE_SECTION_FIELDS,
                path="role_adjudication",
                hydration_log=compact_hydration_log,
            )
        raw_roles = role_section.get("roles")
        roles: Any = raw_roles
        if isinstance(raw_roles, list):
            roles = []
            for index, item in enumerate(raw_roles):
                if not isinstance(item, Mapping):
                    roles.append(item)
                    continue
                normalized_role = _hydrate_compact_rationale_alias(
                    item,
                    path=f"role_adjudication.roles[{index}]",
                    hydration_log=compact_hydration_log,
                )
                normalized_role.pop("schema_version", None)
                if runtime_owns_optional_reasoning:
                    normalized_role = _retain_supported_optional_fields(
                        normalized_role,
                        allowed_fields=_COMPACT_ROLE_ITEM_FIELDS,
                        path=f"role_adjudication.roles[{index}]",
                        hydration_log=compact_hydration_log,
                    )
                    normalized_role = _materialize_missing_role_contract_fields(
                        normalized_role,
                        path=f"role_adjudication.roles[{index}]",
                        hydration_log=compact_hydration_log,
                    )
                    normalized_role = _normalize_runtime_owned_optional_item(
                        normalized_role,
                        path=f"role_adjudication.roles[{index}]",
                        allowed_evidence_refs=set(catalog_by_ref),
                        allowed_context_refs={item.context_ref for item in context_catalog},
                        hydration_log=compact_hydration_log,
                    )
                    normalized_role.pop("reasoning_refs", None)
                    normalized_role.pop("entity_type", None)
                    normalized_role.pop("value", None)
                entity_ref = normalized_role.pop("entity_ref", None)
                role_is_unresolved = normalized_role.get("status") == "unresolved"
                if runtime_owns_optional_reasoning and role_is_unresolved:
                    normalized_role["value"] = None
                    normalized_role["entity_type"] = "unknown"
                    if entity_ref is not None:
                        compact_hydration_log.append(
                            {
                                "stage": "runtime_hydration",
                                "operation": "discard_entity_ref_for_unresolved_role",
                                "field": f"role_adjudication.roles[{index}].entity_ref",
                                "evidence_ref": entity_ref,
                            }
                        )
                    entity_ref = None
                elif runtime_owns_optional_reasoning:
                    entity_ref = _resolve_reference(
                        entity_ref,
                        allowed=set(catalog_by_ref),
                        explicit_rewrites={},
                    )
                    typed_entity = (
                        _resolve_typed_entity_reference(
                            entity_ref,
                            catalog_by_ref=catalog_by_ref,
                        )
                        if isinstance(entity_ref, str)
                        else None
                    )
                    if typed_entity is None:
                        untyped_entity_ref = entity_ref
                        entity_ref = _unique_role_entity_ref(
                            normalized_role,
                            catalog_by_ref=catalog_by_ref,
                        )
                        if entity_ref is not None:
                            compact_hydration_log.append(
                                {
                                    "stage": "runtime_hydration",
                                    "operation": "derive_role_entity_from_unique_cited_value",
                                    "field": f"role_adjudication.roles[{index}].entity_ref",
                                    "evidence_ref": entity_ref,
                                }
                            )
                        elif isinstance(untyped_entity_ref, str):
                            compact_hydration_log.append(
                                {
                                    "stage": "runtime_hydration",
                                    "operation": "reject_untyped_role_entity_reference",
                                    "field": f"role_adjudication.roles[{index}].entity_ref",
                                    "evidence_ref": untyped_entity_ref,
                                }
                            )
                    else:
                        resolved_entity_ref, _, _ = typed_entity
                        if resolved_entity_ref != entity_ref:
                            compact_hydration_log.append(
                                {
                                    "stage": "runtime_hydration",
                                    "operation": "canonicalize_role_entity_reference",
                                    "field": f"role_adjudication.roles[{index}].entity_ref",
                                    "from_evidence_ref": entity_ref,
                                    "to_evidence_ref": resolved_entity_ref,
                                }
                            )
                        entity_ref = resolved_entity_ref
                if model_core is not None and entity_ref is not None:
                    entity_fact = catalog_by_ref.get(entity_ref)
                    if entity_fact is not None:
                        entity_type = _catalog_item_entity_type(entity_fact)
                        if entity_type is None:
                            compact_hydration_log.append(
                                {
                                    "stage": "runtime_hydration",
                                    "operation": "reject_untyped_role_entity_reference",
                                    "field": f"role_adjudication.roles[{index}].entity_ref",
                                    "evidence_ref": entity_ref,
                                }
                            )
                        else:
                            normalized_role["value"] = str(entity_fact.value)
                            normalized_role.setdefault("entity_type", entity_type)
                            evidence_refs = normalized_role.get("evidence_refs")
                            if not isinstance(evidence_refs, list):
                                normalized_role["evidence_refs"] = [entity_ref]
                            elif entity_ref not in evidence_refs:
                                normalized_role["evidence_refs"] = [
                                    *evidence_refs,
                                    entity_ref,
                                ]
                            compact_hydration_log.append(
                                {
                                    "stage": "runtime_hydration",
                                    "operation": "materialize_role_entity_reference",
                                    "field": f"role_adjudication.roles[{index}].entity_ref",
                                    "evidence_ref": entity_ref,
                                }
                            )
                if runtime_owns_optional_reasoning:
                    reasoning_id = materialize_optional_reasoning(
                        normalized_role,
                        field=f"role_adjudication.roles[{index}]",
                    )
                    normalized_role["reasoning_refs"] = [reasoning_id] if reasoning_id is not None else []
                if normalized_role.get("status") == "unresolved" and normalized_role.get("value") is None and (not normalized_role.get("evidence_refs") or not normalized_role.get("reasoning_refs")):
                    compact_hydration_log.append(
                        {
                            "stage": "runtime_hydration",
                            "operation": "drop_unsupported_unresolved_role",
                            "field": f"role_adjudication.roles[{index}]",
                            "role": normalized_role.get("role"),
                        }
                    )
                    continue
                roles.append({"context_refs": [], **normalized_role})
        raw_proposals = None if model_core is not None else role_section.get("response_target_proposals")
        proposals: Any = raw_proposals
        if raw_proposals is None:
            proposals = []
        elif isinstance(raw_proposals, list):
            proposals = []
            for index, item in enumerate(raw_proposals, start=1):
                if not isinstance(item, Mapping):
                    proposals.append(item)
                    continue
                normalized_proposal = _hydrate_compact_rationale_alias(
                    item,
                    path=f"role_adjudication.response_target_proposals[{index - 1}]",
                    hydration_log=compact_hydration_log,
                )
                for runtime_owned_field in (
                    "schema_version",
                    "proposal_id",
                    "policy_review_required",
                    "automation_allowed",
                ):
                    normalized_proposal.pop(runtime_owned_field, None)
                proposals.append(
                    {
                        "context_refs": [],
                        **normalized_proposal,
                        "proposal_id": f"RT-{index:02d}",
                        "policy_review_required": True,
                        "automation_allowed": False,
                    }
                )
        normalized_adjudication = _hydrate_compact_rationale_alias(
            role_section,
            path="role_adjudication",
            hydration_log=compact_hydration_log,
        )
        if runtime_owns_optional_reasoning:
            normalized_adjudication = _materialize_role_adjudication_contract_fields(
                normalized_adjudication,
                roles=roles if isinstance(roles, list) else [],
                hydration_log=compact_hydration_log,
            )
        hydrated["role_adjudication"] = {
            "roles": [] if roles is None else roles,
            "response_target_proposals": proposals,
            "conflicts": [],
            "evidence_gaps": [],
            **{
                key: value
                for key, value in normalized_adjudication.items()
                if key
                not in {
                    "roles",
                    "response_target_proposals",
                    "schema_version",
                }
            },
            "schema_version": "soc.role_adjudication_result.v1",
        }

    if model_core is not None:
        optional_defaults = _analysis_optional_section_defaults()
        for field_name in _ANALYSIS_OPTIONAL_SECTION_FIELDS.values():
            hydrated.setdefault(field_name, optional_defaults[field_name])
        hydrated.setdefault("evidence_gaps", [])
        hydrated.setdefault("manual_checks", [])

    references = _referenced_evidence_ids(hydrated)
    hydrated["evidence"] = [
        {
            "evidence_ref": catalog_by_ref[reference].evidence_ref,
            "source": catalog_by_ref[reference].source_path,
            "description": "Runtime-hydrated current-alert catalog fact",
            "value": catalog_by_ref[reference].value,
        }
        for reference in references
        if reference in catalog_by_ref
    ]
    if model_core is None:
        reasoning_items = hydrated.get("reasoning")
        valid_reasoning_ids = [item.get("reasoning_id") for item in reasoning_items if isinstance(item, Mapping) and isinstance(item.get("reasoning_id"), str)] if isinstance(reasoning_items, list) else []
        hydrated.setdefault(
            "decision_evidence_refs",
            references[:20],
        )
        hydrated.setdefault(
            "decision_reasoning_refs",
            valid_reasoning_ids[:20],
        )
    hydration_log = [
        *compact_hydration_log,
        {
            "stage": "runtime_hydration",
            "operation": "materialize_evidence_catalog_references",
            "reference_count": len(hydrated["evidence"]),
        },
        {
            "stage": "runtime_hydration",
            "operation": "apply_runtime_owned_contract_fields",
            "fields": [
                "schema_version",
                "evidence",
                "knowledge_candidates",
                "decision_evidence_refs",
                "decision_reasoning_refs",
                "nested_schema_versions",
                "response_target_defaults",
            ],
        },
    ]
    if ignored_runtime_fields:
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "discard_model_supplied_runtime_owned_fields",
                "fields": ignored_runtime_fields,
            }
        )
    return hydrated, hydration_log


def _hydrate_compact_reasoning_item(
    item: Mapping[str, Any],
    *,
    index: int,
    hydration_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Materialize redundant compact-output fields from exact references."""

    normalized = dict(item)
    path = f"reasoning[{index}]"
    reason = normalized.get("reason")
    statement = normalized.get("statement")
    if statement is None and isinstance(reason, str) and reason.strip():
        normalized["statement"] = normalized.pop("reason")
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "rename_reason_to_statement",
                "field": path,
            }
        )
    elif isinstance(reason, str) and reason == statement:
        normalized.pop("reason")
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "drop_duplicate_reason_alias",
                "field": path,
            }
        )

    if "context_refs" not in normalized:
        normalized["context_refs"] = []
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_empty_context_refs",
                "field": path,
            }
        )

    basis = normalized.get("basis")
    if basis is None or basis == []:
        derived_basis: list[str] = []
        if isinstance(normalized.get("evidence_refs"), list) and normalized["evidence_refs"]:
            derived_basis.append("current_evidence")
        prefix_basis = {
            "S-": "skill",
            "A-": "adapter_contract",
            "M-": "confirmed_memory",
            "C-": "governed_context",
            "T-": "tool_result",
        }
        context_refs = normalized.get("context_refs")
        if isinstance(context_refs, list):
            for reference in context_refs:
                if not isinstance(reference, str):
                    continue
                label = next(
                    (candidate for prefix, candidate in prefix_basis.items() if reference.startswith(prefix)),
                    None,
                )
                if label is not None and label not in derived_basis:
                    derived_basis.append(label)
        if derived_basis:
            normalized["basis"] = derived_basis
            hydration_log.append(
                {
                    "stage": "runtime_hydration",
                    "operation": "derive_redundant_reasoning_basis",
                    "field": path,
                    "basis": derived_basis,
                }
            )
    return normalized


def _hydrate_compact_rationale_alias(
    item: Mapping[str, Any],
    *,
    path: str,
    hydration_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Accept the model's common ``reason`` alias only when unambiguous."""

    normalized = dict(item)
    reason = normalized.get("reason")
    rationale = normalized.get("rationale")
    if rationale is None and isinstance(reason, str) and reason.strip():
        normalized["rationale"] = normalized.pop("reason")
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "rename_reason_to_rationale",
                "field": path,
            }
        )
    elif isinstance(reason, str) and reason == rationale:
        normalized.pop("reason")
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "drop_duplicate_reason_alias",
                "field": path,
            }
        )
    return normalized


def _hydrate_compact_scenario_name(
    item: Mapping[str, Any],
    *,
    path: str,
    hydration_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use an explicit model-supplied scenario key as a missing display name."""

    normalized = dict(item)
    scenario_name = normalized.get("scenario_name")
    if isinstance(scenario_name, str) and scenario_name.strip():
        return normalized
    scenario_key = normalized.get("scenario_key")
    if not isinstance(scenario_key, str) or not scenario_key.strip():
        return normalized
    normalized["scenario_name"] = scenario_key
    hydration_log.append(
        {
            "stage": "runtime_hydration",
            "operation": "materialize_scenario_name_from_key",
            "field": f"{path}.scenario_name",
        }
    )
    return normalized


def _normalize_runtime_owned_optional_item(
    item: Mapping[str, Any],
    *,
    path: str,
    allowed_evidence_refs: set[str],
    allowed_context_refs: set[str],
    hydration_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply syntax-only normalization before Runtime creates optional R-* items."""

    normalized = dict(item)
    if path.startswith("scenario_assessments[") and normalized.get("origin") not in {
        "upstream_hint",
        "inferred",
        "hybrid",
    }:
        normalized["origin"] = "inferred"
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_conservative_scenario_origin",
                "field": f"{path}.origin",
                "value": "inferred",
            }
        )
    rationale = normalized.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        fallback_rationale = _optional_object_rationale(normalized, path=path)
        if fallback_rationale is not None:
            normalized["rationale"] = fallback_rationale
            hydration_log.append(
                {
                    "stage": "runtime_hydration",
                    "operation": "materialize_missing_optional_rationale",
                    "field": f"{path}.rationale",
                }
            )
    confidence = normalized.get("confidence")
    if isinstance(confidence, str) and re.fullmatch(
        r"(?:0(?:\.\d+)?|1(?:\.0+)?)",
        confidence.strip(),
    ):
        normalized["confidence"] = float(confidence)
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "strict_decimal_string_to_number",
                "field": f"{path}.confidence",
                "original_value": confidence,
                "normalized_value": normalized["confidence"],
            }
        )

    evidence_refs = normalized.get("evidence_refs")
    if isinstance(evidence_refs, list):
        retained, details = _retain_catalog_reference_values(
            evidence_refs,
            allowed=allowed_evidence_refs,
            limit=20,
        )
        if retained != evidence_refs:
            normalized["evidence_refs"] = retained
            hydration_log.append(
                {
                    "stage": "runtime_hydration",
                    "operation": "retain_catalog_backed_optional_evidence_refs",
                    "field": f"{path}.evidence_refs",
                    **details,
                }
            )

    context_refs = normalized.get("context_refs")
    if isinstance(context_refs, list):
        retained, details = _retain_catalog_reference_values(
            context_refs,
            allowed=allowed_context_refs,
            limit=20,
        )
        if retained != context_refs:
            normalized["context_refs"] = retained
            hydration_log.append(
                {
                    "stage": "runtime_hydration",
                    "operation": "retain_catalog_backed_optional_context_refs",
                    "field": f"{path}.context_refs",
                    **details,
                }
            )
    return normalized


def _retain_catalog_reference_values(
    values: list[Any],
    *,
    allowed: set[str],
    limit: int,
) -> tuple[list[str], dict[str, Any]]:
    retained: list[str] = []
    removed: list[str] = []
    rewritten: list[dict[str, str]] = []
    invalid_ref_count = 0
    duplicate_ref_count = 0
    truncated_ref_count = 0
    for reference in values:
        resolved = _resolve_reference(
            reference,
            allowed=allowed,
            explicit_rewrites={},
        )
        if not isinstance(resolved, str) or resolved not in allowed:
            invalid_ref_count += 1
            if isinstance(reference, str):
                removed.append(reference[:64])
            continue
        if resolved in retained:
            duplicate_ref_count += 1
            continue
        if len(retained) >= limit:
            truncated_ref_count += 1
            continue
        retained.append(resolved)
        if resolved != reference:
            rewritten.append({"from": str(reference)[:64], "to": resolved})
    return retained, {
        "original_count": len(values),
        "retained_count": len(retained),
        "removed_refs": removed[:20],
        "rewritten_refs": rewritten[:20],
        "invalid_ref_count": invalid_ref_count,
        "duplicate_ref_count": duplicate_ref_count,
        "truncated_ref_count": truncated_ref_count,
    }


def _retain_supported_optional_fields(
    item: Mapping[str, Any],
    *,
    allowed_fields: frozenset[str],
    path: str,
    hydration_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Drop unknown optional keys without changing any accepted field value."""

    unsupported = sorted(set(item) - allowed_fields)
    if not unsupported:
        return dict(item)
    hydration_log.append(
        {
            "stage": "runtime_hydration",
            "operation": "drop_unsupported_optional_fields",
            "field": path,
            "fields": unsupported,
        }
    )
    return {key: value for key, value in item.items() if key in allowed_fields}


def _materialize_missing_role_contract_fields(
    item: Mapping[str, Any],
    *,
    path: str,
    hydration_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fill only fail-closed role metadata when the model omitted it."""

    normalized = dict(item)
    if "status" not in normalized and isinstance(normalized.get("role"), str):
        normalized["status"] = "tentative"
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_conservative_role_status",
                "field": f"{path}.status",
                "value": "tentative",
            }
        )
    if "confidence" not in normalized and isinstance(normalized.get("role"), str):
        normalized["confidence"] = 0.0
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_conservative_role_confidence",
                "field": f"{path}.confidence",
                "value": 0.0,
            }
        )
    return normalized


def _materialize_role_adjudication_contract_fields(
    item: Mapping[str, Any],
    *,
    roles: list[Any],
    hydration_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive redundant section metadata from already explicit role items."""

    normalized = dict(item)
    if "status" not in normalized:
        statuses = {role.get("status") for role in roles if isinstance(role, Mapping) and isinstance(role.get("status"), str)}
        if not roles:
            status = "not_assessed"
        elif "conflicted" in statuses:
            status = "conflicted"
        elif statuses == {"resolved_from_evidence"}:
            status = "resolved_from_evidence"
        else:
            status = "tentative"
        normalized["status"] = status
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_role_adjudication_status",
                "field": "role_adjudication.status",
                "value": status,
            }
        )
    rationale = normalized.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        normalized["rationale"] = "Runtime retained the model-provided role items; consult each role's rationale and exact references."
        hydration_log.append(
            {
                "stage": "runtime_hydration",
                "operation": "materialize_role_adjudication_rationale",
                "field": "role_adjudication.rationale",
            }
        )
    return normalized


def _optional_object_rationale(
    item: Mapping[str, Any],
    *,
    path: str,
) -> str | None:
    if path.startswith("scenario_assessments["):
        scenario = item.get("scenario_name")
        stage = item.get("activity_stage")
        if isinstance(scenario, str) and scenario.strip() and isinstance(stage, str):
            return f"Model assessed scenario {scenario!r} at activity stage {stage!r} using the cited evidence."
    if path == "network_direction":
        observed = item.get("observed_flow")
        boundary = item.get("boundary_direction")
        if isinstance(observed, str) and isinstance(boundary, str):
            return f"Model assessed observed flow {observed!r} and boundary direction {boundary!r} using the cited evidence."
    if path.startswith("role_adjudication.roles["):
        role = item.get("role")
        status = item.get("status")
        if isinstance(role, str) and isinstance(status, str):
            return f"Model assigned role {role!r} with status {status!r} using the cited evidence."
    return None


def _unique_role_entity_ref(
    role: Mapping[str, Any],
    *,
    catalog_by_ref: Mapping[str, AnalysisEvidenceCatalogItem],
) -> str | None:
    evidence_refs = role.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        return None
    candidates: list[tuple[str, AnalysisEvidenceCatalogItem, str]] = []
    for reference in evidence_refs:
        if not isinstance(reference, str):
            continue
        resolved = _resolve_typed_entity_reference(
            reference,
            catalog_by_ref=catalog_by_ref,
        )
        if resolved is None:
            continue
        resolved_ref, item, entity_type = resolved
        candidates.append((resolved_ref, item, entity_type))
    unique_values = {(entity_type, type(item.value).__name__, str(item.value)) for _, item, entity_type in candidates}
    if len(unique_values) != 1:
        return None
    selected = max(
        candidates,
        key=lambda candidate: _role_entity_rank(candidate[1]),
    )
    return selected[0]


def _resolve_typed_entity_reference(
    reference: str,
    *,
    catalog_by_ref: Mapping[str, AnalysisEvidenceCatalogItem],
) -> tuple[str, AnalysisEvidenceCatalogItem, str] | None:
    """Resolve a role entity to one typed catalog item without vendor aliases."""

    selected = catalog_by_ref.get(reference)
    if selected is None or selected.value is None:
        return None
    entity_type = _catalog_item_entity_type(selected)
    if entity_type is not None:
        return reference, selected, entity_type

    matches: list[tuple[str, AnalysisEvidenceCatalogItem, str]] = []
    for candidate_ref, candidate in catalog_by_ref.items():
        candidate_type = _catalog_item_entity_type(candidate)
        if candidate_type is None or type(candidate.value) is not type(selected.value) or candidate.value != selected.value:
            continue
        matches.append((candidate_ref, candidate, candidate_type))
    if len({candidate_type for _, _, candidate_type in matches}) != 1:
        return None
    if not matches:
        return None
    return max(matches, key=lambda candidate: _role_entity_rank(candidate[1]))


def _catalog_item_entity_type(item: AnalysisEvidenceCatalogItem) -> str | None:
    if item.entity_type is not None:
        return item.entity_type.value
    return _legacy_generic_entity_type(item.source_path, value=item.value)


def _legacy_generic_entity_type(source_path: str, *, value: Any = None) -> str | None:
    """Read historical catalogs only through generic paths or scalar syntax."""

    if isinstance(value, str):
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            return "ip"
        if re.fullmatch(r"[^@\s]+@[^@\s]+", value):
            return "email"

    normalized_path = source_path.casefold()
    if not normalized_path.startswith(("canonical_entities.", "extracted_entities.")):
        return None

    path_tokens = {token for token in re.split(r"[^a-z0-9]+", normalized_path) if token}
    typed_tokens = (
        ({"ip", "ipv4", "ipv6", "srcip", "dstip", "sip", "dip"}, "ip"),
        ({"host", "hosts", "hostname", "hostnames", "device", "endpoint", "asset", "assets"}, "host"),
        ({"domain", "domains", "fqdn"}, "domain"),
        ({"email", "emails", "mail"}, "user"),
        ({"url", "urls", "uri", "uris"}, "url"),
        ({"user", "users", "username", "usernames", "userid", "account", "accounts", "um"}, "user"),
        ({"process", "processes", "pid", "ppid"}, "process"),
        ({"file", "files", "filepath", "filename"}, "file"),
    )
    for candidates, entity_type in typed_tokens:
        if path_tokens & candidates:
            return entity_type
    return None


def _role_entity_rank(item: AnalysisEvidenceCatalogItem) -> tuple[int, int, str]:
    path = item.source_path
    path_rank = 4 if path.startswith("canonical_entities.") else 3 if path.startswith("extracted_entities.") else 2 if path.startswith("fact_reconstruction.role_claims[") else 1 if path.startswith("evidence.highlights[") else 0
    trust_rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    return path_rank, trust_rank.get(item.trust_level.value, 0), path


def _referenced_evidence_ids(data: Mapping[str, Any]) -> list[str]:
    references: list[str] = []

    decision_refs = data.get("decision_evidence_refs")
    if isinstance(decision_refs, list):
        references.extend(value for value in decision_refs if isinstance(value, str))

    def add_from(item: Any) -> None:
        if not isinstance(item, Mapping):
            return
        values = item.get("evidence_refs")
        if isinstance(values, list):
            references.extend(value for value in values if isinstance(value, str))

    for collection_name in ("reasoning", "scenario_assessments"):
        collection = data.get(collection_name)
        if isinstance(collection, list):
            for item in collection:
                add_from(item)
    add_from(data.get("network_direction"))
    direction = data.get("network_direction")
    if isinstance(direction, Mapping) and isinstance(direction.get("connection_initiator_ref"), str):
        references.append(direction["connection_initiator_ref"])
    adjudication = data.get("role_adjudication")
    if isinstance(adjudication, Mapping):
        for collection_name in ("roles", "response_target_proposals"):
            collection = adjudication.get(collection_name)
            if isinstance(collection, list):
                for item in collection:
                    add_from(item)
                    if isinstance(item, Mapping) and isinstance(item.get("entity_ref"), str):
                        references.append(item["entity_ref"])
    return list(dict.fromkeys(references))


def parse_role_verification_output(
    response_content: Any,
    *,
    claims: Sequence[RoleVerificationClaim],
    evidence_catalog: Sequence[AnalysisEvidenceCatalogItem] = (),
    context_catalog: Sequence[AnalysisContextCatalogItem] = (),
    canonical_network: NetworkEntityRef | None = None,
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
        schema_version="soc.role_verification_candidate.v2",
    )
    if strict is not None:
        data, candidate_text = strict
        return ParsedRoleVerificationCandidate(
            candidate=_validate_role_verification_candidate(
                data,
                claims=claims,
                evidence_catalog=evidence_catalog,
                context_catalog=context_catalog,
                canonical_network=canonical_network,
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
            canonical_network=canonical_network,
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
    if data.get("schema_version") in _RUNTIME_OWNED_REASONING_OUTPUT_VERSIONS:
        return {
            "verdict",
            "confidence",
            "summary",
            "decision_evidence_refs",
            "reason",
            "recommended_action",
        }.issubset(data)
    required = {
        "verdict",
        "confidence",
        "summary",
        "reasoning",
        "reason",
        "recommended_action",
    }
    if not required.issubset(data):
        return False
    if data.get("schema_version") in {
        "soc.analysis_result.v4",
        ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION,
        LEGACY_ANALYSIS_MODEL_OUTPUT_SCHEMA_VERSION,
        LEGACY_ANALYSIS_MODEL_OUTPUT_V2_SCHEMA_VERSION,
        LEGACY_ANALYSIS_MODEL_OUTPUT_V3_SCHEMA_VERSION,
    }:
        return True
    return _unversioned_analysis_model_output_version(data) is not None


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
    canonical_network: NetworkEntityRef | None,
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

    if candidate.schema_version != "soc.role_verification_candidate.v2":
        raise LLMOutputParseError(
            "live role verification output must use soc.role_verification_candidate.v2",
            stage="schema_validation",
            parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
            repair_applied=repair_applied,
        )
    if any(review.context_refs for review in candidate.claim_reviews):
        raise LLMOutputParseError(
            "role verification v2 output must use polarity-specific context references",
            stage="schema_validation",
            parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
            repair_applied=repair_applied,
        )

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
        supplied_context_refs = {
            *review.supporting_context_refs,
            *review.contradicting_context_refs,
            *review.context_refs,
        }
        missing_context = sorted(supplied_context_refs - context_refs)
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
    _validate_role_verification_boundary_consistency(
        candidate,
        expected_claims=expected_claims,
        context_catalog=context_catalog,
        canonical_network=canonical_network,
        repair_applied=repair_applied,
    )
    return candidate


def _validate_role_verification_boundary_consistency(
    candidate: RoleVerificationCandidate,
    *,
    expected_claims: Mapping[str, RoleVerificationClaim],
    context_catalog: Sequence[AnalysisContextCatalogItem],
    canonical_network: NetworkEntityRef | None,
    repair_applied: bool,
) -> None:
    """Reject model status that contradicts exact typed ownership constraints."""

    if canonical_network is None:
        return
    source_ip = canonical_network.source_ip
    destination_ip = canonical_network.destination_ip
    if not source_ip or not destination_ip:
        return
    ownership_refs = {value: {item.context_ref for item in context_catalog if _context_establishes_organization_ownership(item, value)} for value in (source_ip, destination_ip)}
    if not all(ownership_refs.values()):
        return

    boundary_claim = next(
        (claim for claim in expected_claims.values() if set(claim.assertion) == {"boundary_direction"}),
        None,
    )
    if boundary_claim is None:
        return
    claimed = boundary_claim.assertion["boundary_direction"]
    expected = "internal_to_internal"
    if claimed == expected:
        return
    review = next(item for item in candidate.claim_reviews if item.claim_ref == boundary_claim.claim_ref)
    cited = set(review.contradicting_context_refs)
    covers_both_endpoints = all(refs & cited for refs in ownership_refs.values())
    alternative = review.alternative.assertion.get("boundary_direction") if review.alternative is not None else None
    if review.status.value != "challenged" or not covers_both_endpoints or alternative != expected:
        raise LLMOutputParseError(
            (f"boundary claim {claimed!r} contradicts typed organization ownership for both canonical endpoints; review must challenge it with {expected!r}, cite ownership context for both endpoints, and return that alternative"),
            stage="semantic_consistency",
            parser_version=ROLE_VERIFICATION_JSON_PARSER_VERSION,
            repair_applied=repair_applied,
            field_paths=(
                f"claim_reviews.{boundary_claim.claim_ref}.status",
                f"claim_reviews.{boundary_claim.claim_ref}.contradicting_context_refs",
                f"claim_reviews.{boundary_claim.claim_ref}.alternative",
            ),
            issue_codes=("typed_network_scope_boundary_conflict",),
        )


def _context_establishes_organization_ownership(
    item: AnalysisContextCatalogItem,
    value: str,
) -> bool:
    matched_values = item.metadata.get("matched_values")
    if not isinstance(matched_values, Mapping):
        return False
    flattened = {str(child) for values in matched_values.values() if isinstance(values, list) for child in values}
    return item.kind.value == "governed_context" and item.metadata.get("fact_kind") == "network_scope" and item.metadata.get("network_scope_membership") == "organization_controlled" and value in flattened


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
    for field_name in (
        "decision_evidence_refs",
        "decision_reasoning_refs",
    ):
        references = normalized.get(field_name)
        if not isinstance(references, list):
            continue
        deduplicated = list(dict.fromkeys(references))
        if deduplicated == references:
            continue
        normalized[field_name] = deduplicated
        repair_log.append(
            {
                "stage": "catalog_reference_normalization",
                "field": field_name,
                "repair": "remove_exact_duplicate_references",
                "removed_count": len(references) - len(deduplicated),
            }
        )
        changed = True
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
    decision_evidence_refs = normalized.get("decision_evidence_refs")
    if isinstance(decision_evidence_refs, list):
        repaired_refs = [
            _resolve_reference(
                reference,
                allowed=evidence_refs,
                explicit_rewrites=evidence_ref_rewrites,
            )
            for reference in decision_evidence_refs
        ]
        if repaired_refs != decision_evidence_refs:
            normalized["decision_evidence_refs"] = repaired_refs
            repair_log.append(
                {
                    "stage": "catalog_reference_normalization",
                    "field": "decision_evidence_refs",
                    "repair": "unique_catalog_reference_expansion",
                    "original_value": decision_evidence_refs,
                    "normalized_value": repaired_refs,
                }
            )
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
    decision_refs = data.get("decision_evidence_refs")
    if isinstance(decision_refs, list):
        referenced_refs.extend(reference for reference in decision_refs if isinstance(reference, str))
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
        field_paths, issue_codes = _validation_error_summary(exc)
        raise LLMOutputParseError(
            f"LLM output failed AnalysisResult schema validation: {exc}",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=field_paths,
            issue_codes=issue_codes,
        ) from exc

    try:
        return validate_analysis_result(result)
    except Exception as exc:  # noqa: BLE001 - normalize domain validation failures
        raise LLMOutputParseError(
            f"LLM output failed analysis domain validation: {exc}",
            stage="domain_validation",
            repair_applied=repair_applied,
        ) from exc


def _validation_error_summary(error: ValidationError) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return bounded structural diagnostics without retaining rejected values."""

    field_paths: list[str] = []
    issue_codes: list[str] = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False):
        location = issue.get("loc")
        if isinstance(location, tuple):
            path = ".".join(str(part) for part in location)
            if path:
                field_paths.append(path)
        issue_type = issue.get("type")
        if isinstance(issue_type, str) and issue_type:
            issue_codes.append(issue_type)
    return (
        tuple(dict.fromkeys(field_paths))[:20],
        tuple(dict.fromkeys(issue_codes))[:20],
    )


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


def _validate_deterministic_role_coherence(
    result: AnalysisResult,
    *,
    analysis_request: LLMAnalysisRequest | None,
    repair_applied: bool,
) -> None:
    """Reject a free-form conflict that contradicts the model's own role values."""

    if analysis_request is None:
        return
    coherence = analysis_request.fact_reconstruction.role_coherence
    if coherence.status is not RoleCoherenceStatus.COHERENT:
        return

    expected = {item.semantic_role: item.semantic_value for item in coherence.relationships if item.semantic_value is not None}
    model_roles = {item.role.value: item for item in result.role_adjudication.roles if item.value is not None}
    if not expected or not all(role in model_roles and model_roles[role].value == value for role, value in expected.items()):
        return
    if any(model_roles[role].status is AdjudicatedRoleStatus.CONFLICTED for role in expected):
        return

    unsupported_conflict = bool(result.role_adjudication.conflicts) or (result.role_adjudication.status is RoleAdjudicationStatus.CONFLICTED)
    if unsupported_conflict:
        raise LLMOutputParseError(
            ("role_adjudication reports a conflict even though its attacker/victim values match the deterministic scenario-role coherence assessment; missing additional corroboration must be reported as an evidence gap"),
            stage="role_coherence_validation",
            repair_applied=repair_applied,
            field_paths=(
                "role_adjudication.status",
                "role_adjudication.conflicts",
            ),
            issue_codes=("unsupported_role_conflict",),
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
    optional_fields = {
        "decision_evidence_refs",
        "decision_reasoning_refs",
    }
    allowed_fields = set(required_fields) | optional_fields
    missing_fields = sorted(required_fields - data.keys())
    if missing_fields:
        raise LLMOutputParseError(
            f"LLM output is missing required fields: {', '.join(missing_fields)}",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=missing_fields,
            issue_codes=("missing",),
        )
    unknown_fields = sorted(data.keys() - allowed_fields)
    if unknown_fields:
        raise LLMOutputParseError(
            f"LLM output contains unsupported fields: {', '.join(unknown_fields)}",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=unknown_fields,
            issue_codes=("extra_forbidden",),
        )
    if data.get("schema_version") != "soc.analysis_result.v4":
        raise LLMOutputParseError(
            "LLM output schema_version must be 'soc.analysis_result.v4'",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=("schema_version",),
            issue_codes=("literal_error",),
        )
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise LLMOutputParseError(
            "LLM output confidence must be a JSON number",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=("confidence",),
            issue_codes=("number_type",),
        )
    scenario_assessments = data.get("scenario_assessments")
    if not isinstance(scenario_assessments, list):
        raise LLMOutputParseError(
            "LLM output scenario_assessments must be a JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=("scenario_assessments",),
            issue_codes=("list_type",),
        )
    for index, assessment in enumerate(scenario_assessments):
        if not isinstance(assessment, dict):
            raise LLMOutputParseError(
                f"LLM output scenario_assessments[{index}] must be a JSON object",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=(f"scenario_assessments.{index}",),
                issue_codes=("dict_type",),
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
                field_paths=tuple(f"scenario_assessments.{index}.{field}" for field in unknown_scenario_fields),
                issue_codes=("extra_forbidden",),
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
                field_paths=(f"scenario_assessments.{index}.confidence",),
                issue_codes=("number_type",),
            )
        for reference_field in ("evidence_refs", "reasoning_refs"):
            references = assessment.get(reference_field)
            if not isinstance(references, list) or not references or any(not isinstance(item, str) for item in references):
                raise LLMOutputParseError(
                    f"LLM output scenario_assessments[{index}].{reference_field} must be a non-empty JSON string array",
                    stage="schema_validation",
                    repair_applied=repair_applied,
                    field_paths=(f"scenario_assessments.{index}.{reference_field}",),
                    issue_codes=("string_list_type",),
                )
        if not isinstance(assessment.get("is_primary"), bool):
            raise LLMOutputParseError(
                f"LLM output scenario_assessments[{index}].is_primary must be a JSON boolean",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=(f"scenario_assessments.{index}.is_primary",),
                issue_codes=("bool_type",),
            )
    _validate_directional_shape(data, repair_applied=repair_applied)
    for field_name in ("evidence_gaps", "manual_checks"):
        values = data.get(field_name)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise LLMOutputParseError(
                f"LLM output {field_name} must be a JSON string array",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=(field_name,),
                issue_codes=("string_list_type",),
            )
    for field_name in ("decision_evidence_refs", "decision_reasoning_refs"):
        values = data.get(field_name)
        if values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise LLMOutputParseError(
                f"LLM output {field_name} must be a JSON string array",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=(field_name,),
                issue_codes=("string_list_type",),
            )
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise LLMOutputParseError(
            "LLM output evidence must be a non-empty JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=("evidence",),
            issue_codes=("list_min_length",),
        )
    allowed_evidence_fields = {"evidence_ref", "source", "description", "value"}
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise LLMOutputParseError(
                f"LLM output evidence[{index}] must be a JSON object",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=(f"evidence.{index}",),
                issue_codes=("dict_type",),
            )
        unknown = sorted(item.keys() - allowed_evidence_fields)
        missing = sorted(allowed_evidence_fields - item.keys())
        if unknown or missing:
            raise LLMOutputParseError(
                f"LLM output evidence[{index}] has missing={missing} unsupported={unknown}",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=tuple(f"evidence.{index}.{field}" for field in [*missing, *unknown]),
                issue_codes=tuple(
                    code
                    for fields, code in (
                        (missing, "missing"),
                        (unknown, "extra_forbidden"),
                    )
                    if fields
                ),
            )

    reasoning = data.get("reasoning")
    if not isinstance(reasoning, list) or not reasoning:
        raise LLMOutputParseError(
            "LLM output reasoning must be a non-empty JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=("reasoning",),
            issue_codes=("list_min_length",),
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
                field_paths=(f"reasoning.{index}",),
                issue_codes=("dict_type",),
            )
        unknown = sorted(item.keys() - allowed_reasoning_fields)
        missing = sorted(allowed_reasoning_fields - item.keys())
        if unknown or missing:
            raise LLMOutputParseError(
                f"LLM output reasoning[{index}] has missing={missing} unsupported={unknown}",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=tuple(f"reasoning.{index}.{field}" for field in [*missing, *unknown]),
                issue_codes=tuple(
                    code
                    for fields, code in (
                        (missing, "missing"),
                        (unknown, "extra_forbidden"),
                    )
                    if fields
                ),
            )
        for reference_field in ("basis", "evidence_refs", "context_refs"):
            references = item.get(reference_field)
            if not isinstance(references, list) or any(not isinstance(reference, str) for reference in references):
                raise LLMOutputParseError(
                    f"LLM output reasoning[{index}].{reference_field} must be a JSON string array",
                    stage="schema_validation",
                    repair_applied=repair_applied,
                    field_paths=(f"reasoning.{index}.{reference_field}",),
                    issue_codes=("string_list_type",),
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
                field_paths=(f"reasoning.{index}.confidence",),
                issue_codes=("number_type",),
            )

    candidates = data.get("knowledge_candidates")
    if not isinstance(candidates, list):
        raise LLMOutputParseError(
            "LLM output knowledge_candidates must be a JSON array",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=("knowledge_candidates",),
            issue_codes=("list_type",),
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
                field_paths=(f"knowledge_candidates.{index}",),
                issue_codes=("dict_type",),
            )
        unknown = sorted(item.keys() - allowed_candidate_fields)
        missing = sorted(allowed_candidate_fields - item.keys())
        if unknown or missing:
            raise LLMOutputParseError(
                f"LLM output knowledge_candidates[{index}] has missing={missing} unsupported={unknown}",
                stage="schema_validation",
                repair_applied=repair_applied,
                field_paths=tuple(f"knowledge_candidates.{index}.{field}" for field in [*missing, *unknown]),
                issue_codes=tuple(
                    code
                    for fields, code in (
                        (missing, "missing"),
                        (unknown, "extra_forbidden"),
                    )
                    if fields
                ),
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
        raise LLMOutputParseError(
            f"LLM output {path} has missing={missing} unsupported={unknown}",
            stage="schema_validation",
            repair_applied=repair_applied,
            field_paths=tuple(f"{path}.{field}" for field in [*missing, *unknown]),
            issue_codes=("object_fields_invalid",),
        )
    return value


def _require_string_array(value: Any, *, path: str, repair_applied: bool) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _raise_shape(f"{path} must be a JSON string array", repair_applied)


def _require_json_number(value: Any, *, path: str, repair_applied: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _raise_shape(f"{path} must be a JSON number", repair_applied)


def _raise_shape(message: str, repair_applied: bool) -> None:
    path = message.split(" ", 1)[0]
    if "missing=" in message:
        code = "object_fields_invalid"
    elif "JSON array" in message:
        code = "list_type"
    elif "JSON string array" in message:
        code = "string_list_type"
    elif "JSON number" in message:
        code = "number_type"
    elif "JSON object" in message:
        code = "dict_type"
    elif "policy_review_required" in message:
        code = "runtime_invariant"
    else:
        code = "shape_invalid"
    raise LLMOutputParseError(
        f"LLM output {message}",
        stage="schema_validation",
        repair_applied=repair_applied,
        field_paths=(path,),
        issue_codes=(code,),
    )
