"""Governed SOC memory retrieval and bounded Runtime projection helpers."""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    AnalysisMemoryContextComparison,
    AnalysisMemoryContextExclusion,
    AnalysisMemoryUseMode,
    LLMAnalysisRequest,
    SocMemoryApplicabilityStatus,
    SocMemoryDecisionImpact,
    SocMemoryMatch,
    SocMemoryQuery,
    SocMemoryRetrievalDiff,
    SocMemoryRetrievalResult,
    Verdict,
)
from soc_agent.memory.profiles import (
    GenericSocMemoryProfile,
    SocMemoryProfile,
    SocMemoryProfileRegistry,
)
from soc_agent.memory.scoring import normalize_memory_facets
from soc_agent.utils.hashing import stable_hash

logger = logging.getLogger(__name__)

MEMORY_RETRIEVAL_POLICY_V1 = "soc.memory_retrieval_policy.v1"
MEMORY_RETRIEVAL_POLICY_V2 = "soc.memory_retrieval_policy.v2"
_MAX_MODEL_COMPARISON_FACETS = 24
_MAX_MODEL_COMPARISON_VALUES = 8
_MODEL_COMPARISON_FACET_PRIORITY = (
    "detection_key",
    "rule_code",
    "rule_name",
    "detection_signature",
    "environment",
    "source_type",
    "source_system",
    "product",
    "category",
    "scenario_key",
    "behavior_strength",
    "attack_behavior_family",
    "network_service",
    "vulnerability_id",
    "behavior_component_strong",
    "behavior_component_core",
    "behavior_component",
    "role_entity",
    "entity",
    "behavior_fingerprint",
)


class MemoryRetrievalPort(Protocol):
    """Minimal retrieval boundary needed by the fixed Runtime."""

    def find_relevant_records(self, query: SocMemoryQuery) -> SocMemoryRetrievalResult: ...


class ConfirmedMemoryAnalysisRequestEnricher:
    """Project governed confirmed memory into the model's M-* context catalog."""

    def __init__(
        self,
        retriever: MemoryRetrievalPort,
        *,
        profile_registry: SocMemoryProfileRegistry | None = None,
        environment: str | None = None,
    ) -> None:
        self._retriever = retriever
        self._profile_registry = profile_registry or SocMemoryProfileRegistry()
        normalized_environment = environment.strip().casefold() if environment is not None else None
        if environment is not None and not normalized_environment:
            raise ValueError("memory environment must not be blank")
        self._environment = normalized_environment

    def __call__(self, request: LLMAnalysisRequest) -> LLMAnalysisRequest:
        if self._environment is not None:
            # Environment is an operator-owned applicability boundary. Never
            # let an alert payload select a broader or different memory scope.
            request = request.model_copy(update={"environment": self._environment})
        profile = self._profile_registry.resolve_request(request)
        query = memory_query_from_analysis_request(request, profile=profile)
        try:
            result = self._retriever.find_relevant_records(query)
        except Exception as exc:  # noqa: BLE001 - memory is optional context, not an analysis availability gate
            logger.warning(
                "confirmed memory retrieval failed for alert %s (%s)",
                request.alert_id,
                type(exc).__name__,
            )
            warning = f"confirmed memory retrieval unavailable ({type(exc).__name__})"
            return request.model_copy(update={"warnings": _dedupe_strings([*request.warnings, warning])})

        memory_items = [
            memory_context_item(
                match,
                query_facets=result.query.facets,
                retrieval_policy_version=result.policy_version,
            )
            for match in result.matches
        ]
        selected, exclusions = _select_memory_reasoning_context(_dedupe_context_items([*request.context_catalog, *memory_items]))
        return request.model_copy(update={"context_catalog": selected, "memory_context_exclusions": exclusions})


