"""Build bounded analysis context for deterministic or configured LLM nodes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
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
    SocSkillContext,
    SourceFieldSemantic,
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
_SENSITIVE_HEADER_LINE_RE = re.compile(r"(?im)^(?P<name>(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key))\s*:\s*.*$")
_PROJECTION_MAX_LIST_ITEMS = 100
_PROJECTION_MAX_STRING_CHARS = 4000
_PROJECTION_MAX_DEPTH = 16
_HIGH_VALUE_EVIDENCE_KEYS = frozenset(
    {
        "access_time",
        "agent_ip",
        "alarm_sip",
        "attack_sip",
        "attack_type",
        "attacker",
        "command_line",
        "detail_info",
        "destination_ip",
        "dip",
        "dport",
        "event_content",
        "event_name",
        "event_time",
        "forwarded_chain",
        "host",
        "host_name",
        "host_state",
        "internal_ip",
        "method",
        "process_tree",
        "req_body",
        "rule_name",
        "sip",
        "source_ip",
        "sport",
        "start_line",
        "timestamp",
        "victim",
        "x-forwarded-for",
        "x_forwarded_for",
    }
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
    return LLMAnalysisRequest(
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
        source_field_semantics=_source_field_semantics(alert),
        conflict_count=len(fact_reconstruction.conflict_reports),
        conflict_types=conflict_types,
        warnings=_dedupe(warnings),
    )


def resolve_skill_context_for_request(request: LLMAnalysisRequest) -> SocSkillContext:
    """Resolve the compact skill context as an explicit Runtime step."""

    skill_resolution = SocSkillResolver().resolve_for_analysis_request(request)
    return build_soc_skill_context(skill_resolution)


def project_analysis_context(request: LLMAnalysisRequest) -> dict[str, Any]:
    """Return the exact bounded context shared by prompting and grounding."""

    fact = request.fact_reconstruction
    context = {
        "schema_version": request.schema_version,
        "alert_id": request.alert_id,
        "source": request.source.model_dump(mode="json", exclude_none=True),
        "detection": request.detection.model_dump(mode="json", exclude_none=True),
        "classification": request.classification.model_dump(mode="json", exclude_none=True),
        "canonical_entities": request.canonical_entities.model_dump(mode="json", exclude_none=True),
        "extracted_entities": request.extracted_entities.model_dump(mode="json", exclude_none=True),
        "evidence": {
            "primary_evidence_path": request.primary_evidence_path,
            "primary_evidence": request.primary_evidence.model_dump(mode="json", exclude_none=True) if request.primary_evidence is not None else None,
            "supplementary_evidence": [item.model_dump(mode="json", exclude_none=True) for item in request.supplementary_evidence],
            "selected_input_path": fact.selected_input_path,
            "selected_input_available": fact.selected_input_available,
            "evidence_policy": fact.evidence_policy.model_dump(mode="json", exclude_none=True) if fact.evidence_policy is not None else None,
            "field_trusts": [item.model_dump(mode="json", exclude_none=True) for item in fact.field_trusts],
            "coverage": _analysis_coverage_context(request),
            "source_field_semantics": [item.model_dump(mode="json", exclude_none=True) for item in request.source_field_semantics],
        },
        "fact_reconstruction": {
            "canonical_field_provenance": [item.model_dump(mode="json", exclude_none=True) for item in fact.canonical_field_provenance],
            "role_claims": [item.model_dump(mode="json", exclude_none=True) for item in fact.role_claims],
            "scenario_hypotheses": [
                {
                    "scenario_type": item.scenario_type,
                    "status": item.status,
                    "confidence": item.confidence,
                    "rationale": item.rationale,
                    "evidence_ref_count": len(item.evidence_paths),
                }
                for item in fact.scenario_hypotheses
            ],
            "role_resolutions": [item.model_dump(mode="json", exclude_none=True) for item in fact.role_resolutions],
            "conflict_count": request.conflict_count,
            "conflict_types": request.conflict_types,
            "conflict_reports": [item.model_dump(mode="json", exclude_none=True) for item in fact.conflict_reports],
            "warnings": request.warnings,
        },
        "skill_context": request.skill_context.model_dump(mode="json", exclude_none=True),
    }
    bounded = _bound_projection(context)
    if not isinstance(bounded, dict):
        raise TypeError("analysis context projection must remain an object")
    return bounded


def _analysis_coverage_context(request: LLMAnalysisRequest) -> dict[str, Any]:
    coverage = request.evidence_coverage
    omission_reason_counts: dict[str, int] = {}
    for omission in coverage.omissions:
        omission_reason_counts[omission.reason] = omission_reason_counts.get(omission.reason, 0) + 1
    return {
        "message_schemas": [
            {
                "parser_name": item.parser_name,
                "parser_version": item.parser_version,
                "schema_fingerprint": item.schema_fingerprint,
                "status": item.status,
                "field_count": item.field_count,
                "warning_count": len(item.warnings),
            }
            for item in coverage.message_schemas
        ],
        "counts": coverage.counts,
        "omission_reason_counts": omission_reason_counts,
        "high_value_gaps": [
            {
                "expected_target": item.expected_target,
                "reason": item.reason,
            }
            for item in coverage.high_value_gaps
        ],
        "truncated_evidence_count": len(coverage.llm_truncated_evidence_paths),
    }


def _bound_projection(value: Any, *, depth: int = 0) -> Any:
    if depth >= _PROJECTION_MAX_DEPTH:
        return "[OMITTED: maximum projection depth reached]"
    if isinstance(value, str):
        if len(value) <= _PROJECTION_MAX_STRING_CHARS:
            return value
        return value[:_PROJECTION_MAX_STRING_CHARS] + "...[TRUNCATED]"
    if isinstance(value, Mapping):
        return {str(key): _bound_projection(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        bounded = [_bound_projection(item, depth=depth + 1) for item in value[:_PROJECTION_MAX_LIST_ITEMS]]
        if len(value) > _PROJECTION_MAX_LIST_ITEMS:
            bounded.append({"_omitted_items": len(value) - _PROJECTION_MAX_LIST_ITEMS})
        return bounded
    return value


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
        (
            content,
            field_truncated,
            projected_paths,
            sanitized_paths,
            omitted_paths,
            omission_reasons,
        ) = _bounded_parsed_projection(
            parsed,
            max_chars=max_chars,
        )
        original_length = parsed.original_length
        parser_name = parsed.parser_name
    elif isinstance(raw_value, str):
        content = raw_value
        original_length = len(raw_value)
        parser_name = None
        field_truncated = False
        projected_paths = []
        sanitized_paths = []
        omitted_paths = []
        omission_reasons = {}
    elif raw_value is not None:
        content = json.dumps(raw_value, ensure_ascii=False, sort_keys=True, default=str)
        original_length = len(content)
        parser_name = None
        field_truncated = False
        projected_paths = []
        sanitized_paths = []
        omitted_paths = []
        omission_reasons = {}
    else:
        return None

    content_truncated = parsed is None and len(content) > max_chars
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
        projected_field_paths=projected_paths,
        sanitized_field_paths=sanitized_paths,
        omitted_field_paths=omitted_paths,
        omission_reasons=omission_reasons,
    )


def _bounded_parsed_projection(
    parsed: ParsedRawMessageEvidence,
    *,
    max_chars: int,
) -> tuple[str, bool, list[str], list[str], list[str], dict[str, str]]:
    safe_fields = _analysis_safe_fields(parsed.fields, parsed.decoded_fields, parsed.repaired_fields)
    safe_decoded = _sanitize_mapping(parsed.decoded_fields)
    safe_repaired = _sanitize_mapping(parsed.repaired_fields)
    candidate_root = {
        "header": _sanitize_mapping(parsed.header),
        "fields": safe_fields,
        "decoded_fields": safe_decoded,
        "repaired_fields": safe_repaired,
        "repair_observations": [observation.model_dump(mode="json") for observation in parsed.repair_observations],
        "parser_warnings": parsed.warnings,
    }
    leaves = _projection_leaves(candidate_root)
    leaves.sort(key=lambda item: (_projection_priority(item[0]), item[2]))

    projection: dict[str, Any] = {}
    projected_paths: list[str] = []
    field_truncated = False
    for path_parts, value, _ in leaves:
        bounded_value, value_truncated = _bound_value(value)
        candidate = deepcopy(projection)
        _assign_projection_path(candidate, path_parts, bounded_value)
        candidate_content = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if len(candidate_content) > max_chars:
            continue
        projection = candidate
        field_truncated = field_truncated or value_truncated
        source_path = _projection_source_path(parsed.source_path, path_parts)
        if source_path is not None:
            projected_paths.append(source_path)

    content = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    all_paths = _all_evidence_paths(parsed)
    sanitized_paths = _sanitized_evidence_paths(parsed)
    projected_set = set(projected_paths)
    omitted_paths = sorted(set(all_paths) - projected_set)
    omission_reasons = {path: ("sensitive_value_redacted" if path in sanitized_paths else "bounded_projection_budget") for path in omitted_paths}
    for path in sanitized_paths:
        omission_reasons.setdefault(path, "sensitive_or_raw_nested_value_sanitized")
    return (
        content,
        field_truncated or bool(omitted_paths),
        sorted(projected_set),
        sorted(set(sanitized_paths)),
        omitted_paths,
        omission_reasons,
    )


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = str(key)
        if _SENSITIVE_FIELD_RE.search(normalized_key):
            result[normalized_key] = "[REDACTED]"
        elif isinstance(item, Mapping):
            result[normalized_key] = _sanitize_mapping(item)
        elif isinstance(item, list):
            result[normalized_key] = [_sanitize_value(child) for child in item]
        else:
            result[normalized_key] = item
    return result


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _projection_leaves(
    value: Any,
    path: tuple[str | int, ...] = (),
    counter: list[int] | None = None,
) -> list[tuple[tuple[str | int, ...], Any, int]]:
    counter = counter if counter is not None else [0]
    if isinstance(value, Mapping):
        result: list[tuple[tuple[str | int, ...], Any, int]] = []
        for key, item in value.items():
            result.extend(_projection_leaves(item, (*path, str(key)), counter))
        if not value:
            counter[0] += 1
            result.append((path, {}, counter[0]))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_projection_leaves(item, (*path, index), counter))
        if not value:
            counter[0] += 1
            result.append((path, [], counter[0]))
        return result
    counter[0] += 1
    return [(path, value, counter[0])]


def _assign_projection_path(
    root: dict[str, Any],
    path: tuple[str | int, ...],
    value: Any,
) -> None:
    current: Any = root
    for index, segment in enumerate(path):
        last = index == len(path) - 1
        if isinstance(segment, int):
            while len(current) <= segment:
                current.append(None)
            if last:
                current[segment] = value
                return
            if current[segment] is None:
                current[segment] = [] if isinstance(path[index + 1], int) else {}
            current = current[segment]
            continue
        if last:
            current[segment] = value
            return
        current = current.setdefault(segment, [] if isinstance(path[index + 1], int) else {})


def _projection_priority(path: tuple[str | int, ...]) -> int:
    keys = {str(part).lower() for part in path if isinstance(part, str)}
    if "header" in keys:
        return 0
    if keys & _HIGH_VALUE_EVIDENCE_KEYS:
        return 1
    if "decoded_fields" in keys or "repaired_fields" in keys:
        return 2
    if "fields" in keys:
        return 3
    return 4


def _projection_source_path(
    source_path: str,
    path: tuple[str | int, ...],
) -> str | None:
    if not path or path[0] not in {"fields", "decoded_fields", "repaired_fields"}:
        return None
    namespace = {
        "fields": "parsed",
        "decoded_fields": "decoded",
        "repaired_fields": "repaired",
    }[path[0]]
    relative = _format_projection_path(path[1:])
    return f"{source_path}#{namespace}.{relative}" if relative else None


def _format_projection_path(path: tuple[str | int, ...]) -> str:
    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else part
    return result


def _all_evidence_paths(parsed: ParsedRawMessageEvidence) -> list[str]:
    paths: list[str] = []
    for namespace, value in (
        ("parsed", parsed.fields),
        ("decoded", parsed.decoded_fields),
        ("repaired", parsed.repaired_fields),
    ):
        for path, _, _ in _projection_leaves(value):
            relative = _format_projection_path(path)
            if relative:
                paths.append(f"{parsed.source_path}#{namespace}.{relative}")
    return paths


def _sanitized_evidence_paths(parsed: ParsedRawMessageEvidence) -> list[str]:
    paths: list[str] = []
    for namespace, value in (
        ("parsed", parsed.fields),
        ("decoded", parsed.decoded_fields),
        ("repaired", parsed.repaired_fields),
    ):
        for path, _, _ in _projection_leaves(value):
            keys = [str(part) for part in path if isinstance(part, str)]
            if not keys:
                continue
            if any(_SENSITIVE_FIELD_RE.search(key) for key in keys) or (namespace == "parsed" and keys[-1].lower() in _DECODED_SEPARATELY_FIELDS):
                paths.append(f"{parsed.source_path}#{namespace}.{_format_projection_path(path)}")
    return paths


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
    """Retain malformed nested text only after conservative secret redaction."""

    if not isinstance(value, str):
        return "[OMITTED NON-STRING CONTENT]"
    redacted = _SENSITIVE_JSON_VALUE_RE.sub(r'\g<prefix>"[REDACTED]"', value)
    return _SENSITIVE_HEADER_LINE_RE.sub(r"\g<name>: [REDACTED]", redacted)


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


def _source_field_semantics(alert: AlertInput) -> list[SourceFieldSemantic]:
    values = alert.extensions.get("source_field_semantics")
    if not isinstance(values, list):
        return []
    semantics: list[SourceFieldSemantic] = []
    for value in values:
        try:
            semantics.append(SourceFieldSemantic.model_validate(value))
        except ValidationError:
            continue
    return semantics


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
