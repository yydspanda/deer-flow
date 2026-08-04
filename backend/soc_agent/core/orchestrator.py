"""Main SOC orchestrator service for unified investigation reports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisRun,
    CorrelationQuery,
    CorrelationResult,
    EntrySurface,
    ServiceRequestContext,
    SocAgentChatRequest,
    SocDomainTriageRequest,
    SocEnrichmentPlan,
    SocMainOrchestratorRequest,
    SocOrchestratorActionSpec,
    SocOrchestratorReviewContextSummary,
    SocOrchestratorRouteStep,
    SocSkillContext,
    UnifiedInvestigationReport,
)
from soc_agent.core.evidence import InMemoryInvestigationEvidenceRepository
from soc_agent.domain import SocDomainTriageService
from soc_agent.protocols import (
    InvestigationEvidenceRepository,
    SocActionAdapterRegistryPort,
    SocEnrichmentPlannerPort,
)
from soc_agent.utils.hashing import stable_hash

from .correlation import InMemoryAlertSummaryRepository, SocCorrelationService
from .service import SocAgentActionDispatcher, SocAgentCapabilityRouter, SocAnalysisService


class SocMainOrchestratorService:
    """Assemble analysis, correlation, evidence, domain triage, and review summary."""

    def __init__(
        self,
        *,
        analysis_service: SocAnalysisService | None = None,
        action_adapter_registry: SocActionAdapterRegistryPort | None = None,
        action_dispatcher: SocAgentActionDispatcher | None = None,
        correlation_service: SocCorrelationService | None = None,
        domain_triage_service: SocDomainTriageService | None = None,
        evidence_repository: InvestigationEvidenceRepository | None = None,
        enrichment_planner: SocEnrichmentPlannerPort | None = None,
    ) -> None:
        self._evidence_repository = evidence_repository or InMemoryInvestigationEvidenceRepository()
        if analysis_service is None and correlation_service is None:
            summary_repository = InMemoryAlertSummaryRepository()
            self._analysis_service = SocAnalysisService(
                summary_repository=summary_repository,
            )
            self._correlation_service = SocCorrelationService(
                summary_repository=summary_repository,
                evidence_repository=self._evidence_repository,
            )
        elif analysis_service is None or correlation_service is None:
            raise ValueError("custom main-orchestrator wiring requires both analysis_service and correlation_service")
        else:
            self._analysis_service = analysis_service
            self._correlation_service = correlation_service
        self._action_dispatcher = action_dispatcher or SocAgentActionDispatcher(
            action_adapter_registry=action_adapter_registry,
            evidence_repository=self._evidence_repository,
        )
        self._domain_triage_service = domain_triage_service or SocDomainTriageService()
        self._enrichment_planner = enrichment_planner

    def run(
        self,
        request: SocMainOrchestratorRequest | Mapping[str, Any],
        *,
        context: ServiceRequestContext | None = None,
    ) -> UnifiedInvestigationReport:
        orchestrator_request = SocMainOrchestratorRequest.model_validate(request)
        request_context = context or _default_context(orchestrator_request)
        run = self._analysis_service.analyze(orchestrator_request.payload, context=request_context)
        skill_context = _skill_context_from_run(run)
        thread_id = orchestrator_request.thread_id or f"THR-{orchestrator_request.sample_id or run.run_id}"
        enrichment_plan = self._enrichment_planner.plan(run, thread_id=thread_id) if self._enrichment_planner is not None else None
        action_specs, planned_deduplicated_count = _merge_action_specs(
            orchestrator_request.action_specs,
            enrichment_plan,
        )
        route_steps = self._dispatch_read_only_actions(
            action_specs,
            run_id=run.run_id,
            alert_id=run.alert_id,
            thread_id=thread_id,
            sample_id=orchestrator_request.sample_id,
            context=request_context,
        )
        evidence = self._evidence_repository.list_evidence(thread_id=thread_id, limit=100)
        correlation_result = self._correlate(run)
        correlation_match_count = len(correlation_result.matches)
        reusable_evidence_count = correlation_result.reusable_evidence_count
        domain_result = self._domain_triage_service.triage(
            SocDomainTriageRequest(
                run=run,
                skill_context=skill_context,
                investigation_evidence=evidence,
                correlation_result=correlation_result,
                capability_card_refs=orchestrator_request.capability_card_refs,
                metadata={
                    "sample_id": orchestrator_request.sample_id,
                    "thread_id": thread_id,
                    "source": "soc_main_orchestrator",
                    "similar_alert_count": correlation_match_count,
                    "correlation_match_count": correlation_match_count,
                },
            )
        )
        review_context = _review_context_summary(
            run,
            action_evidence_count=len(evidence),
            correlation_match_count=correlation_match_count,
            reusable_evidence_count=reusable_evidence_count,
            domain_finding_count=sum(len(item.findings) for item in [domain_result]),
        )
        return UnifiedInvestigationReport(
            sample_id=orchestrator_request.sample_id,
            run=run,
            skill_context=skill_context,
            enrichment_plan=enrichment_plan,
            route_steps=route_steps,
            investigation_evidence=evidence,
            correlation_result=correlation_result,
            domain_triage_results=[domain_result],
            review_context=review_context,
            metadata={
                **orchestrator_request.metadata,
                "thread_id": thread_id,
                "route_step_count": len(route_steps),
                "planned_enrichment_enabled": enrichment_plan is not None,
                "planned_enrichment_action_count": (len(enrichment_plan.actions) if enrichment_plan is not None else 0),
                "planned_enrichment_deduplicated_count": planned_deduplicated_count,
                "correlation_enabled": True,
                "correlation_match_count": correlation_match_count,
                "reusable_correlation_evidence_count": reusable_evidence_count,
                "handler_output_only": True,
                "writes_db": False,
                "executes_high_risk_actions": False,
            },
        )

    def _correlate(self, run: AnalysisRun) -> CorrelationResult:
        return self._correlation_service.correlate(
            CorrelationQuery(
                run_id=run.run_id,
                limit=10,
                candidate_limit=200,
                evidence_limit_per_match=5,
            )
        )

    def _dispatch_read_only_actions(
        self,
        action_specs: list[SocOrchestratorActionSpec],
        *,
        run_id: str,
        alert_id: str,
        thread_id: str,
        sample_id: str | None,
        context: ServiceRequestContext,
    ) -> list[SocOrchestratorRouteStep]:
        if not action_specs:
            return []
        router = SocAgentCapabilityRouter(allowed_routes={item.route for item in action_specs})
        route_steps: list[SocOrchestratorRouteStep] = []
        for index, spec in enumerate(action_specs):
            chat_request = _chat_request_for_action_spec(
                spec,
                run_id=run_id,
                alert_id=alert_id,
                thread_id=thread_id,
                sample_id=sample_id,
                index=index,
            )
            route_decision = router.route(chat_request)
            permission_decision = self._action_dispatcher.check_permission(
                chat_request,
                route_decision,
                context=context,
            )
            result = self._action_dispatcher.dispatch(
                chat_request,
                route_decision,
                context=context,
                permission_decision=permission_decision,
            )
            evidence_id = result.payload.get("evidence_id")
            route_steps.append(
                SocOrchestratorRouteStep(
                    route=result.route,
                    action=result.action,
                    status=result.status,
                    message=result.message,
                    evidence_id=evidence_id if isinstance(evidence_id, str) else None,
                    payload=result.payload,
                    origin=spec.origin,
                    plan_action_id=spec.plan_action_id,
                )
            )
        return route_steps


def _chat_request_for_action_spec(
    spec: SocOrchestratorActionSpec,
    *,
    run_id: str,
    alert_id: str,
    thread_id: str,
    sample_id: str | None,
    index: int,
) -> SocAgentChatRequest:
    payload = dict(spec.payload)
    context_refs = dict(payload.get("context_refs")) if isinstance(payload.get("context_refs"), Mapping) else {}
    context_refs.setdefault("alert_id", alert_id)
    context_refs.setdefault("run_id", run_id)
    context_refs.setdefault("thread_id", thread_id)
    context_refs.setdefault("proposal_id", f"PA11-{sample_id or run_id}-{index + 1}")
    if spec.plan_action_id is not None:
        context_refs.setdefault("enrichment_action_id", spec.plan_action_id)
    payload["context_refs"] = context_refs
    return SocAgentChatRequest(
        message=f"Run orchestrator read-only action {spec.route}",
        thread_id=thread_id,
        run_id=run_id,
        allowed_routes=[spec.route],
        metadata={
            "soc_route": spec.route,
            "action_payload": payload,
            "orchestrator_sample_id": sample_id,
            "orchestrator_action_origin": spec.origin,
            "enrichment_action_id": spec.plan_action_id,
        },
    )


def _merge_action_specs(
    explicit_specs: list[SocOrchestratorActionSpec],
    enrichment_plan: SocEnrichmentPlan | None,
) -> tuple[list[SocOrchestratorActionSpec], int]:
    merged = list(explicit_specs)
    seen = {_action_spec_key(spec) for spec in explicit_specs}
    deduplicated = 0
    if enrichment_plan is None:
        return merged, deduplicated
    for planned in enrichment_plan.actions:
        spec = SocOrchestratorActionSpec(
            route=planned.route,
            action=planned.action,
            payload=planned.payload,
            origin="planned",
            plan_action_id=planned.action_id,
        )
        key = _action_spec_key(spec)
        if key in seen:
            deduplicated += 1
            continue
        seen.add(key)
        merged.append(spec)
    return merged, deduplicated


def _action_spec_key(spec: SocOrchestratorActionSpec) -> tuple[str, str, str]:
    payload = {key: value for key, value in spec.payload.items() if key != "context_refs"}
    return spec.route, spec.action or spec.route, stable_hash(payload)


def _skill_context_from_run(run: AnalysisRun) -> SocSkillContext:
    if run.llm_analysis_request is not None:
        return run.llm_analysis_request.skill_context
    return SocSkillContext()


def _review_context_summary(
    run: AnalysisRun,
    *,
    action_evidence_count: int,
    correlation_match_count: int,
    reusable_evidence_count: int,
    domain_finding_count: int,
) -> SocOrchestratorReviewContextSummary:
    decision = run.decision
    analysis = run.analysis
    return SocOrchestratorReviewContextSummary(
        run_id=run.run_id,
        alert_id=run.alert_id,
        verdict=decision.verdict if decision is not None else None,
        confidence=decision.confidence if decision is not None else None,
        needs_review=decision.needs_review if decision is not None else True,
        reason=decision.reason if decision is not None else None,
        analysis_summary=analysis.summary if analysis is not None else None,
        action_evidence_count=action_evidence_count,
        correlation_match_count=correlation_match_count,
        reusable_evidence_count=reusable_evidence_count,
        domain_finding_count=domain_finding_count,
    )


def _default_context(request: SocMainOrchestratorRequest) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-main-orchestrator",
            actor_type=ActorType.SYSTEM,
            surface=EntrySurface.TEST,
            roles=["soc_orchestrator"],
        ),
        trace_id=f"soc-main-orchestrator:{request.sample_id or 'adhoc'}",
        idempotency_key=f"soc-main-orchestrator:{request.sample_id or 'adhoc'}",
    )
