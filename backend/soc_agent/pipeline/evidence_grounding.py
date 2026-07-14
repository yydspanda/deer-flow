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


def ground_analysis_evidence(
    analysis: AnalysisResult,
    request: LLMAnalysisRequest,
) -> AnalysisEvidenceGroundingReport:
    """Match every analyzer evidence value against the exact prompt projection."""

    context = project_analysis_context(request)
    scalar_index = _scalar_index(context)
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
        parsed_prefix = f"{evidence.source_path}#parsed"
        if source.startswith(f"{parsed_prefix}."):
            suffix = source[len(parsed_prefix) + 1 :]
            if suffix.split(".", 1)[0] in {
                "fields",
                "decoded_fields",
                "repaired_fields",
            }:
                return (f"{context_prefix}#parsed.{suffix}",)
            return tuple(f"{context_prefix}#parsed.{root}.{suffix}" for root in ("fields", "decoded_fields", "repaired_fields"))
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
    return [(path, _normalize_scalar(value))]


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
