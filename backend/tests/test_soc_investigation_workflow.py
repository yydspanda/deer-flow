from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import soc_agent.cli as soc_cli
import soc_agent.core.investigation_workflow as investigation_workflow
from soc_agent.actions.adapters import (
    InMemoryAssetLookupActionAdapter,
    SocActionAdapterRegistry,
    asset_lookup_adapter_descriptor,
)
from soc_agent.application import build_soc_investigation_workflow_service
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertEntitySet,
    AlertInput,
    EntrySurface,
    InvestigationEvidence,
    NetworkEntityRef,
    ServiceRequestContext,
    SocAgentActionResult,
    SocEnrichmentActionAttempt,
    SocEnrichmentAttemptStatus,
    SocEnrichmentCompositionConfig,
    SocEnrichmentExecution,
    SocEnrichmentExecutionCommand,
    SocEnrichmentExecutionStatus,
    SocEnrichmentExecutionTrigger,
    SocEnrichmentReplayCommand,
)
from soc_agent.core import (
    InMemoryInvestigationEvidenceRepository,
    InMemorySocEnrichmentExecutionRepository,
    SocAnalysisService,
    SocEnrichmentPlanner,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.utils.hashing import stable_hash


class _RunRepository:
    def __init__(self, run) -> None:
        self.run = run

    def get_run(self, run_id: str):
        return self.run.model_copy(deep=True) if run_id == self.run.run_id else None


class _RecordingAdapter:
    def __init__(self, results: list[SocAgentActionResult], *, runtime_declared: bool = False) -> None:
        metadata = (
            {
                "result_provenance_contract": "runtime_declared",
                "result_mode_field": "mocked",
            }
            if runtime_declared
            else {"result_provenance_contract": "mock_only"}
        )
        self.descriptor = asset_lookup_adapter_descriptor(adapter_id="asset-lookup-recording").model_copy(update={"metadata": metadata})
        self.results = list(results)
        self.calls = 0

    def dry_run(self, command, *, context):  # pragma: no cover - workflow executes
        raise AssertionError("persistent investigation does not dry-run providers")

    def execute(self, command, *, context):
        self.calls += 1
        index = min(self.calls - 1, len(self.results) - 1)
        return self.results[index]


def test_persistent_workflow_distinguishes_not_found_and_deduplicates_repeat() -> None:
    run = _analysis_run()
    execution_repository = InMemorySocEnrichmentExecutionRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    service = build_soc_investigation_workflow_service(
        composition=_composition(adapter_id="asset-lookup-in-memory"),
        action_adapter_registry=SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter()]),
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )
    command = SocEnrichmentExecutionCommand(
        run_id=run.run_id,
        thread_id="THR-D3-NOT-FOUND",
        trigger=SocEnrichmentExecutionTrigger.KAFKA,
    )
    context = _context("kafka:soc.alerts.raw.v1:0:10")

    first = service.execute(command, context=context)
    second = service.execute(command, context=context)

    assert first.execution.status is SocEnrichmentExecutionStatus.COMPLETED
    assert first.execution.not_found_count == 1
    assert first.execution.failed_count == 0
    assert first.provider_invocation_count == 1
    assert first.attempts[0].status is SocEnrichmentAttemptStatus.NOT_FOUND
    assert first.attempts[0].result_mode.value == "mock"
    assert first.evidence_persisted_count == 1
    assert second.execution.execution_id == first.execution.execution_id
    assert second.idempotent_replay is True
    assert second.provider_invocation_count == 0
    assert len(evidence_repository.list_evidence(run_id=run.run_id)) == 1
    assert evidence_repository.list_evidence(run_id=run.run_id)[0].mocked is True


def test_runtime_declared_mode_mismatch_fails_before_evidence_write() -> None:
    run = _analysis_run()
    adapter = _RecordingAdapter(
        [
            SocAgentActionResult(
                route="asset.lookup",
                action="asset.lookup",
                status="success",
                message="mock result",
                payload={"asset_found": True, "mocked": True},
            )
        ],
        runtime_declared=True,
    )
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    service = build_soc_investigation_workflow_service(
        composition=_composition(result_mode="real"),
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        run_repository=_RunRepository(run),
        execution_repository=InMemorySocEnrichmentExecutionRepository(),
        evidence_repository=evidence_repository,
    )

    result = service.execute(
        SocEnrichmentExecutionCommand(
            run_id=run.run_id,
            thread_id="THR-D3-MODE",
            trigger=SocEnrichmentExecutionTrigger.INTERNAL_BATCH,
        ),
        context=_context("batch:mode-mismatch"),
    )

    assert adapter.calls == 1
    assert result.execution.status is SocEnrichmentExecutionStatus.FAILED
    assert result.execution.retryable is False
    assert result.attempts[0].status is SocEnrichmentAttemptStatus.CONTRACT_FAILED
    assert result.attempts[0].error_type == "ResultModeContractError"
    assert result.evidence_persisted_count == 0
    assert evidence_repository.list_evidence(run_id=run.run_id) == []


