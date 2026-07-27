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
    LLMAnalysisRequest,
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
        "process_id",
        "shell_output",
        "write_result",
    }
)


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
            evidence.value,
            scalar_index,
            request=request,
        )
        for index, evidence in enumerate(analysis.evidence)
    ]
    grounded_count = sum(item.status is AnalysisEvidenceGroundingStatus.GROUNDED for item in items)
    ungrounded_count = len(items) - grounded_count
    warnings = []
    if ungrounded_count:
        warnings.append(f"{ungrounded_count} analyzer evidence item(s) could not be grounded in bounded context")
    if _has_unproven_outcome_claim(analysis, request):
        warnings.append("analysis contains an outcome-success claim without an explicit bounded outcome artifact")
    return AnalysisEvidenceGroundingReport(
        total_count=len(items),
        grounded_count=grounded_count,
        ungrounded_count=ungrounded_count,
        items=items,
        warnings=warnings,
    )


def _ground_item(
    evidence_index: int,
    source: str,
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
        return _item(
            evidence_index,
            source,
            AnalysisEvidenceGroundingStatus.GROUNDED,
            matched_paths=matched_paths,
            reason="evidence value was found in the declared bounded-context source",
        )
    return _item(
        evidence_index,
        source,
        AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND,
        reason="evidence value was not found in the declared bounded-context source",
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
        ]
    )
    if not _contains_positive_outcome_claim(analysis_text):
        return False
    context = project_analysis_context(request)
    keys = _mapping_keys(context)
    return not bool(keys & _OUTCOME_ARTIFACT_KEYS)


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
    if not isinstance(value, str):
        return _normalize_scalar(value)
    return _normalize_scalar(_ENCODED_OMISSION_MARKER_RE.sub(" ", value))


def _normalize_search_text(value: str) -> str:
    return " ".join(re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.lower()).split())


def _path_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}.") or path.startswith(f"{prefix}[")


def _item(
    evidence_index: int,
    source: str,
    status: AnalysisEvidenceGroundingStatus,
    *,
    matched_paths: list[str] | None = None,
    reason: str,
) -> AnalysisEvidenceGroundingItem:
    return AnalysisEvidenceGroundingItem(
        evidence_index=evidence_index,
        source=source,
        status=status,
        matched_context_paths=matched_paths or [],
        reason=reason,
    )


__all__ = ["ground_analysis_evidence"]
