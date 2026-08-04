from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from soc_agent.actions.adapters import (
    InMemoryAssetLookupActionAdapter,
    InMemorySecurityTagLookupActionAdapter,
    InMemoryThreatIntelIpReputationLookupActionAdapter,
    SocActionAdapterRegistry,
)
from soc_agent.contracts import (
    AlertEntitySet,
    AlertInput,
    EntityKind,
    EntityMention,
    HostEntityRef,
    NetworkEntityRef,
    RoleResolution,
    RoleResolutionStatus,
    SocEnrichmentPlanStatus,
    SocEnrichmentPolicy,
    SocEnrichmentSkipReason,
    SocMainOrchestratorRequest,
    SocOrchestratorActionSpec,
    ThreatEntityRef,
    UserEntityRef,
)
from soc_agent.core import (
    InMemoryAlertSummaryRepository,
    InMemoryInvestigationEvidenceRepository,
    SocAnalysisService,
    SocCorrelationService,
    SocEnrichmentPlanner,
    SocMainOrchestratorService,
)


def test_enrichment_policy_requires_exact_supported_route_selection() -> None:
    with pytest.raises(ValidationError, match="cannot enable both"):
        SocEnrichmentPolicy(
            policy_version="test-v1",
            enabled_routes=["asset.lookup", "asset.locate"],
            asset_route="asset.lookup",
        )
    with pytest.raises(ValidationError, match="unsupported automatic enrichment routes"):
        SocEnrichmentPolicy(
            policy_version="test-v1",
            enabled_routes=["mcp.invoke"],
        )
    with pytest.raises(ValidationError, match="must be selected explicitly"):
        SocEnrichmentPolicy(
            policy_version="test-v1",
            enabled_routes=["asset.locate"],
        )
    with pytest.raises(ValidationError, match="must exactly match its route"):
        SocOrchestratorActionSpec(
            route="asset.lookup",
            action="security_tag.lookup",
        )


def test_planner_is_disabled_by_default_and_blocks_tenant_mismatch() -> None:
    run = _analysis_run()
    plan = SocEnrichmentPlanner(SocEnrichmentPolicy(policy_version="disabled-v1")).plan(run, thread_id="THR-disabled")

    assert plan.status is SocEnrichmentPlanStatus.NO_ACTIONS
    assert plan.actions == []
    assert plan.decision_immutable is True
    assert plan.high_risk_actions_allowed is False

    blocked = SocEnrichmentPlanner(
        SocEnrichmentPolicy(
            policy_version="tenant-v1",
            tenant_id="another-tenant",
            enabled_routes=["security_tag.lookup"],
        )
    ).plan(run, thread_id="THR-blocked")
    assert blocked.status is SocEnrichmentPlanStatus.BLOCKED
    assert blocked.skipped[0].reason_code is SocEnrichmentSkipReason.TENANT_MISMATCH


def test_threat_intel_requires_tenant_network_scope_and_filters_internal_ips() -> None:
    run = _analysis_run()
    unscoped = SocEnrichmentPlanner(
        SocEnrichmentPolicy(
            policy_version="ti-unscoped-v1",
            tenant_id="tenant-a",
            enabled_routes=["threat_intel.ip_reputation.lookup"],
        )
    ).plan(run, thread_id="THR-ti-unscoped")

    assert unscoped.status is SocEnrichmentPlanStatus.NO_ACTIONS
    assert {item.reason_code for item in unscoped.skipped} == {SocEnrichmentSkipReason.NETWORK_SCOPE_UNCONFIGURED}

    scoped = SocEnrichmentPlanner(_full_policy(max_actions_total=8)).plan(
        run,
        thread_id="THR-ti-scoped",
    )
    reputation_actions = [action for action in scoped.actions if action.route == "threat_intel.ip_reputation.lookup"]
    assert [action.payload for action in reputation_actions] == [{"ip": "8.8.8.8"}]
    assert any(item.route == "threat_intel.ip_reputation.lookup" and item.entity_key == "30.1.2.3" and item.reason_code is SocEnrichmentSkipReason.INTERNAL_OR_NON_GLOBAL_IP for item in scoped.skipped)


