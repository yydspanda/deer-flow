"""Deterministically validate current facts and model reasoning references."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import (
    AnalysisContextReferenceKind,
    AnalysisEvidenceGroundingItem,
    AnalysisEvidenceGroundingReport,
    AnalysisEvidenceGroundingStatus,
    AnalysisReasoningBasis,
    AnalysisReasoningGroundingItem,
    AnalysisResult,
    LLMAnalysisRequest,
)

_ENCODED_OMISSION_MARKER_RE = re.compile(
    r"<ENCODED:[a-z0-9_]+:\d+:sha256=[a-f0-9]{12}:OMITTED>",
    re.IGNORECASE,
)
_CONTEXT_KIND_BASIS = {
    AnalysisContextReferenceKind.SKILL: AnalysisReasoningBasis.SKILL,
    AnalysisContextReferenceKind.ADAPTER_CONTRACT: AnalysisReasoningBasis.ADAPTER_CONTRACT,
    AnalysisContextReferenceKind.CONFIRMED_MEMORY: AnalysisReasoningBasis.CONFIRMED_MEMORY,
    AnalysisContextReferenceKind.GOVERNED_CONTEXT: AnalysisReasoningBasis.GOVERNED_CONTEXT,
    AnalysisContextReferenceKind.TOOL_RESULT: AnalysisReasoningBasis.TOOL_RESULT,
}


def ground_analysis_evidence(
    analysis: AnalysisResult,
    request: LLMAnalysisRequest,
) -> AnalysisEvidenceGroundingReport:
    """Validate exact E-* facts, then validate R-* reference integrity.

    This intentionally does not compare inference prose to raw alert strings.
    General security reasoning is allowed when it cites grounded current facts
    and declares its basis. Tenant, memory and provider claims additionally
    require their governed context namespace.
    """

    evidence_catalog = {item.evidence_ref: item for item in request.evidence_catalog}
    context_catalog = {item.context_ref: item for item in request.context_catalog}
    items = [_ground_evidence_item(index, evidence, evidence_catalog) for index, evidence in enumerate(analysis.evidence)]
    grounded_evidence_refs = {item.evidence_ref for item in items if item.status is AnalysisEvidenceGroundingStatus.GROUNDED}
    reasoning_items = [
        _ground_reasoning_item(
            index,
            reasoning,
            grounded_evidence_refs=grounded_evidence_refs,
            context_catalog=context_catalog,
        )
        for index, reasoning in enumerate(analysis.reasoning)
    ]

    grounded_count = sum(item.status is AnalysisEvidenceGroundingStatus.GROUNDED for item in items)
    reasoning_grounded_count = sum(item.status is AnalysisEvidenceGroundingStatus.GROUNDED for item in reasoning_items)
    ungrounded_count = len(items) - grounded_count
    reasoning_ungrounded_count = len(reasoning_items) - reasoning_grounded_count
    warnings: list[str] = []
    if ungrounded_count:
        warnings.append(f"{ungrounded_count} analyzer evidence item(s) failed E-* catalog validation")
    if reasoning_ungrounded_count:
        warnings.append(f"{reasoning_ungrounded_count} analyzer reasoning item(s) contain unresolved references")
    return AnalysisEvidenceGroundingReport(
        total_count=len(items),
        grounded_count=grounded_count,
        ungrounded_count=ungrounded_count,
        description_leakage_count=0,
        items=items,
        reasoning_total_count=len(reasoning_items),
        reasoning_grounded_count=reasoning_grounded_count,
        reasoning_ungrounded_count=reasoning_ungrounded_count,
        reasoning_items=reasoning_items,
        warnings=warnings,
    )


def _ground_evidence_item(
    evidence_index: int,
    evidence: Any,
    catalog: Mapping[str, Any],
) -> AnalysisEvidenceGroundingItem:
    evidence_ref = evidence.evidence_ref
    if evidence_ref is None or evidence_ref not in catalog:
        return _evidence_item(
            evidence_index,
            evidence_ref or "E-000000000000",
            evidence.source,
            AnalysisEvidenceGroundingStatus.REFERENCE_NOT_FOUND,
            reason="evidence_ref does not exist in the current-alert catalog",
        )
    catalog_item = catalog[evidence_ref]
    if evidence.source != catalog_item.source_path:
        return _evidence_item(
            evidence_index,
            evidence_ref,
            evidence.source,
            AnalysisEvidenceGroundingStatus.SOURCE_MISMATCH,
            matched_paths=[catalog_item.source_path],
            reason="evidence source does not match the source_path bound to evidence_ref",
        )
    if not _same_scalar(evidence.value, catalog_item.value):
        return _evidence_item(
            evidence_index,
            evidence_ref,
            evidence.source,
            AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND,
            matched_paths=[catalog_item.source_path],
            reason="evidence value does not exactly match the scalar bound to evidence_ref",
        )
    encoded_marker = isinstance(evidence.value, str) and _ENCODED_OMISSION_MARKER_RE.search(evidence.value)
    return _evidence_item(
        evidence_index,
        evidence_ref,
        evidence.source,
        AnalysisEvidenceGroundingStatus.GROUNDED,
        matched_paths=[catalog_item.source_path],
        reason=(
            "E-* reference matches an encoded-omission marker; it proves only visible presence, encoding shape and boundary omission"
            if encoded_marker
            else "E-* reference, source path and scalar value exactly match the current-alert catalog"
        ),
    )


def _ground_reasoning_item(
    reasoning_index: int,
    reasoning: Any,
    *,
    grounded_evidence_refs: set[str],
    context_catalog: Mapping[str, Any],
) -> AnalysisReasoningGroundingItem:
    missing_refs = [ref for ref in reasoning.evidence_refs if ref not in grounded_evidence_refs]
    missing_refs.extend(ref for ref in reasoning.context_refs if ref not in context_catalog)
    if missing_refs:
        return AnalysisReasoningGroundingItem(
            reasoning_index=reasoning_index,
            reasoning_id=reasoning.reasoning_id,
            status=AnalysisEvidenceGroundingStatus.REFERENCE_NOT_FOUND,
            evidence_refs=reasoning.evidence_refs,
            context_refs=reasoning.context_refs,
            missing_refs=list(dict.fromkeys(missing_refs)),
            reason="reasoning references missing or ungrounded evidence/context",
        )

    unsupported_refs = [ref for ref in reasoning.context_refs if _CONTEXT_KIND_BASIS[context_catalog[ref].kind] not in reasoning.basis]
    if unsupported_refs:
        return AnalysisReasoningGroundingItem(
            reasoning_index=reasoning_index,
            reasoning_id=reasoning.reasoning_id,
            status=AnalysisEvidenceGroundingStatus.UNSUPPORTED_REFERENCE,
            evidence_refs=reasoning.evidence_refs,
            context_refs=reasoning.context_refs,
            missing_refs=unsupported_refs,
            reason="reasoning context namespace is present but its basis was not declared",
        )
    return AnalysisReasoningGroundingItem(
        reasoning_index=reasoning_index,
        reasoning_id=reasoning.reasoning_id,
        status=AnalysisEvidenceGroundingStatus.GROUNDED,
        evidence_refs=reasoning.evidence_refs,
        context_refs=reasoning.context_refs,
        reason=("all reasoning references resolve; inference semantics remain model reasoning, not a literal current-alert fact"),
    )


def _same_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _evidence_item(
    evidence_index: int,
    evidence_ref: str,
    source: str,
    status: AnalysisEvidenceGroundingStatus,
    *,
    matched_paths: list[str] | None = None,
    reason: str,
) -> AnalysisEvidenceGroundingItem:
    return AnalysisEvidenceGroundingItem(
        evidence_index=evidence_index,
        evidence_ref=evidence_ref,
        source=source,
        status=status,
        matched_context_paths=matched_paths or [],
        foreign_description_context_paths=[],
        reason=reason,
    )


__all__ = ["ground_analysis_evidence"]