def _select_memory_reasoning_context(
    items: list[AnalysisContextCatalogItem],
) -> tuple[list[AnalysisContextCatalogItem], list[AnalysisMemoryContextExclusion]]:
    # Inspect the whole bounded set before filtering; conflicting exact answers
    # must not be resolved by record order, recency, or retrieval score.
    exact = [
        item
        for item in items
        if (comparison := item.memory_comparison) is not None and comparison.applicability_status is SocMemoryApplicabilityStatus.APPLICABLE and not comparison.missing_required_facet_keys and not comparison.excluded_facet_hits
    ]
    definite = {Verdict.FALSE_POSITIVE, Verdict.TRUE_POSITIVE}
    scope_keys = {"detection_key", "detection_signature", "behavior_fingerprint", "scenario_key"}
    # Display comparisons truncate values; selection must use the full matcher report.
    scopes = {}
    for item in items:
        report = item.metadata.get("applicability_report")
        scopes[item.context_ref] = normalize_memory_facets(report.get("matched_required_facets", {})) if isinstance(report, dict) else {}
    selected: list[AnalysisContextCatalogItem] = []
    exclusions: list[AnalysisMemoryContextExclusion] = []
    for item in items:
        comparison = item.memory_comparison
        if comparison is None or comparison.applicability_status is not SocMemoryApplicabilityStatus.PARTIAL or comparison.reviewed_verdict not in definite:
            selected.append(item)
            continue
        required = scopes[item.context_ref]
        relevant = [other for other in exact if any(required.get(key, set()) & scopes[other.context_ref].get(key, set()) for key in scope_keys)]
        verdicts = {other.memory_comparison.reviewed_verdict for other in relevant}
        preferred = [other for other in relevant if scopes[other.context_ref].get("behavior_fingerprint") or other.memory_comparison.selected_behavior_components]
        if not preferred or len(verdicts) != 1 or not verdicts <= definite or comparison.reviewed_verdict in verdicts:
            selected.append(item)
            continue
        exclusions.append(
            AnalysisMemoryContextExclusion(
                source_id=item.source_id,
                summary=item.summary[:2000],
                record_content_hash=item.metadata.get("record_content_hash"),
                record_facets_hash=item.metadata.get("record_facets_hash"),
                memory_comparison=comparison,
                preferred_source_ids=sorted({other.source_id for other in preferred}),
                explanation="当前行为已有精确匹配的审核经验；这条相似经验结论相反且匹配条件不完整，仅保留作差异审计，不进入本次模型上下文。",
            )
        )
    return selected, exclusions


def memory_query_from_analysis_request(
    request: LLMAnalysisRequest,
    *,
    profile: SocMemoryProfile | None = None,
    policy_version: Literal[
        "soc.memory_retrieval_policy.v1",
        "soc.memory_retrieval_policy.v2",
    ] = MEMORY_RETRIEVAL_POLICY_V2,
) -> SocMemoryQuery:
    """Build a vendor-neutral pre-LLM query from canonical request dimensions."""

    resolved_profile = profile or GenericSocMemoryProfile()
    facets = resolved_profile.project_query_facets(request)

    text_terms: list[str] = []
    for value in (
        request.detection.rule_name,
        request.detection.rule_category,
        request.classification.category,
    ):
        text_terms.extend(_memory_text_terms(value))

    tenant_scope = request.tenant_id or "global"
    return SocMemoryQuery(
        policy_version=policy_version,
        tenant_scope=tenant_scope,
        tenant_id=request.tenant_id,
        facets=facets,
        text_terms=_dedupe_strings(text_terms),
        limit=5,
        max_tokens=900,
        metadata={
            "source": "fixed_runtime_pre_llm",
            "alert_id": request.alert_id,
            "memory_profile_id": resolved_profile.identity.profile_id,
            "memory_profile_version": resolved_profile.identity.profile_version,
            "memory_feature_schema_version": resolved_profile.identity.feature_schema_version,
            "strong_anchor_keys_present": sorted(
                set(facets)
                & {
                    "behavior_fingerprint",
                    "detection_key",
                    "detection_signature",
                    "entity",
                    "integration_name",
                    "role_entity",
                    "rule_code",
                    "scenario_key",
                    "skill",
                    "source_system",
                }
            ),
        },
    )