def test_retry_resumes_only_failed_action_and_replay_creates_linked_execution() -> None:
    run = _analysis_run()
    adapter = _RecordingAdapter(
        [
            SocAgentActionResult(
                route="asset.lookup",
                action="asset.lookup",
                status="failed",
                message="provider temporarily unavailable",
                payload={"error_type": "ProviderExecutionError"},
            ),
            SocAgentActionResult(
                route="asset.lookup",
                action="asset.lookup",
                status="success",
                message="asset located",
                payload={"asset_found": True, "asset_record": {"owner": "soc"}},
            ),
        ]
    )
    execution_repository = InMemorySocEnrichmentExecutionRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    service = build_soc_investigation_workflow_service(
        composition=_composition(max_attempts=2),
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )
    command = SocEnrichmentExecutionCommand(
        run_id=run.run_id,
        thread_id="THR-D3-RETRY",
        trigger=SocEnrichmentExecutionTrigger.KAFKA,
    )
    context = _context("kafka:soc.alerts.raw.v1:0:11")

    failed = service.execute(command, context=context)
    recovered = service.execute(command, context=context)
    replayed = service.replay(
        SocEnrichmentReplayCommand(
            execution_id=recovered.execution.execution_id,
            reason="operator replay against current reviewed composition",
        ),
        context=_context("manual:replay:1"),
    )

    assert failed.execution.status is SocEnrichmentExecutionStatus.RETRYABLE_FAILED
    assert failed.execution.retryable is True
    assert recovered.execution.status is SocEnrichmentExecutionStatus.COMPLETED
    assert [item.status for item in recovered.attempts] == [
        SocEnrichmentAttemptStatus.PROVIDER_FAILED,
        SocEnrichmentAttemptStatus.SUCCESS,
    ]
    assert recovered.provider_invocation_count == 1
    assert replayed.execution.trigger is SocEnrichmentExecutionTrigger.REPLAY
    assert replayed.execution.replay_of_execution_id == recovered.execution.execution_id
    assert replayed.execution.execution_id != recovered.execution.execution_id
    assert replayed.execution.status is SocEnrichmentExecutionStatus.COMPLETED
    assert adapter.calls == 3
    assert len(evidence_repository.list_evidence(run_id=run.run_id)) == 2


def test_stale_running_attempt_recovers_persisted_evidence_without_provider_reinvocation() -> None:
    run = _analysis_run()
    composition = _composition(max_attempts=2)
    context = _context("kafka:soc.alerts.raw.v1:0:12")
    execution_repository = InMemorySocEnrichmentExecutionRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    execution, attempt = _seed_stale_running_execution(
        run=run,
        composition=composition,
        context=context,
        thread_id="THR-D3-STALE-EVIDENCE",
        execution_repository=execution_repository,
    )
    evidence_repository.save_evidence(
        InvestigationEvidence(
            evidence_id=investigation_workflow._evidence_id(
                execution.execution_id,
                attempt.plan_action_id,
            ),
            route=attempt.route,
            action=attempt.action,
            status="success",
            message="asset located before process interruption",
            result_payload={"asset_found": True, "asset_record": {"owner": "soc"}},
            mocked=True,
            run_id=execution.run_id,
            alert_id=execution.alert_id,
            thread_id=execution.thread_id,
            source_proposal_id=attempt.plan_action_id,
            context_hash=execution.plan.input_hash,
            request_id=context.request_id,
            trace_id=context.trace_id,
            actor=context.actor,
        )
    )
    adapter = _RecordingAdapter([_successful_asset_result()])
    service = build_soc_investigation_workflow_service(
        composition=composition,
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )

    recovered = service.execute(
        SocEnrichmentExecutionCommand(
            run_id=run.run_id,
            thread_id=execution.thread_id,
            trigger=SocEnrichmentExecutionTrigger.KAFKA,
        ),
        context=context,
    )

    assert recovered.execution.status is SocEnrichmentExecutionStatus.COMPLETED
    assert recovered.provider_invocation_count == 0
    assert recovered.evidence_persisted_count == 1
    assert [item.status for item in recovered.attempts] == [SocEnrichmentAttemptStatus.SUCCESS]
    assert adapter.calls == 0


