from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import soc_agent.cli as soc_cli
from soc_agent.actions.adapters import (
    InMemoryAssetLookupActionAdapter,
    SocActionAdapterRegistry,
)
from soc_agent.application import build_soc_investigation_workflow_service
from soc_agent.context_bridge import build_lead_agent_review_context_artifact
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertEntitySet,
    AlertInput,
    EntrySurface,
    NetworkEntityRef,
    ReviewQueueItem,
    ServiceRequestContext,
    SocAgentActionResult,
    SocEnrichmentCompositionConfig,
    SocEnrichmentExecutionCommand,
    SocEnrichmentExecutionStatus,
    SocEnrichmentExecutionTrigger,
)
from soc_agent.core import (
    InMemoryInvestigationEvidenceRepository,
    InMemorySocEnrichmentExecutionRepository,
    SocAnalysisService,
    SocInvestigationReportingService,
    SocReviewService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables


class _RunRepository:
    def __init__(self, run) -> None:
        self.run = run

    def get_run(self, run_id: str):
        return self.run.model_copy(deep=True) if run_id == self.run.run_id else None


class _RetryingAdapter:
    def __init__(self) -> None:
        base = InMemoryAssetLookupActionAdapter().descriptor
        self.descriptor = base.model_copy(
            update={
                "adapter_id": "asset-lookup-retrying",
                "metadata": {"result_provenance_contract": "mock_only"},
            }
        )
        self.calls = 0

    def dry_run(self, command, *, context):  # pragma: no cover - workflow executes
        raise AssertionError("reporting test does not dry-run")

    def execute(self, command, *, context):
        self.calls += 1
        if self.calls == 1:
            return SocAgentActionResult(
                route="asset.lookup",
                action="asset.lookup",
                status="failed",
                message="temporary provider outage",
                payload={"error_type": "ProviderExecutionError"},
            )
        return SocAgentActionResult(
            route="asset.lookup",
            action="asset.lookup",
            status="success",
            message="asset located",
            payload={"asset_found": True, "mocked": True},
        )


def test_shadow_report_and_addendum_are_bounded_recomputable_projections() -> None:
    run = _analysis_run()
    execution_repository = InMemorySocEnrichmentExecutionRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    workflow = build_soc_investigation_workflow_service(
        composition=_composition(adapter_id="asset-lookup-in-memory"),
        action_adapter_registry=SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter()]),
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )
    result = workflow.execute(
        SocEnrichmentExecutionCommand(
            run_id=run.run_id,
            thread_id="THR-D4-REPORT",
            trigger=SocEnrichmentExecutionTrigger.INTERNAL_BATCH,
        ),
        context=_context("d4:report"),
    )
    reporting = SocInvestigationReportingService(
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )

    report = reporting.get_shadow_report(result.execution.execution_id)
    addendum = reporting.get_addendum(result.execution.execution_id)
    repeated = reporting.get_addendum(result.execution.execution_id)

    assert report is not None
    assert addendum is not None
    assert repeated is not None
    assert report.not_found_count == 1
    assert report.success_count == 0
    assert report.provider_invocation_count == 1
    assert report.evidence_coverage_ratio == 1.0
    assert report.cost_measurement_status.value == "not_measured"
    assert report.decision_impact == "none"
    assert report.base_run_mutated is False
    assert addendum.addendum_id == repeated.addendum_id
    assert addendum.new_conclusion_produced is False
    assert addendum.base_runtime_verdict == run.decision.verdict.value
    assert addendum.items[0].status == "not_found"
    assert addendum.items[0].evidence_available is True
    telemetry_json = report.model_dump_json()
    assert "10.20.30.40" not in telemetry_json
    assert "asset_key" not in telemetry_json
    assert "result_payload" not in telemetry_json

    [persisted_evidence] = evidence_repository.list_evidence(run_id=run.run_id)
    evidence_repository.save_evidence(persisted_evidence.model_copy(update={"message": "updated evidence summary"}))
    changed_bundle = reporting.get_report_bundle(result.execution.execution_id)
    assert changed_bundle is not None
    changed_report, changed_addendum = changed_bundle
    assert changed_report.source_hash != report.source_hash
    assert changed_addendum.addendum_id != addendum.addendum_id


