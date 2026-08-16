"""Governed SOC memory retrieval and bounded Runtime projection helpers."""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    LLMAnalysisRequest,
    SocMemoryApplicabilityStatus,
    SocMemoryDecisionImpact,
    SocMemoryMatch,
    SocMemoryQuery,
    SocMemoryRetrievalDiff,
    SocMemoryRetrievalResult,
)
from soc_agent.memory.profiles import (
    GenericSocMemoryProfile,
    SocMemoryProfile,
    SocMemoryProfileRegistry,
)
from soc_agent.utils.hashing import stable_hash

logger = logging.getLogger(__name__)

MEMORY_RETRIEVAL_POLICY_V1 = "soc.memory_retrieval_policy.v1"
MEMORY_RETRIEVAL_POLICY_V2 = "soc.memory_retrieval_policy.v2"


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

        memory_items = [_memory_context_item(match, retrieval_policy_version=result.policy_version) for match in result.matches]
        return request.model_copy(update={"context_catalog": _dedupe_context_items([*request.context_catalog, *memory_items])})


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


def _memory_context_item(
    match: SocMemoryMatch,
    *,
    retrieval_policy_version: str,
) -> AnalysisContextCatalogItem:
    record = match.record
    projected_summary = _bounded_memory_summary(record.summary, record.content)
    applicability_status = match.applicability_report.status if match.applicability_report is not None else None
    context_only = bool(match.applicability_report is not None and (match.applicability_report.context_only_allowed or applicability_status is SocMemoryApplicabilityStatus.LEGACY_ANCHOR_ONLY))
    directive_applicable = bool(
        record.decision_directive is not None and record.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION and record.applicability is not None and applicability_status is SocMemoryApplicabilityStatus.APPLICABLE
    )
    if context_only:
        projected_summary = (f"[Context only / 仅作相似模式参考：该 Memory 不具备当前告警的确定性改判权限]\n{projected_summary}")[:4000]
    projection_hash = stable_hash(
        {
            "memory_id": match.memory_id,
            "version": match.version,
            "summary": projected_summary,
            "content_hash": match.content_hash,
            "facets_hash": match.facets_hash,
        }
    )
    return AnalysisContextCatalogItem(
        context_ref=f"M-{projection_hash[:12].upper()}",
        kind=AnalysisContextReferenceKind.CONFIRMED_MEMORY,
        label=record.summary[:256],
        source_id=f"{match.memory_id}@v{match.version}",
        summary=projected_summary,
        content_hash=projection_hash,
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
            "decision_directive_present": record.decision_directive is not None,
            "decision_directive_applicable": directive_applicable,
        },
    )


def _bounded_memory_summary(summary: str, content: str) -> str:
    prefix = summary.strip()
    body = content.strip()
    combined = f"{prefix}\n{body}" if body and body != prefix else prefix
    return combined[:4000]


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
