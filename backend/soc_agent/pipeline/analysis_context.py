"""Build bounded analysis context for stub or future LLM nodes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from soc_agent.contracts import (
    AlertInput,
    BoundedAnalysisEvidence,
    EvidenceLayer,
    EvidenceTrustLevel,
    ExtractedEntities,
    FactReconstructionResult,
    LLMAnalysisRequest,
    ParsedRawMessageEvidence,
)
from soc_agent.pipeline.evidence_coverage import build_evidence_coverage_report
from soc_agent.skills import SocSkillResolver, build_soc_skill_context

_PRIMARY_EVIDENCE_MAX_CHARS = 6000
_SUPPLEMENTARY_EVIDENCE_MAX_CHARS = 3000
_MAX_SUPPLEMENTARY_EVIDENCE = 4
_MAX_FIELD_CHARS = 1000
_DECODED_SEPARATELY_FIELDS = frozenset({"req_body", "rsp_body", "rule_labels", "req_header", "rsp_header"})
_SENSITIVE_FIELD_RE = re.compile(r"(?:authorization|cookie|password|passwd|secret|token|credential|pwd)", re.IGNORECASE)
_SENSITIVE_JSON_VALUE_RE = re.compile(
    r'(?P<prefix>"[^"\\]*(?:authorization|cookie|password|passwd|secret|token|credential|pwd)[^"\\]*"\s*:\s*)'
    r'"(?:\\.|[^"\\])*(?:"|$)',
    re.IGNORECASE,
)


def build_llm_analysis_request(
    alert: AlertInput,
    entities: ExtractedEntities,
    fact_reconstruction: FactReconstructionResult,
) -> LLMAnalysisRequest:
    """Convert runtime state into the only input shape analysis nodes consume."""

    conflict_types = sorted({report.conflict_type for report in fact_reconstruction.conflict_reports})
    primary_evidence, supplementary_evidence = _bounded_evidence(alert, fact_reconstruction)
    evidence_coverage = build_evidence_coverage_report(
        alert,
        fact_reconstruction,
        primary_evidence,
        supplementary_evidence,
    )
    warnings = [
        *fact_reconstruction.warnings,
        *entities.warnings,
        *evidence_coverage.warnings,
    ]
    request = LLMAnalysisRequest(
        alert_id=alert.alert_id,
        tenant_id=alert.tenant_id,
        source=alert.source,
        detection=alert.detection,
        classification=alert.classification,
        canonical_entities=alert.entities,
        extracted_entities=entities,
        fact_reconstruction=fact_reconstruction,
        primary_evidence_path=fact_reconstruction.selected_input_path,
        primary_evidence=primary_evidence,
        supplementary_evidence=supplementary_evidence,
        evidence_coverage=evidence_coverage,
        conflict_count=len(fact_reconstruction.conflict_reports),
        conflict_types=conflict_types,
        warnings=_dedupe(warnings),
    )
    skill_resolution = SocSkillResolver().resolve_for_analysis_request(request)
    request.skill_context = build_soc_skill_context(skill_resolution)
    return request


def _bounded_evidence(
    alert: AlertInput,
    fact_reconstruction: FactReconstructionResult,
) -> tuple[BoundedAnalysisEvidence | None, list[BoundedAnalysisEvidence]]:
    policy = fact_reconstruction.evidence_policy
    if policy is None or policy.selected_input_path is None:
        return None, []

    parsed_by_path = _parsed_messages_by_path(alert)
    primary = None
    if policy.selected_layer is EvidenceLayer.RAW_MESSAGE:
        primary = _bounded_evidence_for_path(
            alert,
            path=policy.selected_input_path,
            layer=policy.selected_layer,
            trust_level=policy.trust_level,
            max_chars=_PRIMARY_EVIDENCE_MAX_CHARS,
            parsed=parsed_by_path.get(policy.selected_input_path),
        )
    supplementary: list[BoundedAnalysisEvidence] = []
    for path in policy.supplementary_input_paths[:_MAX_SUPPLEMENTARY_EVIDENCE]:
        item = _bounded_evidence_for_path(
            alert,
            path=path,
            layer=EvidenceLayer.RAW_MESSAGE,
            trust_level=EvidenceTrustLevel.HIGH,
            max_chars=_SUPPLEMENTARY_EVIDENCE_MAX_CHARS,
            parsed=parsed_by_path.get(path),
        )
        if item is not None:
            supplementary.append(item)
    return primary, supplementary


def _bounded_evidence_for_path(
    alert: AlertInput,
    *,
    path: str,
    layer: EvidenceLayer,
    trust_level: EvidenceTrustLevel,
    max_chars: int,
    parsed: ParsedRawMessageEvidence | None,
) -> BoundedAnalysisEvidence | None:
    raw_value = _resolve_path(alert.raw, path)
    if parsed is not None:
        bounded_value, field_truncated = _bound_value(
            {
                "header": parsed.header,
                "fields": _analysis_safe_fields(
                    parsed.fields,
                    parsed.decoded_fields,
                    parsed.repaired_fields,
                ),
                "decoded_fields": parsed.decoded_fields,
                "repaired_fields": parsed.repaired_fields,
                "repair_observations": [observation.model_dump(mode="json") for observation in parsed.repair_observations],
                "parser_warnings": parsed.warnings,
            }
        )
        content = json.dumps(bounded_value, ensure_ascii=False, sort_keys=True)
        original_length = parsed.original_length
        parser_name = parsed.parser_name
    elif isinstance(raw_value, str):
        content = raw_value
        original_length = len(raw_value)
        parser_name = None
        field_truncated = False
    elif raw_value is not None:
        content = json.dumps(raw_value, ensure_ascii=False, sort_keys=True, default=str)
        original_length = len(content)
        parser_name = None
        field_truncated = False
    else:
        return None

    content_truncated = len(content) > max_chars
    if content_truncated:
        content = content[:max_chars]
    return BoundedAnalysisEvidence(
        source_path=path,
        layer=layer,
        trust_level=trust_level,
        content=content,
        parser_name=parser_name,
        original_length=original_length,
        truncated=field_truncated or content_truncated,
    )


def _analysis_safe_fields(
    fields: Mapping[str, Any],
    decoded_fields: Mapping[str, Any],
    repaired_fields: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in fields.items():
        normalized_key = str(key)
        decoded_value = decoded_fields.get(normalized_key)
        repaired_value = repaired_fields.get(normalized_key)
        if _SENSITIVE_FIELD_RE.search(normalized_key):
            result[normalized_key] = "[REDACTED]"
        elif normalized_key.lower() in _DECODED_SEPARATELY_FIELDS:
            if decoded_value is not None:
                result[normalized_key] = "[SEE decoded_fields]"
            elif repaired_value is not None:
                result[normalized_key] = "[SEE repaired_fields]"
            else:
                result[normalized_key] = _safe_string_fallback(value)
        elif isinstance(value, Mapping):
            result[normalized_key] = _analysis_safe_fields(
                value,
                decoded_value if isinstance(decoded_value, Mapping) else {},
                repaired_value if isinstance(repaired_value, Mapping) else {},
            )
        else:
            result[normalized_key] = value
    return result


def _safe_string_fallback(value: Any) -> Any:
    """Keep malformed nested text useful without exposing obvious secrets."""

    if not isinstance(value, str):
        return "[OMITTED NON-STRING CONTENT]"
    return _SENSITIVE_JSON_VALUE_RE.sub(r'\g<prefix>"[REDACTED]"', value)


def _parsed_messages_by_path(alert: AlertInput) -> dict[str, ParsedRawMessageEvidence]:
    parsed_by_path: dict[str, ParsedRawMessageEvidence] = {}
    values = alert.extensions.get("parsed_raw_messages")
    if not isinstance(values, list):
        return parsed_by_path
    for value in values:
        try:
            parsed = ParsedRawMessageEvidence.model_validate(value)
        except ValidationError:
            continue
        parsed_by_path[parsed.source_path] = parsed
    return parsed_by_path


def _bound_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) <= _MAX_FIELD_CHARS:
            return value, False
        return value[:_MAX_FIELD_CHARS], True
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        truncated = False
        for key, item in value.items():
            bounded, item_truncated = _bound_value(item)
            result[str(key)] = bounded
            truncated = truncated or item_truncated
        return result, truncated
    if isinstance(value, list):
        truncated = len(value) > 20
        result = []
        for item in value[:20]:
            bounded, item_truncated = _bound_value(item)
            result.append(bounded)
            truncated = truncated or item_truncated
        return result, truncated
    return value, False


def _resolve_path(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for segment in path.split("."):
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        if match is None:
            return None
        key, index = match.groups()
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
        if index is not None:
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
    return value


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