def test_reporting_exposes_retry_and_missing_evidence_without_claiming_successful_coverage() -> None:
    run = _analysis_run()
    adapter = _RetryingAdapter()
    execution_repository = InMemorySocEnrichmentExecutionRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    workflow = build_soc_investigation_workflow_service(
        composition=_composition(adapter_id=adapter.descriptor.adapter_id),
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )
    command = SocEnrichmentExecutionCommand(
        run_id=run.run_id,
        thread_id="THR-D4-RETRY",
        trigger=SocEnrichmentExecutionTrigger.KAFKA,
    )
    context = _context("d4:retry")
    first = workflow.execute(command, context=context)
    completed = workflow.execute(command, context=context)
    assert first.execution.status is SocEnrichmentExecutionStatus.RETRYABLE_FAILED
    assert completed.execution.status is SocEnrichmentExecutionStatus.COMPLETED

    reporting = SocInvestigationReportingService(
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )
    report = reporting.get_shadow_report(completed.execution.execution_id)
    assert report is not None
    assert report.retry_count == 1
    assert report.provider_invocation_count == 2
    assert report.success_count == 1
    assert report.routes[0].provider_failure_attempt_count == 1
    assert report.routes[0].attempt_latency_sample_count == 2

    missing_evidence_reporting = SocInvestigationReportingService(
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=InMemoryInvestigationEvidenceRepository(),
    )
    degraded = missing_evidence_reporting.get_shadow_report(completed.execution.execution_id)
    degraded_addendum = missing_evidence_reporting.get_addendum(completed.execution.execution_id)
    assert degraded is not None
    assert degraded_addendum is not None
    assert degraded.persisted_evidence_count == 0
    assert degraded.missing_evidence_count == 1
    assert degraded.evidence_coverage_ratio == 0.0
    assert "referenced_investigation_evidence_missing" in degraded.measurement_gaps
    assert degraded_addendum.analyst_attention_required is True
    assert degraded_addendum.items[0].evidence_available is False


def test_review_context_cli_and_lead_agent_include_the_same_sql_backed_addendum(
    monkeypatch,
    capsys,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = _analysis_run()
    repository.save_run(run)
    queue_item = ReviewQueueItem(
        queue_id="REV-D4-001",
        run_id=run.run_id,
        alert_id=run.alert_id,
        reason="D4 analyst review",
    )
    repository.save_review_item(queue_item)
    workflow = build_soc_investigation_workflow_service(
        composition=_composition(adapter_id="asset-lookup-in-memory"),
        action_adapter_registry=SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter()]),
        run_repository=repository,
        execution_repository=repository,
        evidence_repository=repository,
    )
    result = workflow.execute(
        SocEnrichmentExecutionCommand(
            run_id=run.run_id,
            thread_id="THR-D4-CONTEXT",
            trigger=SocEnrichmentExecutionTrigger.MANUAL,
        ),
        context=_context("d4:context"),
    )
    review_service = SocReviewService(
        repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        enrichment_execution_repository=repository,
    )

    context = review_service.get_investigation_context(queue_item.queue_id)
    artifact = build_lead_agent_review_context_artifact(context)

    assert len(context.investigation_addenda) == 1
    assert context.investigation_addenda[0].execution_id == result.execution.execution_id
    assert context.investigation_view is not None
    assert context.investigation_view.counts["investigation_addenda"] == 1
    assert any(item.kind == "investigation_addendum" for item in context.investigation_view.evidence_timeline)
    assert artifact.investigation_addenda[0]["execution_id"] == result.execution.execution_id
    assert artifact.investigation_addenda[0]["new_conclusion_produced"] is False
    assert artifact.analysis["analysis_materiality"]["core_usable"] is True
    assert artifact.analysis["analysis_materiality"]["review_required"] is False
    assert "attacker_targeting" in {item["capability"] for item in artifact.analysis["analysis_materiality"]["blocked_capabilities"]}

    monkeypatch.setattr(soc_cli, "_repository_from_args", lambda _args: repository)
    exit_code = soc_cli.main(["investigation", "report", result.execution.execution_id, "--pretty"])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["shadow_report"]["report_id"].startswith("ISHR-")
    assert output["investigation_addendum"]["execution_id"] == result.execution.execution_id
    assert output["investigation_addendum"]["base_run_mutated"] is False


def _analysis_run():
    return SocAnalysisService().analyze(
        AlertInput(
            tenant_id="tenant-a",
            alert_id="ALT-D4-001",
            entities=AlertEntitySet(network=NetworkEntityRef(destination_ip="10.20.30.40")),
        ).model_dump(mode="json")
    )


def _composition(*, adapter_id: str) -> SocEnrichmentCompositionConfig:
    return SocEnrichmentCompositionConfig.model_validate(
        {
            "enabled": True,
            "required_result_mode": "mock",
            "policy": {
                "policy_version": "tenant-a-d4-v1",
                "tenant_id": "tenant-a",
                "enabled_routes": ["asset.lookup"],
                "asset_route": "asset.lookup",
                "internal_networks": ["10.0.0.0/8"],
                "max_actions_total": 1,
                "max_actions_per_route": 1,
            },
            "bindings": [
                {
                    "route": "asset.lookup",
                    "action": "asset.lookup",
                    "adapter_id": adapter_id,
                    "adapter_kind": "service",
                }
            ],
        }
    )


def _context(idempotency_key: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-d4-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=["soc_daemon"],
        ),
        trace_id=idempotency_key,
        idempotency_key=idempotency_key,
    )