def test_stale_running_attempt_without_evidence_is_interrupted_then_retried_once() -> None:
    run = _analysis_run()
    composition = _composition(max_attempts=2)
    context = _context("kafka:soc.alerts.raw.v1:0:13")
    execution_repository = InMemorySocEnrichmentExecutionRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    execution, _attempt = _seed_stale_running_execution(
        run=run,
        composition=composition,
        context=context,
        thread_id="THR-D3-STALE-NO-EVIDENCE",
        execution_repository=execution_repository,
    )
    adapter = _RecordingAdapter([_successful_asset_result()])
    service = build_soc_investigation_workflow_service(
        composition=composition,
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )

    recovered = service.execute(
        SocEnrichmentExecutionCommand(
            run_id=run.run_id,
            thread_id=execution.thread_id,
            trigger=SocEnrichmentExecutionTrigger.KAFKA,
        ),
        context=context,
    )

    assert recovered.execution.status is SocEnrichmentExecutionStatus.COMPLETED
    assert recovered.provider_invocation_count == 1
    assert recovered.execution.attempt_count == 2
    assert [item.status for item in recovered.attempts] == [
        SocEnrichmentAttemptStatus.INTERRUPTED,
        SocEnrichmentAttemptStatus.SUCCESS,
    ]
    assert adapter.calls == 1


def test_sql_repository_enforces_cross_instance_idempotency_and_cas() -> None:
    engine = create_engine("sqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    first_repository = SqlAlchemyAlertRepository(session_factory)
    second_repository = SqlAlchemyAlertRepository(session_factory)
    run = _analysis_run()
    first_repository.save_run(run)
    adapter = _RecordingAdapter(
        [
            SocAgentActionResult(
                route="asset.lookup",
                action="asset.lookup",
                status="success",
                message="asset located",
                payload={"asset_found": True},
            )
        ]
    )
    registry = SocActionAdapterRegistry([adapter])
    first_service = build_soc_investigation_workflow_service(
        composition=_composition(),
        action_adapter_registry=registry,
        run_repository=first_repository,
        execution_repository=first_repository,
        evidence_repository=first_repository,
    )
    second_service = build_soc_investigation_workflow_service(
        composition=_composition(),
        action_adapter_registry=registry,
        run_repository=second_repository,
        execution_repository=second_repository,
        evidence_repository=second_repository,
    )
    command = SocEnrichmentExecutionCommand(
        run_id=run.run_id,
        thread_id="THR-D3-SQL",
        trigger=SocEnrichmentExecutionTrigger.INTERNAL_BATCH,
    )
    context = _context("batch:sql:1")

    first = first_service.execute(command, context=context)
    duplicate = second_service.execute(command, context=context)

    assert first.execution.status is SocEnrichmentExecutionStatus.COMPLETED
    assert duplicate.execution.execution_id == first.execution.execution_id
    assert duplicate.idempotent_replay is True
    assert adapter.calls == 1
    assert len(second_repository.list_enrichment_action_attempts(first.execution.execution_id)) == 1
    assert len(second_repository.list_evidence(run_id=run.run_id)) == 1


def test_cli_get_and_replay_use_the_persistent_workflow_boundary(
    monkeypatch,
    capsys,
) -> None:
    run = _analysis_run()
    execution_repository = InMemorySocEnrichmentExecutionRepository()
    evidence_repository = InMemoryInvestigationEvidenceRepository()
    service = build_soc_investigation_workflow_service(
        composition=_composition(adapter_id="asset-lookup-in-memory"),
        action_adapter_registry=SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter()]),
        run_repository=_RunRepository(run),
        execution_repository=execution_repository,
        evidence_repository=evidence_repository,
    )
    original = service.execute(
        SocEnrichmentExecutionCommand(
            run_id=run.run_id,
            thread_id="THR-D3-CLI",
            trigger=SocEnrichmentExecutionTrigger.MANUAL,
        ),
        context=_context("manual:cli:original"),
    )
    monkeypatch.setattr(soc_cli, "_repository_from_args", lambda _args: execution_repository)
    monkeypatch.setattr(
        soc_cli,
        "_investigation_service_from_args",
        lambda _args, _repository: service,
    )

    get_exit = soc_cli.main(["investigation", "get", original.execution.execution_id, "--pretty"])
    fetched = json.loads(capsys.readouterr().out)
    replay_exit = soc_cli.main(
        [
            "investigation",
            "replay",
            original.execution.execution_id,
            "--reason",
            "operator requested a current-policy replay",
            "--idempotency-key",
            "manual:cli:replay",
            "--confirm-investigation",
            "--pretty",
        ]
    )
    replayed = json.loads(capsys.readouterr().out)

    assert get_exit == 0
    assert fetched["execution"]["execution_id"] == original.execution.execution_id
    assert fetched["idempotent_replay"] is False
    assert fetched["provider_invocation_count"] == 0
    assert replay_exit == 0
    assert replayed["execution"]["trigger"] == "replay"
    assert replayed["execution"]["replay_of_execution_id"] == original.execution.execution_id
    assert replayed["execution"]["execution_id"] != original.execution.execution_id
    fetched_from_service = service.get(original.execution.execution_id)
    assert fetched_from_service is not None
    assert fetched_from_service.execution.status is SocEnrichmentExecutionStatus.COMPLETED
    assert fetched_from_service.idempotent_replay is False