def test_planner_is_replay_stable_deduplicates_entities_and_applies_budgets() -> None:
    run = _analysis_run()
    planner = SocEnrichmentPlanner(_full_policy(max_actions_total=4))

    first = planner.plan(run, thread_id="THR-stable")
    second = planner.plan(run, thread_id="THR-stable")
    another_thread = planner.plan(run, thread_id="THR-another-surface")
    reordered_run = run.model_copy(deep=True)
    assert reordered_run.entities is not None
    reordered_run.entities.mentions.reverse()
    reordered = planner.plan(reordered_run, thread_id="THR-stable")

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert another_thread.plan_id == first.plan_id
    assert another_thread.input_hash == first.input_hash
    assert [item.action_id for item in another_thread.actions] == [item.action_id for item in first.actions]
    assert reordered.plan_id == first.plan_id
    assert [item.model_dump(mode="json") for item in reordered.actions] == [item.model_dump(mode="json") for item in first.actions]
    assert first.status is SocEnrichmentPlanStatus.PLANNED
    assert len(first.actions) == 4
    keys = [(action.route, action.entity_kind, action.entity_key) for action in first.actions]
    assert len(keys) == len(set(keys))
    assert all(action.read_only and action.decision_impact == "none" for action in first.actions)
    assert any(item.reason_code is SocEnrichmentSkipReason.ACTION_BUDGET_EXHAUSTED for item in first.skipped)


def test_invalid_typed_ip_is_skipped_instead_of_failing_the_plan() -> None:
    run = _analysis_run()
    assert run.entities is not None
    run.entities.mentions.append(
        EntityMention(
            kind=EntityKind.IP,
            value="not-an-ip",
            key="ip:not-an-ip",
            role="threat_ioc",
            evidence_path="entities.threat.iocs[99]",
        )
    )

    plan = SocEnrichmentPlanner(_full_policy(max_actions_total=8)).plan(run, thread_id="THR-invalid-ip")

    skipped = [item for item in plan.skipped if item.reason_code is SocEnrichmentSkipReason.INVALID_ENTITY]
    assert len(skipped) == 1
    assert skipped[0].entity_key == "not-an-ip"
    assert skipped[0].evidence_refs == ["entities.threat.iocs[99]"]
    assert all(action.entity_key != "not-an-ip" for action in plan.actions)


def test_role_conflict_is_preserved_without_blocking_safe_read_only_lookup() -> None:
    run = _analysis_run()
    assert run.fact_reconstruction is not None
    run.fact_reconstruction.role_resolutions.append(
        RoleResolution(
            role="attacker",
            status=RoleResolutionStatus.CONFLICTED,
            selected_value="8.8.8.8",
            semantic_confidence=0.5,
            rationale="two source claims disagree on the attacker role",
            automation_allowed=False,
        )
    )
    plan = SocEnrichmentPlanner(
        SocEnrichmentPolicy(
            policy_version="conflict-v1",
            tenant_id="tenant-a",
            enabled_routes=["threat_intel.ip_reputation.lookup"],
            internal_networks=["30.0.0.0/8"],
        )
    ).plan(run, thread_id="THR-conflict")

    action = plan.actions[0]
    assert action.payload == {"ip": "8.8.8.8"}
    assert "Role semantics remain conflicted for attacker" in action.rationale
    assert action.decision_impact == "none"
    assert plan.high_risk_actions_allowed is False


