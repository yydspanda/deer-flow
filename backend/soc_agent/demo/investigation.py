"""Persistent SOC investigation demo seeding.

This module intentionally drives the same public services that Web/TUI use
instead of inserting pre-shaped view rows. The seeded data is local/mock, but
the contracts are the production contracts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from soc_agent.actions.adapters import (
    ASSET_LOOKUP_ACTION,
    SECURITY_TAG_LOOKUP_ACTION,
    THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION,
    InMemoryAssetLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    InMemoryThreatIntelIpReputationLookupActionAdapter,
    SocActionAdapterRegistry,
)
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertSourceType,
    AlertSummary,
    AnalysisRun,
    EntrySurface,
    InvestigationEvidence,
    ReviewQueueItem,
    ServiceRequestContext,
    SocAgentChatRequest,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryTargetArtifact,
)
from soc_agent.core import (
    SocAgentActionDispatcher,
    SocAgentCapabilityRouter,
    SocAnalysisService,
    SocMemoryService,
    SocReviewService,
)
from soc_agent.eval import (
    DEFAULT_PINGAN_CAPABILITY_EVAL_DIR,
    PingAnCapabilityEvalAction,
    PingAnCapabilityEvalFixture,
    load_pingan_capability_eval_fixtures,
)
from soc_agent.protocols import (
    AlertRepository,
    AlertSummaryRepository,
    DecisionAuditRepository,
    InvestigationEvidenceRepository,
    MemoryCandidateRepository,
    MemoryRecordRepository,
    ReviewQueueRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_THREAD_PREFIX = "SOC-DEMO"
DEMO_IDEMPOTENCY_VERSION = "v1"


class SocDemoInvestigationRepository(
    AlertRepository,
    AlertSummaryRepository,
    DecisionAuditRepository,
    ReviewQueueRepository,
    InvestigationEvidenceRepository,
    MemoryCandidateRepository,
    MemoryRecordRepository,
    Protocol,
):
    """Repository capabilities required by the persistent demo seeder."""


class SocDemoInvestigationActionResult(BaseModel):
    """One seeded read-only evidence action inside a demo investigation."""

    route: str
    action: str
    status: Literal["success", "denied", "failed", "skipped"]
    evidence_id: str | None = None
    skipped_existing: bool = False
    message: str
    failure_reasons: list[str] = Field(default_factory=list)


class SocDemoInvestigationSampleResult(BaseModel):
    """One persisted demo investigation seed result."""

    schema_version: str = "soc.demo_investigation_sample_result.v1"
    sample_id: str
    scenario: str
    source_path: str
    run_id: str
    alert_id: str
    queue_id: str | None = None
    queue_status: str | None = None
    source_type: str | None = None
    action_count: int = 0
    evidence_count: int = 0
    domain_finding_count: int = 0
    correlation_match_count: int = 0
    relevant_memory_count: int = 0
    timeline_item_count: int = 0
    memory_candidate_id: str | None = None
    memory_record_id: str | None = None
    investigation_view_counts: dict[str, int] = Field(default_factory=dict)
    timeline_kinds: list[str] = Field(default_factory=list)
    passed: bool = False
    failure_reasons: list[str] = Field(default_factory=list)
    actions: list[SocDemoInvestigationActionResult] = Field(default_factory=list)


class SocDemoInvestigationReport(BaseModel):
    """Aggregate result for `soc demo run`."""

    schema_version: str = "soc.demo_investigation_report.v1"
    scenario: str
    sample_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    run_ids: list[str] = Field(default_factory=list)
    queue_ids: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)
    results: list[SocDemoInvestigationSampleResult] = Field(default_factory=list)


def run_pingan_investigation_demo(
    fixtures: Sequence[PingAnCapabilityEvalFixture] | None = None,
    repository: SocDemoInvestigationRepository | None = None,
    *,
    scenario: Literal["all", "apt", "edr", "hids"] = "all",
    analysis_service: SocAnalysisService | None = None,
) -> SocDemoInvestigationReport:
    """Seed a run-scoped PingAn SOC investigation chain into a repository."""

    if repository is None:
        raise ValueError("repository is required for persistent SOC investigation demo")
    source_fixtures = list(load_pingan_capability_eval_fixtures(DEFAULT_PINGAN_CAPABILITY_EVAL_DIR) if fixtures is None else fixtures)
    selected = [fixture for fixture in source_fixtures if _fixture_matches_scenario(fixture, scenario)]
    if not selected:
        raise ValueError(f"no PingAn demo fixtures matched scenario={scenario!r}")

    results = [
        _seed_fixture_investigation(
            fixture,
            repository=repository,
            analysis_service=analysis_service,
        )
        for fixture in selected
    ]
    queue_ids = [result.queue_id for result in results if result.queue_id]
    return SocDemoInvestigationReport(
        scenario=scenario,
        sample_count=len(results),
        passed_count=sum(result.passed for result in results),
        failed_count=sum(not result.passed for result in results),
        run_ids=[result.run_id for result in results],
        queue_ids=queue_ids,
        next_commands=[
            *(f"soc show {result.run_id} --pretty" for result in results[:3]),
            *(f"soc review context {queue_id} --pretty" for queue_id in queue_ids[:3]),
            "Open /workspace/soc/alerts to inspect every completed result",
            "Use /workspace/soc/review/alerts only for unresolved fact conflicts",
        ],
        results=results,
    )


def _seed_fixture_investigation(
    fixture: PingAnCapabilityEvalFixture,
    *,
    repository: SocDemoInvestigationRepository,
    analysis_service: SocAnalysisService | None,
) -> SocDemoInvestigationSampleResult:
    payload = _load_source_payload(fixture)
    context = _demo_context(fixture.sample_id, operation="analysis")
    service = analysis_service or SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        analysis_persistence=repository,
    )
    run = service.analyze(payload, context=context)
    summary = repository.get_alert_summary(run.run_id)
    if summary is None:
        raise ValueError(f"demo analysis did not persist alert summary for run {run.run_id}")
    review_item = _review_item_for_run(repository, run.run_id)
    failure_reasons: list[str] = []

    action_results = _seed_read_only_evidence(
        fixture,
        run=run,
        review_item=review_item,
        repository=repository,
    )
    memory_record = _seed_confirmed_memory(
        fixture,
        run=run,
        summary=summary,
        review_item=review_item,
        repository=repository,
    )
    context_view = _review_context_service(repository).get_alert_investigation_context(run.run_id)
    evidence_count = len(context_view.action_evidence)
    if evidence_count < sum(1 for action in fixture.actions if action.expect_evidence):
        failure_reasons.append(f"expected at least {len(fixture.actions)} read-only evidence records, got {evidence_count}")
    if sum(len(item.findings) for item in context_view.domain_triage_results) == 0:
        failure_reasons.append("expected at least one domain finding in alert result context")
    if context_view.relevant_memories is None or context_view.relevant_memories.returned_count == 0:
        failure_reasons.append("expected at least one retrieval-enabled relevant memory")

    investigation_view = context_view.investigation_view
    counts = dict(investigation_view.counts) if investigation_view is not None else {}
    timeline_kinds = sorted({item.kind for item in investigation_view.evidence_timeline}) if investigation_view is not None else []
    source_type = run.normalization_report.source_type.value if run.normalization_report is not None else None
    action_failures = [reason for item in action_results for reason in item.failure_reasons]
    failure_reasons.extend(action_failures)
    return SocDemoInvestigationSampleResult(
        sample_id=fixture.sample_id,
        scenario=fixture.scenario,
        source_path=fixture.source_path,
        run_id=run.run_id,
        alert_id=run.alert_id,
        queue_id=review_item.queue_id if review_item is not None else None,
        queue_status=review_item.status.value if review_item is not None else None,
        source_type=source_type,
        action_count=len(action_results),
        evidence_count=evidence_count,
        domain_finding_count=counts.get("domain_findings", 0),
        correlation_match_count=counts.get("correlation_matches", 0),
        relevant_memory_count=counts.get("relevant_memories", 0),
        timeline_item_count=counts.get("timeline_items", 0),
        memory_candidate_id=memory_record.source_candidate_id if memory_record is not None else None,
        memory_record_id=memory_record.memory_id if memory_record is not None else None,
        investigation_view_counts=counts,
        timeline_kinds=timeline_kinds,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        actions=action_results,
    )


def _seed_read_only_evidence(
    fixture: PingAnCapabilityEvalFixture,
    *,
    run: AnalysisRun,
    review_item: ReviewQueueItem | None,
    repository: InvestigationEvidenceRepository,
) -> list[SocDemoInvestigationActionResult]:
    existing = _existing_evidence_by_route(repository, thread_id=_thread_id(fixture.sample_id))
    registry = _registry_for_fixture(fixture)
    dispatcher = SocAgentActionDispatcher(
        action_adapter_registry=registry,
        evidence_repository=repository,
    )
    router = SocAgentCapabilityRouter(allowed_routes={action.route for action in fixture.actions})

    results: list[SocDemoInvestigationActionResult] = []
    for index, action in enumerate(fixture.actions):
        if action.route in existing:
            evidence = existing[action.route]
            results.append(
                SocDemoInvestigationActionResult(
                    route=action.route,
                    action=evidence.action,
                    status="skipped",
                    evidence_id=evidence.evidence_id,
                    skipped_existing=True,
                    message="existing demo evidence reused",
                )
            )
            continue

        request = _chat_request_for_action(fixture, action, run=run, review_item=review_item, index=index)
        route_decision = router.route(request)
        context = _demo_context(fixture.sample_id, operation=f"action:{action.route}:{index + 1}")
        permission_decision = dispatcher.check_permission(request, route_decision, context=context)
        result = dispatcher.dispatch(
            request,
            route_decision,
            context=context,
            permission_decision=permission_decision,
        )
        evidence_id = result.payload.get("evidence_id")
        failure_reasons = []
        if action.expect_status != result.status:
            failure_reasons.append(f"{action.route}: expected status={action.expect_status}, got {result.status}")
        if action.expect_evidence and not isinstance(evidence_id, str):
            failure_reasons.append(f"{action.route}: expected persisted InvestigationEvidence")
        results.append(
            SocDemoInvestigationActionResult(
                route=result.route,
                action=result.action,
                status=result.status,
                evidence_id=evidence_id if isinstance(evidence_id, str) else None,
                message=result.message,
                failure_reasons=failure_reasons,
            )
        )
    return results


def _seed_confirmed_memory(
    fixture: PingAnCapabilityEvalFixture,
    *,
    run: AnalysisRun,
    summary: AlertSummary,
    review_item: ReviewQueueItem | None,
    repository: SocDemoInvestigationRepository,
) -> SocMemoryRecord | None:
    evidence_refs = [item.evidence_id for item in repository.list_evidence(thread_id=_thread_id(fixture.sample_id), limit=50)]
    if not evidence_refs:
        evidence_refs = [run.run_id]
        if review_item is not None:
            evidence_refs.insert(0, review_item.queue_id)

    service = SocMemoryService(candidate_repository=repository, record_repository=repository)
    command = SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary=f"Demo confirmed memory for {fixture.scenario}",
        content=_memory_content_for_fixture(fixture, summary=summary),
        tenant_scope=summary.tenant_id or "global",
        tenant_id=summary.tenant_id,
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.EVAL_FIXTURE,
            source_surface=EntrySurface.CLI,
            source_id=f"soc-demo:{fixture.sample_id}",
            run_id=run.run_id,
            alert_id=run.alert_id,
            queue_id=review_item.queue_id if review_item is not None else None,
            eval_sample_id=fixture.sample_id,
            metadata={"scenario": fixture.scenario, "source_path": fixture.source_path},
        ),
        evidence_refs=evidence_refs,
        validity=SocMemoryCandidateValidity(notes="Local demo seed only; validates retrieval plumbing, not production PingAn policy."),
        idempotency_key=f"soc-demo:memory:{fixture.sample_id}:{DEMO_IDEMPOTENCY_VERSION}",
        confidence=0.82,
        facets=_memory_facets_for_fixture(fixture, run=run, summary=summary),
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc-demo",
        labels=["soc-demo", "pingan-demo", "confirmed-memory"],
        metadata={
            "soc_demo": True,
            "mock": True,
            "sample_id": fixture.sample_id,
            "scenario": fixture.scenario,
        },
    )
    candidate = service.propose_candidate(command, context=_demo_context(fixture.sample_id, operation="memory:propose"))
    result = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Seed a confirmed retrieval memory for local SOC investigation demo.",
            metadata={"soc_demo": True, "retrieval_activation_requested": True},
        ),
        context=_demo_context(fixture.sample_id, operation="memory:confirm"),
    )
    record = result.memory_record or repository.get_memory_record_by_candidate_id(candidate.candidate_id)
    if record is None:
        return None
    if record.retrieval_enabled:
        return record
    activation = service.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=record.memory_id,
            action=SocMemoryRetrievalActivationAction.ENABLE,
            expected_record_version=record.version,
            reason="Enable bounded retrieval for the local SOC investigation demo.",
            activation_valid_until=datetime.now(UTC) + timedelta(days=180),
            review_after_days=30,
            metadata={"soc_demo": True, "mock": True},
        ),
        context=_demo_context(fixture.sample_id, operation="memory:retrieval-enable"),
    )
    return activation.record


def _review_context_service(
    repository: SocDemoInvestigationRepository,
) -> SocReviewService:
    return SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        memory_candidate_repository=repository,
        memory_record_repository=repository,
    )


def _review_item_for_run(repository: ReviewQueueRepository, run_id: str) -> ReviewQueueItem | None:
    item = repository.get_open_review_item_by_run(run_id)
    if item is not None:
        return item
    for candidate in repository.list_review_items(status=None, limit=200):
        if candidate.run_id == run_id:
            return candidate
    return None


def _fixture_matches_scenario(
    fixture: PingAnCapabilityEvalFixture,
    scenario: Literal["all", "apt", "edr", "hids"],
) -> bool:
    if scenario == "all":
        return True
    source_type = fixture.expected_source_type
    sample_id = fixture.sample_id.lower()
    if scenario == "edr":
        return source_type == AlertSourceType.EDR or "edr" in sample_id
    if scenario == "hids":
        return source_type == AlertSourceType.HIDS or "hids" in sample_id
    return source_type in {AlertSourceType.NDR, AlertSourceType.NIDS, AlertSourceType.THREAT_INTEL} or "apt" in sample_id


def _existing_evidence_by_route(
    repository: InvestigationEvidenceRepository,
    *,
    thread_id: str,
) -> dict[str, InvestigationEvidence]:
    evidence_by_route: dict[str, InvestigationEvidence] = {}
    for evidence in repository.list_evidence(thread_id=thread_id, limit=100):
        evidence_by_route.setdefault(evidence.route, evidence)
    return evidence_by_route


def _registry_for_fixture(fixture: PingAnCapabilityEvalFixture) -> SocActionAdapterRegistry:
    return SocActionAdapterRegistry(
        [
            InMemoryAssetLookupActionAdapter(records=_records(fixture, ASSET_LOOKUP_ACTION)),
            InMemorySecurityTagLookupActionAdapter(records=_records(fixture, SECURITY_TAG_LOOKUP_ACTION)),
            InMemoryThreatIntelIpReputationLookupActionAdapter(records=_records(fixture, THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION)),
        ]
    )


def _records(fixture: PingAnCapabilityEvalFixture, route: str) -> list[dict[str, Any]] | None:
    if route not in fixture.mock_records:
        return None
    return fixture.mock_records[route]


def _chat_request_for_action(
    fixture: PingAnCapabilityEvalFixture,
    action: PingAnCapabilityEvalAction,
    *,
    run: AnalysisRun,
    review_item: ReviewQueueItem | None,
    index: int,
) -> SocAgentChatRequest:
    payload = dict(action.payload)
    context_refs = dict(payload.get("context_refs")) if isinstance(payload.get("context_refs"), Mapping) else {}
    context_refs.update(
        {
            "alert_id": run.alert_id,
            "run_id": run.run_id,
            "thread_id": _thread_id(fixture.sample_id),
            "proposal_id": f"SOC-DEMO-{fixture.sample_id}-{index + 1}",
        }
    )
    if review_item is not None:
        context_refs["queue_id"] = review_item.queue_id
    payload["context_refs"] = context_refs
    return SocAgentChatRequest(
        message=f"Run SOC demo read-only action {action.route}",
        thread_id=_thread_id(fixture.sample_id),
        queue_id=review_item.queue_id if review_item is not None else None,
        run_id=run.run_id,
        allowed_routes=[action.route],
        metadata={
            "soc_route": action.route,
            "action_payload": payload,
            "demo_sample_id": fixture.sample_id,
        },
    )


def _memory_facets_for_fixture(
    fixture: PingAnCapabilityEvalFixture,
    *,
    run: AnalysisRun,
    summary: AlertSummary,
) -> dict[str, list[str]]:
    facets: dict[str, list[str]] = {
        "topic": ["soc-investigation-demo"],
        "demo_sample_id": [fixture.sample_id],
        "scenario": [fixture.scenario],
    }
    _add_facet(facets, "source_type", summary.source_type.value)
    _add_facet(facets, "source_system", summary.source_system)
    _add_facet(facets, "detection_key", summary.detection_key)
    _add_facet(facets, "rule_code", summary.rule_code)
    _add_facet(facets, "rule_name", summary.rule_name)
    _add_facet(facets, "severity", summary.severity)
    _add_facet(facets, "category", summary.category)
    for entity_key in summary.entity_keys:
        _add_facet(facets, "entity", entity_key)
    if run.llm_analysis_request is not None:
        for skill in run.llm_analysis_request.skill_context.selected_skills:
            _add_facet(facets, "skill", skill.skill_name)
        for conflict_type in run.llm_analysis_request.conflict_types:
            _add_facet(facets, "conflict_type", conflict_type)
    for action in fixture.actions:
        _add_facet(facets, "route", action.route)
        _add_facet(facets, "action", action.action or action.route)
    return {key: values for key, values in facets.items() if values}


def _add_facet(facets: dict[str, list[str]], key: str, value: str | None) -> None:
    if value is None:
        return
    normalized = str(value).strip()
    if not normalized:
        return
    values = facets.setdefault(key, [])
    if normalized not in values:
        values.append(normalized)


def _memory_content_for_fixture(
    fixture: PingAnCapabilityEvalFixture,
    *,
    summary: AlertSummary,
) -> str:
    action_routes = ", ".join(action.route for action in fixture.actions) or "no read-only routes"
    return (
        f"Demo memory for scenario {fixture.scenario}. "
        f"When source_type={summary.source_type.value}, keep raw evidence, read-only action evidence, "
        f"domain findings, and the run-scoped alert context together before changing verdict. "
        f"Expected demo read-only routes: {action_routes}. "
        "This record is retrieval-enabled only for local demo validation."
    )


def _demo_context(sample_id: str, *, operation: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-demo",
            actor_type=ActorType.SYSTEM,
            surface=EntrySurface.CLI,
            roles=["soc_demo", "analyst", "soc_memory_reviewer"],
        ),
        trace_id=f"soc-demo:{sample_id}:{operation}",
        idempotency_key=f"soc-demo:{operation}:{sample_id}:{DEMO_IDEMPOTENCY_VERSION}",
    )


def _thread_id(sample_id: str) -> str:
    return f"{DEMO_THREAD_PREFIX}-{sample_id}"


def _load_source_payload(fixture: PingAnCapabilityEvalFixture) -> dict[str, Any]:
    source_path = _resolve_source_path(fixture.source_path, fixture_path=fixture.fixture_path)
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read PingAn demo source sample {source_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid PingAn demo source sample JSON {source_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"PingAn demo source sample {source_path} must be a JSON object")
    return data


def _resolve_source_path(source_path: str, *, fixture_path: str | None) -> Path:
    path = Path(source_path)
    if path.is_absolute():
        return path
    candidates = [REPO_ROOT / path, Path.cwd() / path]
    if fixture_path:
        candidates.append(Path(fixture_path).resolve().parent / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
