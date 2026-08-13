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
    EvidenceTrustLevel,
    LLMAnalysisRequest,
    TriageActivityStage,
)
from soc_agent.pipeline.analysis_context import project_analysis_context

_ENCODED_OMISSION_MARKER_RE = re.compile(
    r"<ENCODED:[a-z0-9_]+:\d+:sha256=[a-f0-9]{12}:OMITTED>",
    re.IGNORECASE,
)
_OUTCOME_CLAIM_RE = re.compile(
    r"(?:攻击成功|利用成功|成功利用|写入成功|执行成功|已写入|已执行|已入侵|已攻陷|"
    r"successful(?:ly)?\s+(?:exploit|execut|writ)|confirmed\s+compromise|command\s+executed|file\s+(?:was\s+)?written)",
    re.IGNORECASE,
)
_OUTCOME_NEGATION_TERMS = (
    "未",
    "无",
    "无法",
    "不能",
    "是否",
    "尚未",
    "没有",
    "缺少",
    "缺乏",
    "不",
)
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
    if _has_unproven_outcome_claim(analysis, request):
        warnings.append("analysis contains an outcome-success claim without an explicit bounded outcome artifact")
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


def _has_unproven_outcome_claim(
    analysis: AnalysisResult,
    request: LLMAnalysisRequest,
) -> bool:
    analysis_text = " ".join(
        [
            analysis.summary,
            analysis.reason,
            analysis.recommended_action,
            *(item.statement for item in analysis.reasoning),
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


def _has_bounded_provider_outcome_assertion(
    request: LLMAnalysisRequest,
) -> bool:
    if any(item.semantic_type == _PROVIDER_OUTCOME_SEMANTIC_TYPE and item.trust_level is EvidenceTrustLevel.HIGH for item in request.evidence_highlights):
        return True
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
