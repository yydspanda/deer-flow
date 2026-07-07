"""PingAn SOC capability eval fixtures.

These fixtures validate tenant-specific read-only evidence flows without
turning PingAn knowledge into public skills or production prompts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from soc_agent.actions.adapters import (
    ASSET_LOOKUP_ACTION,
    ENDPOINT_PROCESS_TREE_LOOKUP_ACTION,
    HOST_EVENT_CONTEXT_LOOKUP_ACTION,
    SECURITY_TAG_LOOKUP_ACTION,
    THREAT_INTEL_IP_REPUTATION_LOOKUP_ACTION,
    InMemoryAssetLookupActionAdapter,
    InMemoryEndpointProcessTreeLookupActionAdapter,
    InMemoryHostEventContextLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    InMemoryThreatIntelIpReputationLookupActionAdapter,
    SocActionAdapterRegistry,
)
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertSourceType,
    AnalysisRun,
    EntrySurface,
    ServiceRequestContext,
    SocAgentChatRequest,
    SocDomainName,
    SocDomainTriageRequest,
    SocMainOrchestratorRequest,
    SocOrchestratorActionSpec,
    SocSkillContext,
    UnifiedInvestigationReport,
)
from soc_agent.core import (
    InMemoryInvestigationEvidenceRepository,
    SocAgentActionDispatcher,
    SocAgentCapabilityRouter,
    SocAnalysisService,
    SocDomainTriageService,
    SocMainOrchestratorService,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PINGAN_CAPABILITY_EVAL_DIR = REPO_ROOT / "backend" / "samples" / "eval" / "pingan"

_MISSING = object()


@dataclass(frozen=True)
class _PingAnFixtureExecution:
    run: AnalysisRun
    source_type: AlertSourceType | None
    conflict_count: int
    conflict_types: list[str]
    action_results: list[PingAnCapabilityEvalActionResult]
    evidence_repository: InMemoryInvestigationEvidenceRepository


class PingAnCapabilityEvalAction(BaseModel):
    """One read-only action expected for a PingAn eval sample."""

    route: str = Field(min_length=1)
    action: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    expect_status: Literal["success", "denied", "failed"] = "success"
    expect_evidence: bool = True
    expect_payload_values: dict[str, Any] = Field(default_factory=dict)
    expect_payload_contains: dict[str, Any] = Field(default_factory=dict)


class PingAnCapabilityEvalFixture(BaseModel):
    """One PingAn capability fixture bound to one alert sample."""

    schema_version: str = "soc.pingan_capability_eval_fixture.v1"
    sample_id: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    fixture_path: str | None = None
    expected_source_type: AlertSourceType | None = None
    min_conflict_count: int = Field(default=0, ge=0)
    expected_conflict_types: list[str] = Field(default_factory=list)
    mock_records: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    actions: list[PingAnCapabilityEvalAction] = Field(default_factory=list)


class PingAnCapabilityEvalActionResult(BaseModel):
    """One read-only action result inside a PingAn eval sample."""

    route: str
    action: str
    status: Literal["success", "denied", "failed"]
    evidence_id: str | None = None
    passed: bool = False
    failure_reasons: list[str] = Field(default_factory=list)


class PingAnCapabilityEvalSampleResult(BaseModel):
    """One PingAn eval sample result."""

    sample_id: str
    scenario: str
    source_path: str
    source_type: AlertSourceType | None = None
    conflict_count: int = 0
    conflict_types: list[str] = Field(default_factory=list)
    action_count: int = 0
    evidence_count: int = 0
    passed: bool = False
    failure_reasons: list[str] = Field(default_factory=list)
    actions: list[PingAnCapabilityEvalActionResult] = Field(default_factory=list)


class PingAnCapabilityEvalReport(BaseModel):
    """Aggregate report for PingAn capability eval fixtures."""

    schema_version: str = "soc.pingan_capability_eval_report.v1"
    sample_count: int = 0
    action_count: int = 0
    evidence_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    source_type_counts: dict[str, int] = Field(default_factory=dict)
    results: list[PingAnCapabilityEvalSampleResult] = Field(default_factory=list)


class PingAnDomainTriageEvalFinding(BaseModel):
    """Compact domain finding snapshot for PingAn PA-10 eval output."""

    finding_id: str
    domain: SocDomainName
    title: str
    severity: str
    disposition: str
    confidence: float
    evidence_refs: list[str] = Field(default_factory=list)
    capability_card_refs: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)


class PingAnDomainTriageEvalSampleResult(BaseModel):
    """One PingAn domain triage eval result."""

    sample_id: str
    scenario: str
    source_path: str
    expected_domain: SocDomainName
    domain: SocDomainName | None = None
    handler_id: str | None = None
    finding_count: int = 0
    evidence_ref_count: int = 0
    passed: bool = False
    failure_reasons: list[str] = Field(default_factory=list)
    findings: list[PingAnDomainTriageEvalFinding] = Field(default_factory=list)


class PingAnDomainTriageEvalReport(BaseModel):
    """Aggregate report for PingAn PA-10 domain triage fixtures."""

    schema_version: str = "soc.pingan_domain_triage_eval_report.v1"
    sample_count: int = 0
    finding_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    domain_counts: dict[str, int] = Field(default_factory=dict)
    results: list[PingAnDomainTriageEvalSampleResult] = Field(default_factory=list)


class PingAnMainOrchestratorEvalSampleResult(BaseModel):
    """One PA-11 main-orchestrator demo result."""

    sample_id: str
    scenario: str
    source_path: str
    route_step_count: int = 0
    skill_count: int = 0
    evidence_count: int = 0
    domain_finding_count: int = 0
    review_context_ready: bool = False
    passed: bool = False
    failure_reasons: list[str] = Field(default_factory=list)
    report: UnifiedInvestigationReport


class PingAnMainOrchestratorEvalReport(BaseModel):
    """Aggregate report for PingAn PA-11 main-orchestrator demo fixtures."""

    schema_version: str = "soc.pingan_main_orchestrator_eval_report.v1"
    sample_count: int = 0
    route_step_count: int = 0
    evidence_count: int = 0
    domain_finding_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    results: list[PingAnMainOrchestratorEvalSampleResult] = Field(default_factory=list)


def load_pingan_capability_eval_fixtures(path: str | Path = DEFAULT_PINGAN_CAPABILITY_EVAL_DIR) -> list[PingAnCapabilityEvalFixture]:
    """Load one PingAn eval fixture file or all JSON fixtures in a directory."""

    fixture_path = Path(path)
    files = sorted(fixture_path.glob("*.json")) if fixture_path.is_dir() else [fixture_path]
    fixtures: list[PingAnCapabilityEvalFixture] = []
    for item in files:
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read PingAn eval fixture {item}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid PingAn eval fixture JSON {item}: {exc}") from exc
        fixture = PingAnCapabilityEvalFixture.model_validate(data)
        fixtures.append(fixture.model_copy(update={"fixture_path": str(item)}))
    if not fixtures:
        raise ValueError(f"no PingAn eval fixtures found in {fixture_path}")
    return fixtures


def run_pingan_capability_eval(fixtures: Sequence[PingAnCapabilityEvalFixture]) -> PingAnCapabilityEvalReport:
    """Run PingAn capability fixtures through the safe action evidence path."""

    results = [_run_fixture(fixture) for fixture in fixtures]
    source_type_counts: dict[str, int] = {}
    for result in results:
        if result.source_type is not None:
            source_type_counts[result.source_type.value] = source_type_counts.get(result.source_type.value, 0) + 1
    return PingAnCapabilityEvalReport(
        sample_count=len(results),
        action_count=sum(result.action_count for result in results),
        evidence_count=sum(result.evidence_count for result in results),
        passed_count=sum(result.passed for result in results),
        failed_count=sum(not result.passed for result in results),
        source_type_counts=source_type_counts,
        results=results,
    )


def run_pingan_domain_triage_eval(fixtures: Sequence[PingAnCapabilityEvalFixture]) -> PingAnDomainTriageEvalReport:
    """Run PingAn fixtures through PA-10 deterministic domain triage."""

    results = [_run_domain_fixture(fixture) for fixture in fixtures]
    domain_counts: dict[str, int] = {}
    for result in results:
        if result.domain is not None:
            domain_counts[result.domain.value] = domain_counts.get(result.domain.value, 0) + 1
    return PingAnDomainTriageEvalReport(
        sample_count=len(results),
        finding_count=sum(result.finding_count for result in results),
        passed_count=sum(result.passed for result in results),
        failed_count=sum(not result.passed for result in results),
        domain_counts=domain_counts,
        results=results,
    )


def run_pingan_main_orchestrator_eval(fixtures: Sequence[PingAnCapabilityEvalFixture]) -> PingAnMainOrchestratorEvalReport:
    """Run PingAn fixtures through PA-11 main-orchestrator unified reports."""

    results = [_run_main_orchestrator_fixture(fixture) for fixture in fixtures]
    return PingAnMainOrchestratorEvalReport(
        sample_count=len(results),
        route_step_count=sum(result.route_step_count for result in results),
        evidence_count=sum(result.evidence_count for result in results),
        domain_finding_count=sum(result.domain_finding_count for result in results),
        passed_count=sum(result.passed for result in results),
        failed_count=sum(not result.passed for result in results),
        results=results,
    )


def _run_fixture(fixture: PingAnCapabilityEvalFixture) -> PingAnCapabilityEvalSampleResult:
    execution = _execute_fixture(fixture)
    failure_reasons: list[str] = []

    if fixture.expected_source_type is not None and execution.source_type is not fixture.expected_source_type:
        failure_reasons.append(f"expected source_type={fixture.expected_source_type.value}, got {execution.source_type.value if execution.source_type else None}")
    if execution.conflict_count < fixture.min_conflict_count:
        failure_reasons.append(f"expected at least {fixture.min_conflict_count} conflicts, got {execution.conflict_count}")
    missing_conflicts = sorted(set(fixture.expected_conflict_types) - set(execution.conflict_types))
    if missing_conflicts:
        failure_reasons.append(f"missing expected conflict types: {', '.join(missing_conflicts)}")

    failure_reasons.extend(reason for item in execution.action_results for reason in item.failure_reasons)
    evidence_count = len(execution.evidence_repository.list_evidence(thread_id=f"THR-{fixture.sample_id}", limit=100))
    return PingAnCapabilityEvalSampleResult(
        sample_id=fixture.sample_id,
        scenario=fixture.scenario,
        source_path=fixture.source_path,
        source_type=execution.source_type,
        conflict_count=execution.conflict_count,
        conflict_types=execution.conflict_types,
        action_count=len(execution.action_results),
        evidence_count=evidence_count,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        actions=execution.action_results,
    )


def _run_domain_fixture(fixture: PingAnCapabilityEvalFixture) -> PingAnDomainTriageEvalSampleResult:
    execution = _execute_fixture(fixture)
    expected_domain = _expected_domain_for_fixture(fixture, execution.source_type)
    evidence = execution.evidence_repository.list_evidence(thread_id=f"THR-{fixture.sample_id}", limit=100)
    skill_context = execution.run.llm_analysis_request.skill_context if execution.run.llm_analysis_request is not None else None
    request = SocDomainTriageRequest(
        run=execution.run,
        domain=expected_domain,
        skill_context=skill_context or SocSkillContext(),
        investigation_evidence=evidence,
        capability_card_refs=_expected_capability_cards(expected_domain),
        metadata={"eval_sample_id": fixture.sample_id, "source_path": fixture.source_path},
    )
    result = SocDomainTriageService().triage(request)

    failure_reasons: list[str] = []
    if result.domain is not expected_domain:
        failure_reasons.append(f"expected domain={expected_domain.value}, got {result.domain.value}")
    if not result.findings:
        failure_reasons.append("expected at least one domain finding")
    if result.metadata.get("writes_db") is not False:
        failure_reasons.append("domain triage result must not write DB")
    if result.metadata.get("executes_actions") is not False:
        failure_reasons.append("domain triage result must not execute actions")
    for card in _expected_capability_cards(expected_domain):
        if not any(card in finding.capability_card_refs for finding in result.findings):
            failure_reasons.append(f"missing expected capability card ref {card}")
    if evidence and not any(finding.evidence_refs for finding in result.findings):
        failure_reasons.append("expected finding evidence refs when investigation evidence exists")

    findings = [
        PingAnDomainTriageEvalFinding(
            finding_id=finding.finding_id,
            domain=finding.domain,
            title=finding.title,
            severity=finding.severity.value,
            disposition=finding.disposition.value,
            confidence=finding.confidence,
            evidence_refs=finding.evidence_refs,
            capability_card_refs=finding.capability_card_refs,
            skill_names=finding.skill_names,
        )
        for finding in result.findings
    ]
    return PingAnDomainTriageEvalSampleResult(
        sample_id=fixture.sample_id,
        scenario=fixture.scenario,
        source_path=fixture.source_path,
        expected_domain=expected_domain,
        domain=result.domain,
        handler_id=result.handler_id,
        finding_count=len(result.findings),
        evidence_ref_count=result.evidence_ref_count,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        findings=findings,
    )


def _run_main_orchestrator_fixture(fixture: PingAnCapabilityEvalFixture) -> PingAnMainOrchestratorEvalSampleResult:
    source_payload = _load_source_payload(fixture)
    expected_domain = _expected_domain_for_fixture(fixture, fixture.expected_source_type)
    service = SocMainOrchestratorService(action_adapter_registry=_registry_for_fixture(fixture))
    report = service.run(
        SocMainOrchestratorRequest(
            payload=source_payload,
            sample_id=fixture.sample_id,
            thread_id=f"THR-{fixture.sample_id}",
            action_specs=[
                SocOrchestratorActionSpec(
                    route=action.route,
                    action=action.action,
                    payload=action.payload,
                )
                for action in fixture.actions
            ],
            capability_card_refs=_expected_capability_cards(expected_domain),
            metadata={"source_path": fixture.source_path, "fixture_path": fixture.fixture_path},
        )
    )
    failure_reasons: list[str] = []
    if len(report.route_steps) != len(fixture.actions):
        failure_reasons.append(f"expected {len(fixture.actions)} route steps, got {len(report.route_steps)}")
    failed_steps = [item.route for item in report.route_steps if item.status != "success"]
    if failed_steps:
        failure_reasons.append(f"route steps failed: {', '.join(failed_steps)}")
    if fixture.actions and not all(item.evidence_id for item in report.route_steps):
        failure_reasons.append("expected every read-only route step to produce evidence_id")
    if not report.skill_context.selected_skills:
        failure_reasons.append("expected selected SOC skills in unified report")
    if len(report.investigation_evidence) < sum(1 for action in fixture.actions if action.expect_evidence):
        failure_reasons.append("expected investigation evidence for read-only actions")
    finding_count = sum(len(result.findings) for result in report.domain_triage_results)
    if finding_count == 0:
        failure_reasons.append("expected at least one domain finding")
    if report.review_context.run_id != report.run.run_id or report.review_context.alert_id != report.run.alert_id:
        failure_reasons.append("review context does not reference the orchestrated run")
    if report.metadata.get("writes_db") is not False:
        failure_reasons.append("main orchestrator demo must not write DB")
    if report.metadata.get("executes_high_risk_actions") is not False:
        failure_reasons.append("main orchestrator demo must not execute high-risk actions")

    return PingAnMainOrchestratorEvalSampleResult(
        sample_id=fixture.sample_id,
        scenario=fixture.scenario,
        source_path=fixture.source_path,
        route_step_count=len(report.route_steps),
        skill_count=len(report.skill_context.selected_skills),
        evidence_count=len(report.investigation_evidence),
        domain_finding_count=finding_count,
        review_context_ready=report.review_context.run_id == report.run.run_id,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        report=report,
    )


def _execute_fixture(fixture: PingAnCapabilityEvalFixture) -> _PingAnFixtureExecution:
    source_payload = _load_source_payload(fixture)
    run = SocAnalysisService().analyze(source_payload)
    source_type = run.normalization_report.source_type if run.normalization_report is not None else None
    conflict_types = [item.conflict_type for item in run.fact_reconstruction.conflict_reports] if run.fact_reconstruction is not None else []
    registry = _registry_for_fixture(fixture)
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    dispatcher = SocAgentActionDispatcher(
        action_adapter_registry=registry,
        evidence_repository=evidence_repository,
    )
    router = SocAgentCapabilityRouter(allowed_routes={action.route for action in fixture.actions})
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="pingan-capability-eval",
            actor_type=ActorType.SYSTEM,
            surface=EntrySurface.TEST,
            roles=["soc_eval"],
        ),
        trace_id=f"pingan-eval:{fixture.sample_id}",
        idempotency_key=f"pingan-eval:{fixture.sample_id}",
    )

    action_results: list[PingAnCapabilityEvalActionResult] = []
    for index, action in enumerate(fixture.actions):
        request = _chat_request_for_action(fixture, action, run_id=run.run_id, index=index)
        route_decision = router.route(request)
        permission_decision = dispatcher.check_permission(request, route_decision, context=context)
        result = dispatcher.dispatch(
            request,
            route_decision,
            context=context,
            permission_decision=permission_decision,
        )
        evidence_id = result.payload.get("evidence_id")
        action_result = _evaluate_action_result(
            action,
            status=result.status,
            payload=result.payload,
            evidence_id=evidence_id if isinstance(evidence_id, str) else None,
            evidence_repository=evidence_repository,
        )
        action_results.append(action_result)

    return _PingAnFixtureExecution(
        run=run,
        source_type=source_type,
        conflict_count=len(conflict_types),
        conflict_types=conflict_types,
        action_results=action_results,
        evidence_repository=evidence_repository,
    )


def _evaluate_action_result(
    action: PingAnCapabilityEvalAction,
    *,
    status: str,
    payload: Mapping[str, Any],
    evidence_id: str | None,
    evidence_repository: InMemoryInvestigationEvidenceRepository,
) -> PingAnCapabilityEvalActionResult:
    failure_reasons: list[str] = []
    expected_action = action.action or action.route
    if status != action.expect_status:
        failure_reasons.append(f"{action.route}: expected status={action.expect_status}, got {status}")
    if action.expect_evidence and not evidence_id:
        failure_reasons.append(f"{action.route}: expected InvestigationEvidence evidence_id")
    if evidence_id:
        evidence = [item for item in evidence_repository.list_evidence(limit=100) if item.evidence_id == evidence_id]
        if not evidence:
            failure_reasons.append(f"{action.route}: evidence_id {evidence_id} was not persisted")
    for path, expected in action.expect_payload_values.items():
        actual = _resolve_payload_path(payload, path)
        if actual != expected:
            failure_reasons.append(f"{action.route}: expected payload {path}={expected!r}, got {None if actual is _MISSING else actual!r}")
    for path, expected in action.expect_payload_contains.items():
        actual = _resolve_payload_path(payload, path)
        if not _contains(actual, expected):
            failure_reasons.append(f"{action.route}: expected payload {path} to contain {expected!r}, got {None if actual is _MISSING else actual!r}")
    return PingAnCapabilityEvalActionResult(
        route=action.route,
        action=expected_action,
        status=status,  # type: ignore[arg-type]
        evidence_id=evidence_id,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
    )


def _expected_domain_for_fixture(
    fixture: PingAnCapabilityEvalFixture,
    source_type: AlertSourceType | None,
) -> SocDomainName:
    effective_source_type = fixture.expected_source_type or source_type
    if effective_source_type is AlertSourceType.EDR:
        return SocDomainName.EDR
    if effective_source_type is AlertSourceType.HIDS:
        return SocDomainName.HIDS
    if effective_source_type in {AlertSourceType.NDR, AlertSourceType.NIDS, AlertSourceType.THREAT_INTEL}:
        return SocDomainName.APT
    return SocDomainName.GENERIC


def _expected_capability_cards(domain: SocDomainName) -> list[str]:
    if domain is SocDomainName.APT:
        return ["PA-APT-001", "PA-APT-003", "PA-APT-004"]
    if domain is SocDomainName.EDR:
        return ["PA-EDR-001", "PA-EDR-002"]
    if domain is SocDomainName.HIDS:
        return ["PA-HIDS-001", "PA-HIDS-003"]
    return []


def _registry_for_fixture(fixture: PingAnCapabilityEvalFixture) -> SocActionAdapterRegistry:
    return SocActionAdapterRegistry(
        [
            InMemoryAssetLookupActionAdapter(records=_records(fixture, ASSET_LOOKUP_ACTION)),
            InMemoryEndpointProcessTreeLookupActionAdapter(records=_records(fixture, ENDPOINT_PROCESS_TREE_LOOKUP_ACTION)),
            InMemoryHostEventContextLookupActionAdapter(records=_records(fixture, HOST_EVENT_CONTEXT_LOOKUP_ACTION)),
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
    run_id: str,
    index: int,
) -> SocAgentChatRequest:
    payload = dict(action.payload)
    context_refs = dict(payload.get("context_refs")) if isinstance(payload.get("context_refs"), Mapping) else {}
    context_refs.setdefault("alert_id", fixture.sample_id)
    context_refs.setdefault("run_id", run_id)
    context_refs.setdefault("thread_id", f"THR-{fixture.sample_id}")
    context_refs.setdefault("proposal_id", f"PA08-{fixture.sample_id}-{index + 1}")
    payload["context_refs"] = context_refs
    return SocAgentChatRequest(
        message=f"Run PingAn eval action {action.route}",
        thread_id=f"THR-{fixture.sample_id}",
        run_id=run_id,
        allowed_routes=[action.route],
        metadata={
            "soc_route": action.route,
            "action_payload": payload,
            "eval_sample_id": fixture.sample_id,
        },
    )


def _load_source_payload(fixture: PingAnCapabilityEvalFixture) -> dict[str, Any]:
    source_path = _resolve_source_path(fixture.source_path, fixture_path=fixture.fixture_path)
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read PingAn eval source sample {source_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid PingAn eval source sample JSON {source_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"PingAn eval source sample {source_path} must be a JSON object")
    return data


def _resolve_source_path(source_path: str, *, fixture_path: str | None) -> Path:
    path = Path(source_path)
    if path.is_absolute():
        return path
    candidates = [REPO_ROOT / path, Path.cwd() / path]
    if fixture_path:
        fixture_parent = Path(fixture_path).resolve().parent
        candidates.append(fixture_parent / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _resolve_payload_path(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part, _MISSING)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else _MISSING
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def _contains(actual: Any, expected: Any) -> bool:
    if actual is _MISSING:
        return False
    if isinstance(actual, str):
        return str(expected) in actual
    if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray)):
        return expected in actual
    return actual == expected


__all__ = [
    "DEFAULT_PINGAN_CAPABILITY_EVAL_DIR",
    "PingAnCapabilityEvalAction",
    "PingAnCapabilityEvalActionResult",
    "PingAnCapabilityEvalFixture",
    "PingAnCapabilityEvalReport",
    "PingAnCapabilityEvalSampleResult",
    "PingAnDomainTriageEvalFinding",
    "PingAnDomainTriageEvalReport",
    "PingAnDomainTriageEvalSampleResult",
    "PingAnMainOrchestratorEvalReport",
    "PingAnMainOrchestratorEvalSampleResult",
    "load_pingan_capability_eval_fixtures",
    "run_pingan_capability_eval",
    "run_pingan_domain_triage_eval",
    "run_pingan_main_orchestrator_eval",
]