def build_memory_retrieval_diff(
    baseline: SocMemoryRetrievalResult,
    current: SocMemoryRetrievalResult,
) -> SocMemoryRetrievalDiff:
    """Compare replay-stable match semantics while ignoring generated timestamps."""

    baseline_by_id = {match.memory_id: match for match in baseline.matches}
    current_by_id = {match.memory_id: match for match in current.matches}
    baseline_ids = set(baseline_by_id)
    current_ids = set(current_by_id)
    shared_ids = baseline_ids & current_ids
    changed_ids = sorted(memory_id for memory_id in shared_ids if _match_projection(baseline_by_id[memory_id]) != _match_projection(current_by_id[memory_id]))
    unchanged_ids = sorted(shared_ids - set(changed_ids))
    added_ids = sorted(current_ids - baseline_ids)
    removed_ids = sorted(baseline_ids - current_ids)
    return SocMemoryRetrievalDiff(
        baseline_policy_version=baseline.policy_version,
        current_policy_version=current.policy_version,
        added_memory_ids=added_ids,
        removed_memory_ids=removed_ids,
        changed_memory_ids=changed_ids,
        unchanged_memory_ids=unchanged_ids,
        changed=bool(added_ids or removed_ids or changed_ids),
    )


def _match_projection(match: SocMemoryMatch) -> dict[str, Any]:
    return {
        "memory_id": match.memory_id,
        "version": match.version,
        "score": match.score,
        "match_reasons": match.match_reasons,
        "matched_facets": match.matched_facets,
        "anchor_match_reasons": match.anchor_match_reasons,
        "matched_anchor_facets": match.matched_anchor_facets,
        "applicability_report": (match.applicability_report.model_dump(mode="json") if match.applicability_report is not None else None),
        "token_estimate": match.token_estimate,
        "content_hash": match.content_hash,
        "facets_hash": match.facets_hash,
    }


def memory_context_item(
    match: SocMemoryMatch,
    *,
    query_facets: dict[str, list[str]],
    retrieval_policy_version: str,
) -> AnalysisContextCatalogItem:
    record = match.record
    projected_summary = _memory_summary(record.summary, record.content)
    applicability_status = match.applicability_report.status if match.applicability_report is not None else None
    context_only = bool(match.applicability_report is not None and (match.applicability_report.context_only_allowed or applicability_status is SocMemoryApplicabilityStatus.LEGACY_ANCHOR_ONLY))
    directive_applicable = bool(
        record.decision_directive is not None and record.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION and record.applicability is not None and applicability_status is SocMemoryApplicabilityStatus.APPLICABLE
    )
    comparison = _memory_context_comparison(
        match,
        query_facets=query_facets,
        context_only=context_only,
        directive_applicable=directive_applicable,
    )
    if context_only:
        projected_summary = f"[Context-only reviewed experience / 受治理相似经验：可在比较共同条件、差异和失效条件后参与 Base Decision；不可直接应用确定性 Memory Directive 或授权动作]\n{projected_summary}"
    projection_hash = stable_hash(
        {
            "memory_id": match.memory_id,
            "version": match.version,
            "summary": projected_summary,
            "content_hash": match.content_hash,
            "facets_hash": match.facets_hash,
            "memory_comparison": comparison.model_dump(mode="json"),
        }
    )
    return AnalysisContextCatalogItem(
        context_ref=f"M-{projection_hash[:12].upper()}",
        kind=AnalysisContextReferenceKind.CONFIRMED_MEMORY,
        label=record.summary[:256],
        source_id=f"{match.memory_id}@v{match.version}",
        summary=projected_summary,
        content_hash=projection_hash,
        memory_comparison=comparison,
        metadata={
            "memory_id": match.memory_id,
            "memory_version": match.version,
            "retrieval_score": match.score,
            "retrieval_policy_version": retrieval_policy_version,
            "match_reasons": match.match_reasons[:20],
            "matched_facets": match.matched_facets,
            "applicability_status": (match.applicability_report.status.value if match.applicability_report is not None else None),
            "applicability_report": (match.applicability_report.model_dump(mode="json") if match.applicability_report is not None else None),
            "context_only": context_only,
            "record_content_hash": match.content_hash,
            "record_facets_hash": match.facets_hash,
            "business_lesson_schema_version": (record.business_lesson.schema_version if record.business_lesson is not None else None),
            "decision_directive_present": record.decision_directive is not None,
            "decision_directive_applicable": directive_applicable,
        },
    )


