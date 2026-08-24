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
    BoundedEvidenceHighlight,
    EncodedSpanOmission,
    EvidenceCompactionReport,
    EvidenceLayer,
    EvidenceTrustLevel,
    ExtractedEntities,
    FactReconstructionResult,
    LLMAnalysisRequest,
    ParsedRawMessageEvidence,
    SensitiveEvidenceMode,
    SocSkillContext,
    SourceFieldSemantic,
)
from soc_agent.pipeline.encoded_context import (
    OmittedEncodedSpan,
    compact_encoded_spans,
)
from soc_agent.pipeline.evidence_coverage import build_evidence_coverage_report
from soc_agent.pipeline.observation_compactor import (
    build_evidence_compaction_report,
)
from soc_agent.skills import SocSkillResolver, build_soc_skill_context

_PRIMARY_EVIDENCE_MAX_CHARS = 6000
_SUPPLEMENTARY_EVIDENCE_MAX_CHARS = 3000
_MAX_SUPPLEMENTARY_EVIDENCE = 4
_MAX_EVIDENCE_HIGHLIGHTS = 100
_MAX_HIGHLIGHT_PATHS = 5
_MAX_HIGHLIGHT_VALUE_CHARS = 500
_MAX_HIGHLIGHT_TOTAL_CHARS = 12_000
_MAX_FIELD_CHARS = 1000
_DECODED_SEPARATELY_FIELDS = frozenset(
    {
        "req_body",
        "rsp_body",
        "rule_labels",
        "req_header",
        "rsp_header",
        "request_header_str",
        "response_header_str",
        "response_hqeader_str",
    }
)
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
_MAX_PROJECTED_PROVENANCE_ITEMS = 40
_MAX_PROJECTED_ROLE_CLAIMS = 40
_MAX_PROJECTED_ROLE_ENTITIES = 50
_MAX_MODEL_REFERENCE_ITEMS = 100
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
    *,
    sensitive_evidence_mode: SensitiveEvidenceMode = SensitiveEvidenceMode.REDACT,
) -> LLMAnalysisRequest:
    """Convert runtime state into the only input shape analysis nodes consume."""

    conflict_types = sorted({report.conflict_type for report in fact_reconstruction.conflict_reports})
    evidence_compaction = build_evidence_compaction_report(
        alert,
        primary_evidence_path=fact_reconstruction.selected_input_path,
    )
    primary_evidence, supplementary_evidence = _bounded_evidence(
        alert,
        fact_reconstruction,
        supplementary_paths=(evidence_compaction.selected_evidence_paths[1:] if evidence_compaction.behavior_group_count else None),
        sensitive_evidence_mode=sensitive_evidence_mode,
    )
    evidence_highlights, highlighted_paths = _build_evidence_highlights(
        alert,
        fact_reconstruction,
        primary_evidence,
        supplementary_evidence,
        sensitive_evidence_mode=sensitive_evidence_mode,
    )
    evidence_coverage = build_evidence_coverage_report(
        alert,
        fact_reconstruction,
        primary_evidence,
        supplementary_evidence,
        highlighted_paths=highlighted_paths,
        compacted_paths=evidence_compaction.represented_field_paths,
    )
    warnings = [
        *fact_reconstruction.warnings,
        *entities.warnings,
        *evidence_coverage.warnings,
        *evidence_compaction.warnings,
    ]
    return LLMAnalysisRequest(
        alert_id=alert.alert_id,
        tenant_id=alert.tenant_id,
        environment=_string_extension(alert, "environment"),
        source=alert.source,
        detection=alert.detection,
        classification=alert.classification,
        canonical_entities=alert.entities,
        extracted_entities=entities,
        fact_reconstruction=fact_reconstruction,
        primary_evidence_path=fact_reconstruction.selected_input_path,
        primary_evidence=primary_evidence,
        supplementary_evidence=supplementary_evidence,
        evidence_highlights=evidence_highlights,
        evidence_compaction=evidence_compaction,
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


def _string_extension(alert: AlertInput, key: str) -> str | None:
    value = alert.extensions.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def project_analysis_context(request: LLMAnalysisRequest) -> dict[str, Any]:
    """Return the exact bounded context shared by prompting and grounding."""

    fact = request.fact_reconstruction
    context = {
        "schema_version": request.schema_version,
        "alert_id": request.alert_id,
        "environment": request.environment,
        "source": request.source.model_dump(mode="json", exclude_none=True),
        "detection": request.detection.model_dump(mode="json", exclude_none=True),
        "classification": request.classification.model_dump(mode="json", exclude_none=True),
        "canonical_entities": request.canonical_entities.model_dump(mode="json", exclude_none=True),
        "extracted_entities": request.extracted_entities.model_dump(mode="json", exclude_none=True),
        "evidence": {
            "primary_evidence_path": request.primary_evidence_path,
            "primary_evidence": (_project_bounded_evidence(request.primary_evidence) if request.primary_evidence is not None else None),
            "supplementary_evidence": [_project_bounded_evidence(item) for item in request.supplementary_evidence],
            "highlights": [
                {
                    "schema_version": item.schema_version,
                    "semantic_type": item.semantic_type,
                    "meaning": item.meaning,
                    "value": item.value,
                    "trust_level": item.trust_level,
                    "occurrence_count": item.occurrence_count,
                    "truncated": item.truncated,
                }
                for item in request.evidence_highlights
            ],
            "selected_input_path": fact.selected_input_path,
            "selected_input_available": fact.selected_input_available,
            "evidence_policy": fact.evidence_policy.model_dump(mode="json", exclude_none=True) if fact.evidence_policy is not None else None,
            "field_trusts": [item.model_dump(mode="json", exclude_none=True) for item in fact.field_trusts],
            "coverage": _analysis_coverage_context(request),
            "adapter_contract_count": sum(item.participates_in_reasoning for item in request.source_field_semantics),
            "adapter_contract_projection": "reference_catalogs.reasoning_context:A-*",
        },
        "evidence_compaction": _project_evidence_compaction(
            request.evidence_compaction,
        ),
        "fact_reconstruction": {
            "canonical_field_provenance": [
                {
                    "canonical_path": item.canonical_path,
                    "selected_value": item.selected_value,
                    "selected_from": item.selected_from,
                    "trust_level": item.trust_level,
                }
                for item in sorted(
                    fact.canonical_field_provenance,
                    key=lambda item: (
                        ".observations[" in item.canonical_path,
                        item.canonical_path,
                        item.selected_from,
                    ),
                )[:_MAX_PROJECTED_PROVENANCE_ITEMS]
            ],
            "canonical_field_provenance_count": len(fact.canonical_field_provenance),
            "canonical_field_provenance_truncated": (len(fact.canonical_field_provenance) > _MAX_PROJECTED_PROVENANCE_ITEMS),
            "role_claims": [
                {
                    "role": item.role,
                    "value": item.value,
                    "claim_type": item.claim_type,
                    "evidence_path": item.evidence_path,
                    "observation_scope": item.observation_scope,
                    "evidence_trust": item.evidence_trust,
                    "semantic_confidence": item.semantic_confidence,
                }
                for item in fact.role_claims[:_MAX_PROJECTED_ROLE_CLAIMS]
            ],
            "role_claim_count": len(fact.role_claims),
            "role_claims_truncated": (len(fact.role_claims) > _MAX_PROJECTED_ROLE_CLAIMS),
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
            "role_coherence": fact.role_coherence.model_dump(
                mode="json",
                exclude_none=True,
            ),
            "conflict_count": request.conflict_count,
            "conflict_types": request.conflict_types,
            "conflict_reports": [item.model_dump(mode="json", exclude_none=True) for item in fact.conflict_reports],
            "warnings": request.warnings,
        },
        "skill_context": request.skill_context.model_dump(mode="json", exclude_none=True),
        "reference_catalogs": {
            "current_alert_evidence": [
                {
                    "evidence_ref": item.evidence_ref,
                    "source_path": item.source_path,
                    "value": item.value,
                    "trust_level": item.trust_level.value,
                }
                for item in request.evidence_catalog
            ],
            "role_entities": _project_role_entities(request),
            "reasoning_context": [
                {
                    "context_ref": item.context_ref,
                    "kind": item.kind.value,
                    "label": item.label,
                    "source_id": item.source_id,
                    "summary": item.summary,
                    **(
                        {
                            "memory_comparison": item.memory_comparison.model_dump(
                                mode="json",
                                exclude_none=True,
                            )
                        }
                        if item.memory_comparison is not None
                        else {}
                    ),
                }
                for item in request.context_catalog
            ],
        },
    }
    bounded = _bound_projection(context)
    if not isinstance(bounded, dict):
        raise TypeError("analysis context projection must remain an object")
    return bounded


def _project_role_entities(request: LLMAnalysisRequest) -> list[dict[str, Any]]:
    """Expose one preferred E-* reference per Runtime-typed entity value."""

    projected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in request.evidence_catalog[:_MAX_MODEL_REFERENCE_ITEMS]:
        if item.entity_type is None:
            continue
        key = (
            item.entity_type.value,
            item.value_type,
            json.dumps(item.value, ensure_ascii=False, sort_keys=True),
        )
        if key in seen:
            continue
        seen.add(key)
        projected.append(
            {
                "evidence_ref": item.evidence_ref,
                "entity_type": item.entity_type.value,
                "value": item.value,
                "source_path": item.source_path,
                "trust_level": item.trust_level.value,
            }
        )
        if len(projected) >= _MAX_PROJECTED_ROLE_ENTITIES:
            break
    return projected


def _analysis_coverage_context(request: LLMAnalysisRequest) -> dict[str, Any]:
    coverage = request.evidence_coverage
    omission_reason_counts: dict[str, int] = {}
    for omission in coverage.omissions:
        omission_reason_counts[omission.reason] = omission_reason_counts.get(omission.reason, 0) + 1

    schema_status_counts = {
        "recognized": 0,
        "degraded": 0,
        "unsupported": 0,
    }
    for item in coverage.message_schemas:
        status = item.status.value
        if status in schema_status_counts:
            schema_status_counts[status] += 1

    high_value_gap_count = len(coverage.high_value_gaps)
    schema_warning_count = schema_status_counts["degraded"] + schema_status_counts["unsupported"]
    if high_value_gap_count:
        readiness_status = "degraded_by_high_value_gap"
        readiness_summary = "存在未投影的高价值证据，结论应显式保留该证据缺口。"
    elif schema_warning_count:
        readiness_status = "usable_with_schema_warnings"
        readiness_summary = "当前证据可用于研判，但部分 Message 解析不完整或缺少受支持的解析器。"
    else:
        readiness_status = "ready"
        readiness_summary = "当前主要证据已进入模型上下文；常规预算省略不代表关键证据缺失。"

    return {
        "analysis_readiness": {
            "status": readiness_status,
            "summary": readiness_summary,
            "high_value_gap_count": high_value_gap_count,
        },
        "message_parsing": {
            "message_count": len(coverage.message_schemas),
            "recognized_count": schema_status_counts["recognized"],
            "degraded_count": schema_status_counts["degraded"],
            "unsupported_count": schema_status_counts["unsupported"],
            "parsers": [
                {
                    "parser": item.parser_name or "unavailable",
                    "status": item.status,
                    "parsed_field_count": item.field_count,
                    "warning_count": len(item.warnings),
                }
                for item in coverage.message_schemas
            ],
        },
        "model_projection": {
            "visible_field_count": coverage.counts.get("llm_projected_count", 0),
            "documented_omission_count": len(coverage.omissions),
            "sanitized_field_count": coverage.counts.get("llm_sanitized_count", 0),
            "encoded_span_compaction_count": len(coverage.llm_compacted_encoded_paths),
            "bounded_evidence_with_omissions_count": len(coverage.llm_truncated_evidence_paths),
        },
        "documented_omissions": [
            {
                "reason": reason,
                "count": count,
            }
            for reason, count in sorted(omission_reason_counts.items())
        ],
        "high_value_gaps": [
            {
                "expected_target": item.expected_target,
                "reason": item.reason,
                "importance": item.importance,
            }
            for item in coverage.high_value_gaps
        ],
    }


def _project_evidence_compaction(
    report: EvidenceCompactionReport,
) -> dict[str, Any]:
    """Project compact facts while retaining full source paths only in audit data."""

    return {
        "schema_version": report.schema_version,
        "strategy_version": report.strategy_version,
        "raw_payload_retained": report.raw_payload_retained,
        "source_message_count": report.source_message_count,
        "typed_observation_count": report.typed_observation_count,
        "behavior_group_count": report.behavior_group_count,
        "profile_count": report.profile_count,
        "repeated_shape_message_count": report.repeated_shape_message_count,
        "collapsed_repetition_count": report.collapsed_repetition_count,
        "non_dominant_profile_count": report.non_dominant_profile_count,
        "represented_source_count": report.represented_source_count,
        "represented_field_count": report.represented_field_count,
        "unrepresented_source_count": report.unrepresented_source_count,
        "high_value_omission_count": report.high_value_omission_count,
        "groups": [
            {
                "group_id": group.group_id,
                "parser_names": group.parser_names,
                "observation_kinds": group.observation_kinds,
                "occurrence_count": group.occurrence_count,
                "representative_source_path": group.representative_source_path,
                "source_path_count": group.source_path_count,
                "source_paths_truncated": group.source_paths_truncated,
                "first_seen": group.first_seen,
                "last_seen": group.last_seen,
                "stable_facts": [fact.model_dump(mode="json") for fact in group.stable_facts],
                "varying_facts": [variation.model_dump(mode="json") for variation in group.varying_facts],
                "profiles": [profile.model_dump(mode="json") for profile in group.profiles],
                "profile_count": group.profile_count,
                "profiles_truncated": group.profiles_truncated,
                "non_dominant_profile_count": group.non_dominant_profile_count,
            }
            for group in report.groups
        ],
        "warnings": report.warnings,
    }


def _project_bounded_evidence(
    evidence: BoundedAnalysisEvidence,
) -> dict[str, Any]:
    try:
        content: Any = json.loads(evidence.content)
        content_format = "json"
    except json.JSONDecodeError:
        content = evidence.content
        content_format = "text"

    omission_reason_counts: dict[str, int] = {}
    for reason in evidence.omission_reasons.values():
        omission_reason_counts[reason] = omission_reason_counts.get(reason, 0) + 1

    if evidence.omitted_field_paths:
        projection_status = "bounded_with_documented_omissions"
    elif evidence.sanitized_field_paths:
        projection_status = "bounded_with_sanitization"
    elif evidence.encoded_span_omissions:
        projection_status = "complete_with_encoded_compaction"
    else:
        projection_status = "complete_within_budget"

    return {
        "source_path": evidence.source_path,
        "layer": evidence.layer,
        "trust_level": evidence.trust_level,
        "sensitive_evidence_mode": evidence.sensitive_evidence_mode,
        "parser": evidence.parser_name,
        "content_format": content_format,
        "content": content,
        "projection": {
            "status": projection_status,
            "original_character_count": evidence.original_length,
            "visible_field_count": len(evidence.projected_field_paths),
            "sanitized_field_count": len(evidence.sanitized_field_paths),
            "omitted_field_count": len(evidence.omitted_field_paths),
            "encoded_span_compaction_count": len(evidence.encoded_span_omissions),
            "omission_reasons": [
                {
                    "reason": reason,
                    "count": count,
                }
                for reason, count in sorted(omission_reason_counts.items())
            ],
        },
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
    *,
    supplementary_paths: list[str] | None = None,
    sensitive_evidence_mode: SensitiveEvidenceMode,
) -> tuple[BoundedAnalysisEvidence | None, list[BoundedAnalysisEvidence]]:
    policy = fact_reconstruction.evidence_policy
    if policy is None or policy.selected_input_path is None:
        return None, []

    parsed_by_path = _parsed_messages_by_path(alert)
    reasoning_priority_paths = _reasoning_priority_paths(alert)
    non_reasoning_paths = _non_reasoning_paths(alert)
    primary = _bounded_evidence_for_path(
        alert,
        path=policy.selected_input_path,
        layer=policy.selected_layer,
        trust_level=policy.trust_level,
        max_chars=_PRIMARY_EVIDENCE_MAX_CHARS,
        parsed=parsed_by_path.get(policy.selected_input_path),
        reasoning_priority_paths=reasoning_priority_paths,
        non_reasoning_paths=non_reasoning_paths,
        sensitive_evidence_mode=sensitive_evidence_mode,
    )
    supplementary: list[BoundedAnalysisEvidence] = []
    candidate_paths = supplementary_paths if supplementary_paths is not None else policy.supplementary_input_paths
    for path in candidate_paths[:_MAX_SUPPLEMENTARY_EVIDENCE]:
        if path == policy.selected_input_path:
            continue
        item = _bounded_evidence_for_path(
            alert,
            path=path,
            layer=EvidenceLayer.RAW_MESSAGE,
            trust_level=EvidenceTrustLevel.HIGH,
            max_chars=_SUPPLEMENTARY_EVIDENCE_MAX_CHARS,
            parsed=parsed_by_path.get(path),
            reasoning_priority_paths=reasoning_priority_paths,
            non_reasoning_paths=non_reasoning_paths,
            sensitive_evidence_mode=sensitive_evidence_mode,
        )
        if item is not None:
            supplementary.append(item)
    return primary, supplementary


def _build_evidence_highlights(
    alert: AlertInput,
    fact_reconstruction: FactReconstructionResult,
    primary: BoundedAnalysisEvidence | None,
    supplementary: list[BoundedAnalysisEvidence],
    *,
    sensitive_evidence_mode: SensitiveEvidenceMode,
) -> tuple[list[BoundedEvidenceHighlight], list[str]]:
    projected_paths = {path for evidence in (primary, *supplementary) if evidence is not None for path in evidence.projected_field_paths}
    typed_paths = {
        *(item.selected_from for item in fact_reconstruction.canonical_field_provenance),
        *(item.evidence_path for item in fact_reconstruction.role_claims),
        *(path for item in fact_reconstruction.scenario_hypotheses for path in item.evidence_paths),
    }
    parsed_by_path = _parsed_messages_by_path(alert)
    groups: dict[
        tuple[str, str, str, EvidenceTrustLevel, bool],
        dict[str, Any],
    ] = {}
    for semantic in _source_field_semantics(alert):
        if not semantic.participates_in_reasoning or semantic.field_path in typed_paths:
            continue
        value = _semantic_field_value(parsed_by_path, semantic.field_path)
        bounded = _bounded_highlight_value(
            value,
            field_path=semantic.field_path,
            sensitive_evidence_mode=sensitive_evidence_mode,
        )
        if bounded is None:
            continue
        rendered_value, truncated = bounded
        trust_level = _highlight_trust_level(
            semantic.field_path,
            fact_reconstruction,
        )
        key = (
            semantic.semantic_type,
            semantic.meaning,
            rendered_value,
            trust_level,
            truncated,
        )
        existing = groups.get(key)
        if existing is not None:
            existing["occurrence_count"] += 1
            existing["all_paths"].append(semantic.field_path)
            if semantic.field_path not in projected_paths:
                existing["unprojected_paths"].append(semantic.field_path)
            continue
        groups[key] = {
            "semantic_type": semantic.semantic_type,
            "meaning": semantic.meaning,
            "value": rendered_value,
            "trust_level": trust_level,
            "occurrence_count": 1,
            "truncated": truncated,
            "sensitive_evidence_mode": sensitive_evidence_mode,
            "all_paths": [semantic.field_path],
            "unprojected_paths": ([] if semantic.field_path in projected_paths else [semantic.field_path]),
        }

    selected: list[BoundedEvidenceHighlight] = []
    highlighted_paths: list[str] = []
    total_chars = 0
    for candidate in groups.values():
        if not candidate["unprojected_paths"]:
            continue
        item_chars = len(candidate["semantic_type"]) + len(candidate["meaning"]) + len(candidate["value"])
        if len(selected) >= _MAX_EVIDENCE_HIGHLIGHTS or total_chars + item_chars > _MAX_HIGHLIGHT_TOTAL_CHARS:
            continue
        representative_paths = _dedupe(
            [
                *candidate.pop("unprojected_paths"),
                *candidate.pop("all_paths"),
            ]
        )
        candidate["evidence_paths"] = representative_paths[:_MAX_HIGHLIGHT_PATHS]
        candidate["evidence_paths_truncated"] = len(representative_paths) > _MAX_HIGHLIGHT_PATHS
        selected.append(BoundedEvidenceHighlight.model_validate(candidate))
        highlighted_paths.extend(representative_paths)
        total_chars += item_chars
    return selected, highlighted_paths


def _highlight_trust_level(
    field_path: str,
    fact_reconstruction: FactReconstructionResult,
) -> EvidenceTrustLevel:
    source_path = field_path.split("#", 1)[0]
    field_trust = next(
        (item for item in fact_reconstruction.field_trusts if item.field_path == source_path and item.participates),
        None,
    )
    if field_trust is None:
        return EvidenceTrustLevel.UNKNOWN
    return field_trust.source_trust


def _semantic_field_value(
    parsed_by_path: Mapping[str, ParsedRawMessageEvidence],
    field_path: str,
) -> Any:
    match = re.fullmatch(r"(.+)#(parsed|decoded|repaired)\.(.+)", field_path)
    if match is None:
        return None
    source_path, namespace, relative_path = match.groups()
    parsed = parsed_by_path.get(source_path)
    if parsed is None:
        return None
    root = {
        "parsed": parsed.fields,
        "decoded": parsed.decoded_fields,
        "repaired": parsed.repaired_fields,
    }[namespace]
    return _resolve_path(root, relative_path)


def _bounded_highlight_value(
    value: Any,
    *,
    field_path: str,
    sensitive_evidence_mode: SensitiveEvidenceMode,
) -> tuple[str, bool] | None:
    if value is None or value == "":
        return None
    if sensitive_evidence_mode is SensitiveEvidenceMode.REDACT and _SENSITIVE_FIELD_RE.search(field_path):
        return "[REDACTED]", False
    safe_value = deepcopy(value) if sensitive_evidence_mode is SensitiveEvidenceMode.FULL else _sanitize_value(value)
    if isinstance(safe_value, str) and sensitive_evidence_mode is SensitiveEvidenceMode.REDACT:
        safe_value = _safe_string_fallback(safe_value)
    compacted, encoded_omissions = compact_encoded_spans(safe_value)
    rendered = compacted if isinstance(compacted, str) else json.dumps(compacted, ensure_ascii=False, sort_keys=True, default=str)
    if not isinstance(rendered, str):
        rendered = str(rendered)
    truncated = bool(encoded_omissions) or len(rendered) > _MAX_HIGHLIGHT_VALUE_CHARS
    return rendered[:_MAX_HIGHLIGHT_VALUE_CHARS], truncated


def _bounded_evidence_for_path(
    alert: AlertInput,
    *,
    path: str,
    layer: EvidenceLayer,
    trust_level: EvidenceTrustLevel,
    max_chars: int,
    parsed: ParsedRawMessageEvidence | None,
    reasoning_priority_paths: frozenset[str],
    non_reasoning_paths: frozenset[str],
    sensitive_evidence_mode: SensitiveEvidenceMode,
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
            encoded_span_omissions,
        ) = _bounded_parsed_projection(
            parsed,
            max_chars=max_chars,
            reasoning_priority_paths=reasoning_priority_paths,
            non_reasoning_paths=non_reasoning_paths,
            sensitive_evidence_mode=sensitive_evidence_mode,
        )
        original_length = parsed.original_length
        parser_name = parsed.parser_name
        content_truncated = False
    elif isinstance(raw_value, Mapping):
        serialized = json.dumps(raw_value, ensure_ascii=False, sort_keys=True, default=str)
        (
            content,
            field_truncated,
            projected_paths,
            sanitized_paths,
            omitted_paths,
            omission_reasons,
            encoded_span_omissions,
        ) = _bounded_structured_projection(
            raw_value,
            source_path=path,
            max_chars=max_chars,
            non_reasoning_paths=non_reasoning_paths,
            sensitive_evidence_mode=sensitive_evidence_mode,
        )
        original_length = len(serialized)
        parser_name = None
        content_truncated = False
    elif isinstance(raw_value, str):
        content = raw_value if sensitive_evidence_mode is SensitiveEvidenceMode.FULL else _safe_string_fallback(raw_value)
        compacted_content, compacted = compact_encoded_spans(content)
        content = str(compacted_content)
        encoded_span_omissions = [
            EncodedSpanOmission(
                field_path=path,
                kind=item.kind,
                original_chars=item.original_chars,
                sha256=item.sha256,
            )
            for item in compacted
        ]
        original_length = len(raw_value)
        parser_name = None
        field_truncated = False
        projected_paths = []
        sanitized_paths = []
        omitted_paths = []
        omission_reasons = {}
        content_truncated = len(content) > max_chars
    elif raw_value is not None:
        content = json.dumps(raw_value, ensure_ascii=False, sort_keys=True, default=str)
        original_length = len(content)
        parser_name = None
        field_truncated = False
        projected_paths = []
        sanitized_paths = []
        omitted_paths = []
        omission_reasons = {}
        encoded_span_omissions = []
        content_truncated = len(content) > max_chars
    else:
        return None

    if content_truncated:
        content = content[:max_chars]
    return BoundedAnalysisEvidence(
        source_path=path,
        layer=layer,
        trust_level=trust_level,
        sensitive_evidence_mode=sensitive_evidence_mode,
        content=content,
        parser_name=parser_name,
        original_length=original_length,
        truncated=field_truncated or content_truncated,
        projected_field_paths=projected_paths,
        sanitized_field_paths=sanitized_paths,
        omitted_field_paths=omitted_paths,
        omission_reasons=omission_reasons,
        encoded_span_omissions=encoded_span_omissions,
    )


def _bounded_parsed_projection(
    parsed: ParsedRawMessageEvidence,
    *,
    max_chars: int,
    reasoning_priority_paths: frozenset[str],
    non_reasoning_paths: frozenset[str],
    sensitive_evidence_mode: SensitiveEvidenceMode,
) -> tuple[
    str,
    bool,
    list[str],
    list[str],
    list[str],
    dict[str, str],
    list[EncodedSpanOmission],
]:
    if sensitive_evidence_mode is SensitiveEvidenceMode.FULL:
        safe_fields = deepcopy(parsed.fields)
        safe_decoded = deepcopy(parsed.decoded_fields)
        safe_repaired = deepcopy(parsed.repaired_fields)
        safe_header = deepcopy(parsed.header)
    else:
        safe_fields = _analysis_safe_fields(parsed.fields, parsed.decoded_fields, parsed.repaired_fields)
        safe_decoded = _sanitize_mapping(parsed.decoded_fields)
        safe_repaired = _sanitize_mapping(parsed.repaired_fields)
        safe_header = _sanitize_mapping(parsed.header)
    candidate_root = {
        "header": safe_header,
        "fields": safe_fields,
        "decoded_fields": safe_decoded,
        "repaired_fields": safe_repaired,
        "repair_observations": [observation.model_dump(mode="json") for observation in parsed.repair_observations],
        "parser_warnings": parsed.warnings,
    }
    compacted_root, compacted = compact_encoded_spans(candidate_root)
    if not isinstance(compacted_root, dict):
        raise TypeError("parsed evidence compaction must preserve the projection object")
    candidate_root = compacted_root
    encoded_span_omissions = [_parsed_encoded_span_omission(parsed.source_path, item) for item in compacted]
    leaves = _projection_leaves(candidate_root)
    leaves.sort(
        key=lambda item: (
            _projection_priority(
                item[0],
                source_path=parsed.source_path,
                reasoning_priority_paths=reasoning_priority_paths,
            ),
            item[2],
        )
    )

    projection: dict[str, Any] = {}
    projected_paths: list[str] = []
    adapter_excluded_paths: set[str] = set()
    field_truncated = False
    for path_parts, value, _ in leaves:
        source_path = _projection_source_path(parsed.source_path, path_parts)
        if source_path is not None and _is_non_reasoning_path(
            source_path,
            non_reasoning_paths,
        ):
            adapter_excluded_paths.add(source_path)
            continue
        bounded_value, value_truncated = _projection_value(
            value,
            sensitive_evidence_mode=sensitive_evidence_mode,
        )
        candidate = deepcopy(projection)
        _assign_projection_path(candidate, path_parts, bounded_value)
        candidate_content = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if len(candidate_content) > max_chars:
            continue
        projection = candidate
        field_truncated = field_truncated or value_truncated
        if source_path is not None:
            projected_paths.append(source_path)

    content = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    all_paths = _all_evidence_paths(parsed)
    sanitized_paths = [] if sensitive_evidence_mode is SensitiveEvidenceMode.FULL else _sanitized_evidence_paths(parsed)
    projected_set = set(projected_paths)
    omitted_paths = sorted(set(all_paths) - projected_set)
    omission_reasons = {path: ("adapter_excluded_from_reasoning" if path in adapter_excluded_paths else ("sensitive_value_redacted" if path in sanitized_paths else "bounded_projection_budget")) for path in omitted_paths}
    for path in sanitized_paths:
        omission_reasons.setdefault(path, "sensitive_or_raw_nested_value_sanitized")
    return (
        content,
        field_truncated or bool(omitted_paths),
        sorted(projected_set),
        sorted(set(sanitized_paths)),
        omitted_paths,
        omission_reasons,
        encoded_span_omissions,
    )


def _bounded_structured_projection(
    value: Mapping[str, Any],
    *,
    source_path: str,
    max_chars: int,
    non_reasoning_paths: frozenset[str],
    sensitive_evidence_mode: SensitiveEvidenceMode,
) -> tuple[
    str,
    bool,
    list[str],
    list[str],
    list[str],
    dict[str, str],
    list[EncodedSpanOmission],
]:
    candidate_root = deepcopy(dict(value)) if sensitive_evidence_mode is SensitiveEvidenceMode.FULL else _sanitize_mapping(value)
    compacted_root, compacted = compact_encoded_spans(candidate_root)
    if not isinstance(compacted_root, dict):
        raise TypeError("structured evidence compaction must preserve the projection object")
    candidate_root = compacted_root
    encoded_span_omissions = [
        EncodedSpanOmission(
            field_path=f"{source_path}{item.path.removeprefix('$')}",
            kind=item.kind,
            original_chars=item.original_chars,
            sha256=item.sha256,
        )
        for item in compacted
    ]
    leaves = _projection_leaves(candidate_root)
    leaves.sort(key=lambda item: (_projection_priority(item[0]), item[2]))

    projection: dict[str, Any] = {}
    projected_paths: list[str] = []
    adapter_excluded_paths: set[str] = set()
    for path_parts, field_value, _ in leaves:
        field_path = _structured_source_path(source_path, path_parts)
        if _is_non_reasoning_path(field_path, non_reasoning_paths):
            adapter_excluded_paths.add(field_path)
            continue
        bounded_value, _ = _projection_value(
            field_value,
            sensitive_evidence_mode=sensitive_evidence_mode,
        )
        candidate = deepcopy(projection)
        _assign_projection_path(candidate, path_parts, bounded_value)
        if len(json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str)) > max_chars:
            continue
        projection = candidate
        projected_paths.append(field_path)

    all_paths = [_structured_source_path(source_path, path_parts) for path_parts, _, _ in _projection_leaves(value)]
    sanitized_paths = [] if sensitive_evidence_mode is SensitiveEvidenceMode.FULL else _sensitive_mapping_paths(value, source_path=source_path)
    projected_set = set(projected_paths)
    omitted_paths = sorted(set(all_paths) - projected_set)
    omission_reasons = {path: ("adapter_excluded_from_reasoning" if path in adapter_excluded_paths else ("sensitive_value_redacted" if path in sanitized_paths else "bounded_projection_budget")) for path in omitted_paths}
    for path in sanitized_paths:
        omission_reasons.setdefault(path, "sensitive_value_redacted")
    return (
        json.dumps(projection, ensure_ascii=False, sort_keys=True, default=str),
        bool(omitted_paths),
        sorted(projected_set),
        sorted(set(sanitized_paths)),
        omitted_paths,
        omission_reasons,
        encoded_span_omissions,
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


def _projection_priority(
    path: tuple[str | int, ...],
    *,
    source_path: str | None = None,
    reasoning_priority_paths: frozenset[str] = frozenset(),
) -> int:
    projected_path = _projection_source_path(source_path, path) if source_path is not None else None
    if projected_path in reasoning_priority_paths:
        return 0
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


def _reasoning_priority_paths(alert: AlertInput) -> frozenset[str]:
    return frozenset(semantic.field_path for semantic in _source_field_semantics(alert) if semantic.participates_in_reasoning and any(marker in semantic.field_path for marker in ("#parsed.", "#decoded.", "#repaired.")))


def _non_reasoning_paths(alert: AlertInput) -> frozenset[str]:
    return frozenset(semantic.field_path for semantic in _source_field_semantics(alert) if not semantic.participates_in_reasoning)


def _is_non_reasoning_path(
    field_path: str,
    non_reasoning_paths: frozenset[str],
) -> bool:
    return any(field_path == excluded or field_path.startswith((f"{excluded}.", f"{excluded}[")) for excluded in non_reasoning_paths)


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


def _parsed_encoded_span_omission(
    source_path: str,
    omission: OmittedEncodedSpan,
) -> EncodedSpanOmission:
    for projection_name, namespace in (
        ("fields", "parsed"),
        ("decoded_fields", "decoded"),
        ("repaired_fields", "repaired"),
        ("header", "header"),
    ):
        prefix = f"$.{projection_name}"
        if omission.path == prefix or omission.path.startswith((f"{prefix}.", f"{prefix}[")):
            suffix = omission.path[len(prefix) :]
            return EncodedSpanOmission(
                field_path=f"{source_path}#{namespace}{suffix}",
                kind=omission.kind,
                original_chars=omission.original_chars,
                sha256=omission.sha256,
            )
    return EncodedSpanOmission(
        field_path=f"{source_path}#projection{omission.path.removeprefix('$')}",
        kind=omission.kind,
        original_chars=omission.original_chars,
        sha256=omission.sha256,
    )


def _format_projection_path(path: tuple[str | int, ...]) -> str:
    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else part
    return result


def _structured_source_path(
    source_path: str,
    path: tuple[str | int, ...],
) -> str:
    relative = _format_projection_path(path)
    return f"{source_path}.{relative}" if relative else source_path


def _sensitive_mapping_paths(
    value: Mapping[str, Any],
    *,
    source_path: str,
) -> list[str]:
    paths: list[str] = []
    for path, _, _ in _projection_leaves(value):
        keys = [str(part) for part in path if isinstance(part, str)]
        if any(_SENSITIVE_FIELD_RE.search(key) for key in keys):
            paths.append(_structured_source_path(source_path, path))
    return paths


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


def _projection_value(
    value: Any,
    *,
    sensitive_evidence_mode: SensitiveEvidenceMode,
) -> tuple[Any, bool]:
    if sensitive_evidence_mode is SensitiveEvidenceMode.FULL:
        return deepcopy(value), False
    return _bound_value(value)


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