def _analysis_run():
    return SocAnalysisService().analyze(
        AlertInput(
            tenant_id="tenant-a",
            alert_id="ALT-D3-001",
            entities=AlertEntitySet(network=NetworkEntityRef(destination_ip="10.20.30.40")),
        ).model_dump(mode="json")
    )


def _seed_stale_running_execution(
    *,
    run,
    composition: SocEnrichmentCompositionConfig,
    context: ServiceRequestContext,
    thread_id: str,
    execution_repository: InMemorySocEnrichmentExecutionRepository,
) -> tuple[SocEnrichmentExecution, SocEnrichmentActionAttempt]:
    assert context.idempotency_key is not None
    plan = SocEnrichmentPlanner(composition.policy).plan(run, thread_id=thread_id)
    planned = plan.actions[0]
    execution_id = investigation_workflow._execution_id(context.idempotency_key)
    stale_at = datetime.now(UTC) - timedelta(seconds=composition.retry_policy.stale_after_seconds + 1)
    execution = SocEnrichmentExecution(
        execution_id=execution_id,
        idempotency_key=context.idempotency_key,
        trigger=SocEnrichmentExecutionTrigger.KAFKA,
        run_id=run.run_id,
        alert_id=run.alert_id,
        thread_id=thread_id,
        plan=plan,
        composition_hash=stable_hash(composition.model_dump(mode="json")),
        required_result_mode=composition.required_result_mode,
        request_id=context.request_id,
        trace_id=context.trace_id,
        actor_id=context.actor.actor_id,
        created_at=stale_at,
        updated_at=stale_at,
    )
    action_key = investigation_workflow._action_idempotency_key(
        execution_id,
        planned.action_id,
        1,
    )
    attempt = SocEnrichmentActionAttempt(
        attempt_id=investigation_workflow._attempt_id(action_key),
        execution_id=execution_id,
        plan_action_id=planned.action_id,
        attempt_number=1,
        action_idempotency_key=action_key,
        route=planned.route,
        action=planned.action,
        adapter_id="asset-lookup-recording",
        request_id=context.request_id,
        trace_id=context.trace_id,
        started_at=stale_at,
    )
    assert execution_repository.create_enrichment_execution(execution)
    assert execution_repository.create_enrichment_action_attempt(attempt)
    return execution, attempt


def _successful_asset_result() -> SocAgentActionResult:
    return SocAgentActionResult(
        route="asset.lookup",
        action="asset.lookup",
        status="success",
        message="asset located",
        payload={"asset_found": True, "asset_record": {"owner": "soc"}},
    )


def _composition(
    *,
    result_mode: str = "mock",
    max_attempts: int = 3,
    adapter_id: str = "asset-lookup-recording",
) -> SocEnrichmentCompositionConfig:
    return SocEnrichmentCompositionConfig.model_validate(
        {
            "enabled": True,
            "required_result_mode": result_mode,
            "policy": {
                "policy_version": "tenant-a-enrichment-v1",
                "tenant_id": "tenant-a",
                "enabled_routes": ["asset.lookup"],
                "asset_route": "asset.lookup",
                "internal_networks": ["10.0.0.0/8"],
                "max_actions_total": 1,
                "max_actions_per_route": 1,
            },
            "retry_policy": {
                "max_attempts_per_action": max_attempts,
                "stale_after_seconds": 60,
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
            actor_id="soc-d3-test",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=["soc_daemon"],
        ),
        trace_id=idempotency_key,
        idempotency_key=idempotency_key,
    )