def _memory_context_comparison(
    match: SocMemoryMatch,
    *,
    query_facets: dict[str, list[str]],
    context_only: bool,
    directive_applicable: bool,
) -> AnalysisMemoryContextComparison:
    record_facets = _normalized_facets(match.record.facets)
    current_facets = _normalized_facets(query_facets)
    shared: dict[str, list[str]] = {}
    current_only: dict[str, list[str]] = {}
    memory_only: dict[str, list[str]] = {}
    for key in _ordered_comparison_keys(set(record_facets) | set(current_facets)):
        record_values = set(record_facets.get(key, []))
        current_values = set(current_facets.get(key, []))
        _add_bounded_facet(shared, key, record_values & current_values)
        _add_bounded_facet(current_only, key, current_values - record_values)
        _add_bounded_facet(memory_only, key, record_values - current_values)

    report = match.applicability_report
    use_mode = AnalysisMemoryUseMode.DIRECTIVE_APPLICABLE if directive_applicable else AnalysisMemoryUseMode.CONTEXT_ONLY if context_only else AnalysisMemoryUseMode.EXACT_CONTEXT
    return AnalysisMemoryContextComparison(
        use_mode=use_mode,
        applicability_status=report.status if report is not None else None,
        reviewed_verdict=match.record.reviewed_verdict,
        decision_directive_applicable=directive_applicable,
        shared_facets=shared,
        selected_behavior_components=(report.selected_behavior_components if report else [])[:40],
        missing_behavior_components=(report.missing_behavior_components if report else [])[:40],
        current_only_facets=current_only,
        memory_only_facets=memory_only,
        missing_reuse_conditions=[item.model_copy(update={"values": item.values[:_MAX_MODEL_COMPARISON_VALUES]}) for item in (report.missing_reuse_conditions[:_MAX_MODEL_COMPARISON_FACETS] if report is not None else [])],
        matched_required_facets=(_bounded_facets(report.matched_required_facets) if report is not None else {}),
        missing_required_facet_keys=(report.missing_required_facet_keys[:_MAX_MODEL_COMPARISON_FACETS] if report is not None else []),
        excluded_facet_hits=(_bounded_facets(report.excluded_facet_hits) if report is not None else {}),
        reason_codes=(report.reason_codes[:_MAX_MODEL_COMPARISON_FACETS] if report is not None else []),
    )


def _normalized_facets(facets: dict[str, list[str]]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for raw_key, raw_values in facets.items():
        key = str(raw_key).strip()
        if not key:
            continue
        values = sorted({str(value).strip() for value in raw_values if str(value).strip()})
        if values:
            normalized[key] = values
    return normalized


def _ordered_comparison_keys(keys: set[str]) -> list[str]:
    priority = {key: index for index, key in enumerate(_MODEL_COMPARISON_FACET_PRIORITY)}
    return sorted(keys, key=lambda key: (priority.get(key, len(priority)), key))[:_MAX_MODEL_COMPARISON_FACETS]


def _add_bounded_facet(
    target: dict[str, list[str]],
    key: str,
    values: set[str],
) -> None:
    if values:
        target[key] = sorted(values)[:_MAX_MODEL_COMPARISON_VALUES]


def _bounded_facets(facets: dict[str, list[str]]) -> dict[str, list[str]]:
    normalized = _normalized_facets(facets)
    return {key: normalized[key][:_MAX_MODEL_COMPARISON_VALUES] for key in _ordered_comparison_keys(set(normalized))}


def _memory_summary(summary: str, content: str) -> str:
    prefix = summary.strip()
    body = content.strip()
    combined = f"{prefix}\n{body}" if body and body != prefix else prefix
    return combined


def _memory_text_terms(text: str | None) -> list[str]:
    if not text:
        return []
    terms: list[str] = []
    for token in str(text).replace("/", " ").replace(",", " ").replace("，", " ").replace(":", " ").split():
        normalized = token.strip()
        if len(normalized) >= 3 and normalized not in terms:
            terms.append(normalized[:80])
        if len(terms) >= 12:
            break
    return terms


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _dedupe_context_items(
    items: list[AnalysisContextCatalogItem],
) -> list[AnalysisContextCatalogItem]:
    by_ref: dict[str, AnalysisContextCatalogItem] = {}
    for item in items:
        by_ref.setdefault(item.context_ref, item)
    return sorted(by_ref.values(), key=lambda item: item.context_ref)


__all__ = [
    "ConfirmedMemoryAnalysisRequestEnricher",
    "MEMORY_RETRIEVAL_POLICY_V1",
    "MEMORY_RETRIEVAL_POLICY_V2",
    "MemoryRetrievalPort",
    "build_memory_retrieval_diff",
    "memory_query_from_analysis_request",
]
