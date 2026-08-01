"""Deterministically ground analyzer evidence in the bounded prompt context."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import (
    AnalysisEvidenceGroundingItem,
    AnalysisEvidenceGroundingReport,
    AnalysisEvidenceGroundingStatus,
    AnalysisResult,
    EvidenceTrustLevel,
    LLMAnalysisRequest,
    TriageActivityStage,
)
from soc_agent.pipeline.analysis_context import project_analysis_context

_SOURCE_PREFIXES: dict[str, tuple[str, ...]] = {
    "alert": ("alert_id",),
    "alert_id": ("alert_id",),
    "source": ("source",),
    "detection": ("detection",),
    "rule": ("detection",),
    "classification": ("classification",),
    "entities": ("canonical_entities", "extracted_entities"),
    "entity": ("canonical_entities", "extracted_entities"),
    "canonical_entities": ("canonical_entities",),
    "extracted_entities": ("extracted_entities",),
    "network": ("canonical_entities.network", "extracted_entities"),
    "command_line": ("canonical_entities.process.command_line", "extracted_entities.processes"),
    "process": ("canonical_entities.process", "extracted_entities.processes"),
    "fact_reconstruction": ("fact_reconstruction",),
    "conflict_report": ("fact_reconstruction.conflict_reports",),
    "evidence": ("evidence",),
    "primary_evidence": ("evidence.primary_evidence",),
    "supplementary_evidence": ("evidence.supplementary_evidence",),
    "raw_log": ("evidence.primary_evidence", "evidence.supplementary_evidence"),
    "raw_message": ("evidence.primary_evidence", "evidence.supplementary_evidence"),
    "message": ("evidence.primary_evidence", "evidence.supplementary_evidence"),
    "evidence_coverage": ("evidence.coverage",),
    "skill_context": ("skill_context",),
}
_COMPOSITE_SEPARATOR_RE = re.compile(r"\s*[,;，；]\s*")
_ENCODED_OMISSION_MARKER_RE = re.compile(
    r"<ENCODED:[a-z0-9_]+:\d+:sha256=[a-f0-9]{12}:OMITTED>",
    re.IGNORECASE,
)
_DISTINCTIVE_PATH_TOKEN_RE = re.compile(
    r"(?:0x[0-9a-f]{4,}|(?<![a-f0-9])[a-f0-9]{12,}(?![a-f0-9]))",
    re.IGNORECASE,
)
_SHORT_DESCRIPTION_FACTS = frozenset(
    {
        "delete",
        "dns",
        "get",
        "head",
        "http",
        "https",
        "options",
        "patch",
        "post",
        "put",
        "rdp",
        "ssh",
        "tcp",
        "udp",
    }
)
_DESCRIPTION_FACT_STOPLIST = frozenset(
    {
        "false",
        "high",
        "low",
        "medium",
        "none",
        "null",
        "true",
        "unknown",
    }
)
_DESCRIPTION_FACT_DISCLAIMERS = (
    "不代表",
    "不能证明",
    "无法证明",
    "不是",
    "并非",
    "非独立",
    "not evidence",
    "not proof",
)
_OUTCOME_CLAIM_RE = re.compile(
    r"(?:攻击成功|利用成功|成功利用|写入成功|执行成功|已写入|已执行|已入侵|已攻陷|"
    r"successful(?:ly)?\s+(?:exploit|execut|writ)|confirmed\s+compromise|command\s+executed|file\s+(?:was\s+)?written)",
    re.IGNORECASE,
)
_OUTCOME_NEGATION_TERMS = ("未", "无", "无法", "不能", "是否", "尚未", "没有", "缺少", "缺乏", "不")
_OUTCOME_ARTIFACT_KEYS = frozenset(
    {
        "command_output",
        "created_file",
        "execution_result",
        "file_created",
        "authentication_result",
        "callback_observed",
        "login_result",
        "persistence_result",
        "process_id",
        "shell_output",
        "write_result",
    }
)
_PROVIDER_OUTCOME_SEMANTIC_TYPE = "provider_detection_outcome_assertion"


def ground_analysis_evidence(
    analysis: AnalysisResult,
    request: LLMAnalysisRequest,
) -> AnalysisEvidenceGroundingReport:
    """Match every analyzer evidence value against the exact prompt projection."""

    context = project_analysis_context(request)
    scalar_index = [(path, candidate) for path, candidate in _scalar_index(context) if ".encoded_span_omissions" not in path]
    scalar_index.extend(_bounded_evidence_content_index(request))
    items = [
        _ground_item(
            index,
            evidence.source,
            evidence.description,
            evidence.value,
            scalar_index,
            request=request,
        )
        for index, evidence in enumerate(analysis.evidence)
    ]
    grounded_count = sum(item.status is AnalysisEvidenceGroundingStatus.GROUNDED for item in items)
    ungrounded_count = len(items) - grounded_count
    description_leakage_count = sum(item.status is AnalysisEvidenceGroundingStatus.DESCRIPTION_CONTEXT_LEAKAGE for item in items)
    warnings = []
    if ungrounded_count:
        warnings.append(f"{ungrounded_count} analyzer evidence item(s) could not be grounded in bounded context")
    if description_leakage_count:
        warnings.append(f"{description_leakage_count} analyzer evidence item(s) describe bounded-context facts outside their cited value")
    if _has_unproven_outcome_claim(analysis, request):
        warnings.append("analysis contains an outcome-success claim without an explicit bounded outcome artifact")
    return AnalysisEvidenceGroundingReport(
        total_count=len(items),
        grounded_count=grounded_count,
        ungrounded_count=ungrounded_count,
        description_leakage_count=description_leakage_count,
        items=items,
        warnings=warnings,
    )


def _ground_item(
    evidence_index: int,
    source: str,
    description: str,
    value: str | int | float | bool | None,
    scalar_index: list[tuple[str, str]],
    *,
    request: LLMAnalysisRequest,
) -> AnalysisEvidenceGroundingItem:
    source_prefixes = _source_prefixes(source, scalar_index, request=request)
    if source_prefixes is None:
        return _item(
            evidence_index,
            source,
            AnalysisEvidenceGroundingStatus.SOURCE_MISMATCH,
            reason="evidence source is not a recognized bounded-context section or path",
        )
    if value is None or (isinstance(value, str) and not value.strip()):
        return _item(
            evidence_index,
            source,
            AnalysisEvidenceGroundingStatus.MISSING_VALUE,
            reason="evidence value is empty and cannot prove a factual claim",
        )

    candidates = [(path, candidate) for path, candidate in scalar_index if any(_path_matches(path, prefix) for prefix in source_prefixes)]
    matched_paths = _matching_paths(value, candidates)
    if matched_paths:
        foreign_description_paths = _foreign_description_context_paths(
            description,
            value,
            source,
            scalar_index,
        )
        if foreign_description_paths:
            return _item(
                evidence_index,
                source,
                AnalysisEvidenceGroundingStatus.DESCRIPTION_CONTEXT_LEAKAGE,
                matched_paths=matched_paths,
                foreign_description_paths=foreign_description_paths,
                reason=("evidence value was grounded, but its description also cites bounded-context facts outside the quoted value"),
            )
        return _item(
            evidence_index,
            source,
            AnalysisEvidenceGroundingStatus.GROUNDED,
            matched_paths=matched_paths,
            reason=(
                "encoded-omission marker was found in the declared bounded-context source; it grounds only the visible field presence, encoding kind, and omission metadata"
                if isinstance(value, str) and _ENCODED_OMISSION_MARKER_RE.search(value)
                else "evidence value was found in the declared bounded-context source"
            ),
        )
    return _item(
        evidence_index,
        source,
        AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND,
        reason="evidence value was not found in the declared bounded-context source",
    )


def _foreign_description_context_paths(
    description: str,
    value: str | int | float | bool,
    source: str,
    scalar_index: list[tuple[str, str]],
) -> list[str]:
    """Find distinctive bounded facts mentioned outside one evidence value."""

    foreign_paths: list[str] = []
    for path, candidate in scalar_index:
        if _is_synthetic_description_audit_path(path):
            continue
        if not _is_distinctive_description_fact(path, candidate):
            continue
        if not _description_affirms_fact(description, candidate):
            continue
        if _description_contains_fact(str(value), candidate):
            continue
        if _description_contains_fact(source, candidate):
            continue
        foreign_paths.append(path)

    for path, _candidate in scalar_index:
        for token in _DISTINCTIVE_PATH_TOKEN_RE.findall(path):
            if not _description_contains_fact(description, token):
                continue
            if _description_contains_fact(str(value), token):
                continue
            if _description_contains_fact(source, token):
                continue
            foreign_paths.append(path)
    return list(dict.fromkeys(foreign_paths))[:10]


def _is_synthetic_description_audit_path(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
    return leaf == "key"


def _is_distinctive_description_fact(path: str, value: str) -> bool:
    normalized = _normalize_search_text(value)
    if not normalized or normalized in _DESCRIPTION_FACT_STOPLIST:
        return False
    if normalized in _SHORT_DESCRIPTION_FACTS:
        return True
    compact = normalized.replace(" ", "")
    if compact.isdigit():
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
        if "port" in leaf:
            return 0 <= int(compact) <= 65535
        return len(compact) >= 3
    if any("\u4e00" <= char <= "\u9fff" for char in compact):
        return len(compact) >= 3
    if any(char.isdigit() for char in compact):
        return len(compact) >= 4
    if any(char in value for char in "./:@_-"):
        return len(compact) >= 5
    return len(compact) >= 8


def _description_affirms_fact(text: str, fact: str) -> bool:
    normalized_text = _normalize_search_text(text)
    normalized_fact = _normalize_search_text(fact)
    if not normalized_text or not normalized_fact:
        return False

    if " " in normalized_fact or any("\u4e00" <= char <= "\u9fff" for char in normalized_fact):
        matches = re.finditer(re.escape(normalized_fact), normalized_text)
    else:
        matches = re.finditer(
            rf"(?<!\w){re.escape(normalized_fact)}(?!\w)",
            normalized_text,
        )
    for match in matches:
        index = match.start()
        prefix = normalized_text[max(0, index - 24) : index]
        suffix = normalized_text[index + len(normalized_fact) : index + len(normalized_fact) + 24]
        surrounding = f"{prefix} {suffix}"
        if not any(disclaimer in surrounding for disclaimer in _DESCRIPTION_FACT_DISCLAIMERS):
            return True
    return False


def _description_contains_fact(text: str, fact: str) -> bool:
    normalized_text = _normalize_search_text(text)
    normalized_fact = _normalize_search_text(fact)
    if not normalized_text or not normalized_fact:
        return False
    if " " in normalized_fact or any("\u4e00" <= char <= "\u9fff" for char in normalized_fact):
        return normalized_fact in normalized_text
    return (
        re.search(
            rf"(?<!\w){re.escape(normalized_fact)}(?!\w)",
            normalized_text,
        )
        is not None
    )


def _source_prefixes(
    source: str,
    scalar_index: list[tuple[str, str]],
    *,
    request: LLMAnalysisRequest,
) -> tuple[str, ...] | None:
    if _COMPOSITE_SEPARATOR_RE.search(source):
        return None
    normalized = source.strip().lower().replace(" ", "_")
    aliases = _SOURCE_PREFIXES.get(normalized)
    if aliases is not None:
        return aliases
    evidence_prefixes = _source_evidence_prefixes(source.strip(), request)
    if evidence_prefixes:
        return evidence_prefixes
    for alias, prefixes in _SOURCE_PREFIXES.items():
        if normalized.startswith(f"{alias}."):
            suffix = normalized[len(alias) :]
            return tuple(f"{prefix}{suffix}" for prefix in prefixes)
    exact_path = source.strip()
    if any(_path_matches(path, exact_path) for path, _ in scalar_index):
        return (exact_path,)
    return None


def _has_unproven_outcome_claim(
    analysis: AnalysisResult,
    request: LLMAnalysisRequest,
) -> bool:
    analysis_text = " ".join(
        [
            analysis.summary,
            analysis.reason,
            analysis.recommended_action,
            *(item.description for item in analysis.evidence),
            *(item.rationale for item in analysis.scenario_assessments),
            *(explanation for item in analysis.scenario_assessments for explanation in item.competing_explanations),
        ]
    )
    claims_confirmed_impact = any(item.activity_stage is TriageActivityStage.IMPACT_CONFIRMED for item in analysis.scenario_assessments)
    if not claims_confirmed_impact and not _contains_positive_outcome_claim(analysis_text):
        return False
    context = project_analysis_context(request)
    keys = _mapping_keys(context)
    return not (bool(keys & _OUTCOME_ARTIFACT_KEYS) or _has_bounded_provider_outcome_assertion(request))


def _has_bounded_provider_outcome_assertion(request: LLMAnalysisRequest) -> bool:
    """Accept adapter-declared outcomes only when the exact high-trust value is model-visible."""

    evidence_items = [item for item in (request.primary_evidence, *request.supplementary_evidence) if item is not None and item.trust_level is EvidenceTrustLevel.HIGH]
    for semantic in request.source_field_semantics:
        if semantic.semantic_type != _PROVIDER_OUTCOME_SEMANTIC_TYPE or not semantic.participates_in_reasoning:
            continue
        for evidence in evidence_items:
            if semantic.field_path in evidence.projected_field_paths and semantic.field_path not in evidence.omitted_field_paths:
                return True
    return False


def _contains_positive_outcome_claim(value: str) -> bool:
    for match in _OUTCOME_CLAIM_RE.finditer(value):
        prefix = value[max(0, match.start() - 12) : match.start()]
        if any(term in prefix for term in _OUTCOME_NEGATION_TERMS):
            continue
        return True
    return False


def _mapping_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        keys = {str(key).lower() for key in value}
        for item in value.values():
            keys.update(_mapping_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_mapping_keys(item))
        return keys
    return set()


def _source_evidence_prefixes(
    source: str,
    request: LLMAnalysisRequest,
) -> tuple[str, ...]:
    evidence_items = []
    if request.primary_evidence is not None:
        evidence_items.append(("evidence.primary_evidence.content", request.primary_evidence))
    evidence_items.extend((f"evidence.supplementary_evidence[{index}].content", item) for index, item in enumerate(request.supplementary_evidence))
    for context_prefix, evidence in evidence_items:
        if source == evidence.source_path:
            return (context_prefix,)
        for namespace, content_root in (
            ("parsed", "fields"),
            ("decoded", "decoded_fields"),
            ("repaired", "repaired_fields"),
        ):
            source_prefix = f"{evidence.source_path}#{namespace}"
            if source.startswith(f"{source_prefix}."):
                suffix = source[len(source_prefix) + 1 :]
                if namespace == "parsed" and suffix.split(".", 1)[0] in {
                    "fields",
                    "decoded_fields",
                    "repaired_fields",
                }:
                    return (f"{context_prefix}#parsed.{suffix}",)
                return (f"{context_prefix}#parsed.{content_root}.{suffix}",)
    return ()


def _matching_paths(
    value: str | int | float | bool,
    candidates: list[tuple[str, str]],
) -> list[str]:
    normalized = _normalize_scalar(value)
    direct = [path for path, candidate in candidates if _candidate_matches(normalized, path=path, candidate=candidate)]
    if direct:
        return list(dict.fromkeys(direct))[:10]

    if isinstance(value, str):
        parts = [part for part in _COMPOSITE_SEPARATOR_RE.split(value) if part]
        if len(parts) > 1:
            part_paths: list[str] = []
            for part in parts:
                normalized_part = _normalize_scalar(part)
                matches = [
                    path
                    for path, candidate in candidates
                    if _candidate_matches(
                        normalized_part,
                        path=path,
                        candidate=candidate,
                    )
                ]
                if not matches:
                    return []
                part_paths.extend(matches)
            return list(dict.fromkeys(part_paths))[:10]
    return []


def _value_matches(value: str, candidate: str) -> bool:
    if value == candidate:
        return True
    if len(value) >= 4 and value in candidate:
        return True
    searchable_value = _normalize_search_text(value)
    searchable_candidate = _normalize_search_text(candidate)
    return len(searchable_value) >= 4 and searchable_value in searchable_candidate


def _candidate_matches(value: str, *, path: str, candidate: str) -> bool:
    if _value_matches(value, candidate):
        return True
    leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
    return _value_matches(value, f"{leaf}: {candidate}")


def _scalar_index(value: Any, *, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        result: list[tuple[str, str]] = []
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            result.extend(_scalar_index(item, path=child_path))
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(_scalar_index(item, path=f"{path}[{index}]"))
        return result
    if value is None:
        return []
    candidate = _normalize_groundable_scalar(value)
    return [(path, candidate)] if candidate else []


def _bounded_evidence_content_index(
    request: LLMAnalysisRequest,
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    evidence_items = []
    if request.primary_evidence is not None:
        evidence_items.append(("evidence.primary_evidence.content", request.primary_evidence.content))
    evidence_items.extend((f"evidence.supplementary_evidence[{index}].content", item.content) for index, item in enumerate(request.supplementary_evidence))
    for context_prefix, content in evidence_items:
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        result.extend(_scalar_index(parsed, path=f"{context_prefix}#parsed"))
    return result


def _normalize_scalar(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).strip().lower().split())


def _normalize_groundable_scalar(value: str | int | float | bool) -> str:
    return _normalize_scalar(value)


def _normalize_search_text(value: str) -> str:
    return " ".join(
        re.sub(
            r"[^\w\u4e00-\u9fff]+",
            " ",
            value.lower().replace("_", " "),
        ).split()
    )


def _path_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}.") or path.startswith(f"{prefix}[")


def _item(
    evidence_index: int,
    source: str,
    status: AnalysisEvidenceGroundingStatus,
    *,
    matched_paths: list[str] | None = None,
    foreign_description_paths: list[str] | None = None,
    reason: str,
) -> AnalysisEvidenceGroundingItem:
    return AnalysisEvidenceGroundingItem(
        evidence_index=evidence_index,
        source=source,
        status=status,
        matched_context_paths=matched_paths or [],
        foreign_description_context_paths=foreign_description_paths or [],
        reason=reason,
    )


__all__ = ["ground_analysis_evidence"]