def test_main_orchestrator_dispatches_planned_actions_without_mutating_analysis_run() -> None:
    summary_repository = InMemoryAlertSummaryRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    recording_analysis = _RecordingAnalysisService(SocAnalysisService(summary_repository=summary_repository))
    registry = SocActionAdapterRegistry(
        [
            InMemoryAssetLookupActionAdapter(),
            InMemoryThreatIntelIpReputationLookupActionAdapter(),
            InMemorySecurityTagLookupActionAdapter(),
        ]
    )
    service = SocMainOrchestratorService(
        analysis_service=recording_analysis,  # type: ignore[arg-type]
        correlation_service=SocCorrelationService(
            summary_repository=summary_repository,
            evidence_repository=evidence_repository,
        ),
        evidence_repository=evidence_repository,
        action_adapter_registry=registry,
        enrichment_planner=SocEnrichmentPlanner(_full_policy(max_actions_total=8)),
    )

    report = service.run(
        SocMainOrchestratorRequest(
            payload=_alert_payload(),
            sample_id="auto-enrichment",
            thread_id="THR-auto-enrichment",
        )
    )

    assert report.enrichment_plan is not None
    assert report.enrichment_plan.status is SocEnrichmentPlanStatus.PLANNED
    assert report.route_steps
    assert all(step.origin == "planned" and step.plan_action_id for step in report.route_steps)
    assert all(step.status == "success" and step.evidence_id for step in report.route_steps)
    assert len(report.investigation_evidence) == len(report.route_steps)
    assert recording_analysis.run_snapshot == report.run.model_dump(mode="json")
    assert report.metadata["writes_db"] is False
    assert report.metadata["executes_high_risk_actions"] is False


def test_explicit_action_takes_precedence_over_same_planned_action() -> None:
    summary_repository = InMemoryAlertSummaryRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    service = SocMainOrchestratorService(
        analysis_service=SocAnalysisService(summary_repository=summary_repository),
        correlation_service=SocCorrelationService(
            summary_repository=summary_repository,
            evidence_repository=evidence_repository,
        ),
        evidence_repository=evidence_repository,
        action_adapter_registry=SocActionAdapterRegistry([InMemoryThreatIntelIpReputationLookupActionAdapter()]),
        enrichment_planner=SocEnrichmentPlanner(
            SocEnrichmentPolicy(
                policy_version="ti-only-v1",
                tenant_id="tenant-a",
                enabled_routes=["threat_intel.ip_reputation.lookup"],
                internal_networks=["30.0.0.0/8"],
            )
        ),
    )

    report = service.run(
        SocMainOrchestratorRequest(
            payload=_alert_payload(),
            sample_id="explicit-dedup",
            thread_id="THR-explicit-dedup",
            action_specs=[
                SocOrchestratorActionSpec(
                    route="threat_intel.ip_reputation.lookup",
                    payload={"ip": "8.8.8.8"},
                )
            ],
        )
    )

    assert len(report.route_steps) == 1
    assert report.route_steps[0].origin == "explicit"
    assert report.metadata["planned_enrichment_deduplicated_count"] == 1


class _RecordingAnalysisService:
    def __init__(self, delegate: SocAnalysisService) -> None:
        self._delegate = delegate
        self.run_snapshot: dict | None = None

    def analyze(self, payload: dict, *, context=None):
        run = self._delegate.analyze(payload, context=context)
        self.run_snapshot = deepcopy(run.model_dump(mode="json"))
        return run


def _analysis_run():
    return SocAnalysisService().analyze(_alert_payload())


def _alert_payload() -> dict:
    return AlertInput(
        tenant_id="tenant-a",
        alert_id="ALT-ENRICHMENT-001",
        entities=AlertEntitySet(
            network=NetworkEntityRef(
                source_ip="8.8.8.8",
                destination_ip="30.1.2.3",
                domain="example.test",
            ),
            host=HostEntityRef(
                host_name="workstation-01",
                ip_addresses=["30.1.2.3"],
            ),
            user=UserEntityRef(
                username="analyst-a",
                um_account="UM00001",
            ),
            threat=ThreatEntityRef(iocs=["30.1.2.3"]),
        ),
    ).model_dump(mode="json")


def _full_policy(*, max_actions_total: int) -> SocEnrichmentPolicy:
    return SocEnrichmentPolicy(
        policy_version="tenant-a-enrichment-v1",
        tenant_id="tenant-a",
        enabled_routes=[
            "asset.lookup",
            "threat_intel.ip_reputation.lookup",
            "security_tag.lookup",
        ],
        asset_route="asset.lookup",
        internal_networks=["30.0.0.0/8"],
        max_actions_total=max_actions_total,
        max_actions_per_route=3,
    )
