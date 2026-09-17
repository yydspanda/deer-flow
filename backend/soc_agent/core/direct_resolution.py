"""Reuse explicit operator authority without asking a model to reconfirm it."""

from __future__ import annotations

import logging

from soc_agent.contracts import (
    AnalysisRun,
    Decision,
    ServiceRequestContext,
    SocMemoryDecisionEffect,
    SocMemoryReviewEffect,
    SocOperationalDisposition,
    TenantPolicyEvaluationStatus,
    TenantPolicyMode,
    Verdict,
)
from soc_agent.contracts.schemas import SocDirectResolution
from soc_agent.core.service import SocMemoryService
from soc_agent.core.tenant_policy import SocTenantPolicyEvaluationService
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.retrieval import memory_context_item, memory_query_from_analysis_request

logger = logging.getLogger(__name__)


class SocDirectResolutionService:
    def __init__(self, *, tenant_policy_service: SocTenantPolicyEvaluationService | None = None, memory_service: SocMemoryService | None = None, profile_registry: SocMemoryProfileRegistry | None = None, environment: str | None = None):
        self._policy = tenant_policy_service
        self._memory = memory_service
        self._profiles = profile_registry or SocMemoryProfileRegistry()
        self._environment = environment

    def _policy_check(self, run):
        # Direct handling bypasses the request enricher that normally binds this scope.
        if run.llm_analysis_request is not None and self._environment is not None:
            run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"environment": self._environment})
        result = self._policy.evaluate(run, context=ServiceRequestContext(), before_analysis=True, persist=False) if self._policy else None
        if run.steps:
            run.steps[-1].metadata["policy_precheck"] = {
                "status": result.evaluation_status.value if result is not None else "disabled_or_unconfigured",
                "policy_id": result.policy_id if result is not None else None,
                "deferred_rule_ids": [item.rule_id for item in result.rule_evaluations if item.deferred] if result is not None else [],
            }
        return result

    def resolve_policy(self, run: AnalysisRun) -> SocDirectResolution | None:
        result = self._policy_check(run)
        if result is None or result.evaluation_status is not TenantPolicyEvaluationStatus.MATCHED or not result.auto_apply_allowed:
            return None
        if result.recommended_disposition not in {SocOperationalDisposition.IGNORED, SocOperationalDisposition.ESCALATED, SocOperationalDisposition.SUPPRESSED}:
            return None
        summary = result.summary
        decision = Decision(
            verdict=Verdict.UNKNOWN,
            suggested_action=(result.suggested_action or summary)[:1000],
            needs_review=False,
            reason=summary,
            policy_version="soc.direct_resolution.v1",
            confidence_explanation="企业策略已直接确定处置，未调用主模型，不生成模型风险判断或置信度。",
        )
        return SocDirectResolution(
            source_kind="tenant_policy",
            source_id=result.policy_id,
            source_version=result.policy_version,
            source_hash=result.policy_hash,
            selected_rule_id=result.selected_rule_id,
            decision=decision,
            disposition=result.recommended_disposition,
            summary=summary,
            policy_snapshot=result.model_dump(mode="json"),
        )

    def resolve_memory(self, run: AnalysisRun) -> SocDirectResolution | None:
        if self._memory is None or run.llm_analysis_request is None:
            return None
        policy = self._policy_check(run)
        if policy is not None and policy.policy_mode is TenantPolicyMode.ENFORCED and any(item.deferred for item in policy.rule_evaluations):
            return None
        request = run.llm_analysis_request
        if self._environment is not None:
            request = request.model_copy(update={"environment": self._environment})
        profile = self._profiles.resolve_request(request)
        request = request.model_copy(update={"memory_profile": {"profile_id": profile.identity.profile_id, "profile_version": profile.identity.profile_version, "feature_schema_version": profile.identity.feature_schema_version}})
        run.llm_analysis_request = request
        query = memory_query_from_analysis_request(request, profile=profile)
        try:
            result = self._memory.find_directive_records(query)
        except Exception as exc:  # noqa: BLE001 - unavailable memory leaves the ordinary analyzer available
            logger.warning("direct Memory retrieval unavailable for %s (%s)", run.alert_id, type(exc).__name__)
            if run.steps:
                run.steps[-1].metadata.update({"fallback_reason": "memory_retrieval_unavailable", "error_type": type(exc).__name__})
            return None
        eligible = []
        for match in result.matches:
            directive = match.record.decision_directive
            item = memory_context_item(match, query_facets=query.facets, retrieval_policy_version=result.policy_version)
            if (
                directive is None
                or directive.effect is not SocMemoryDecisionEffect.OVERRIDE
                or item.metadata.get("decision_directive_applicable") is not True
                or match.score < directive.minimum_match_score
                or any(not match.matched_facets.get(key) for key in directive.required_facet_keys)
            ):
                continue
            eligible.append((match.record, item))
        verdicts = {record.decision_directive.target_verdict for record, _ in eligible}
        run.direct_memory_conflicted = len(verdicts) > 1
        if run.steps:
            run.steps[-1].metadata.update(
                {
                    "eligible_override_count": len(eligible),
                    "target_verdicts": sorted(verdict.value for verdict in verdicts),
                    "inventory_budget_applied": False,
                    "conflicting_sources": [{"memory_id": item.memory_id, "version": item.version, "content_hash": item.content_hash, "target_verdict": item.decision_directive.target_verdict.value} for item, _ in eligible]
                    if run.direct_memory_conflicted
                    else [],
                }
            )
        # Inspect every eligible override before limiting the persisted reference catalog.
        if len(verdicts) != 1 or len(eligible) > 100 or verdicts & {Verdict.UNKNOWN, Verdict.NEEDS_REVIEW}:
            return None
        verdict = next(iter(verdicts))
        record = eligible[0][0]
        summary = record.business_lesson.conclusion if record.business_lesson else record.content
        disposition = SocOperationalDisposition.IGNORED if verdict is Verdict.FALSE_POSITIVE else SocOperationalDisposition.ESCALATED
        requires_transfer = any(item.decision_directive.review_effect is SocMemoryReviewEffect.REQUIRE for item, _ in eligible)
        if requires_transfer:
            disposition = SocOperationalDisposition.ESCALATED
        action = record.decision_directive.suggested_action or ("沿用审核经验，忽略本次告警。" if disposition is SocOperationalDisposition.IGNORED else "沿用审核经验，转交处置。")
        if requires_transfer:
            action = "按经验审核要求直接转交复核。"
        decision = Decision(verdict=verdict, suggested_action=action, needs_review=False, reason=summary, policy_version="soc.direct_resolution.v1", confidence_explanation="直接复用已审核经验；未调用主模型，不生成模型置信度。")
        items = [item for _, item in eligible]
        resolution = SocDirectResolution(
            source_kind="memory",
            source_id=record.memory_id,
            source_version=str(record.version),
            source_hash=record.content_hash,
            decision=decision,
            disposition=disposition,
            summary=summary[:4000],
            memory_refs=[item.context_ref for item in items],
            matched_conditions={item.source_id: item.metadata for item in items},
        )
        run.llm_analysis_request = request.model_copy(update={"context_catalog": items, "memory_context_exclusions": []})
        return resolution
